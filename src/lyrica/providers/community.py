"""Community TTML provider — word-level lyrics with no authentication at all.

No token, no captcha, no signing, no key. Both reference extensions reach
word-level timing through this service, and measuring it against tracks actually
played on this machine gave word-level coverage on 6 of 10 — the best
effort-to-coverage ratio of any source examined.

Its search returns every variant of a title: the album cut, the single with a
guest, a live recording, a remix. They differ mainly in duration, so duration
carries real weight in the scoring — a live version's lyrics are the same words
at entirely different times, which is worse than no word timing at all.
"""
import logging

import requests

from lyrica.lyrics import Lyrics, Precision
from lyrica.providers.base import LyricsProvider
from lyrica.textmatch import fold
from lyrica.ttml import parse_ttml

SEARCH_URL = "https://lyrics-api.binimum.org/getLyrics"
HEADERS = {"User-Agent": "lyrica/0.2.1 (personal overlay)"}
TIMEOUT = 15

logger = logging.getLogger(__name__)


def _score(rec: dict, artist: str, title: str, duration: float) -> float:
    """How much this result looks like the recording that is playing."""
    score = 0.0
    got_artist = fold(rec.get("artist_name", ""))
    got_title = fold(rec.get("track_name", ""))
    want_artist, want_title = fold(artist), fold(title)

    if want_artist and got_artist:
        if want_artist == got_artist:
            score += 3
        elif want_artist in got_artist or got_artist in want_artist:
            score += 2
        else:
            # Heavy enough to sink a result that matches on title and duration
            # alone. Songs share titles, and a different performer's recording
            # of the same length is precisely the trap this has to catch.
            score -= 5
    if want_title == got_title:
        score += 3
    elif want_title and (want_title in got_title or got_title in want_title):
        score += 1
    else:
        score -= 2

    # Weighted hard: the variants this search returns differ mostly by length,
    # and a live take's timings are wrong for the studio cut even though every
    # word matches.
    if duration > 1 and rec.get("duration"):
        diff = abs(float(rec["duration"]) - duration)
        if diff <= 2:
            score += 3
        elif diff <= 5:
            score += 1.5
        elif diff <= 15:
            score -= 1
        else:
            score -= 4

    # A tiebreak, never a reason to pick the wrong recording.
    if str(rec.get("timing_type", "")).lower() == "word":
        score += 0.5
    return score


class CommunityTtmlProvider(LyricsProvider):
    name = "community-ttml"
    max_precision = Precision.WORD
    carries_backing = True

    MIN_SCORE = 3.0

    # Artist, title and duration all agreeing. Used only to mark the result
    # exact, which decides whether an instrumental may end the cascade.
    EXACT_SCORE = 8.0

    def fetch(self, artist: str, title: str, duration: float = 0.0,
              album: str = "") -> Lyrics | None:
        if not title:
            return None
        best = self._best_match(artist, title, duration)
        if best is None:
            return None
        rec, score = best
        body = self._body(rec)
        if not body:
            return None
        lyrics = parse_ttml(body)
        if lyrics is None:
            logger.info("community-ttml: unparseable document for %r - %r", artist, title)
            return None
        lyrics.source = f"community-ttml/{rec.get('timing_type', '?')}"
        lyrics.exact = score >= self.EXACT_SCORE
        return lyrics

    def _best_match(self, artist: str, title: str,
                    duration: float) -> tuple[dict, float] | None:
        try:
            r = requests.get(SEARCH_URL, params={"q": f"{title} {artist}".strip()},
                             headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            results = r.json().get("results") or []
        except (requests.RequestException, ValueError):
            logger.debug("community-ttml search failed for %r - %r", artist, title,
                         exc_info=True)
            return None
        if not results:
            return None

        best = max(results, key=lambda rec: _score(rec, artist, title, duration))
        score = _score(best, artist, title, duration)
        if score < self.MIN_SCORE:
            logger.info("community-ttml: discarding %r by %r for %r - %r (score %.1f)",
                        best.get("track_name"), best.get("artist_name"), artist, title, score)
            return None
        return best, score

    def _body(self, rec: dict) -> str | None:
        url = rec.get("lyricsUrl")
        if not url:
            return None
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            logger.debug("community-ttml document fetch failed: %s", url, exc_info=True)
            return None
