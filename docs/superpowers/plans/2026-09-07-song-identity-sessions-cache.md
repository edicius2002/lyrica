# Song Identity, Playback Sessions, and Lyrics Cache Implementation Plan

> **Execution note:** The user explicitly prohibited additional workers. Execute this plan inline in the current session with red-green-refactor checkpoints.

**Goal:** Reject contradictory lyric matches, keep all playback work bound to one stable Windows session, and make cached lyrics recover safely from provider failures.

**Architecture:** Add small pure identity and session-selection modules, introduce an explicit outcome at the provider boundary, and evolve the cache into a validated v12 entry with atomic replacement and per-provider retry state. Keep `app.py` integration limited to immutable snapshot bindings.

**Tech Stack:** Python 3.11, pytest, requests, WinRT through `winsdk`, Ruff.

**Spec:** `docs/song-identity-sessions-cache.md`

## Global Constraints

- Preserve concurrent provider racing, precision/staging ranking, hybrid backing behavior, and existing Musixmatch locking/cooldown.
- Make no live provider calls in tests and keep non-Windows imports working.
- Do not change rendering geometry, resizing, fades, backing paths, `tests/test_backing.py`, `docs/IMPLEMENTATION_PLAN.md`, or `research/VIABILITY.md`.
- Add no providers, endpoints, player selector, retry button, mass migration, or full-library refresh.

---

### Task 1: Provider outcome and identity contract

**Files:**
- Create: `src/lyrica/providers/identity.py`
- Modify: `src/lyrica/providers/base.py`
- Modify: `src/lyrica/providers/lrclib.py`
- Modify: `src/lyrica/providers/community.py`
- Modify: `src/lyrica/providers/netease.py`
- Modify: `src/lyrica/providers/musixmatch.py`
- Test: `tests/test_identity.py`
- Test: existing provider adapter tests

**Interfaces:**
- Produce `SongQuery`, `IdentityDecision`, `ProviderOutcome`, and `OutcomeKind`.
- `LyricsProvider.fetch(query)` returns a `ProviderOutcome`; adapters retain their existing network ordering and endpoints.

- [ ] Add failing identity tests for wrong known artist, conflicting version, featured credits, punctuation, missing artist/version, and unknown duration.
- [ ] Run those tests and confirm the current LRCLIB wrong-artist result is accepted.
- [ ] Implement pure identity comparison and preserve raw title evidence in a `SongQuery`.
- [ ] Add failing adapter tests for network, timeout, rate-limit, auth/cooldown, malformed data, valid misses, and accepted hits.
- [ ] Implement explicit outcomes through every adapter, surfacing Musixmatch's existing cooldown retry time.
- [ ] Run all provider adapter and metadata tests.

### Task 2: Recoverable cache and concurrent cascade

**Files:**
- Create: `src/lyrica/providers/cache.py`
- Modify: `src/lyrica/providers/__init__.py`
- Test: `tests/test_provider_cache.py`
- Test: `tests/test_providers.py`
- Test: `tests/test_cascade_precision.py`

**Interfaces:**
- Produce validated `CacheEntry` and `ProviderState` values plus atomic `read_entry`/`write_entry` operations.
- Cascade accepts an incumbent lyric and a bounded provider subset, returning the best lyric plus provider outcomes.

- [ ] Add failing regressions for exception recovery, timeout/rate-limit/cooldown recovery, valid miss TTL, bounded retry/backoff, and partial-quality retry.
- [ ] Add failing regressions proving a cached hit survives failed or lower-quality upgrades.
- [ ] Add failing v10/v11 hit and legacy-miss migration tests with a fake clock.
- [ ] Add failing concurrent reader/writer tests that reject partial JSON observations.
- [ ] Implement v12 state, selection of due providers, one-provider legacy migration batches, and incumbent-preserving merges.
- [ ] Implement unique same-directory temporary writes plus flush and atomic replace under a per-path lock.
- [ ] Run provider, cascade, cache-location, and cache tests.

### Task 3: Stable Windows playback-session binding

**Files:**
- Create: `src/lyrica/sessions/selection.py`
- Modify: `src/lyrica/sessions/base.py`
- Modify: `src/lyrica/sessions/windows.py`
- Modify: `src/lyrica/app.py`
- Test: `tests/test_sessions.py`
- Test: `tests/test_seek.py`
- Test: `tests/test_worker_results.py`

**Interfaces:**
- Produce immutable `SessionRecord` values and a stateful `SessionSelector.choose(records)` policy.
- `Snapshot.session_id` carries the selected binding; `Snapshot.playback_key()` includes it while `track_key()` remains unchanged.
- `SessionReader.seek(seconds, snapshot)` and `read_artwork(snapshot)` fail closed when the binding no longer resolves.

- [ ] Add failing reader integration tests for reordered equal-playing sessions, pause switching, disappearance, shared app ids, and restart.
- [ ] Add failing seek/artwork tests for exact binding and in-flight track changes.
- [ ] Implement record mapping and stable pure selection without retaining WinRT objects across loops.
- [ ] Pass the shown/loading snapshot at the three minimal `app.py` integration points.
- [ ] Run sessions, seek, worker-result, artwork, metadata, and YouTube tests.

### Task 4: Documentation, verification, and scoped commits

**Files:**
- Modify: `docs/song-identity-sessions-cache.md` only if implementation details changed.

- [ ] Run the focused provider/cascade/session/seek/YouTube/metadata/config/Musixmatch/NetEase/cache suite with isolated system temp and cache paths.
- [ ] Run the complete offline pytest suite with the same isolation.
- [ ] Run `ruff check .`.
- [ ] Review `git diff` and confirm prohibited rendering files and tests are untouched.
- [ ] Commit coherent conventional changes locally without merge, push, issue, or PR operations.
- [ ] Update the Orca worktree comment with exact verification status and commit ids.
