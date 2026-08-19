# Changelog

## Unreleased

- Let the border's light leave the panel. The overlay is clipped to a one-bit
  rounded rectangle by `SetWindowRgn`, so the glow stopped dead at the window's
  edge — and the falloff carried a term whose only purpose was to extinguish its
  outward half before it arrived there, because a hard cut across a glow reads
  worse than no glow at all. Ten attempts at the profile, the blur, the colour
  ramp and the supersampling were all working on the wrong thing: half the light
  was being deleted to hide a boundary. A companion `WS_EX_LAYERED` window now
  carries that half with real per-pixel alpha and no boundary to stop at, and
  the outward field reaches 34 design pixels onto the desktop instead of being
  taken to nothing over 3.4. It takes no click and no focus, it sits under the
  panel in the z-order, and it is punched through wherever the panel's own
  footprint is, so it can never cover a word.
- Cache the two fields together and bound the cache at 48 MiB rather than 24.
  An entry carries twice the band it used to, and at the old bound a collapse
  kept fourteen of its twenty-one sizes — so the unfold, which asks for exactly
  the same twenty-one, paid for a third of them again.

## 0.2.7 — 2026-08-18

- Place the window through the platform rather than through `wm geometry`, which
  cannot express a negative coordinate. Its offsets are signed, but a leading `-`
  means "this far from the *opposite* edge of the primary screen" — so a panel
  whose top belonged at -20 was given `1800x640+2262-20` and put at
  1200 - 20 - 640 = 540 instead. Any monitor above or left of the primary has
  negative coordinates across part of it, and measured on a desktop whose second
  screen starts at y=-460, one press of `Ctrl`+`Alt`+`+` threw the panel 760 px
  down and off every screen. Startup restored a saved place from up there to the
  wrong row for the same reason.
- Grow and shrink from where the panel actually is, not from where it was last
  dragged to. Those differ whenever something moves it without a hand — a compact
  card expanding into a lyric panel takes its own anchor, and either can be
  pulled off its top by a monitor edge — and growing from the stale one moved the
  panel out from under the eye watching it. The trade is that a clamp is no
  longer temporary: grow until an edge stops it, shrink again, and the panel
  stays where the edge left it rather than springing back.
- Only flush Tk's idle work when finding a window handle actually needs it, which
  is before the window has been mapped. Placing through the platform would
  otherwise have put a synchronous flush on every animated resize frame.

## 0.2.6 — 2026-08-18

- Bound the blurred-glyph cache, which was the whole of the "Fail to allocate
  bitmap" crash. Its keys carry the palette colour and the palette follows the
  cover, so every song added about 800 images that no later song would ever ask
  for again. Each one is a GDI bitmap — measured at one handle and 29.6 KiB — and
  a process is given 10,000 handles, so the quota was reached after roughly
  fifteen songs and Tk ended the process from `Tk_GetPixmap`. Reproduced at song
  twelve; 2,400 images are now kept by last use, and forty songs hold flat at
  2,436 handles.
- Refuse to start a second overlay. Each one drew its own always-on-top panel
  over the same lyrics and added its own notification-area icon.
- Remove the notification-area icon whichever way the overlay ends. The teardown
  sat past `mainloop` rather than in a `finally`, and `stop` posted its quit to a
  daemon thread without waiting — so an ending that was not a clean quit left an
  icon Windows keeps drawing until something makes it ask the owning window
  whether it is still there, which is why the leftovers vanished as soon as the
  notification area was opened.

## 0.2.5 — 2026-08-10

- Give every letter of an expanding word the same size in the same frame. The
  per-frame image budget was spent a letter at a time, so a word wore up to four
  scales at once and deformed differently each frame instead of swelling.
- Carry each halo with the letter it belongs to, which travels up to twelve
  pixels as a word grows about its own centre.
- Budget the halo separately from the growth, so a cold cache no longer holds the
  light still for whole frames and then jumps it.
- Restore a line's grown letters when it becomes visible again, instead of
  leaving hidden text behind a hidden image.

## 0.2.4 — 2026-08-10

- Hide lyric lines once their edge fade reaches zero instead of leaving opaque
  background-coloured glyphs on the canvas.
- Keep the song card above every lyric and the active line above inactive and
  backing lines, preventing dark text from covering illuminated content.

## 0.2.3 — 2026-08-10

- Blur only the lyric halo's alpha mask and apply its colour afterwards, so
  faint illuminated edges remain white instead of revealing dark glyphs.

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
