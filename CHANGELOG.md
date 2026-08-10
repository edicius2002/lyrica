# Changelog

## 0.2.2 — 2026-08-10

- Increase the lyric row spacing and panel height so backing vocals, bloom and
  word growth occupy their own complete visual band.
- Attach left and right ad-libs to the corresponding lower outer corner of the
  lead line, close to its final letters rather than to a fixed panel lane.
- Keep every transition endpoint inside the canvas using the maximum glyph,
  halo and growth bounds, and use those same bounds for edge fading.
- Narrow the wrap box enough to preserve full horizontal effects at every
  supported display scale.

## 0.2.1 — 2026-08-10

- Let adjacent words breathe apart while a struck word expands, preserving the
  resting gap and returning to the exact original layout as it settles.
- Keep the growth centre attached to lyric lines after they move into a
  secondary artist's lane.
- Move left and right ad-libs into gentler inset lanes with eased arrival and
  departure instead of aligning short phrases against the window edge.
- Reserve optical edge margins for secondary voices, including the space their
  word-growth gesture can consume.

## 0.2.0 — 2026-08-10

- Give every struck word an independent 14% grouped pulse with a fixed motion
  clock, while its two-stage bloom continues to follow the sung duration.
- Keep word growth available when bloom or washed composition is unavailable.
- Stage the first two TTML vocalists in responsive opposing lanes and place
  simultaneous backing vocals in the lane opposite the active lead.
- Continue alternate metadata readings when they can still recover staged TTML.
- Give the reactive border a crisp core, wide halo, perceptual contrast floor,
  music-driven weight, adjustable intensity and the new `aurora` style.
- Add committed synthetic visual baselines and machine-checked visual contracts
  for word strikes, duet lanes, backing vocals and quiet/loud beam states.

## 0.1.0

- Initial personal release of the synced desktop lyrics overlay.
