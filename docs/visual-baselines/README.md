# Visual baselines

These synthetic frames make visual changes reviewable without exposing a real
desktop, track or listening history. They cover the moments most likely to
regress while ordinary unit tests remain green:

- `word-strike.png` — grouped growth at its attack peak and the sung/unsung ramp;
- `duet-lanes.png` — the first two TTML agents in opposing responsive lanes;
- `backing-vocal.png` — an independently timed supporting line in the open lane;
- `beam-quiet.png` and `beam-loud.png` — the border's two energy extremes.

The two beam frames are rendered against a *cover's* palette, not the neutral
`palette.DEFAULT`. They used the default for as long as they existed, and it
hid the fault they were most needed for: the border climbed to white with the
level, and against a grey palette a white border is what a correct one looks
like too. Any change to the border's colour has to be reviewed on a palette
that has colour in it.

The committed images are human review artifacts. Their stable measurements are
stored in `tests/visual_contracts.json` and enforced in CI, avoiding
font-antialiasing differences between Windows and macOS runners.

Regenerate the frames on Windows after an intentional visual change:

```powershell
py research/viability/render_visual_baselines.py
```

Inspect all five images before committing them, update the JSON contract only
when the changed measurement is deliberate, then run `py -m pytest -q`.

These are composed in PIL, which is exact and proves nothing about the
compositor. To photograph the real overlay — real acrylic plate, real clip
region, real layered companion, and the corner at six times life size — use
`research/viability/shoot_backlit.py`, which writes to the gitignored
`research/shots/`. Quit any running Lyrica first.
