"""Provider cascade with a shared on-disk cache.

Providers are asked in order and the **best** answer wins, not the first one.
Stopping at the first source that replies at all would let a plain-text hit
beat a synced hit from the next source, and the overlay can only page plain
text by playback progress — which looks synchronised while being a guess.

Searching stops as soon as an answer is definitive (see `Lyrics.is_definitive`),
so the extra request is only ever spent when the answer in hand is weak.
"""
import hashlib
import json
import logging
import time
from pathlib import Path

from lyrica import config
from lyrica.lyrics import Lyrics, Precision
from lyrica.providers.base import LyricsProvider
from lyrica.providers.community import CommunityTtmlProvider
from lyrica.providers.lrclib import LrclibProvider
from lyrica.providers.musixmatch import MusixmatchProvider
from lyrica.providers.netease import NeteaseProvider

logger = logging.getLogger(__name__)


def default_cache_dir() -> Path:
    """Where lyric lookups are cached.

    Under the store `LYRICA_CACHE_DIR` points at, which is all the cache needs
    to follow you between machines: every entry is written once, named by a
    hash of the track, and never modified. Two machines can add different files
    but can never disagree about one — so plain folder sync is enough, and a
    database here would be worse, not better, since a shared SQLite file is
    exactly what concurrent writers corrupt.
    """
    # Deliberately still "cache": renaming it would orphan everything already
    # looked up, and a cache that silently starts empty is worse than a name
    # that is merely unspecific.
    return config.cache_root() / "cache"


CACHE_DIR = default_cache_dir()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Ordered by expected precision first, then by everything else the sources cost.
#
# The community source leads: word-level, and no token, key or captcha anywhere.
# Musixmatch follows because it carries word-level for far more tracks (13/16
# against 6/10) but throttles and is undocumented, so it is only reached when
# the free word source came up short. LRCLIB then covers line level in ~0.7 s
# with near-total coverage, and NetEase last at ~2.6 s.
#
# The Musixmatch token is stored beside the cache so it survives a restart:
# re-requesting one on every launch is both needless traffic and a good way to
# look like abuse.
PROVIDERS: list[LyricsProvider] = [
    CommunityTtmlProvider(),
    MusixmatchProvider(token_path=CACHE_DIR.parent / "musixmatch-token.json"),
    LrclibProvider(),
    NeteaseProvider(),
]

_CACHE_FIELDS = ("plain", "synced", "source", "instrumental", "exact")


def _cache_path(artist: str, title: str, duration: float) -> Path:
    key = f"{artist.lower()}|{title.lower()}|{int(duration)}"
    # Naming a file, not protecting anything: the digest just turns arbitrary
    # track text into a safe filename.
    digest = hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()
    return CACHE_DIR / (digest + ".json")


def _provider_names() -> list[str]:
    return [p.name for p in PROVIDERS]


def _cache_read(path: Path) -> tuple[Lyrics | None, list[str]]:
    """Return the cached result and which providers produced it.

    Entries written before providers were recorded report an empty list, which
    makes them look unexhausted — correct, since back then there was no way to
    know whether a better source had been asked.
    """
    d = json.loads(path.read_text(encoding="utf-8"))
    asked = d.get("asked", [])
    if d.get("miss"):
        return None, asked
    # Tolerate fields added after an entry was written: a missing one takes the
    # dataclass default rather than discarding an otherwise good answer.
    lyr = Lyrics(**{k: d[k] for k in _CACHE_FIELDS if k in d})
    lyr.lines = [tuple(x) for x in d["lines"]]
    # JSON has no tuples, so word timings come back as lists and would compare
    # unequal to freshly parsed ones. Restoring the shape keeps a cached hit
    # indistinguishable from a live one.
    lyr.words = [[tuple(w) for w in line] for line in d.get("words", [])]
    return lyr, asked


def _cache_write(path: Path, result: Lyrics | None, asked: list[str]) -> None:
    if result is None:
        payload = {"miss": True, "asked": asked}
    else:
        payload = {"lines": result.lines, "words": result.words, "asked": asked}
        payload.update({k: getattr(result, k) for k in _CACHE_FIELDS})
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _better(new: Lyrics | None, best: Lyrics | None) -> bool:
    if new is None:
        return False
    return best is None or new.precision > best.precision


def _nothing_left_to_beat(best: Lyrics | None, remaining: list[LyricsProvider]) -> bool:
    """True when no unasked source could improve on what is already held.

    This is what stops a line-level answer either ending the search while a
    word-level source goes unasked, or forcing every source to be queried on
    every track to find that out.
    """
    if best is None:
        return False
    if best.instrumental and best.exact:
        return True
    ceiling = max((p.max_precision for p in remaining), default=Precision.NONE)
    return best.precision >= ceiling


def _ask_providers(artist: str, title: str, duration: float,
                   album: str) -> tuple[Lyrics | None, list[str]]:
    """Walk the cascade keeping the best answer. Returns it and who was asked."""
    best: Lyrics | None = None
    asked: list[str] = []
    for index, provider in enumerate(PROVIDERS):
        started = time.perf_counter()
        try:
            result = provider.fetch(artist, title, duration, album)
        except Exception:
            # One broken source must not deny the track lyrics that another
            # source has. Logged with its traceback, then skipped.
            logger.exception("provider %s failed for %r - %r", provider.name, artist, title)
            result = None
        elapsed_ms = (time.perf_counter() - started) * 1000
        asked.append(provider.name)

        tier = result.precision.name if result else "MISS"
        logger.info("%s answered %s in %.0f ms for %r - %r",
                    provider.name, tier, elapsed_ms, artist, title)

        if _better(result, best):
            best = result
        if _nothing_left_to_beat(best, PROVIDERS[index + 1:]):
            break
    return best, asked


def fetch_lyrics(artist: str, title: str, duration: float = 0.0,
                 album: str = "") -> Lyrics | None:
    """Best answer any provider has for one artist/title pair, or None.

    The answer is cached, misses included: a track with no lyrics anywhere is
    the common case on SoundCloud, and without caching that the whole cascade
    would run again on every replay.
    """
    if not title:
        return None

    cpath = _cache_path(artist, title, duration)
    if cpath.exists():
        try:
            cached, asked = _cache_read(cpath)
        except (OSError, ValueError, KeyError, TypeError):
            cached, asked = None, _provider_names()  # unreadable: fetch again below
            cpath.unlink(missing_ok=True)
        else:
            # A cached answer is only trusted while nothing unasked could beat
            # it. Once a better source exists that this entry never saw, it is
            # worth revisiting — that is what lets a word-level provider added
            # later supersede a line-level hit instead of being shadowed by it.
            unasked = [p for p in PROVIDERS if p.name not in asked]
            if not unasked or _nothing_left_to_beat(cached, unasked):
                return cached
            logger.info("re-querying %r - %r: %s never asked", artist, title,
                        [p.name for p in unasked])

    best, asked = _ask_providers(artist, title, duration, album)
    try:
        _cache_write(cpath, best, asked)
    except OSError:
        logger.warning("could not cache result for %r - %r", artist, title, exc_info=True)
    return best


def fetch_for_candidates(candidates: list[tuple[str, str]], duration: float = 0.0,
                         album: str = "") -> Lyrics | None:
    """Best answer across every reading of the metadata.

    Browsers disagree about what their fields mean, so a payload can have
    several defensible readings. Each is tried and the best result kept, with
    the search stopping early on a definitive answer.
    """
    best: Lyrics | None = None
    for artist, title in candidates:
        result = fetch_lyrics(artist, title, duration, album)
        if _better(result, best):
            best = result
        # Every candidate asks the whole cascade, so the bar to clear here is
        # the best any source could give, not what is left to ask.
        if _nothing_left_to_beat(best, PROVIDERS):
            return best
    return best


__all__ = ["PROVIDERS", "Precision", "fetch_for_candidates", "fetch_lyrics"]
