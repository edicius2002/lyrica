# Implementation Plan and Decision Log

> **Status:** Word-by-word lyrics, four ranked sources behind one cascade. The overlay resolves
> tracks from Spotify, YouTube, YouTube Music and SoundCloud, and every pull request is linted and
> tested.
> **Last updated:** 2026-08-19
> **Review status:** Phase 7 merged across [#19](https://github.com/edicius2002/lyrica/pull/19),
> [#21](https://github.com/edicius2002/lyrica/pull/21), [#23](https://github.com/edicius2002/lyrica/pull/23),
> [#25](https://github.com/edicius2002/lyrica/pull/25) and [#27](https://github.com/edicius2002/lyrica/pull/27).
> **Phase closure:** Steps 0–7 complete. Deferred by agreement: the overlay outline has not been
> eyeballed over a genuinely bright background, configurable provider ordering waits for step 8,
> and the visual treatment is being researched separately.
> **Next delivery:** Step 8, packaging. One issue per slice, written before its work.

---

## Documentation Rule

This file is the repository's source of truth for confirmed product decisions, implementation
progress, scope changes, and technical rationale.

- Update it before starting a phase, when a decision changes, and when a phase is completed.
- Preserve prior decisions in the Decision Log; do not rewrite history. A decision that no longer
  holds moves to **Superseded decisions** with the date it changed.
- Repository content, issues, pull requests, commits, code and user-facing application text are
  written in **English**. Conversation about the project may happen in any language.
- Measurements belong in `research/VIABILITY.md`; this file cites them as rationale rather than
  repeating the numbers.

---

## Repository and Delivery Workflow

| Item              | Value                                                                        |
| ----------------- | ---------------------------------------------------------------------------- |
| Repository        | [edicius2002/lyrica](https://github.com/edicius2002/lyrica) (private)        |
| Default branch    | `main`                                                                       |
| Reference clones  | `repos/` — seven third-party projects, gitignored, behaviour reference only  |
| Language          | **Python 3.11**                                                              |
| Packaging         | `pyproject.toml`, src-layout, editable install (`pip install -e .[dev]`)     |
| Platform          | **Windows 10/11** — the media session API has no cross-platform equivalent   |
| Runtime data      | `%LOCALAPPDATA%\Lyrica\cache` — outside the repository                       |
| Branches          | `chore/…`, `feat/…`, `fix/…`, `docs/…`                                       |
| Commits           | Conventional Commits                                                         |
| Merge             | Squash merge preferred                                                       |
| Workflow          | Issue → branch → PR → checks → merge                                         |

### Delivery sequence

| Order | Delivery                    | Scope                                                                                                     |
| ----- | --------------------------- | --------------------------------------------------------------------------------------------------------- |
| 0     | **Research and viability**  | Probe every candidate source under real conditions; clone and licence-check reference projects.           |
| 1     | **MVP overlay**             | Prove end to end that a Windows session can drive synced lyrics on screen.                                |
| 2     | **Repository foundation**   | src-layout package, provider interface, offline tests, packaging metadata, clean initial history.         |
| 3     | **Docs PR**                 | This plan and the viability report in English. No code changes.                                           |
| 4     | **Hygiene and CI**          | `LICENSE`, pinned lint ruleset, GitHub Actions, issue/PR templates, probe-script rename.                  |
| 5     | **Browser validation**      | Verify detection and position drift end to end in Chrome across YouTube, YT Music and SoundCloud.         |
| 6     | **Providers**               | NetEase as a second synced provider; provider ordering and per-provider diagnostics.                      |
| 7     | **Word-by-word**            | Karaoke layer: amll-ttml-db lookup and, if the endpoint is solved, Musixmatch richsync.                   |
| 8     | **Packaging**               | PyInstaller executable, system tray, start with Windows, settings persistence.                            |

---

## Confirmed Folder Structure

Create folders only when they have an active responsibility. No empty placeholders.

```text
lyrica/
|-- src/
|   `-- lyrica/
|       |-- __init__.py              # version
|       |-- __main__.py              # python -m lyrica
|       |-- app.py                   # tkinter overlay, render loop
|       |-- smtc.py                  # Windows media session reader
|       |-- lyrics.py                # Lyrics model + LRC parser
|       `-- providers/
|           |-- __init__.py          # cascade + on-disk cache
|           |-- base.py              # LyricsProvider interface
|           `-- lrclib.py            # LRCLIB implementation
|-- tests/                           # offline unit tests, no network
|-- research/
|   |-- VIABILITY.md                 # measured source conditions
|   `-- viability/                   # probe scripts (network, run by hand)
|-- docs/
|   `-- IMPLEMENTATION_PLAN.md       # THIS FILE
|-- repos/                           # gitignored — third-party reference clones
|-- pyproject.toml
|-- README.md
|-- .gitattributes
`-- .gitignore
```

### Dependency rules

- `providers/*` may import `lyrics` and `providers.base`; it must **not** import `app` or `smtc`.
  A provider knows nothing about who is playing or how anything is drawn.
- `smtc` must not import `providers` or `lyrics`. It reports what the operating system says and
  nothing else.
- `app` is the only module allowed to depend on both sides, and the only one that touches tkinter.
- No module reaches a lyrics API directly; every network call lives behind a `LyricsProvider`.

---

## Product Baseline

An always-on-top desktop overlay for Windows that displays time-synced lyrics for whatever is
currently playing, regardless of which application is playing it.

**Out of scope:** mobile; cloud sync; a hosted service; downloading or storing audio; editing or
contributing lyrics upstream; any platform other than Windows; audio fingerprinting (revisit only
if SoundCloud coverage becomes the priority).

### Supported players

| Source              | Detection            | Expected coverage                                          |
| ------------------- | -------------------- | ------------------------------------------------------------ |
| Spotify desktop app | Media session        | Best case: clean metadata, accurate position                |
| YouTube Music       | Media session        | Best case                                                   |
| YouTube             | Media session        | Good once titles are normalized; non-musical videos miss    |
| SoundCloud          | Media session        | Mainstream resolves; underground uploads have no lyrics     |
| Any other player    | Media session        | Works if it responds to media keys                          |

### Feature outcomes

| Area           | Outcome                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------ |
| Detection      | Track identity and playback position from the Windows media session, interpolated         |
| Lookup         | Provider cascade with scoring and an on-disk cache that also remembers misses             |
| Display        | Previous / current / next line, drag to move, adjustable sync offset, instrumental marker |
| Degradation    | Word level when available, line level as the baseline, plain text paged by progress       |

---

## Source and Data Plane

Three kinds of traffic with different lifetimes. Do not mix them.

```text
A) Track identity     → Windows media session, polled twice per second
B) Lyrics content     → provider cascade over HTTP, cached on disk indefinitely
C) Playback position  → derived in memory every 100 ms, never stored
```

| Kind                                   | Store                              | Notes                                                     |
| -------------------------------------- | ---------------------------------- | ----------------------------------------------------------- |
| Artist, title, album, duration, state  | Memory, replaced each poll         | Immutable snapshot; the render loop never mutates it       |
| Lyrics documents and misses            | `%LOCALAPPDATA%\Lyrica\cache`      | Keyed by artist, title and duration; a miss is a result    |
| Playback position                      | Memory, derived on read            | Reported value plus elapsed time since it was reported     |
| Sync offset, window position           | Memory only, for now               | Becomes persisted settings in step 8                       |

Lyrics are fetched for local display and cached for reuse by the same user. Nothing is
redistributed, republished or bundled with the repository.

### Lookup runtime

1. The media session reader publishes a new snapshot; the render loop notices the track key changed.
2. Artist and title are normalized — browser titles split on a separator, video noise stripped.
3. A worker thread walks the provider cascade, guarded by a generation counter so a stale response
   from a previous track can never overwrite the current one.
4. The result, hit or miss, is written to the cache.
5. The render loop interpolates the position and selects the active line every 100 ms.

---

## Architecture Targets

### Threading model

```text
main thread          tkinter mainloop → render tick every 100 ms
smtc-reader thread   own asyncio loop → publishes an immutable Snapshot every 500 ms
fetch worker         one short-lived thread per track change, guarded by a generation counter
```

The overlay never blocks on the network, and no lock is needed: the reader publishes whole
snapshots by rebinding one attribute, and the render loop only ever reads.

### Overlay

- `overrideredirect` window, always on top, transparent background colour key.
- Three lines rendered: previous, current, next, with the current line emphasized.
- Redraw only when the rendered tuple changes, so a static line costs nothing.
- Drag to move, `Esc` or right click to quit, `+` / `-` to nudge the sync offset by 0.25 s.
- Text outline for legibility over light backgrounds is a known gap — see step 5.

### Quality

- `pytest`, offline only. **No test may touch the network**, so the suite stays deterministic and
  a source being down never fails the build. Network behaviour is exercised by the probes in
  `research/viability/`, run by hand.
- `ruff` for linting. The ruleset is not yet pinned in `pyproject.toml`, so the current run
  reports whatever the installed version defaults to — closing that is step 4.
- CI on pull requests and `main`: lint plus tests. Windows runner, because the package imports
  `winsdk` at module scope.

### Errors

| Source                        | Behaviour                                                    | Phase   |
| ----------------------------- | -------------------------------------------------------------- | ------- |
| No media session              | Overlay shows a waiting state                                | Done    |
| Media session read fails      | Falls back to an empty snapshot; the overlay keeps running    | Done    |
| Provider network failure      | Treated as a miss and the cascade continues                   | Done    |
| No provider has the track     | "No lyrics found", and the miss is cached                     | Done    |
| Unexpected failure diagnosis  | Blind `except` blocks currently swallow the cause             | Step 4  |

### Code conventions

- Modules and functions `snake_case`; classes `PascalCase`.
- Type hints on public functions; `X | None` rather than `Optional[X]`.
- Comments state constraints the code cannot express, not what the next line does.
- A new lyrics source is a new module under `providers/`, never a branch inside an existing one.

---

## Feature IDs

| ID          | Function                                                                       |
| ----------- | -------------------------------------------------------------------------------- |
| SMTC-01…05  | Session read, snapshot model, position interpolation, metadata normalization    |
| PROV-01…08  | Provider interface, LRCLIB, cache, cascade ordering, NetEase, diagnostics       |
| SYNC-01…04  | LRC parsing, active-line lookup, offset nudging, word-level timing              |
| UI-01…08    | Overlay window, three-line render, drag, legibility, settings surface           |
| PKG-01…04   | Executable, tray, start with Windows, persisted settings                        |
| RES-01…05   | Source probes, viability report, licence review of reference clones             |

---

## Phase Checklists

### 0 — Research and viability

**Status:** Complete. Findings in [`research/VIABILITY.md`](../research/VIABILITY.md).

- [x] Probe LRCLIB for coverage, latency and rate limiting under three metadata profiles
- [x] Probe the unofficial Musixmatch desktop API, including the richsync endpoint
- [x] Probe amll-ttml-db reachability and index size
- [x] Probe the `syncedlyrics` aggregator provider by provider
- [x] Verify the Windows media session live against a real player
- [x] Clone seven reference projects and record their licences

### 1 — MVP overlay

**Status:** Complete. Verified live against Spotify desktop: the displayed line matched the
reported playback position.

- [x] Media session reader with position interpolation
- [x] LRCLIB lookup with exact match and fuzzy fallback
- [x] Transparent always-on-top window with three-line rendering
- [x] On-disk cache

### 2 — Repository foundation

**Status:** Complete. Six commits on `main`, clean tree, nine tests passing.

- [x] `git init`, `.gitignore` excluding reference clones and runtime data
- [x] Migrate the flat MVP to a src-layout package (decision S.1)
- [x] Extract the provider interface and cascade from the LRCLIB call site
- [x] Move the cache out of the source tree (decision S.2)
- [x] Translate code and comments to English (decision S.3)
- [x] Offline unit tests for LRC parsing and metadata normalization
- [x] `pyproject.toml` with a `lyrica` console script and dev extras
- [x] `.gitattributes` normalizing line endings

### 3 — Docs PR

**Status:** Complete. This document, merged; the branch it names is long gone.

- [x] `docs/IMPLEMENTATION_PLAN.md` — this file
- [x] Translate the viability report to English and rename it `research/VIABILITY.md`
- [x] Open the PR against `main`

**Out of scope:** any change under `src/` or `tests/`.

### 4 — Hygiene and CI

**Status:** Complete. Delivered in [#11](https://github.com/edicius2002/lyrica/pull/11),
[#12](https://github.com/edicius2002/lyrica/pull/12) and
[#13](https://github.com/edicius2002/lyrica/pull/13).

- [x] `LICENSE` file matching the `pyproject.toml` declaration (decision 5.5)
- [x] Pin the `ruff` ruleset explicitly and fix or justify each finding (decision 5.6)
- [x] Replace blind `except Exception` blocks with logged, narrowed handling (decision 3.10)
- [x] Logging to a rotating file, since the overlay has no console (decision 5.7)
- [x] Rename `research/viability/test_*.py` to `probe_*.py`
- [x] GitHub Actions on a Windows runner: lint plus tests (decision 5.8)
- [x] Issue and pull request templates

`ruff check .` is clean and the CI run on its own pull request passed in 54 seconds.

### 5 — Browser validation

**Status:** Complete. Measurements in [`research/BROWSER_SESSIONS.md`](../research/BROWSER_SESSIONS.md).
Delivered in [#4](https://github.com/edicius2002/lyrica/pull/4),
[#6](https://github.com/edicius2002/lyrica/pull/6) and
[#8](https://github.com/edicius2002/lyrica/pull/8).

This was the largest open risk in the plan, and measuring it removed rather than confirmed it.

- [x] Capture real media-session payloads from Chrome on YouTube, YT Music and SoundCloud
- [x] Measure position drift against the page's own clock — 0.001 s of spread (decision 6.1)
- [x] Companion browser extension **dropped**: interpolation needs no help (decision 6.1)
- [x] Correct the normalization rules against captured payloads (decisions 6.2, 6.3)
- [x] Rewrite the browser metadata tests from those payloads (decision 6.5)
- [x] Text outline, since browser use means arbitrary backgrounds behind the overlay

**Deferred:** the outline has been seen rendering, but not over a genuinely bright background —
a white test window could not be placed behind a topmost overlay without covering it. Worth one
eyeball over a light page.

**Not captured:** SoundCloud position drift. It exposes no `<audio>` element in the DOM, so the
page-clock comparison used for YouTube does not apply; its own player clock would have to be
read instead. Low priority, since the interpolation being measured is the same code path.

### 6 — Providers

**Status:** Complete. Delivered in [#15](https://github.com/edicius2002/lyrica/pull/15) and
[#16](https://github.com/edicius2002/lyrica/pull/16).

Ranking came first, before adding a source, because a second provider on the old cascade would
have made it worse rather than better: `fetch_lyrics()` returned the first provider that answered
**at all**, so a plain hit shadowed a synced one.

- [x] Rank result tiers explicitly and keep the best answer (decision 7.1)
- [x] Stop early only on a definitive answer, so a request is spent only when the answer is weak
- [x] NetEase as a second synced provider behind the same interface (decision 7.3)
- [x] Per-provider diagnostics: which source answered, at what tier, and how long it took
- [x] Cache entries record which providers produced them, so a new source supersedes a weak hit
- [x] A broken provider no longer denies the track lyrics another source has (decision 7.6)

**Deferred to step 8:** configurable provider order. The default ordering is now principled —
precision, then measured latency — and inventing a settings format here would only be replaced by
the settings persistence step 8 already owns.

**Found by verifying rather than assuming:** LRCLIB's fuzzy search returned an *instrumental*
record for a song that plainly has lyrics. Instrumentals counted as definitive, so the cascade
stopped and never asked anyone else — a silent wrong answer that the tests could not see and that
would otherwise have shipped. Fixed by decision 7.2.

### 7 — Word-by-word

**Status:** Complete. Delivered in [#19](https://github.com/edicius2002/lyrica/pull/19),
[#21](https://github.com/edicius2002/lyrica/pull/21), [#23](https://github.com/edicius2002/lyrica/pull/23),
[#25](https://github.com/edicius2002/lyrica/pull/25) and [#27](https://github.com/edicius2002/lyrica/pull/27).

Phase 0 concluded that no free, unlimited source had broad word-level coverage. That was wrong on
both counts, and re-measuring is what moved this phase from doubtful to done — see decision 8.1.

- [x] Optional per-word timings on `Lyrics`, plus the sweep fraction a renderer needs
- [x] TTML parser, including the two robustness cases real documents forced (decision 8.2)
- [x] Community TTML source — word-level with no authentication of any kind (decision 8.3)
- [x] Musixmatch richsync as the second word source, rate-limit aware (decisions 8.4, 8.5)
- [x] Render the current line word by word, degrading to whole lines without timings
- [x] Cache location configurable, so it follows between machines (decision 8.7)
- [ ] amll-ttml-db — **dropped**: measured at ~2,400 Spotify-indexed entries with 1 of 4 Western
      probes resolving, and it keys on an ID the media session does not expose. Two better sources
      now cover the same ground.

Measured end to end on real tracks, the cascade resolves word-level for every one tried, with the
community source carrying the common case and Musixmatch covering what it misses.

**Deferred:** the visual treatment — frosted background, Apple-style motion, easing. Being
researched separately, since it may argue for a different rendering stack and that decision should
not be made inside a data-layer phase.

### 8 — Packaging

**Status:** Mostly complete. Delivered in
[#69](https://github.com/edicius2002/lyrica/pull/69) and
[#70](https://github.com/edicius2002/lyrica/pull/70).

- [x] PyInstaller executable — `pyinstaller lyrica.spec`, one file, ~30 MB, no
      Python on the target machine. `winsdk` needed collecting explicitly or the
      build started happily and then never noticed a track.
- [x] System tray icon with show, hide and quit — written against
      `Shell_NotifyIcon` directly rather than through a library, since the rest
      of the Windows integration is already ctypes and a dependency would also
      have had to survive PyInstaller.
- [x] Optional start with Windows — a per-user registry entry, offered only by
      the packaged build because a source checkout cannot name a stable command.
- [x] Persist window position and sync offset — kept in a single settings file
      beside the cache. The *middle* of the window is stored rather than its
      corner, because the panel collapses to the card for songs with no lyrics
      and a corner is only true for the width it was taken at: measured
      reopening 400 px right of where it had been put.
- [ ] Configurable provider order — still fixed in code. A preference rather
      than a fix: the current order is measured rather than assumed.

### 9 — Look and reaction

**Status:** Complete for now, and entirely demand-driven rather than planned.

- [x] Colours derived from the cover art, solved against the window's own
      composition law ([#61](https://github.com/edicius2002/lyrica/pull/61),
      [#76](https://github.com/edicius2002/lyrica/pull/76)).
- [x] Rounded corners, traded for the frosting after measuring that DWM draws
      the accent plate over the whole window rectangle and ignores the clip
      region ([#63](https://github.com/edicius2002/lyrica/pull/63)).
- [x] Global shortcuts and a compact panel for songs with no lyrics
      ([#67](https://github.com/edicius2002/lyrica/pull/67),
      [#68](https://github.com/edicius2002/lyrica/pull/68),
      [#73](https://github.com/edicius2002/lyrica/pull/73)).
- [x] A border that reacts to what is playing, read from the Windows endpoint
      meter rather than by capturing audio
      ([#77](https://github.com/edicius2002/lyrica/pull/77),
      [#80](https://github.com/edicius2002/lyrica/pull/80),
      [#84](https://github.com/edicius2002/lyrica/pull/84)).
- [x] Even frame scheduling, which turned out to be why the existing lyric
      animation stuttered as much as why the new border did
      ([#79](https://github.com/edicius2002/lyrica/pull/79)).

---

## Decision Log

### 1. Repository and workflow

| ID  | Decision                                                                        | Rationale                                                                                                        |
| --- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 1.1 | Private repository `edicius2002/lyrica`.                                         | Personal tool; nothing here needs an audience yet.                                                               |
| 1.2 | Issues, branches, Conventional Commits, pull requests.                           | An incremental, reviewable history, even working alone.                                                          |
| 1.3 | English for repository content, code and UI.                                     | Consistency, and the option to publish later without a rewrite.                                                  |
| 1.4 | This plan is the living source of truth.                                         | One spine. Decisions stop living in chat logs.                                                                   |
| 1.5 | Reference clones stay in gitignored `repos/`; no code is copied from them.       | They are behaviour references. Vendoring would import their licences along with their code.                      |
| 1.6 | Docs land before further code.                                                   | The plan should describe the next phase before the next phase starts, not after.                                 |

### 2. Product scope

| ID  | Decision                                                                        | Rationale                                                                                                                                       |
| --- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2.1 | A desktop overlay, not a browser extension.                                      | An extension cannot see the Spotify desktop app, and a per-site extension needs new code per platform. One media session covers every player.    |
| 2.2 | Windows only.                                                                    | The design depends on the Windows media session; Linux MPRIS and macOS have no equivalent shape. Cross-platform would be a different program.    |
| 2.3 | Line-level sync is the product baseline; word level is an enhancement.           | Measurement: no free, unlimited source has broad word-level coverage. Shipping line level means shipping something that works for every track.   |
| 2.4 | Lyrics are displayed and cached locally, never redistributed.                    | The cache is a private convenience for one user, not a database being republished.                                                              |
| 2.5 | Audio fingerprinting is out of scope for now.                                    | It is the only angle on SoundCloud underground uploads, but it is a whole subsystem serving the least-covered platform. Revisit if that flips.  |

### 3. Architecture

| ID  | Decision                                                                        | Rationale                                                                                                                                                                       |
| --- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3.1 | The Windows media session is the single detection mechanism.                     | Every player that answers media keys publishes to it, so Spotify and four browser sites cost one implementation rather than five.                                               |
| 3.2 | Position is interpolated from a timestamped snapshot, never read as live.        | The API reports where playback was at a given moment, not where it is now. Reading it directly would leave lyrics lagging by up to the poll interval, which is visible on screen. |
| 3.3 | Snapshots are immutable and published by rebinding one attribute.                | The reader thread and the render loop share state without a lock, and the render loop can never observe a half-written snapshot.                                                 |
| 3.4 | Every source sits behind a `LyricsProvider`, and the cascade is ordered.          | The measured conclusion was that no single source suffices. Adding one becomes a new module rather than a new branch in the lookup path.                                         |
| 3.5 | The cache stores misses as results.                                              | A track with no lyrics is the common case for SoundCloud. Without caching the miss, every replay pays the full cascade again for a known-empty answer.                           |
| 3.6 | The cache lives in `%LOCALAPPDATA%`, not in the repository.                      | It is user data, not source. In the source tree it needed a `.gitignore` entry and would have shipped inside any future executable.                                             |
| 3.7 | Browser metadata is normalized before lookup, not inside the provider.           | Noise like "(Official Video)" is a property of how the track was announced, not of who is being asked for lyrics. Every provider would otherwise reimplement the same cleaning.  |
| 3.8 | A generation counter guards fetches.                                             | Skipping tracks quickly leaves several requests in flight. Without it, a slow response for a previous track can overwrite the lyrics for the current one.                        |
| 3.9 | tkinter for the overlay; Qt deferred.                                            | tkinter ships with Python and already gives a transparent, always-on-top, click-through-adjacent window. Qt buys better text rendering, which only matters once legibility does. |
| 3.10 | The reader thread keeps a broad `except`, but logs the traceback.               | It is the overlay's only source of truth. If it dies the window silently freezes on a stale line with nothing to indicate anything is wrong, which is worse than any exception it might swallow. Logging turns the breadth into a visibility cost rather than a diagnosis one. Per-session reads narrow to `OSError`, which is how WinRT reports a session that vanished mid-read. |

### 4. Sources

Measurements behind these decisions are recorded in [`research/VIABILITY.md`](../research/VIABILITY.md).

| ID  | Decision                                                                        | Rationale                                                                                                                                                                                    |
| --- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.1 | LRCLIB is the primary source.                                                    | The only candidate that is keyless, free and unmetered under test: thirty consecutive requests all succeeded. Full synced coverage on mainstream material at roughly 680 ms.                  |
| 4.2 | Lookup is exact `/get` first, then scored fuzzy `/search`.                        | `/get` needs a duration within ±2 s, which browser sources cannot always supply. Two of ten clean-metadata tracks and two of three YouTube titles resolved only through the fallback.         |
| 4.3 | The unofficial Musixmatch API is never a dependency.                             | It works today without registration, but it is undocumented, in a licensing grey area, and can break or ban an IP without notice. Acceptable as an enhancement, never as a foundation.        |
| 4.4 | amll-ttml-db is an enhancement layer, not a provider tier.                        | Free and unmetered, but roughly 2,400 Spotify-indexed entries with a strong Asian-pop bias — one of four Western probes resolved. It also keys on a track ID the media session does not give. |
| 4.5 | Word-by-word ships only where a source actually has it, degrading silently.       | Both word-level sources are narrow. A karaoke feature that works for one track in four must degrade to line level invisibly rather than look broken.                                          |
| 4.6 | NetEase is the second synced provider.                                            | It answered with synced lyrics for every probe track, is free, and needs no key. Genius returns plain text only, and Megalobiz did not answer at all.                                         |

### 5. Tooling

| ID  | Decision                                                                        | Rationale                                                                                                                                                                        |
| --- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5.1 | Python 3.11 with a src-layout package and an editable install.                    | src-layout means the tests import the installed package rather than whatever happens to sit in the working directory, so a broken packaging change fails loudly instead of silently. |
| 5.2 | Tests never touch the network.                                                    | A suite that calls LRCLIB fails when LRCLIB is slow, which teaches you to ignore red. Network behaviour belongs to the probes, which are run deliberately.                        |
| 5.3 | Probe scripts are versioned alongside the report they produced.                   | A measurement whose method is not reproducible is an anecdote. The probes are how any of these numbers can be challenged later.                                                   |
| 5.4 | CI runs on a Windows runner.                                                      | The package imports `winsdk` at module scope, so a Linux runner cannot even import it.                                                                                            |
| 5.5 | GPL-3.0-or-later.                                                                 | A deliberate choice, not an inherited obligation: no third-party code was copied, so any licence was available. GPL matches the ecosystem this tool grew out of.                  |
| 5.6 | The lint ruleset is selected explicitly, never inherited.                          | Setting only `line-length` left the enforced rules to whatever the installed ruff defaulted to, so an upgrade could change what the build accepts without a commit. Exemptions are per-file and carry their reason. |
| 5.7 | Logging goes to a rotating file under `%LOCALAPPDATA%`.                            | The overlay has no console — from the Start menu, or a packaged executable, stderr goes nowhere. Logging into the void is worse than not logging, because the code then reads as though failures are being recorded. |
| 5.8 | CI stays to one install and two commands, and cancels superseded runs.             | Windows minutes bill at double rate on private repositories, and Windows is not optional (decision 5.4). Keeping the job small is what makes running it on every push affordable. |

### 6. Browsers

Measurements behind these are in [`research/BROWSER_SESSIONS.md`](../research/BROWSER_SESSIONS.md).

| ID  | Decision                                                                        | Rationale                                                                                                                                                                                                                                                        |
| --- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 6.1 | No companion browser extension. Interpolation stands alone.                      | Scored against the page's own clock, the error was flat: 0.001 s of spread across a 27 s run, before and after a seek. Chrome states its position once and re-anchors on discontinuities, which is exactly when interpolation needs one. There is nothing to fix. |
| 6.2 | The artist field is never trusted on its own; readings are ranked.                | Each site means something different by it. YouTube states it correctly *and* repeats it in the title; SoundCloud puts the uploader's handle there while the real artist sits in the title. Nothing in the payload says which case applies, so ranking beats guessing. |
| 6.3 | A leading repetition of the artist is stripped across a separator, or across a longer credit. | "Dua Lipa - Levitating" with artist "Dua Lipa" is a repetition; "Madonna Of The Wasps" is a song. Requiring the separator is what tells them apart — but "Tiago PZK, Myke Towers - Traductor" with artist "Tiago PZK" is the same repetition with a second name in the way, so a credit joiner (`,` `&` `feat.` `x` `con`) counts as well. A song called "Airbag" by an artist called "Air" still has neither. |
| 6.7 | The card is named after the reading that found the lyrics, and covers ask every reading. | Only one reading finds the song, and it is the closest thing to a correct name anything in the process knows. Naming the card from the raw payload contradicted the lyrics on screen — "BillieEilishVEVO — Billie Eilish - CHIHIRO" over a match found as "Billie Eilish — CHIHIRO" — and asking only the first reading for a cover left label-channel videos wearing their own letterboxed thumbnail: that reading found nothing in 482 ms where the next found 38 KB. |
| 6.9 | A catalogue entry outranks every reading of the payload, at a higher bar than a cover needs. | A browser gives a description of a video, not a name for a track: "Kendrick Lamar - Die Hard (Letra Español)" from a channel called "Slimad Shady". The catalogue answered "Die Hard" by "Kendrick Lamar, Blxst & Amanda Reifer" from *Mr. Morale & The Big Steppers* — the featured artists and the album, which nothing else in the process has for a browser at all. The bar is 4 rather than the 3 a cover needs: a wrong sleeve is a cosmetic loss, a wrong name tells somebody something false. |
| 6.10 | Where a video's song starts is asked of SponsorBlock, not derived. | Decision 6.8 was right that it cannot be detected, and wrong that correction was the only option left — it considered metadata and audio, and the answer was in neither. `music_offtopic` exists only for music videos and marks what is not the song, so a segment beginning at zero states where the song starts. Validated by removing every marked stretch from twelve videos and comparing what was left against the release's duration: eleven agreed within 7 s. The twelfth was Thriller, whose video is a fourteen-minute short film with an extended dance break — its music is a different edit, and no correction holds a release's lyrics on it. Modelled as a piecewise mapping rather than an offset because two videos in fifteen carry a marked stretch in the middle, past which a constant is wrong by its whole length. Asked through the hashed-prefix endpoint, so the service never learns what is playing. |
| 6.8 | A video's intro is corrected by one keypress, not detected.                     | It cannot be detected. All twenty synced records LRCLIB holds for one song begin at 00:02.17 while claiming durations from 5 s to 935 s: the duration field is whatever file the submitter had, and there is one timeline per song, anchored to the release. From metadata the intro is only bounded — for that song, anywhere in [0, 24.4 s]. The only other signal is audio, and a render-endpoint peak meter can find "quiet → loud" but not "intro music → verse". So `Enter` says the first line starts now, and the answer is kept per track. |
| 6.4 | Duration is retried as absent when an exact lookup misses.                        | LRCLIB matches within ±2 s, and a re-upload can be padded — one copy of a 3-minute song reported 12 minutes. A wrong duration turns a findable track into a miss.                                                                                                |
| 6.5 | Browser metadata tests are only written from captured payloads.                   | The previous test asserted that Chrome leaves `artist` empty. Chrome never does, so it passed while describing a payload that does not exist, and the bug it was meant to guard shipped anyway.                                                                  |
| 6.6 | Text is outlined rather than shadowed, with symmetric offsets.                     | A shadow is directional and reads as depth; an outline reads as separation, which is what arbitrary backgrounds need. Ringing the centre rather than filling a square is 8 draws per line instead of 24, with the difference hidden under the fill anyway.        |

### 7. Providers

| ID  | Decision                                                                        | Rationale                                                                                                                                                                                                                                                            |
| --- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 7.1 | The best answer wins, not the first. Search stops only on a definitive one.       | Answers are not interchangeable: plain text has no timing, and the overlay can only page it by playback progress, which looks synchronised while being a guess. Stopping only when the answer is good enough spends an extra request precisely when it is warranted. |
| 7.2 | An instrumental is definitive only when the provider matched the track exactly.   | Found live: a fuzzy search returned an instrumental record for a song that plainly has lyrics, ending the cascade. Karaoke and backing-track uploads sit right beside the songs they came from, so a loose match lands on them easily and reports a silent falsehood. |
| 7.3 | NetEase is queried directly, not through `syncedlyrics`.                          | That package would pull beautifulsoup4, rapidfuzz and their trees into the runtime to reach one source. The two endpoints it wraps are two plain HTTP calls.                                                                                                        |
| 7.4 | Providers are ordered by precision, then by measured latency.                     | Neither source offers word-level timing today, so speed is the live tiebreak: LRCLIB answers in ~0.7 s against NetEase's ~2.6 s, and the ranking means the slower one is only reached when the faster one came up short.                                            |
| 7.5 | A search result is verified before it is trusted, and discarded when it is not.   | NetEase's search has no notion of failure — it returns the nearest thing it can find. One probe track came back as a different artist's song of the same name, with a duration close enough to pass a duration check on its own.                                     |
| 7.6 | A provider that raises does not deny the track lyrics another source has.         | One source being broken or blocked is not evidence the song has no lyrics. The traceback is logged and the cascade continues.                                                                                                                                       |
| 7.7 | Configurable provider ordering waits for the settings work in step 8.             | The default ordering is principled rather than arbitrary, so a config format invented here would buy nothing and be replaced by the persistence step 8 already owns.                                                                                                |

### 8. Word-level lyrics

Measurements in [`research/VIABILITY.md`](../research/VIABILITY.md) and the probes beside it.

| ID  | Decision                                                                        | Rationale                                                                                                                                                                                                                                                     |
| --- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 8.1 | Sources are re-measured before a phase, not trusted from an earlier one.         | Phase 0 recorded richsync as dead on 404s that came from calling it by name instead of by track id, and NetEase as having no word data because the probe asked for a format almost no record uses. Two sources were written off by measurement error, not absence. |
| 8.2 | The TTML parser repairs what it can and refuses what it must.                     | Real documents use namespace prefixes they never declare, and discarding the lyrics over a missing attribute helps nobody. A DTD is refused outright instead: these arrive over the network, and entity expansion is the only attack ElementTree still allows.  |
| 8.3 | The community TTML source leads the cascade.                                      | Word-level with no token, key, captcha or signing anywhere — the only source here with no authentication at all. 6 of 10 tracks played on this machine came back word-timed.                                                                                    |
| 8.4 | Musixmatch is second, never first.                                                | Wider coverage (13/16 against 6/10) but undocumented, commercial and throttled. Placing it behind the free source keeps it off the common path, so it is asked only for what the free source missed.                                                            |
| 8.5 | A refusal stops the provider entirely for fifteen minutes.                        | It throttles after roughly twenty paced lookups, and refuses by reporting *no match* rather than an error — so a run that hits the limit silently reports every remaining track as missing. Retrying into that is what turns a temporary limit into a block.    |
| 8.6 | An inferred word duration is capped.                                              | richsync gives offsets and no durations, so a word's end comes from the next word's start. Uncapped, an instrumental break inside a line reads as one enormous word and leaves the highlight stuck mid-line for seconds.                                        |
| 8.7 | The cache stays many small immutable files, and its location is configurable.      | That shape is what makes plain folder sync safe between machines: entries are written once and never modified, so two machines can add different files but never disagree about one. A shared database would be worse — concurrent writers are what corrupt one. |
| 8.8 | Canvas items are built per line and afterwards only recoloured.                    | A sweep changes continuously, so rebuilding each frame is exactly what makes it look stepped. The fast tick also applies only while a word is lit: the overlay sits on screen all day, and 33 ms on a still line is pure cost.                                  |
| 8.9 | Word-level degrades silently to line level, per line.                              | Coverage is not all-or-nothing even within a track — verses can be word-timed and a shouted chorus not. Representing that per line means the seam never shows.                                                                                                 |

### 9. Look and reaction

The frame budget these are weighed against is 16 ms: the reactive border keeps the loop at 60 Hz for as long as anything is playing, so work done per frame is paid tens of thousands of times an hour.

| ID  | Decision                                                                        | Rationale                                                                                                                                                                                                                                                     |
| --- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 9.1 | A cache whose answer depends on the scale carries the scale in its key, rather than being cleared from the resize path. | The card had three measurement caches and the resize path cleared two of them. The third kept a width measured at the previous font and the card was centred on it at every size. A cache that only one caller knows to clear is one that will eventually be missed, and this one already had been — under a comment claiming the fault was fixed. |
| 9.2 | The glyph image cache is bounded by count *and* by bytes, and evicts by last use.  | It was never cleared in production: thirty songs reached 14,152 images, and fifteen font sizes over ten songs about 1.3 GB. The eviction argument is from capacity — a picture on screen was used recently, and 2400 others must arrive before it is dropped. Worth recording that this is the weaker of the two guarantees considered: a picture that stays up is never asked for again, so recency does not actually track what is being shown. Asking Tk `image inuse` does, and is the answer to reach for if that ceiling is ever lowered. |
| 9.3 | A scale change moves the window and the border over a curve; the words wait for it to land. | Both of those are arithmetic and interpolate, and the ring is cheap to relay now that it reuses its items. Text cannot come along: Tk font sizes are integers — the same reason word growth is drawn from pre-rasterised images — and a scale change is twenty sizes rather than one. The column is left empty for the move because text sized for the window it is going to, inside the window it is still in, hangs out past the border for the whole of it. |
| 9.4 | Size presses are folded into one rebuild per burst.                               | A poll returns everything queued since the last tick and each step was a whole rebuild — a settings write, a clip region, the ring, every view, the cover derived again. No frame is drawn between them, so N presses painted N-1 sizes nobody could see; crossing the range was half a second of a panel that did not answer. |
| 9.5 | The light that falls outside the panel is carried by a companion `WS_EX_LAYERED` window, not by the overlay. | The border was rejected ten times for looking plastic, and none of the ten attempts was working on the fault. The overlay is clipped by `SetWindowRgn` to a one-bit rounded rectangle, so every pixel of light stopped dead at the window's edge — and `halo.Shape` carried an `edge` term whose only purpose was to extinguish the outward half *before* it got there, because a hard cut across a glow reads worse than no glow. Half the light was being deleted to hide a boundary. Tk offers `-alpha` (one number for the window) and `-transparentcolor` (one bit a pixel) and nothing else, so the spill cannot come from Tk at any price. `UpdateLayeredWindow` is per-pixel alpha in user32 with no new dependency; measured (`probe_layered_glow.py`), it coexists with Tk in one process and one mainloop, takes no click and no focus, and stays under the panel in the z-order with an owner and without. |
| 9.6 | The two halves are laid out from one geometry and repainted from one loop and one strip index. | They are one stretch of edge cut by a window boundary, not two effects. `beam.py` produces four tables describing the whole circumference; both halves are looked up through those tables, on the same strip, in the same call. The alternative considered was driving the companion from `Beam` beside `Ring.paint`, which reads more directly and makes lockstep a property of call order instead of construction — and what a one-frame disagreement looks like is the border lit to one colour inside the panel and another outside it, along the exact line the split runs down. That is the artefact the whole exercise exists to remove. |
| 9.7 | The companion's bitmap only ever grows, and a smaller panel is presented as a sub-rectangle of it. | The open risk the probe could not close: a collapse changes the panel's size on every one of its twenty-one frames, and `CreateDIBSection` per frame is a GDI allocation and free on the path `bloom.py` documents the consequences of. Measured instead (`probe_glow_in_app.py collapse`): with a grow-only surface the bitmap was rebuilt **0 times across a fold and an unfold**, because the panel starts at its largest and everything after is smaller. Worth recording that the rebuild itself turned out cheap — 0.02 ms median for a 1334×536 section, 0.43 ms to touch every byte of it once — so the decision is not "a rebuild was too dear" but "there is no rebuild to pay for". The cost of the slack is address space nobody touches; `present` is given the size actually wanted and reads the top-left corner. |
| 9.8 | Only the outward half is masked at the panel's outline. The canvas half is left whole and the clip region cuts it. | The clip is one bit and rasterised by GDI, and no mask computed in `halo.py` is guaranteed to agree with it to the pixel. Masking both would make a disagreement a *gap* — a dark notch a pixel wide round each corner, which is precisely the artefact this replaces. Leaving the canvas half whole makes a disagreement an overlap instead, and an overlap is one pixel of light composed twice. Photographed at seven times on the running app: the ridge covers the staircase and neither shows. |
| 9.9 | The field cache is bounded at 48 MiB rather than 24.                              | An entry now carries the outward band as well as the inward one and measured 2.4 MiB against 0.7 MiB. At the old bound a collapse of the default panel kept fourteen of its twenty-one sizes, so the unfold — which asks for exactly the same twenty-one — started paying for a third of them again: a warm re-fold went from 4.7 ms a frame to 9.8. The bound exists to hold one whole animation; 48 MiB is what that now costs. |

### Open

| ID  | Question                                                                        | What is known                                                                                                                                                                                                                                                    |
| --- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| O.1 | The border is mostly absent for the length of a resize animation.                 | `halo.PER_CALL` repaints one strip a frame, and a resize invalidates all four every frame, so at any moment during a collapse or a scale glide three of the four are blank. **Pre-existing and unchanged by decision 9.5** — photographed at `97204e4` and after, side by side, and identical. It is more noticeable now only because the missing light used to have nowhere to go anyway. Lifting the cap for strips whose *fields* changed (as against strips whose colour moved) would fix it and costs three more strip paints on a frame that already measured 12-24 ms, so it belongs to its own decision rather than to this one. |

### Superseded decisions

| ID  | Change                                                                                  | When       |
| --- | ----------------------------------------------------------------------------------------- | ---------- |
| S.1 | Flat `mvp/` folder → `src/lyrica/` package with a provider sub-package.                  | 2026-08-06 |
| S.2 | Cache under the source tree → `%LOCALAPPDATA%\Lyrica\cache`.                             | 2026-08-06 |
| S.3 | Code, comments and UI text in Spanish → English (decision 1.3).                          | 2026-08-06 |
| S.4 | A browser extension in the style of better-lyrics → a desktop overlay (decision 2.1).    | 2026-08-06 |
| S.5 | Local-only git → GitHub remote with the issue/PR workflow.                               | 2026-08-06 |
| S.6 | `research/INFORME.md` in Spanish → `research/VIABILITY.md` in English.                   | 2026-08-06 |
| S.7 | A companion extension "if drift proves interpolation insufficient" → dropped outright, since it did not (decision 6.1). | 2026-08-06 |
| S.8 | Title split only when `artist` is empty → ranked readings, since Chrome never leaves it empty (decision 6.2). | 2026-08-06 |
| S.9 | Overlay rendered with tkinter `Label`s → a `Canvas`, which is the only way to outline text (decision 6.6). | 2026-08-06 |
| S.10 | Lint rules inherited from ruff's defaults → selected explicitly (decision 5.6).         | 2026-08-06 |
| S.11 | Failures swallowed silently → logged to a file the user can actually read (decisions 3.10, 5.7). | 2026-08-06 |
| S.12 | The cascade returning the first answer → the best answer (decision 7.1).                | 2026-08-06 |
| S.13 | Any instrumental ending the search → only an exactly matched one (decision 7.2).        | 2026-08-06 |
| S.14 | "No free source has broad word-level coverage" → two do; the phase 0 finding was a measurement error (decision 8.1). | 2026-08-06 |
| S.15 | Musixmatch richsync written off as a dead endpoint → it keys on track id, not names.    | 2026-08-06 |
| S.16 | amll-ttml-db as the word-level source → dropped; two better sources cover it.           | 2026-08-06 |
| S.17 | Cover Art Archive as a cover source → dropped; patchiest of three and the only one returning non-square scans. | 2026-08-07 |
| S.18 | Apple leading the cover sources → Discogs leads, on measurement: 5 of 5 tracks against 3 of 5, same resolution. | 2026-08-07 |
| S.19 | Translated lyrics planned → dropped by agreement, never started.                        | 2026-08-07 |
| S.20 | Decision 8.8's second clause — the fast tick applies only while a word is lit — no longer holds. The reactive border asks for 60 Hz unconditionally while anything is playing, so the overlay runs at the fast tick all day, which is the cost 8.8 set out to avoid. Whether the border is worth it is open. Its first clause, canvas items built per line and afterwards only recoloured, still stands. | 2026-08-19 |

---

## Document Changelog

| Date       | Summary                                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------------------------ |
| 2026-08-06 | Sources probed under real conditions; LRCLIB confirmed unmetered, word-level sources found narrow.                |
| 2026-08-06 | MVP overlay built and verified live against Spotify desktop.                                                     |
| 2026-08-06 | Repository initialized; flat MVP migrated to src-layout with a provider interface and offline tests.             |
| 2026-08-06 | GitHub remote created; workflow set to issue → branch → PR → merge.                                              |
| 2026-08-06 | Formal plan established. Delivery sequence agreed through step 8, including word-by-word and packaging.          |
| 2026-08-06 | Browsers measured (#4). Interpolation holds to 0.001 s of spread, so the companion extension is dropped.         |
| 2026-08-06 | Browser metadata handled (#6). YouTube and SoundCloud now resolve on the exact endpoint instead of missing.      |
| 2026-08-06 | Overlay text outlined (#8). Step 5 complete; only the bright-background eyeball is deferred.                     |
| 2026-08-06 | Licence added and probes renamed (#11). The declared licence now has a file behind it.                           |
| 2026-08-07 | Packaged, given a tray icon and optional autostart (#69, #70). Step 8 all but closed.                            |
| 2026-08-07 | Frosting traded for rounded corners after measuring that DWM ignores the clip region under an accent (#63).      |
| 2026-08-07 | Border made reactive from the endpoint meter, with no audio captured and no dependency added (#77, #80, #84).    |
| 2026-08-07 | Tempo lookup and tempo-from-loudness both measured and rejected as unreliable; onset rate used instead (#72, #83).|
| 2026-08-07 | Status corrected: steps 3 and 8 had been reporting stale state, and the roadmap listed shipped work as pending.  |
| 2026-08-06 | Lint pinned and failures logged (#12). The reader thread keeps its broad catch, but no longer hides why.         |
| 2026-08-06 | CI on every pull request (#13), verified by its own run. Step 4 complete; next delivery is step 6, providers.    |
| 2026-08-06 | Answers ranked by precision (#15). The cascade keeps the best result rather than the first one offered.          |
| 2026-08-06 | NetEase added (#16), and guessed instrumentals no longer end the search — a silent wrong answer caught live.     |
| 2026-08-06 | Step 6 complete, ordering config deferred to step 8. Next delivery is step 7, word-by-word.                      |
| 2026-08-19 | The light leaves the panel. A companion layered window carries the half that falls outside the clip region (decisions 9.5-9.9), and the term that used to delete it is gone. |
| 2026-08-07 | Covers reordered on measurement, and translated lyrics dropped rather than left as an open intention.            |
| 2026-08-06 | Word timings and a TTML parser (#19). The WORD tier becomes reachable after existing unused since step 6.        |
| 2026-08-06 | Community TTML source and precision ceilings (#21). Caught word timings never reaching the cache.                |
| 2026-08-06 | The current line renders word by word (#23), rebuilt per line and recoloured per frame.                          |
| 2026-08-06 | The cache can live in a synced folder (#25), so a second machine inherits it.                                    |
| 2026-08-06 | Musixmatch richsync added (#27). Step 7 complete; the visual treatment is deferred to its own work.              |
