"""LRCLIB provider (https://lrclib.net) — free, keyless, no rate limits.

Lookup strategy: exact /get first (duration-matched ±2 s server-side),
then fuzzy /search scored by artist/title/duration similarity.
"""

import requests

from lyrica.artist_names import ArtistReading, artist_relation, resolved_name
from lyrica.lyrics import Lyrics, parse_lrc
from lyrica.providers.base import LyricsProvider, ProviderOutcome
from lyrica.providers.identity import SongQuery, validate_identity

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "lyrica/0.2.7 (personal research overlay)"}


def _from_record(d: dict, source: str, *, exact: bool) -> Lyrics | None:
    name = resolved_name(d.get("artistName") or "", d.get("trackName") or "")
    if d.get("instrumental"):
        return Lyrics(instrumental=True, source=source, exact=exact, resolved=name)
    if d.get("syncedLyrics"):
        return Lyrics(lines=parse_lrc(d["syncedLyrics"]), plain=d.get("plainLyrics") or "",
                      synced=True, source=source, exact=exact, resolved=name)
    if d.get("plainLyrics"):
        return Lyrics(plain=d["plainLyrics"], synced=False, source=source, exact=exact, resolved=name)
    return None


def _score(rec: dict, artist: str, title: str, duration: float, *,
           artist_reading: ArtistReading | None = None) -> float:
    s = 0.0
    relation = artist_relation(artist_reading or artist, rec.get("artistName") or "")
    rt = (rec.get("trackName") or "").lower()
    if relation not in ('unknown', 'mismatch'):
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
        return self.lookup(SongQuery(artist, title, duration, album, title)).lyrics

    @staticmethod
    def _failure(response) -> ProviderOutcome:
        status = response.status_code
        if status in (401, 403, 429):
            retry_after = None
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                pass
            return ProviderOutcome.unavailable(
                reason=f"http_{status}", retry_after=retry_after)
        return ProviderOutcome.retryable(reason=f"http_{status}")

    @staticmethod
    def _identity(query: SongQuery, record: dict):
        return validate_identity(
            requested_artist=query.artist,
            requested_artist_reading=query.artist_reading,
            requested_title=query.title,
            requested_raw_title=query.raw_title or query.title,
            returned_artist=record.get("artistName") or "",
            returned_title=record.get("trackName") or "",
        )

    def lookup(self, query: SongQuery) -> ProviderOutcome:
        if not query.title:
            return ProviderOutcome.no_match(reason="empty_title")

        # Exact lookup, then the same lookup without the duration. A re-upload
        # can be padded or concatenated — one SoundCloud copy of a 3-minute
        # song reported 12 minutes — and LRCLIB matches duration within ±2 s,
        # so a wrong duration turns a findable track into a miss.
        attempts: list[dict] = []
        base = {"artist_name": query.artist, "track_name": query.title}
        if query.album:
            base["album_name"] = query.album
        if query.duration > 1:
            attempts.append({**base, "duration": round(query.duration)})
        attempts.append(base)

        # A /get hit names the track; a /search hit is the closest thing found.
        # The distinction matters for instrumentals — see Lyrics.is_definitive.
        for params in attempts:
            try:
                r = requests.get(f"{API}/get", params=params, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    record = r.json()
                    identity = self._identity(query, record)
                    result = _from_record(record, "lrclib/get", exact=True)
                    if identity.accepted and result is not None:
                        return ProviderOutcome.hit(result, reason=identity.reason)
                elif r.status_code in (401, 403, 429):
                    return self._failure(r)
            except requests.RequestException:
                continue
            except (TypeError, ValueError):
                continue

        try:
            q = f"{query.artist} {query.title}".strip()
            r = requests.get(f"{API}/search", params={"q": q}, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                return self._failure(r)
            records = r.json()
        except requests.RequestException:
            return ProviderOutcome.retryable(reason="transport")
        except (TypeError, ValueError):
            return ProviderOutcome.retryable(reason="invalid_json")
        if not isinstance(records, list):
            return ProviderOutcome.retryable(reason="invalid_payload")
        if not records:
            return ProviderOutcome.no_match(reason="empty_search")

        accepted = []
        rejected = []
        for record in records:
            if not isinstance(record, dict):
                continue
            decision = self._identity(query, record)
            if decision.accepted:
                accepted.append(record)
            else:
                rejected.append(decision.reason)
        if not accepted:
            reason = rejected[0] if rejected else "invalid_payload"
            return ProviderOutcome.no_match(reason=reason)

        best = max(accepted, key=lambda rec: _score(
            rec, query.artist, query.title, query.duration, artist_reading=query.artist_reading))
        if _score(best, query.artist, query.title, query.duration, artist_reading=query.artist_reading) < 2:
            return ProviderOutcome.no_match(reason="low_score")
        result = _from_record(best, "lrclib/search", exact=False)
        if result is None:
            return ProviderOutcome.no_match(reason="no_lyrics")
        return ProviderOutcome.hit(result, reason="compatible")
