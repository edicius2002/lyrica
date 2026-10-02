"""Cache payload validation, migration, and atomic replacement (offline)."""

import json
import threading

import pytest

from lyrica.lyrics import Lyrics
from lyrica.providers import cache
from lyrica.providers.base import OutcomeKind, ProviderOutcome

IDENTITY = {"artist": "A", "title": "B", "duration": 200}


@pytest.mark.parametrize('bad', ['Artist', ['Artist'], ['Artist', 1], ['', 'Song']])
def test_invalid_resolved_name_is_rejected(tmp_path, bad):
    path = tmp_path / 'entry.json'
    cache.write_entry(path, entry(), IDENTITY)
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['resolved'] = bad
    path.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='resolved'):
        cache.read_entry(path, IDENTITY)


def test_resolved_name_survives_cache(tmp_path):
    saved = entry()
    saved.lyrics.resolved = ('Artist', 'Song')
    path = tmp_path / 'entry.json'
    cache.write_entry(path, saved, IDENTITY)
    assert cache.read_entry(path, IDENTITY).lyrics.resolved == ('Artist', 'Song')


def test_existing_netease_cache_hides_boundary_credits(tmp_path):
    path = tmp_path / 'entry.json'
    saved = cache.CacheEntry(
        Lyrics(lines=[(0.0, '作词：Someone'), (1.0, '作曲：Someone'),
                      (20.0, 'First sung line'),
                      (193.0, '母带工程师：Someone')], synced=True, source='netease'),
        {'netease': cache.ProviderState(OutcomeKind.HIT, 1_000.0)},
    )
    cache.write_entry(path, saved, IDENTITY)
    restored = cache.read_entry(path, IDENTITY).lyrics
    assert restored.lines == [(20.0, 'First sung line')]


def entry(source="one"):
    return cache.CacheEntry(
        Lyrics(lines=[(0.0, "placeholder")], synced=True, source=source),
        {"stub": cache.ProviderState(OutcomeKind.HIT, 1_000.0)},
    )


def test_write_stages_complete_json_in_the_destination_directory(
        tmp_path, monkeypatch):
    path = tmp_path / "entry.json"
    path.write_text('{"old": true}', encoding="utf-8")
    real_replace = cache.os.replace
    observed = []

    def replace(source, destination):
        source, destination = cache.Path(source), cache.Path(destination)
        observed.append((source.parent, json.loads(source.read_text(encoding="utf-8"))))
        assert path.read_text(encoding="utf-8") == '{"old": true}'
        real_replace(source, destination)

    monkeypatch.setattr(cache.os, "replace", replace)

    cache.write_entry(path, entry(), IDENTITY)

    assert observed[0][0] == path.parent
    assert observed[0][1]["v"] == cache.CACHE_VERSION
    assert cache.read_entry(path, IDENTITY).lyrics.source == "one"


def test_a_failed_miss_write_cannot_replace_an_existing_hit(tmp_path):
    path = tmp_path / "entry.json"
    cache.write_entry(path, entry("known-good"), IDENTITY)

    failed = cache.CacheEntry(
        None,
        {"stub": cache.ProviderState(
            OutcomeKind.RETRYABLE, 2_000.0, retry_at=2_030.0, failures=2)},
    )
    cache.write_entry(path, failed, IDENTITY)

    saved = cache.read_entry(path, IDENTITY)
    assert saved.lyrics.source == "known-good"
    assert saved.states["stub"].kind is OutcomeKind.RETRYABLE


def test_malformed_provider_state_is_rejected(tmp_path):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps({
        "v": cache.CACHE_VERSION,
        "identity": IDENTITY,
        "miss": True,
        "provider_states": {"stub": {"kind": "not-real", "checked_at": "never"}},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="provider state"):
        cache.read_entry(path, IDENTITY)


def test_malformed_lyric_rows_are_rejected_before_reaching_playback(tmp_path):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps({
        "v": cache.CACHE_VERSION,
        "identity": IDENTITY,
        "lines": [["not-a-time"]],
        "words": [[]],
        "source": "broken",
        "synced": True,
        "provider_states": {},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="lyrics payload"):
        cache.read_entry(path, IDENTITY)


def test_cache_identity_metadata_must_match_the_lookup(tmp_path):
    path = tmp_path / "entry.json"
    cache.write_entry(path, entry(), IDENTITY)

    with pytest.raises(ValueError, match="identity"):
        cache.read_entry(path, {**IDENTITY, "artist": "Someone Else"})


def test_failure_backoff_is_bounded_after_repeated_attempts():
    state = None
    now = 1_000.0
    for _attempt in range(20):
        state = cache.state_from_outcome(
            ProviderOutcome.retryable(reason="timeout"), state, now)
        delay = state.retry_at - now
        assert 0.0 <= delay <= cache.RETRY_BACKOFF_MAX_S
        now = state.retry_at

    assert state.retry_at - state.checked_at == cache.RETRY_BACKOFF_MAX_S


def test_legacy_hit_is_usable_without_refreshing_every_old_provider(tmp_path):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps({
        "v": 11,
        "lines": [[0.0, "placeholder"]],
        "words": [[]],
        "synced": True,
        "source": "legacy",
        "asked": ["old-a", "old-b"],
    }), encoding="utf-8")

    saved = cache.read_entry(path, IDENTITY)

    assert saved.lyrics.source == "legacy"
    assert set(saved.states) == {"old-a", "old-b"}
    assert all(not state.due(99_999_999.0) for state in saved.states.values())


def test_concurrent_external_readers_only_see_complete_replacements(tmp_path):
    path = tmp_path / "entry.json"
    cache.write_entry(path, entry("initial"), IDENTITY)
    started = threading.Event()
    finished = threading.Event()
    errors = []
    observed = set()

    def writer():
        started.wait()
        try:
            for index in range(30):
                value = entry(f"source-{index}")
                value.lyrics.plain = "x" * 100_000
                cache.write_entry(path, value, IDENTITY)
        except OSError as error:
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=writer)
    thread.start()
    started.set()
    while not finished.is_set():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            observed.add(payload["source"])
        except PermissionError:
            # Windows can briefly deny a new open while the atomic rename has
            # the directory entry locked. That is unavailability, not a torn
            # payload; the production reader serializes this in-process.
            continue
        except (OSError, ValueError, KeyError) as error:
            errors.append(error)
    thread.join()

    assert not errors
    assert observed
