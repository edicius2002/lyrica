"""Does the derived palette stay legible on every cover we have actually seen?

Runs the real pipeline — backdrop, colour extraction, derivation — over the
local cover cache and reports the two numbers the design is answerable for:

  contrast  the unsung line against the wash it sits on. WCAG's 3:1 floor for
            large text is the bar; below it the line is there but working.
  sweep dE  the sung word against the unsung tail, measured over a near-white
            desktop, which is where the additive clamp is at its worst. This is
            the one a contrast ratio cannot see: over a bright desktop both
            colours clamp toward white and the ratio reads 1.00 while they are
            plainly different on screen.

Run it after touching anything in palette.py, glass.py, songcolour.py or the
backdrop, and compare the summary line. Pass a directory to check covers other
than the cache.

    python research/viability/probe_palette.py [dir-of-covers]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lyrica import artwork, config, glass, songcolour
from lyrica import palette as pal_mod
from lyrica.chrome import Chrome, ChromeMode
from lyrica.glass import contrast, rgb_of

GLASS = Chrome(ChromeMode.PANEL, "#000000", glass.PANEL)
WINDOW = (760, 300)

# Large text at 30 px bold. Below this the line is legible but working for it.
MIN_CONTRAST = 3.0


def covers(where: Path | None) -> list[Path]:
    root = where or (config.cache_root() / "covers")
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.stat().st_size > 4096)


def main() -> int:
    where = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    files = covers(where)
    if not files:
        print("no covers to check — play something first, or pass a directory")
        return 1

    print(f"{'cover':12s} {'B95':>4s} {'wash':>14s} {'hue':>5s} {'str':>5s} "
          f"{'unsung':>8s} {'contrast':>9s} {'sweep dE':>9s}")
    worst_contrast, worst_de, failures, neutral = 99.0, 99.0, 0, 0

    for path in files:
        data = path.read_bytes()
        back = artwork.make_backdrop(data, *WINDOW)
        if back is None:
            print(f"{path.stem[:12]:12s}  (not an image)")
            continue
        song = songcolour.extract(data)
        pal = pal_mod.for_song(GLASS, song, back.colour)
        # As it lands, not as it is drawn. The desktop puts a pedestal under
        # both the text and its backdrop, which costs contrast, and the amount
        # depends on how bright the desktop is — so the worst one counts.
        law = GLASS.composition
        ratio = min(contrast(law.compose((d,) * 3, rgb_of(pal.unsung)),
                             law.compose((d,) * 3, back.colour))
                    for d in range(0, 256, 15))

        worst_contrast = min(worst_contrast, ratio)
        worst_de = min(worst_de, pal.sweep_de)
        neutral += song.neutral
        flag = ""
        if ratio < MIN_CONTRAST:
            failures += 1
            flag = "  <-- under 3:1"

        print(f"{path.stem[:12]:12s} {back.peak:4d} {back.colour!s:>14s} "
              f"{pal.hue:5.0f} {pal.strength:5.2f} {pal.unsung:>8s} "
              f"{ratio:7.2f}:1 {pal.sweep_de:9.1f}{flag}")

    print(f"\n{len(files)} covers, {neutral} of them with no colour worth using")
    print(f"worst contrast  {worst_contrast:.2f}:1   (floor {MIN_CONTRAST}:1)")
    print(f"worst sweep dE  {worst_de:.1f}     (target {pal_mod.SWEEP_DE})")
    if failures:
        print(f"\n{failures} cover(s) below the contrast floor")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
