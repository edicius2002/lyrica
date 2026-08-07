"""LRCLIB provider (https://lrclib.net) — free, keyless, no rate limits.

Lookup strategy: exact /get first (duration-matched ±2 s server-side),
then fuzzy /search scored by artist/title/duration similarity.
"""

import requests

from lyrica.lyrics import Lyrics, parse_lrc
from lyrica.providers.base import LyricsProvider

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "lyrica/0.1.0 (personal research overlay)"}


def _from_record(d: dict, source: str) -> Lyrics | None:
    if d.get("instrumental"):
        return Lyrics(instrumental=True, source=source)
    if d.get("syncedLyrics"):
        return Lyrics(lines=parse_lrc(d["syncedLyrics"]), plain=d.get("plainLyrics") or "",
                      synced=True, source=source)
    if d.get("plainLyrics"):
        return Lyrics(plain=d["plainLyrics"], synced=False, source=source)
    return None


def _score(rec: dict, artist: str, title: str, duration: float) -> float:
    s = 0.0
    ra = (rec.get("artistName") or "").lower()
    rt = (rec.get("trackName") or "").lower()
    if artist and (artist.lower() in ra or ra in artist.lower()):
        s += 2
    if title.lower() == rt:
        s += 2
    elif title.lower() in rt or rt in title.lower():
        s += 1
    if duration and rec.get("duration"):
        diff = abs(rec["duration"] - duration)
        s += 2 if diff <= 3 else (1 if diff <= 10 else -1)
    if rec.get("syncedLyrics"):
        s += 0.5
    return s


class LrclibProvider(LyricsProvider):
    name = "lrclib"

    def fetch(self, artist: str, title: str, duration: float = 0.0,
              album: str = "") -> Lyrics | None:
        if not title:
            return None

        # Exact lookup, then the same lookup without the duration. A re-upload
        # can be padded or concatenated — one SoundCloud copy of a 3-minute
        # song reported 12 minutes — and LRCLIB matches duration within ±2 s,
        # so a wrong duration turns a findable track into a miss.
        attempts: list[dict] = []
        base = {"artist_name": artist, "track_name": title}
        if album:
            base["album_name"] = album
        if duration > 1:
            attempts.append({**base, "duration": round(duration)})
        attempts.append(base)

        for params in attempts:
            try:
                r = requests.get(f"{API}/get", params=params, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    result = _from_record(r.json(), "lrclib/get")
                    if result is not None:
                        return result
            except requests.RequestException:
                pass

        try:
            q = f"{artist} {title}".strip()
            r = requests.get(f"{API}/search", params={"q": q}, headers=HEADERS, timeout=10)
            if r.status_code == 200 and r.json():
                best = max(r.json(), key=lambda rec: _score(rec, artist, title, duration))
                if _score(best, artist, title, duration) >= 2:
                    return _from_record(best, "lrclib/search")
        except requests.RequestException:
            pass
        return None
