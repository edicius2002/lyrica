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
import queue
import threading
import time
from copy import copy
from pathlib import Path
from statistics import median

from lyrica import config
from lyrica.lyrics import Lyrics, Precision
from lyrica.providers.base import LyricsProvider
from lyrica.providers.community import CommunityTtmlProvider
from lyrica.providers.lrclib import LrclibProvider
from lyrica.providers.musixmatch import MusixmatchProvider
from lyrica.providers.netease import NeteaseProvider
from lyrica.textmatch import fold

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

# Nothing may hold a track's lyrics hostage. Every provider has its own request
# timeout, but a stalled connection that never returns would otherwise leave the
# search waiting on a name that is never coming.
OVERALL_TIMEOUT_S = 12.0
# A hybrid is an improvement, never a reason to leave a fresh track blank
# behind a slow community lookup.  Most successful CommunityTTML replies land
# within this small window; after it Richsync is shown as-is.
HYBRID_GRACE_S = 1.5
HYBRID_DURATION_TOLERANCE_S = 2.0
HYBRID_MAX_MEDIAN_RESIDUAL_S = 0.35
HYBRID_MAX_RESIDUAL_S = 0.75

_CACHE_FIELDS = ("plain", "synced", "source", "instrumental", "exact",
                 "queried")

# What shape the entries on disk are. Raised whenever a result carries
# something an older entry could not, so the older ones are fetched again
# rather than answering with a hole: backing vocals were parsed, cached
# without, and every replay came back with none of them, because nothing else
# would ever have refreshed an entry whose providers had all been asked.
# 3: entries written while the cascade kept whichever word-timed answer
#    arrived first. Those that lost a race hold a source with no backing
#    vocals, and nothing in the entry says whether it lost one or the other
#    source simply had nothing — so they are all asked again.
# 4: entries written before who sings each line was parsed at all. Same story
#    as 2, and the same answer: an entry whose providers have all been asked is
#    never refreshed by anything else, so the version is what refreshes it.
# 5: parenthetical backing phrases were still part of the lead line. Re-reading
#    them is necessary because cached word timings otherwise have no chance to
#    be separated into the backing channel.
# 6: Richsync parenthetical suffixes were not normalized into the backing
#    channel, even though they carried usable serial word timings.
# 7: backing timing confidence lets short Richsync suffixes use a conservative
#    visual window, while exact TTML backing keeps its normal animation.
# 8: inferred Richsync backing tails gained 250 ms, bounded by the raised
#    1.75 s word cap, so older serial end times must be refreshed.
# 9: sequential Richsync suffixes became their own timeline rows instead of
#    being rendered as overlapping backing layers.
# 10: those suffixes correctly remain in the backing lane, while their lead
#     stays current until the sequential response has finished.
CACHE_VERSION = 10


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
    if d.get("v") != CACHE_VERSION:
        # Written by an older shape. Reported as a miss nobody has been asked
        # about, which is what sends it round the providers again.
        return None, []
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
    lyr.queried = tuple(lyr.queried)     # JSON has no tuples
    lyr.backing = list(d.get("backing", []))
    lyr.backing_words = [[tuple(w) for w in line]
                         for line in d.get("backing_words", [])]
    lyr.backing_timing = list(d.get("backing_timing", []))
    lyr.backing_modes = list(d.get("backing_modes", []))
    lyr.voices = list(d.get("voices", []))
    lyr.singers = dict(d.get("singers", {}))
    return lyr, asked


def _cache_write(path: Path, result: Lyrics | None, asked: list[str]) -> None:
    if result is None:
        payload = {"miss": True, "asked": asked, "v": CACHE_VERSION}
    else:
        payload = {"lines": result.lines, "words": result.words, "asked": asked,
                   "backing": result.backing, "backing_words": result.backing_words,
                   "backing_timing": result.backing_timing,
                   "backing_modes": result.backing_modes,
                   "voices": result.voices, "singers": result.singers,
                   "v": CACHE_VERSION}
        payload.update({k: getattr(result, k) for k in _CACHE_FIELDS})
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _staged(lyr: Lyrics) -> bool:
    """Whether this answer knows anything about who was singing.

    Backing vocals and named voices come from the same place — the one dialect
    that records either — so they are one property here rather than two tests
    that would always agree. What it separates is a source that transcribed the
    words from one that transcribed the performance.
    """
    return any(lyr.backing) or bool(lyr.voices)


def _better(new: Lyrics | None, best: Lyrics | None) -> bool:
    """Whether `new` should replace `best`.

    Precision first, and then whether the answer knows who was singing. That
    second test is not a refinement: measured against a real cache, Levitating
    was held as `musixmatch/richsync` — the same WORD precision as the source
    that has its twelve backing vocals, and none of them.
    """
    if new is None:
        return False
    if best is None:
        return True
    if new.precision != best.precision:
        return new.precision > best.precision
    return _staged(new) and not _staged(best)


def _line_pairs(richsync: Lyrics, community: Lyrics) -> list[tuple[int, int]]:
    """Pair identically transcribed lead lines while preserving their order.

    A repeated chorus is deliberately paired with its next occurrence, not
    merely with the closest timestamp.  The latter would make a shifted live
    recording appear to validate itself.
    """
    pairs: list[tuple[int, int]] = []
    rich_at = 0
    for community_at, (_start, text) in enumerate(community.lines):
        key = fold(text)
        if not key:
            continue
        for candidate in range(rich_at, len(richsync.lines)):
            if fold(richsync.lines[candidate][1]) == key:
                pairs.append((candidate, community_at))
                rich_at = candidate + 1
                break
    return pairs


def _merge_community_backing(richsync: Lyrics | None, community: Lyrics | None,
                             duration: float) -> Lyrics | None:
    """Attach verified TTML backing to Richsync's more complete lead lyric.

    This is intentionally much stricter than provider title matching.  It
    borrows *absolute* timestamps, so an alternate album/live/remix recording
    is worse than no borrowing at all.  Equal duration, several ordered text
    anchors, and a stable clock offset are all required before any x-bg timing
    crosses from CommunityTTML into the Richsync result.
    """
    if (richsync is None or community is None
            or not richsync.source.startswith("musixmatch/richsync")
            or not community.source.startswith("community-ttml")
            or richsync.precision is not Precision.WORD
            or community.precision is not Precision.WORD
            or not any(community.backing)):
        return None
    recorded = float(getattr(community, "recording_duration", 0.0) or 0.0)
    if (duration > 1 and (recorded <= 1
                          or abs(recorded - duration) > HYBRID_DURATION_TOLERANCE_S)):
        return None

    pairs = _line_pairs(richsync, community)
    required = min(3, len(richsync.lines), len(community.lines))
    if len(pairs) < required:
        return None
    offsets = [richsync.lines[ri][0] - community.lines[ci][0]
               for ri, ci in pairs]
    offset = median(offsets)
    residuals = [abs(value - offset) for value in offsets]
    if (median(residuals) > HYBRID_MAX_MEDIAN_RESIDUAL_S
            or max(residuals) > HYBRID_MAX_RESIDUAL_S):
        return None

    by_community = {ci: ri for ri, ci in pairs}
    backed = [ci for ci, text in enumerate(community.backing) if text]
    # We never move a response to a "similar" line.  Every TTML backing line
    # must have an ordered, text-identical Richsync anchor.
    if not backed or any(ci not in by_community for ci in backed):
        return None

    merged = copy(richsync)
    merged.backing = list(richsync.backing)
    merged.backing_words = [list(words) for words in richsync.backing_words]
    merged.backing_timing = list(richsync.backing_timing)
    while len(merged.backing) < len(merged.lines):
        merged.backing.append("")
    while len(merged.backing_words) < len(merged.lines):
        merged.backing_words.append([])
    while len(merged.backing_timing) < len(merged.lines):
        merged.backing_timing.append("")
    for ci in backed:
        ri = by_community[ci]
        merged.backing[ri] = community.backing[ci]
        merged.backing_words[ri] = [
            (start + offset, end + offset, text)
            for start, end, text in community.backing_words[ci]
        ]
        merged.backing_timing[ri] = "exact"
    merged.source = "musixmatch/richsync+community-ttml-adlibs"
    return merged


def _waiting_for_hybrid(best: Lyrics | None,
                        pending: dict[str, LyricsProvider]) -> bool:
    """Whether one half of a possible Richsync/TTML hybrid is still in flight."""
    if best is None:
        return False
    if best.source.startswith("musixmatch/richsync"):
        return "community-ttml" in pending
    if best.source.startswith("community-ttml"):
        return "musixmatch" in pending
    return False


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


def _may_add_backing(best: Lyrics | None,
                     pending: list[LyricsProvider]) -> bool:
    """Whether something in flight knows what was sung behind the line.

    Precision is not the whole question, which the first version assumed. Two
    sources can agree on it and disagree on whether anything was sung behind the
    line: measured against a real cache, Levitating was held as
    `musixmatch/richsync` — word-timed, and without the twelve backing vocals
    the other source has for it. The first answer to arrive had won a race it
    should not have been allowed to end.

    Asked only of the sources, never of the readings of a name: this is a
    property of where the words came from, not of what they were asked for.
    """
    if best is None or _staged(best):
        return False
    return any(getattr(p, "carries_backing", False) for p in pending)


def _candidate_is_complete(best: Lyrics | None) -> bool:
    """Whether another reading of the metadata cannot improve the staging.

    Every provider is asked for one reading, but browsers often supply several
    defensible artist/title pairs. Word precision used to stop that outer loop
    even when the result carried no agents or backing vocals; a later reading
    could have reached the TTML performance and was never tried.
    """
    if not _nothing_left_to_beat(best, PROVIDERS):
        return False
    staged_source_exists = any(
        getattr(provider, "carries_backing", False) for provider in PROVIDERS)
    return not staged_source_exists or _staged(best)


def _ask_one(provider: LyricsProvider, artist: str, title: str,
             duration: float, album: str):
    started = time.perf_counter()
    try:
        result = provider.fetch(artist, title, duration, album)
    except Exception:
        # One broken source must not deny the track lyrics that another source
        # has. Logged with its traceback, then treated as a miss.
        logger.exception("provider %s failed for %r - %r",
                         provider.name, artist, title)
        result = None
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info("%s answered %s in %.0f ms for %r - %r", provider.name,
                result.precision.name if result else "MISS", elapsed_ms,
                artist, title)
    return provider, result


def _ask_providers(artist: str, title: str, duration: float,
                   album: str) -> tuple[Lyrics | None, list[str]]:
    """Ask every provider at once, keeping the best. Returns it and who answered.

    Asked together rather than in turn. The order still decides *what wins* —
    a word-level answer beats a line-level one wherever it came from — but it
    no longer decides how long the track waits, and that was costing whole
    seconds. Measured over seven tracks: a source that misses is a full round
    trip before the next one starts, so a track whose words live in the second
    provider waited 1508 ms for the first to miss and then 3208 ms more, where
    asking both together answers in 3208 ms.

    The early exit survives, and is what keeps this from being merely faster:
    the moment an answer arrives that nothing outstanding could beat, the rest
    are abandoned and the track has its lyrics.

    The cost is that every provider is now asked on every uncached track rather
    than only until one satisfies the ceiling. Acceptable because the result is
    cached — this is once per track ever, not once per play.
    """
    best: Lyrics | None = None
    asked: list[str] = []
    pending = {p.name: p for p in PROVIDERS}
    answers_by_provider: dict[str, Lyrics | None] = {}
    answers: queue.Queue = queue.Queue()

    for provider in PROVIDERS:
        # Daemon threads and a queue rather than a pool. A pool's shutdown
        # waits for every worker, which would undo the early exit entirely —
        # the answer would be in hand and the call would still sit there until
        # the slowest source finished. These are abandoned instead, and being
        # daemons they cannot hold up the process on the way out.
        threading.Thread(
            target=lambda p=provider, a=artist, ti=title, d=duration, al=album:
                answers.put(_ask_one(p, a, ti, d, al)),
            name=f"lyrics-{provider.name}", daemon=True).start()

    deadline = time.monotonic() + OVERALL_TIMEOUT_S
    hybrid_deadline: float | None = None
    while pending:
        try:
            wait_until = min(deadline, hybrid_deadline or deadline)
            provider, result = answers.get(
                timeout=max(0.05, wait_until - time.monotonic()))
        except queue.Empty:
            logger.info("gave up waiting on %s for %r - %r",
                        sorted(pending), artist, title)
            break
        asked.append(provider.name)
        pending.pop(provider.name, None)
        answers_by_provider[provider.name] = result
        richsync = answers_by_provider.get("musixmatch")
        community = answers_by_provider.get("community-ttml")
        hybrid = _merge_community_backing(richsync, community, duration)
        if richsync is not None and community is not None:
            # The product decision here is deliberate: Richsync owns the lead
            # lyric.  CommunityTTML may improve it only by passing the strict
            # backing-timing proof above; a near-match must not win a race and
            # replace the fallback with an alternate recording.
            best = hybrid or richsync
        elif hybrid is not None:
            best = hybrid
        elif _better(result, best):
            best = result
        waiting = list(pending.values())
        if (_nothing_left_to_beat(best, waiting) and not _may_add_backing(
                best, waiting) and not _waiting_for_hybrid(best, pending)):
            # Nothing still in flight could improve on this, so the track has
            # its lyrics now. The rest finish into a queue nobody reads.
            break
        if _waiting_for_hybrid(best, pending) and hybrid_deadline is None:
            hybrid_deadline = time.monotonic() + HYBRID_GRACE_S

    return best, asked


def _stamp(result: Lyrics | None, artist: str, title: str) -> Lyrics | None:
    """Record which reading of the metadata produced this answer.

    Set on the way out rather than trusted from the cache, so entries written
    before the field existed carry it too.
    """
    if result is not None:
        result.queried = (artist, title)
    return result


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
                return _stamp(cached, artist, title)
            logger.info("re-querying %r - %r: %s never asked", artist, title,
                        [p.name for p in unasked])

    best, asked = _ask_providers(artist, title, duration, album)
    _stamp(best, artist, title)
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
        if _candidate_is_complete(best):
            return best
    return best


__all__ = ["PROVIDERS", "Precision", "fetch_for_candidates", "fetch_lyrics"]
