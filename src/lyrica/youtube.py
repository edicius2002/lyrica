"""Which video is playing, when the only evidence is what the media session says.

Windows does not publish it. `GlobalSystemMediaTransportControlsSessionMediaProperties`
has ten properties and none of them is a URL; the source app is `chrome.exe`;
and the thumbnail, which for YouTube really is `i.ytimg.com/vi/<id>/…`, arrives
as a decoded bitmap with its origin discarded at the browser boundary.

So the identifier is recovered rather than read: search for what the player says
the track is called, then keep only the result whose length matches what the
player says it lasts. The verbose title a browser reports — the one that repeats
the artist and carries "(Official Video)" — is an asset here, because it is very
nearly the video's real title.

The length check is what makes this safe rather than merely likely. Without it a
SoundCloud stream, which reaches the media session looking much the same, would
be matched to somebody's YouTube upload and given that video's intro.

Needs a key of the user's own, for the same reason Discogs does: a credential
inside a repository is a credential waiting to be committed. Without one this
returns nothing and everything downstream simply does not happen.
"""
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

from lyrica import config

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
HEADERS = {"User-Agent": "lyrica/0.2.5 (personal overlay)"}

# How many candidates the length check gets to choose between. A search costs a
# hundred quota units against a free allowance of ten thousand a day, so the
# limit that matters is the number of searches, not their width.
CANDIDATES = 5

# How far a candidate's length may be from the session's before it is refused.
# The session reports the video's own duration, so a real match is exact; this
# only absorbs rounding, since the API states whole seconds.
TOLERANCE_S = 2.0

TTL_S = 30 * 24 * 3600

ISO = re.compile(r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$")


def api_key() -> str:
    """The user's key, if they set one. Read from the environment, never stored."""
    return os.environ.get("LYRICA_YOUTUBE_KEY", "").strip()


def parse_duration(value: str) -> float | None:
    """Seconds from an ISO 8601 duration, or None if it is not one."""
    m = ISO.match((value or "").strip())
    if not m:
        return None
    days, hours, minutes, seconds = (float(g or 0) for g in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _path(key: str) -> Path:
    root = config.cache_root() / "videos"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(key.lower().encode(), usedforsecurity=False).hexdigest()
    return root / (digest + ".json")


def _get(url: str, params: dict) -> dict | None:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=8)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        # Deliberately quiet about the response: a rejected key comes back in
        # the body, and this goes to a log file.
        logger.debug("youtube lookup failed")
        return None


def _lengths(ids: list, key: str) -> dict:
    data = _get(VIDEOS_URL, {"part": "contentDetails", "id": ",".join(ids),
                             "key": key})
    if not data:
        return {}
    out = {}
    for item in data.get("items", []):
        seconds = parse_duration(item.get("contentDetails", {}).get("duration", ""))
        if seconds is not None:
            out[item["id"]] = seconds
    return out


def video_id_for(title: str, duration: float) -> str | None:
    """The video whose title is this and whose length is that, or None.

    Cached by the pair, misses included, because a track that is not on YouTube
    stays not on YouTube and a search costs a hundredth of a day's allowance.
    """
    key = api_key()
    if not key or not title or duration <= 1:
        return None
    path = _path(f"{title}|{int(duration)}")
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - path.stat().st_mtime < TTL_S:
            return cached or None
    except (OSError, ValueError):
        pass

    found = _get(SEARCH_URL, {"part": "snippet", "q": title, "type": "video",
                              "maxResults": CANDIDATES, "key": key})
    if found is None:
        return None      # not cached: a failed ask is not a missing video
    ids = [i["id"]["videoId"] for i in found.get("items", [])
           if i.get("id", {}).get("videoId")]
    # Walked in the search's own order, so relevance breaks ties between two
    # uploads of the same length — a lookup by dictionary order would not.
    lengths = _lengths(ids, key) if ids else {}
    best = None
    for video_id in ids:
        seconds = lengths.get(video_id)
        if seconds is not None and abs(seconds - duration) <= TOLERANCE_S:
            best = video_id
            break
    try:
        path.write_text(json.dumps(best), encoding="utf-8")
    except OSError:
        logger.debug("could not cache the video id", exc_info=True)
    return best
