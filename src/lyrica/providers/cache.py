"""Validated, recoverable on-disk state for lyrics provider lookups."""

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from lyrica.lyrics import BACKING_INFERRED, Lyrics, Precision
from lyrica.providers.base import OutcomeKind, ProviderOutcome

CACHE_VERSION = 12
CONFIRMED_MISS_TTL_S = 7 * 24 * 60 * 60
RETRY_BACKOFF_INITIAL_S = 30.0
RETRY_BACKOFF_MAX_S = 30 * 60.0
LEGACY_MIGRATION_BATCH = 1

_LYRIC_FIELDS = ("plain", "synced", "source", "instrumental", "exact", "queried", "resolved")
_locks_guard = threading.Lock()
_path_locks: dict[Path, threading.RLock] = {}


@dataclass(frozen=True)
class ProviderState:
    """What one completed attempt established and when it may run again."""

    kind: OutcomeKind
    checked_at: float
    retry_at: float = 0.0
    failures: int = 0
    reason: str = ""

    def due(self, now: float) -> bool:
        if self.kind is OutcomeKind.HIT:
            return False
        if self.kind is OutcomeKind.NO_MATCH:
            return now >= self.checked_at + CONFIRMED_MISS_TTL_S
        return now >= self.retry_at


@dataclass(frozen=True)
class CacheEntry:
    """Usable incumbent lyrics plus independent provider freshness state."""

    lyrics: Lyrics | None = None
    states: dict[str, ProviderState] | None = None
    legacy_pending: tuple[str, ...] = ()
    legacy_miss: bool = False

    def __post_init__(self):
        if self.states is None:
            object.__setattr__(self, "states", {})


def state_from_outcome(outcome: ProviderOutcome, previous: ProviderState | None,
                       now: float) -> ProviderState:
    """Translate an attempt into bounded freshness/backoff state."""
    if outcome.kind in (OutcomeKind.HIT, OutcomeKind.NO_MATCH):
        return ProviderState(outcome.kind, now, reason=outcome.reason)

    repeated = previous is not None and previous.kind in (
        OutcomeKind.RETRYABLE, OutcomeKind.UNAVAILABLE)
    failures = previous.failures + 1 if repeated else 1
    if outcome.kind is OutcomeKind.RETRYABLE and failures == 1:
        delay = 0.0
    else:
        exponent = failures - (2 if outcome.kind is OutcomeKind.RETRYABLE else 1)
        delay = min(RETRY_BACKOFF_MAX_S,
                    RETRY_BACKOFF_INITIAL_S * (2 ** max(0, exponent)))
    if outcome.retry_after is not None:
        delay = max(delay, max(0.0, outcome.retry_after))
    return ProviderState(
        outcome.kind,
        now,
        retry_at=now + delay,
        failures=failures,
        reason=outcome.reason,
    )


def _lock_for(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _locks_guard:
        return _path_locks.setdefault(resolved, threading.RLock())


def _lyrics_payload(lyrics: Lyrics) -> dict:
    payload = {
        "lines": lyrics.lines,
        "words": lyrics.words,
        "backing": lyrics.backing,
        "backing_words": lyrics.backing_words,
        "backing_timing": lyrics.backing_timing,
        "backing_alignment": lyrics.backing_alignment,
        "backing_modes": lyrics.backing_modes,
        "voices": lyrics.voices,
        "singers": lyrics.singers,
    }
    payload.update({field: getattr(lyrics, field) for field in _LYRIC_FIELDS})
    if hasattr(lyrics, "recording_duration"):
        payload["recording_duration"] = lyrics.recording_duration
    return payload


def _lyrics_from_payload(payload: dict) -> Lyrics:
    lines = payload.get("lines")
    words = payload.get("words", [])
    if not isinstance(lines, list) or not isinstance(words, list):
        raise ValueError("invalid lyrics payload")
    if any(
        not isinstance(line, (list, tuple))
        or len(line) != 2
        or not isinstance(line[0], (int, float))
        or isinstance(line[0], bool)
        or not isinstance(line[1], str)
        for line in lines
    ):
        raise ValueError("invalid lyrics payload")
    if any(
        not isinstance(line, list)
        or any(
            not isinstance(word, (list, tuple))
            or len(word) != 3
            or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       for value in word[:2])
            or not isinstance(word[2], str)
            for word in line
        )
        for line in words
    ):
        raise ValueError("invalid lyrics payload")
    resolved = payload.get("resolved", [])
    if (not isinstance(resolved, (list, tuple)) or len(resolved) not in (0, 2)
            or any(not isinstance(part, str) or not part.strip() for part in resolved)):
        raise ValueError("invalid resolved name")
    lyrics = Lyrics(**{
        field: payload[field] for field in _LYRIC_FIELDS if field in payload
    })
    lyrics.lines = [tuple(line) for line in lines]
    lyrics.words = [[tuple(word) for word in line] for line in words]
    lyrics.queried = tuple(lyrics.queried)
    lyrics.resolved = tuple(resolved)
    lyrics.backing = list(payload.get("backing", []))
    lyrics.backing_words = [
        [tuple(word) for word in line]
        for line in payload.get("backing_words", [])
    ]
    lyrics.backing_timing = list(payload.get("backing_timing", []))
    lyrics.backing_alignment = list(payload.get("backing_alignment", []))
    lyrics.backing_modes = list(payload.get("backing_modes", []))
    lyrics.voices = list(payload.get("voices", []))
    lyrics.singers = dict(payload.get("singers", {}))
    if "recording_duration" in payload:
        lyrics.recording_duration = float(payload["recording_duration"])
    if lyrics.precision is Precision.NONE and not lyrics.instrumental:
        raise ValueError("invalid lyrics payload")
    return lyrics


def _legacy_v10_is_safe(payload: dict) -> bool:
    source = str(payload.get("source", ""))
    inferred_backing = (
        source.startswith("musixmatch/richsync")
        and any(payload.get("backing", []))
        and any(timing == BACKING_INFERRED
                for timing in payload.get("backing_timing", []))
    )
    return (
        not source.startswith("musixmatch/richsync+community-ttml-adlibs")
        and not inferred_backing
    )


def _state_from_payload(payload: dict) -> ProviderState:
    if not isinstance(payload, dict):
        raise ValueError("invalid provider state")
    try:
        kind = OutcomeKind(payload["kind"])
        checked_at = float(payload["checked_at"])
        retry_at = float(payload.get("retry_at", 0.0))
        failures = int(payload.get("failures", 0))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid provider state") from error
    if failures < 0:
        raise ValueError("invalid provider failure count")
    return ProviderState(
        kind,
        checked_at,
        retry_at=retry_at,
        failures=failures,
        reason=str(payload.get("reason", "")),
    )


def _read_unlocked(path: Path, identity: dict | None = None) -> CacheEntry | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("cache entry is not an object")
    version = payload.get("v")
    if version == CACHE_VERSION:
        stored_identity = payload.get("identity")
        if identity is not None and stored_identity != identity:
            raise ValueError("cache identity does not match its path")
        raw_states = payload.get("provider_states", {})
        if not isinstance(raw_states, dict):
            raise ValueError("invalid provider states")
        states = {str(name): _state_from_payload(state)
                  for name, state in raw_states.items()}
        pending = payload.get("legacy_pending", [])
        if not isinstance(pending, list) or not all(
                isinstance(name, str) for name in pending):
            raise ValueError("invalid legacy provider list")
        lyrics = None if payload.get("miss") else _lyrics_from_payload(payload)
        return CacheEntry(
            lyrics,
            states,
            tuple(pending),
            bool(payload.get("legacy_miss", False)),
        )

    if version not in (10, 11):
        return None
    asked = payload.get("asked", [])
    if not isinstance(asked, list) or not all(isinstance(name, str) for name in asked):
        raise ValueError("invalid legacy provider list")
    if payload.get("miss"):
        return CacheEntry(None, {}, tuple(asked), legacy_miss=True)
    if version == 10 and not _legacy_v10_is_safe(payload):
        return None
    lyrics = _lyrics_from_payload(payload)
    # A legacy hit is usable and must not trigger a request storm. Its old
    # ``asked`` list cannot distinguish hits from misses, so treat those names
    # as settled until a genuinely new provider appears.
    states = {
        name: ProviderState(OutcomeKind.HIT, checked_at=0.0, reason="legacy_hit")
        for name in asked
    }
    return CacheEntry(lyrics, states)


def read_entry(path: Path, identity: dict | None = None) -> CacheEntry | None:
    """Read and validate one entry while local replacement is serialized."""
    with _lock_for(path):
        return _read_unlocked(path, identity)


def _entry_payload(entry: CacheEntry, identity: dict) -> dict:
    payload = {
        "v": CACHE_VERSION,
        "identity": identity,
        "provider_states": {
            name: {
                "kind": state.kind.value,
                "checked_at": state.checked_at,
                "retry_at": state.retry_at,
                "failures": state.failures,
                "reason": state.reason,
            }
            for name, state in entry.states.items()
        },
        "legacy_pending": list(entry.legacy_pending),
        "legacy_miss": entry.legacy_miss,
    }
    if entry.lyrics is None:
        payload["miss"] = True
    else:
        payload.update(_lyrics_payload(entry.lyrics))
    return payload


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Bound the brief Windows sharing race with an external cache reader."""
    delays = (0.0, 0.001, 0.002, 0.004, 0.008, 0.016, 0.032, 0.064, 0.128)
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == len(delays) - 1:
                raise


def write_entry(path: Path, entry: CacheEntry, identity: dict) -> None:
    """Flush a complete sibling file, then atomically replace the destination.

    Within this process a miss never replaces an already usable hit. Across
    machines, independent atomic renames are deliberately last-rename-wins;
    this is not a distributed conflict-resolution protocol.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(path)
    with lock:
        if path.exists():
            try:
                existing = _read_unlocked(path, identity)
            except (OSError, ValueError, KeyError, TypeError):
                existing = None
            if existing is not None and existing.lyrics is not None and entry.lyrics is None:
                entry = replace(entry, lyrics=existing.lyrics)
        data = json.dumps(_entry_payload(entry, identity), ensure_ascii=False)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_retry(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
