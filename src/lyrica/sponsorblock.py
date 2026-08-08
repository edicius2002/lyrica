"""Where the song actually starts in a music video, asked of the people who
already wrote it down.

A lyric timeline is anchored to the release. A music video is not: it opens with
a film, a spoken intro, a countdown, and the words arrive whole seconds late.
Nothing in the metadata says how many — every synced record for a song claims a
different duration and the same first timestamp — and nothing in the audio finds
it either, at least not with anything cheap enough to ship.

SponsorBlock has a category called `music_offtopic` that exists only for music
videos, and whose stated purpose is that a video, once its segments are skipped,
should resemble the track as the music services have it. A segment beginning at
zero therefore says exactly the thing this module needs: the song starts at its
end. Thousands of people annotated that boundary by hand, for an unrelated
reason.

Checked against twelve videos by taking the video's length, removing every
off-topic stretch, and comparing what was left against the release's own
duration: eleven agreed within seven seconds. The twelfth was Thriller, whose
video is a fourteen-minute short film with an extended dance break — its music
is a different edit from the record, and no offset keeps a release's lyrics on
it. That is the shape of this method's limit.

Queried through the hashed-prefix endpoint, so the service is told four
characters of a digest and answers with every video that shares them. It never
learns which one is playing.
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from lyrica import config

logger = logging.getLogger(__name__)

API = "https://sponsor.ajay.app/api/skipSegments"
HEADERS = {"User-Agent": "lyrica/0.1.0 (personal overlay)"}

# How much of the video's digest is sent. Four hex characters is what the
# service's own privacy endpoint takes, and it returns every video sharing them.
PREFIX = 4

# A segment is believed when it has been locked by a moderator or has attracted
# at least this many votes. Anyone may submit one, and an intro is being trusted
# here to move the lyrics by whole seconds.
MIN_VOTES = 1

# How long an answer is kept. Indefinitely in practice: a video's edit does not
# change, and a fortnight is only here so a video annotated after we first asked
# is eventually picked up.
TTL_S = 14 * 24 * 3600


@dataclass(frozen=True)
class Cuts:
    """The stretches of a video that are not the song.

    Not a single offset, because they are not always one: two videos in fifteen
    carried an off-topic stretch in the middle as well as at the front, and past
    one of those a constant would drift by its whole length.

    An empty `spans` is the identity, so an unannotated video and a video
    annotated as needing no cut behave alike and nothing else has to care.
    """

    spans: tuple = ()

    @property
    def intro(self) -> float:
        """Seconds before the song starts, or 0.0 if it starts at once."""
        return self.spans[0][1] if self.spans and self.spans[0][0] <= 0.01 else 0.0

    def to_song(self, video_t: float) -> float:
        """Where in the recording the video's playhead is.

        A position inside a cut has not reached the next sung moment yet, so it
        maps to the instant that stretch will end at — which holds the lyrics
        still through an intro instead of running them backwards.
        """
        removed = 0.0
        for start, end in self.spans:
            if video_t <= start:
                break
            removed += min(video_t, end) - start
        return video_t - removed

    def to_video(self, song_t: float) -> float:
        """The playhead a moment of the recording sits at. Inverse of `to_song`."""
        at = song_t
        for start, end in self.spans:
            if start > at:
                break
            at += end - start
        return at


def _path(video_id: str) -> Path:
    root = config.cache_root() / "cuts"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(video_id.encode(), usedforsecurity=False).hexdigest()
    return root / (digest + ".json")


def _usable(segment: dict) -> bool:
    return (segment.get("category") == "music_offtopic"
            and (segment.get("locked") or (segment.get("votes") or 0) >= MIN_VOTES))


def _spans(entries: list, video_id: str) -> tuple:
    for entry in entries:
        if entry.get("videoID") != video_id:
            continue
        found = sorted(tuple(s["segment"]) for s in entry.get("segments", [])
                       if _usable(s))
        # Overlaps would be counted twice by `to_song`, and submissions do
        # overlap: two people mark the same intro to slightly different ends.
        merged: list = []
        for start, end in found:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return tuple(merged)
    return ()


def cuts_for(video_id: str) -> Cuts | None:
    """What is not the song in this video, or None if nobody has said.

    None and `Cuts(())` are different answers and the caller should treat them
    so: the first is ignorance, the second is somebody having looked at the
    video and marked nothing at the front.
    """
    if not video_id:
        return None
    path = _path(video_id)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - path.stat().st_mtime < TTL_S:
            return Cuts(tuple(tuple(s) for s in cached)) if cached is not None else None
    except (OSError, ValueError):
        pass

    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:PREFIX]
    try:
        r = requests.get(f"{API}/{prefix}", params={"category": "music_offtopic"},
                         headers=HEADERS, timeout=8)
        if r.status_code == 404:
            entries = []
        else:
            r.raise_for_status()
            entries = r.json()
    except (requests.RequestException, ValueError):
        logger.debug("sponsorblock lookup failed", exc_info=True)
        return None      # not written: a failed ask says nothing about the video

    known = any(e.get("videoID") == video_id for e in entries)
    spans = _spans(entries, video_id) if known else None
    try:
        path.write_text(json.dumps(spans), encoding="utf-8")
    except OSError:
        logger.debug("could not cache the cuts", exc_info=True)
    return Cuts(spans) if spans is not None else None
