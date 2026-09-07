"""NetEase Cloud Music provider — free, keyless, no observed throttling.

Queried directly rather than through `syncedlyrics`, which would pull
beautifulsoup4, rapidfuzz and their dependency trees into the runtime to reach
one source.

Two measured properties shape this (see `research/viability/probe_netease.py`):

- It answers in roughly 2.6 s against LRCLIB's 0.7 s, so it belongs *after*
  LRCLIB rather than in front of it.
- Its search returns a best-effort match with no notion of failure. Probing six
  tracks, one came back as a different artist's song of the same name — with a
  duration close enough to pass a duration check on its own. So a result is
  verified before it is trusted, and an unverifiable one is discarded rather
  than shown.
"""
import logging

import requests

from lyrica.lyrics import Lyrics, parse_lrc
from lyrica.providers.base import LyricsProvider, ProviderOutcome
from lyrica.providers.identity import SongQuery, validate_identity
from lyrica.textmatch import fold

SEARCH_URL = "https://music.163.com/api/search/get"
LYRIC_URL = "https://music.163.com/api/song/lyric"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"}
TIMEOUT = 12

logger = logging.getLogger(__name__)


def _artists_of(song: dict) -> str:
    return " ".join(a.get("name", "") for a in song.get("artists", []))


def _score(song: dict, artist: str, title: str, duration: float) -> float:
    """How much this result looks like the track that is actually playing.

    The artist is weighted hardest because that is the axis the search gets
    wrong: a cover, or an unrelated song sharing a title, matches on title and
    duration alone.
    """
    score = 0.0
    got_artist, got_title = fold(_artists_of(song)), fold(song.get("name", ""))
    want_artist, want_title = fold(artist), fold(title)

    if want_artist and got_artist:
        if want_artist == got_artist:
            score += 3
        elif want_artist in got_artist or got_artist in want_artist:
            score += 2
        else:
            score -= 2
    if want_title == got_title:
        score += 2
    elif want_title and (want_title in got_title or got_title in want_title):
        score += 1
    else:
        score -= 1

    if duration > 1 and song.get("duration"):
        diff = abs(song["duration"] / 1000 - duration)
        score += 1.5 if diff <= 3 else (0.5 if diff <= 10 else -1.5)
    return score


class NeteaseProvider(LyricsProvider):
    name = "netease"

    # Below this a result is treated as the search reaching for anything rather
    # than finding the track. Two points is roughly "the artist is right but
    # nothing else could be confirmed".
    MIN_SCORE = 2.0

    # Artist, title and duration all agreeing. Below this the result is usable
    # but not proof the track was identified.
    EXACT_SCORE = 6.0

    def fetch(self, artist: str, title: str, duration: float = 0.0,
              album: str = "") -> Lyrics | None:
        return self.lookup(SongQuery(artist, title, duration, album, title)).lyrics

    @staticmethod
    def _http_failure(response) -> ProviderOutcome:
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

    def lookup(self, query: SongQuery) -> ProviderOutcome:
        if not query.title:
            return ProviderOutcome.no_match(reason="empty_title")
        search = f"{query.artist} {query.title}".strip()
        try:
            response = requests.post(
                SEARCH_URL,
                data={"s": search, "type": 1, "limit": 5, "offset": 0},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            logger.debug("netease search failed for %r", search, exc_info=True)
            return ProviderOutcome.retryable(reason="transport")
        if response.status_code != 200:
            return self._http_failure(response)
        try:
            songs = (response.json().get("result") or {}).get("songs") or []
        except (AttributeError, TypeError, ValueError):
            return ProviderOutcome.retryable(reason="invalid_payload")
        if not isinstance(songs, list):
            return ProviderOutcome.retryable(reason="invalid_payload")
        if not songs:
            return ProviderOutcome.no_match(reason="empty_search")

        accepted = []
        rejected = []
        for song in songs:
            if not isinstance(song, dict):
                continue
            decision = validate_identity(
                requested_artist=query.artist,
                requested_title=query.title,
                requested_raw_title=query.raw_title or query.title,
                returned_artist=_artists_of(song),
                returned_title=song.get("name") or "",
            )
            if decision.accepted:
                accepted.append(song)
            else:
                rejected.append(decision.reason)
        if not accepted:
            return ProviderOutcome.no_match(
                reason=rejected[0] if rejected else "invalid_payload")

        song = max(accepted, key=lambda item: _score(
            item, query.artist, query.title, query.duration))
        score = _score(song, query.artist, query.title, query.duration)
        if score < self.MIN_SCORE:
            return ProviderOutcome.no_match(reason="low_score")
        try:
            response = requests.get(
                LYRIC_URL,
                params={"id": song["id"], "lv": 1, "kv": 1, "tv": -1},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
        except (requests.RequestException, KeyError):
            logger.debug("netease lyric fetch failed for %s", song.get("id"),
                         exc_info=True)
            return ProviderOutcome.retryable(reason="transport")
        if response.status_code == 404:
            return ProviderOutcome.no_match(reason="no_lyrics")
        if response.status_code != 200:
            return self._http_failure(response)
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return ProviderOutcome.retryable(reason="invalid_payload")

        lrc = (payload.get("lrc") or {}).get("lyric") or ""
        if not lrc:
            return ProviderOutcome.no_match(reason="no_lyrics")
        lines = parse_lrc(lrc)
        if lines:
            lyrics = Lyrics(lines=lines, synced=True, source="netease",
                            exact=score >= self.EXACT_SCORE)
        else:
            text = "\n".join(line for line in lrc.splitlines() if line.strip())
            if not text:
                return ProviderOutcome.no_match(reason="no_lyrics")
            lyrics = Lyrics(plain=text, synced=False, source="netease",
                            exact=score >= self.EXACT_SCORE)
        return ProviderOutcome.hit(lyrics, reason="compatible")

    def _best_match(self, artist: str, title: str,
                    duration: float) -> tuple[dict, float] | None:
        query = f"{artist} {title}".strip()
        try:
            r = requests.post(SEARCH_URL, data={"s": query, "type": 1, "limit": 5, "offset": 0},
                              headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            songs = (r.json().get("result") or {}).get("songs") or []
        except (requests.RequestException, ValueError):
            logger.debug("netease search failed for %r", query, exc_info=True)
            return None
        if not songs:
            return None

        best = max(songs, key=lambda s: _score(s, artist, title, duration))
        score = _score(best, artist, title, duration)
        if score < self.MIN_SCORE:
            logger.info("netease: discarding %r by %r for %r - %r (score %.1f)",
                        best.get("name"), _artists_of(best), artist, title, score)
            return None
        return best, score

    def _lyrics_for(self, song: dict, *, exact: bool) -> Lyrics | None:
        try:
            r = requests.get(LYRIC_URL, params={"id": song["id"], "lv": 1, "kv": 1, "tv": -1},
                             headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError, KeyError):
            logger.debug("netease lyric fetch failed for %s", song.get("id"), exc_info=True)
            return None

        lrc = (payload.get("lrc") or {}).get("lyric") or ""
        if not lrc:
            return None
        lines = parse_lrc(lrc)
        if lines:
            return Lyrics(lines=lines, synced=True, source="netease", exact=exact)
        # Some entries carry lyrics with no timestamps at all.
        text = "\n".join(ln for ln in lrc.splitlines() if ln.strip())
        return Lyrics(plain=text, synced=False, source="netease", exact=exact) if text else None
