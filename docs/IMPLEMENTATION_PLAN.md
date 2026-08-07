# Implementation Plan and Decision Log

> **Status:** Browsers measured and handled. The overlay resolves tracks from Spotify, YouTube,
> YouTube Music and SoundCloud.
> **Last updated:** 2026-08-06
> **Review status:** Phase 5 merged across [#4](https://github.com/edicius2002/lyrica/pull/4),
> [#6](https://github.com/edicius2002/lyrica/pull/6) and [#8](https://github.com/edicius2002/lyrica/pull/8).
> **Phase closure:** Steps 0–3 and 5 complete. One check deferred: the overlay outline has not
> been eyeballed over a genuinely bright background.
> **Next delivery:** Step 4, hygiene and CI. One issue per slice, written before its work.

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

**Status:** In progress on `docs/implementation-plan`.

- [x] `docs/IMPLEMENTATION_PLAN.md` — this file
- [x] Translate the viability report to English and rename it `research/VIABILITY.md`
- [ ] Open the PR against `main`

**Out of scope:** any change under `src/` or `tests/`.

### 4 — Hygiene and CI

**Status:** Not started.

- [ ] `LICENSE` file matching the `pyproject.toml` declaration (decision 5.5)
- [ ] Pin the `ruff` ruleset explicitly and fix or justify each finding
- [ ] Replace blind `except Exception` blocks with logged, narrowed handling
- [ ] Rename `research/viability/test_*.py` to `probe_*.py` — they are probes, not tests, and the
      name invites accidental collection
- [ ] GitHub Actions on a Windows runner: lint plus tests
- [ ] Issue and pull request templates

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

**Status:** Not started.

- [ ] NetEase as a second synced provider behind the same interface
- [ ] Configurable provider order
- [ ] Per-provider diagnostics: which source answered, and how long it took
- [ ] Cache entries record their source so a better provider can supersede a weaker hit

### 7 — Word-by-word

**Status:** Not started. Scope constrained by measurement — see decisions 4.4 and 4.5.

- [ ] Extend the `Lyrics` model to carry optional per-word timings
- [ ] amll-ttml-db lookup, including resolving a Spotify track ID the media session does not expose
- [ ] Musixmatch richsync only if the endpoint can be solved reliably
- [ ] Render word highlighting, degrading to line highlighting when timings are absent

### 8 — Packaging

**Status:** Not started.

- [ ] PyInstaller executable
- [ ] System tray icon with show, hide and quit
- [ ] Optional start with Windows
- [ ] Persist window position, sync offset and provider order

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

### 6. Browsers

Measurements behind these are in [`research/BROWSER_SESSIONS.md`](../research/BROWSER_SESSIONS.md).

| ID  | Decision                                                                        | Rationale                                                                                                                                                                                                                                                        |
| --- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 6.1 | No companion browser extension. Interpolation stands alone.                      | Scored against the page's own clock, the error was flat: 0.001 s of spread across a 27 s run, before and after a seek. Chrome states its position once and re-anchors on discontinuities, which is exactly when interpolation needs one. There is nothing to fix. |
| 6.2 | The artist field is never trusted on its own; readings are ranked.                | Each site means something different by it. YouTube states it correctly *and* repeats it in the title; SoundCloud puts the uploader's handle there while the real artist sits in the title. Nothing in the payload says which case applies, so ranking beats guessing. |
| 6.3 | A leading repetition of the artist is stripped, but only across a separator.      | "Dua Lipa - Levitating" with artist "Dua Lipa" is a repetition; "Madonna Of The Wasps" is a song. Requiring the separator is what tells them apart.                                                                                                              |
| 6.4 | Duration is retried as absent when an exact lookup misses.                        | LRCLIB matches within ±2 s, and a re-upload can be padded — one copy of a 3-minute song reported 12 minutes. A wrong duration turns a findable track into a miss.                                                                                                |
| 6.5 | Browser metadata tests are only written from captured payloads.                   | The previous test asserted that Chrome leaves `artist` empty. Chrome never does, so it passed while describing a payload that does not exist, and the bug it was meant to guard shipped anyway.                                                                  |
| 6.6 | Text is outlined rather than shadowed, with symmetric offsets.                     | A shadow is directional and reads as depth; an outline reads as separation, which is what arbitrary backgrounds need. Ringing the centre rather than filling a square is 8 draws per line instead of 24, with the difference hidden under the fill anyway.        |

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
