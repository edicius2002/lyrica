# Browser Media Sessions — Measured Behaviour

**Date:** 2026-08-06 · **Environment:** Windows 10, Chrome, Python 3.11.9
Probes: `research/viability/probe_browser_session.py`, `research/viability/probe_browser_drift.py`

Phase 5, slice 1. Everything the overlay assumed about browsers had been written from
expectation rather than observation. This is what the browsers actually do.

## 1. Interpolation holds — no companion extension is needed

This was the open architectural question: whether the overlay could track a browser's playback
position without a browser extension feeding it exact timings.

**Chrome never restates its position periodically.** Across a 40-second probe it published its
timeline exactly once and then went silent, so there is no second reading to score a prediction
against. The only remaining ground truth is the media element itself, which is what
`probe_browser_drift.py` compares against: samples are taken in the page carrying the wall-clock
instant they were read, and the overlay's interpolation is then evaluated at that same instant,
so the latency of moving data out of the browser cannot distort the result.

| Scenario                       | Median error | Max error | Spread over the run |
| ------------------------------ | ------------ | --------- | ------------------- |
| YouTube, 27 s continuous play  | +0.095 s     | 0.096 s   | **0.001 s**         |
| YouTube, after a seek to 30 s  | +0.079 s     | 0.080 s   | **0.001 s**         |

The spread is the number that matters. A constant offset is already adjustable with the `+`/`-`
keys; a growing one would be unfixable without a new source of truth. There is no growth: the
error is flat to a millisecond across the whole run.

**Chrome does restate its position on discontinuities.** After a seek the anchor moved to
30.009 s, and the prediction stayed accurate. So the browser publishes a fresh anchor exactly
when one is needed — at start and at every jump — and stays quiet while playback is simply
running, which is when interpolation from the last anchor is already correct.

Spotify behaves differently: it restates its timeline roughly every 4.5 s regardless. Both
behaviours are served by the same interpolation.

**Consequence:** the companion browser extension considered in the plan is unnecessary. Removed
from scope.

## 2. Metadata quality differs sharply by site

| Site          | `artist`     | `title`                                                          | Verdict |
| ------------- | ------------ | ------------------------------------------------------------------ | ------- |
| YouTube Music | `NewJeans`   | `Supernatural`                                                     | Clean — equal to Spotify |
| YouTube       | `Dua Lipa`   | `Dua Lipa - Levitating Featuring DaBaby (Official Music Video)`   | **Artist duplicated inside the title** |
| SoundCloud    | —            | —                                                                  | Not captured, see section 4 |

YouTube Music publishes a proper artist and a clean track title, so it needs no special handling
at all.

YouTube proper is the problem, and not in the way the code expected. The `artist` field **is**
populated — it carries the channel's artist name — while the title *also* begins with that same
artist name. Both are true at once.

## 3. The normalization rule was wrong, and the test agreed with it

`Snapshot.norm_artist_title()` splits `"Artist - Title"` only when `artist` is empty:

```python
if not artist and self.is_browser:
    artist, title = split_browser_title(title)
```

Against the real payload that branch never runs, so the lookup becomes:

```
artist = 'Dua Lipa'
title  = 'Dua Lipa - Levitating Featuring DaBaby'
```

The artist is asked for twice. LRCLIB's exact `/get` misses, and only the fuzzy `/search`
fallback rescues the track — confirmed live: the cache entry for that play was written with
`source=lrclib/search`. It works, but it pays a wasted round trip and depends on the scoring
being generous.

The unit test `test_snapshot_normalizes_browser_metadata` asserts this exact behaviour with
`artist=""`, so it passes while encoding a payload Chrome does not send. It is a test written
from imagination, and it is why this was not caught earlier. Tests for browser metadata must be
built from captured payloads from now on.

## 4. SoundCloud remains unmeasured

Playback could not be started in this session: after clicking play, the page had no `<audio>` or
`<video>` element at all and `navigator.mediaSession.playbackState` stayed `none`. The account
was signed out, which is the likeliest cause. Until a real session is captured, nothing about
SoundCloud's payload shape should be assumed — including that it resembles YouTube's.

## 5. What this changes

| Finding                                                        | Consequence                                                        |
| -------------------------------------------------------------- | -------------------------------------------------------------------- |
| Interpolation is accurate to a millisecond of spread            | No companion extension. The architecture stands as built.           |
| Chrome anchors on discontinuities, not on a timer               | Current design is correct; no polling change needed.                |
| YouTube duplicates the artist into the title                    | Normalization must strip a leading artist prefix from the title.    |
| YouTube Music is clean                                          | No per-site special casing needed there.                            |
| The existing browser test encodes a payload that never occurs   | Rewrite it against captured payloads.                               |
| SoundCloud unverified                                           | Stays an open item; do not assume it behaves like YouTube.          |
