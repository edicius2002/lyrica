# Source Viability Report

**Date:** 2026-08-06 · **Environment:** Windows 10, Python 3.11.9
Reproducible probes live in `research/viability/`. Every figure below was measured, not taken
from documentation.

## 1. Measured conditions per lyrics source

### LRCLIB — the backbone (free, unlimited, confirmed)

- **No API key, no registration.** Burst of 30 consecutive requests: 30/30 HTTP 200, **no rate
  limiting observed**.
- Latency: median **~680 ms**, max ~810 ms. Acceptable, and paid only once per track once the
  local cache is in place.
- Coverage measured with exact `/get` plus a `/search` fallback:
  - Spotify / YT Music profile (clean metadata): **10/10 with synced lyrics**, 2 of which needed
    the `/search` fallback.
  - YouTube profile (noisy titles such as "(Official Video)"): 3/3 synced, but 2 of 3 **only**
    through `/search` after regex title cleaning. Title normalization is mandatory, not optional.
  - SoundCloud profile: well-known remixes resolve; underground and "Free DL" uploads exist in no
    lyrics database at all — an unrecoverable miss at the metadata level.
- Real constraint: `/get` requires the duration to match within ±2 s. Without a trustworthy
  duration (YouTube), the only path is `/search` with client-side scoring on artist, title,
  duration and sync availability.

### Musixmatch, unofficial desktop API — works, but fragile

- The free user token is still obtainable today without registration (`token.get`).
- Line-level synced lyrics work (3/3 tracks tested, Spanish-language material included).
- `has_richsync=True` on all three tracks, **but** `track.richsync.get` returned **404** when
  queried by name. Word-level sync therefore needs extra engineering (numeric track_id plus the
  exact parameters the official app sends) and may require an account or be geo-restricted.
- Real constraint: undocumented endpoint, terms of service in a grey area, liable to break or to
  get an IP banned at any time. Usable as an *optional enhancement*, never as a foundation.

### amll-ttml-db, word-by-word TTML on GitHub — free, minimal coverage

- Direct access through `raw.githubusercontent` and jsDelivr, no practical limits.
- **However:** only ~2,364 entries indexed by Spotify ID (~3,401 NetEase, ~2,828 QQ). Of four
  Western tracks probed, one resolved.
- Real constraint: strong bias toward Asian pop. Useful as a "premium when present" layer, not as
  a primary source. Lookup is by Spotify track ID, which the Windows media session does **not**
  expose — resolving that ID needs a separate API.

### `syncedlyrics` package — useful as a fallback aggregator

- Lrclib OK, NetEase OK (synced), Genius OK (plain text only), Megalobiz down or missing,
  Musixmatch intermittent.
- `enhanced=True` (word level) returned no per-word timestamps in the probe.
- Real constraint: NetEase is a solid second free synced provider; Genius is plain text only.

### Conclusion on word-by-word sync

No source today is simultaneously free, unlimited and broadly covering for karaoke-style word
sync. The correct architecture is a degrading cascade:
`amll-ttml-db (when present) → Musixmatch richsync (if the endpoint is solved) → LRCLIB line level
(≈99% of cases) → plain text`.

## 2. Track detection through the Windows media session — verified

- `winsdk` reads the global media session: app, artist, title, album, duration, position and
  playback state.
- Verified live against **Spotify.exe**: complete metadata and accurate position.
- Position arrives as a snapshot (`position` plus `last_updated_time`), so it must be
  **interpolated** (`pos + (now − last_updated)` while playing). Implemented in the overlay.
- Browsers publish metadata through the Media Session API; artist is sometimes empty with
  "Artist - Title" packed into the title, which the overlay splits and cleans.

## 3. Reference clones in `repos/` — licences and verdict

| Repository | Licence | Verdict |
| --- | --- | --- |
| **Lyricify-Lyrics-Helper** | Apache 2.0 | Best multi-format parsing library (LRC/TTML/YRC/richsync), C#. Ideal base for a .NET port |
| **better-lyrics** | GPLv3 | UX reference and provider-cascade reference (extension, YT Music only) |
| **YouLyPlus** | MIT | Reference for word-by-word rendering and Musixmatch handling in JS |
| **syncedlyrics** | MIT | Ready-to-use multi-provider fallback from Python |
| **lyrictified** | MPL 2.0 | Minimal Python overlay, comparable in scope to ours |
| **FrontLine-Lyrics-Desktop** | GPLv3 | Interesting for audio fingerprinting, the only angle on SoundCloud underground |
| **Lyric-Immersion-and-Karaoke** | **Proprietary** | Not forkable; feature inspiration only |

No code from any of these was copied into this repository.

## 4. Which platform works best (measured)

1. **Spotify app / YouTube Music** — clean metadata plus near-total synced coverage. The best
   experience by a wide margin.
2. **YouTube proper** — fine once titles are cleaned; non-musical videos will miss, which is
   expected.
3. **SoundCloud** — mainstream resolves; remixes and underground uploads have no metadata-based
   solution. The only remaining angle would be audio fingerprinting.

## 5. Local or cloud

Local, without qualification. Everything runs on the machine and the APIs are queried directly:
no servers, no cost. Cloud would only earn its place if the lyrics had to reach a phone, which is
outside the current scope.
