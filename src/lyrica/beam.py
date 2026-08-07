"""A light that travels the panel's edge, brightened by what is playing.

Segments laid once around the rounded rectangle and then only recoloured, for
the same reason the lyric sweep keeps its glyphs: rebuilding canvas items every
frame is what turns a smooth thing into a stuttering one.

The head advances at a fixed rate and the *brightness* is what the music moves.
That split is deliberate. Tying the speed to the beat needs a beat, and the
endpoint meter this is driven by has no spectrum to find one in — it reports
loudness and nothing else. A beam that always moves and breathes with the music
says everything a loudness signal honestly can; one that tried to lurch on each
kick would be guessing.
"""
import math

from lyrica.glass import hex_of, rgb_of

# Spacing is deliberately uneven, and evenly spacing it is what looked wrong
# first. The default panel's perimeter is about 2900 px while a corner arc is
# 28, so at any segment count cheap enough to recolour at 30 Hz a corner gets
# less than one segment and reads as a chamfer rather than a curve. The corners
# are therefore given a fixed budget of their own and the straights share what
# is left by length.
CORNER_POINTS = 7
STRAIGHT_SPACING = 34.0

# How long the head takes to go once round. Slow enough not to compete with the
# words being read, quick enough that the movement is the thing you notice.
PERIOD_S = 6.0

# How much of the ring trails behind the head, as a fraction of the whole. Short
# on purpose: a comet with a tail a quarter of the way round is a lit border
# with a bright patch, which is not the same thing to look at.
TAIL = 0.14

# What the level does to the beam. The floor is high on purpose: the beam has to
# be plainly there with no audio at all, because there often is none to read.
# Measured on this machine — Spotify was controlling playback over Connect, so
# the track advanced while every render endpoint on the box read silence, and a
# beam that needed sound to be visible was invisible. The level flares it; it
# does not switch it on.
FLOOR = 0.62
GAIN = 0.38

# Where the tail stops being the song's colour and starts becoming the head.
# Below this the beam fades out to nothing; above it, up to white.
COLOUR_STOP = 0.55
GRADIENT_STEPS = 48


def _rounded_path(width: int, height: int, radius: int,
                  inset: float) -> list[tuple[float, float]]:
    """Points around a rounded rectangle, clockwise from the top left corner.

    Corners get a fixed number of points and straights get one every
    `STRAIGHT_SPACING`, so a curve is always drawn as a curve however long the
    edges beside it are.
    """
    left, top = inset, inset
    right, bottom = width - inset, height - inset
    r = max(0.0, min(radius, (right - left) / 2, (bottom - top) / 2))

    def arc(cx, cy, start):
        return [(cx + r * math.cos(start + (math.pi / 2) * k / CORNER_POINTS),
                 cy + r * math.sin(start + (math.pi / 2) * k / CORNER_POINTS))
                for k in range(CORNER_POINTS)]

    def run(x0, y0, x1, y1):
        length = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(length / STRAIGHT_SPACING))
        return [(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)
                for k in range(steps)]

    return (run(left + r, top, right - r, top)
            + arc(right - r, top + r, -math.pi / 2)
            + run(right, top + r, right, bottom - r)
            + arc(right - r, bottom - r, 0.0)
            + run(right - r, bottom, left + r, bottom)
            + arc(left + r, bottom - r, math.pi / 2)
            + run(left, bottom - r, left, top + r)
            + arc(left + r, top + r, math.pi))


def _gradient(palette, steps: int = GRADIENT_STEPS) -> list[str]:
    """From invisible, through the song's colour, to the head.

    The first entry is the *backdrop* rather than the palette's dimmest text
    colour, and that distinction is the whole effect. Lit from the lyric ramp,
    the unlit part of the ring was drawn at the unsung level — a pale border all
    the way round, with the head barely brighter than it. A travelling light
    needs somewhere dark to travel through.
    """
    dark = tuple(palette.backdrop)
    mid = rgb_of(palette.unsung)
    head = rgb_of(palette.sung)
    out = []
    for i in range(steps):
        t = i / (steps - 1)
        if t < COLOUR_STOP:
            k, src, dst = t / COLOUR_STOP, dark, mid
        else:
            k, src, dst = (t - COLOUR_STOP) / (1 - COLOUR_STOP), mid, head
        out.append(hex_of(tuple(s + (d - s) * k
                                for s, d in zip(src, dst, strict=True))))
    return out


class Beam:
    """The ring of segments, and the state to colour them."""

    def __init__(self, canvas, width: int, height: int, radius: int,
                 scale: float = 1.0):
        self.canvas = canvas
        self._phase = 0.0
        self._items: list[int] = []
        self._shades: list[str] = []
        self._thickness = max(1.0, 2.0 * scale)
        self._gradient: list[str] = []
        self._palette = None
        self.reshape(width, height, radius)

    def reshape(self, width: int, height: int, radius: int) -> None:
        """Lay the ring out again, for a window that changed size."""
        self.destroy()
        points = _rounded_path(width, height, radius, self._thickness)
        for i, start in enumerate(points):
            end = points[(i + 1) % len(points)]
            item = self.canvas.create_line(*start, *end, width=self._thickness,
                                           fill="#000000", capstyle="round")
            self._items.append(item)
            self._shades.append("#000000")

    def destroy(self) -> None:
        for item in self._items:
            self.canvas.delete(item)
        self._items.clear()
        self._shades.clear()

    def advance(self, dt: float, level: float, palette) -> None:
        """Move the head and recolour the ring.

        The gradient is derived from the palette, so the beam wears the cover's
        colour, and rebuilt only when the palette itself changes.
        """
        if not self._items:
            return
        if palette is not self._palette:
            self._palette = palette
            self._gradient = _gradient(palette)
            self._shades = [""] * len(self._items)   # force a full repaint

        self._phase = (self._phase + dt / PERIOD_S) % 1.0
        strength = FLOOR + GAIN * max(0.0, min(1.0, level))
        top = len(self._gradient) - 1

        for i, item in enumerate(self._items):
            # Distance behind the head, once round the ring.
            behind = (self._phase - i / len(self._items)) % 1.0
            glow = 0.0 if behind > TAIL else (1.0 - behind / TAIL) ** 2
            shade = self._gradient[int(top * glow * strength)]
            if shade != self._shades[i]:
                self.canvas.itemconfigure(item, fill=shade)
                self._shades[i] = shade
