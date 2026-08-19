"""What the border looks like against a backdrop it cannot hide on, and how far it reaches.

Three proposals for the same border are being compared, so they need one frame
rendered the same way: a 900x320 panel at a corner radius of 14, over five
vertical bands at grey 0, 64, 128, 192 and 255 with a line of body text crossing
them. Grey 255 is the hard one — a light with any wash left in it goes grey
against white, and grey 0 is where a bloom that should be invisible is not.

Composed in PIL rather than grabbed off the glass, for the same reason
`render_visual_baselines.beam` is: the border is images, so the frame can be
assembled from the exact pixels both windows would be handed, which is more
faithful than a capture and free of whatever else happened to be on screen. The
photographs of the running app are the other half of the evidence and live
beside these.

The composite is the only place the two halves are seen *together* off the
desktop, so it also carries the arithmetic the real screen does to them: the Tk
window is presented at 0.92 alpha over whatever is behind it and the companion
surface at 1.0, which is where the known one-pixel seam dip comes from. Skipping
that would make the seam look better here than it ever does in life.

    py research/viability/probe_backlit.py           # the three frames
    py research/viability/probe_backlit.py cross     # the cross-section, in levels
"""
import sys
from pathlib import Path

sys.path.insert(0, __file__.rsplit("research", 1)[0] + "src")

from PIL import Image, ImageDraw, ImageFont

from lyrica.beam import SHINE, Beam
from lyrica.meter import Character

WIDTH, HEIGHT = 900, 320
RADIUS = 14
BANDS = (0, 64, 128, 192, 255)

# What the panel itself is: the acrylic plate the overlay wears, and the alpha
# Tk presents its window at. Both are needed or the silhouette — which is now
# the thing defining the shape — is drawn against the wrong colour.
PANEL = (16, 19, 26)
PANEL_ALPHA = 0.92

TEXT = "the quick brown fox jumps over the lazy dog"


def palette():
    """The colour a real cover gives the border, not `palette.DEFAULT`.

    The default palette is grey, and a grey light on a white band is invisible
    for a reason that has nothing to do with the design being judged. What ships
    is always tinted by the album art, so that is what is drawn here — the warm
    amber a dark sleeve produces.
    """
    from lyrica import palette as pal_mod
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL as PLATE
    from lyrica.songcolour import SongColour

    return pal_mod.for_song(Chrome(ChromeMode.PANEL, "#10131a", PLATE),
                            SongColour(34.0, 0.78, 0.52, 34.0, False, (0, 0, 0)),
                            (34, 26, 16))


def backdrop(width: int, height: int) -> Image.Image:
    """Five vertical greys with a line of body text across them."""
    made = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(made)
    step = width / len(BANDS)
    for index, grey in enumerate(BANDS):
        draw.rectangle((round(index * step), 0, round((index + 1) * step), height),
                       fill=(grey, grey, grey))
    try:
        font = ImageFont.truetype("segoeui.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    # Mid grey, so it is legible over black and over white alike and cannot be
    # mistaken for part of the light.
    draw.text((24, height - 44), TEXT, font=font, fill=(128, 128, 128))
    return made


def _rounded(width: int, height: int, radius: int) -> Image.Image:
    """The panel's own footprint, as the region clip would cut it."""
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1),
                                           radius=radius, fill=255)
    return mask


def compose(character: Character) -> Image.Image:
    """One frame: backdrop, the outward half, the panel, the inward half.

    In that order, because it is the order the compositor works in. The
    companion surface is behind the overlay and the overlay is a translucent
    plate with the canvas half drawn on it, so light that falls outside the
    panel is composited against the desktop and light that falls on it is
    composited against the panel and *then* faded by the window's alpha.
    """
    import tkinter as tk

    import numpy as np

    root = tk.Tk()
    root.withdraw()
    canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT)
    surface = _Surface()
    ring = Beam(canvas, WIDTH, HEIGHT, RADIUS, 1.0, SHINE, glow=surface)
    ring.advance(0.0, character, palette())
    # Every strip, rather than the one a frame is allowed: this is a still, and
    # `halo.PER_CALL` exists to bound a frame's cost, not to describe the border.
    for _ in range(len(ring.light.strips) * 2):
        ring.light.paint(ring._tables)

    pad = ring.pad
    frame = backdrop(WIDTH + 2 * pad, HEIGHT + 2 * pad).convert("RGBA")

    # The outward half, straight onto the desktop. The surface is premultiplied
    # BGRA; PIL wants straight RGBA, so it is unmultiplied back here — the only
    # place in the process that ever needs to.
    spill = surface.frame()[:HEIGHT + 2 * pad, :WIDTH + 2 * pad].astype(np.float32)
    alpha = spill[..., 3:4]
    colour = np.where(alpha > 0, spill[..., 2::-1] * 255.0 / np.maximum(alpha, 1e-6),
                      0.0)
    out = np.concatenate([np.clip(colour, 0, 255), alpha], axis=2).astype(np.uint8)
    frame.alpha_composite(Image.fromarray(out, "RGBA"))

    # The panel: an acrylic plate with the inward half drawn on it, clipped to
    # the rounded rectangle, then presented at the window's own alpha.
    plate = Image.new("RGBA", (WIDTH, HEIGHT), (*PANEL, 255))
    for strip in ring.light.strips:
        if strip.box is not None:
            plate.alpha_composite(ring.light.image(strip, ring._tables),
                                  (strip.box[0], strip.box[1]))
    plate.putalpha(_rounded(WIDTH, HEIGHT, RADIUS).point(
        lambda value: round(value * PANEL_ALPHA)))
    frame.alpha_composite(plate, (pad, pad))

    ring.destroy()
    root.destroy()
    return frame.convert("RGB")


class _Surface:
    """The companion window, in memory: the five calls `halo.Spill` makes."""

    def __init__(self):
        import numpy as np

        self._np = np
        self.capacity = (0, 0)
        self._frame = None

    def reserve(self, width, height):
        if width <= self.capacity[0] and height <= self.capacity[1]:
            return False
        width, height = max(width, self.capacity[0]), max(height, self.capacity[1])
        self._frame = self._np.zeros((height, width, 4), self._np.uint8)
        self.capacity = (width, height)
        return True

    def frame(self):
        return self._frame

    def present(self, *_a):
        pass

    def move(self, *_a):
        pass

    def behind(self, *_a):
        pass

    def visible(self, *_a):
        pass

    def destroy(self):
        pass


def cross_section() -> None:
    """The profile in levels of 255, across the middle of the left edge.

    Read off a real composite rather than off the curve, so it includes the
    dither, the eight-bit write and the window alpha — which is to say, it is
    what an eyedropper on the screen would report.
    """
    import numpy as np

    for name, character in (("rest", Character(level=0.0, dynamics=0.0, rate=0.0)),
                            ("lit", Character(level=1.0, dynamics=1.0, rate=0.8))):
        frame = compose(character)
        pad = (frame.width - WIDTH) // 2
        row = np.asarray(frame.convert("L"), dtype=int)[frame.height // 2]
        # Left to right through the edge: `pad` is the panel's own boundary.
        here = row[max(0, pad - 34):pad + 12]
        print(f"{name}: pad {pad}")
        print("   x from the silhouette: "
              + " ".join(f"{value - pad + max(0, pad - 34):+d}"[:3]
                         for value in range(len(here))))
        print("   level:                 "
              + " ".join(f"{value:3d}" for value in here))


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "shots"
    out.mkdir(parents=True, exist_ok=True)
    frames = (("compare-backlit-rest", Character(level=0.0, dynamics=0.0, rate=0.0)),
              ("compare-backlit-lit", Character(level=1.0, dynamics=1.0, rate=0.8)),
              ("compare-backlit", Character(level=0.55, dynamics=0.5, rate=0.4)))
    for name, character in frames:
        compose(character).save(out / f"{name}.png")
        print(f"wrote {out / name}.png")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cross":
        cross_section()
    else:
        main()
