"""The panel's border as light, evaluated rather than blurred.

The border used to be two Tk strokes: a five-pixel core at full colour and a
fifteen-pixel halo at a flat mix of 42 % toward the backdrop. Both are solid and
both have ends, so the cross-section of the border was a step function with
exactly one step in it — bright, then a plateau, then nothing. Real light falls
off continuously, and no choice of the two widths or of the 42 % changes the
shape of that graph. It is a property of `create_line`, so the first fix was to
stop using one and draw the ring into a mask, blur it, and hand Tk an image.

That removed the step and brought a subtler defect of its own, which is what
this version exists to remove. **PIL blurs in eight bits and nowhere else.**
`GaussianBlur` on an `"L"` mask is a byte in, a byte out, so the falloff was
quantised before it was ever seen; the field was then built at a third of the
resolution and resampled up, so each of those bytes was smeared over three
pixels. Measured on the left edge of the default panel, a sixty-pixel section of
the finished alpha held twenty-six to twenty-nine distinct values where a smooth
ramp holds sixty — plateaus two and three pixels wide, which is contouring, and
resolution does not fix it because the loss happened in the byte and not in the
sampling. Bicubic upsampling and a full-resolution rebuild once the panel
settles were both tried; both leave the plateaus, because both are asked to
recover information the eight-bit blur had already thrown away.

So nothing is blurred here any more. A rounded rectangle has a closed-form
signed distance:

    q = (|x| - a + r, |y| - b + r)
    d = |max(q, 0)| + min(max(q.x, q.y), 0) - r

and with `d` in hand the light is a *function of `d`*, evaluated per pixel at
native resolution in floating point. There is no mask, no kernel, no resample
and no intermediate byte: the first and only quantisation is the one the screen
insists on, at the moment the alpha is written. It is what a shader does, done
on the CPU, and `numpy` makes it a dozen vectorised passes over the edge band.

Two things the closed form has to be *told*, because a blur used to imply them:

    * The falloff is asymmetric. Inward there is panel to spread across, so the
      light carries `reach`; outward there is desktop, so it carries `spill`,
      and `spill` is the longer of the two because that is where light with
      nothing in its way goes.

      This used to say the opposite, and that was the whole fault. The overlay
      is clipped to a rounded rectangle by `SetWindowRgn`, which is one bit per
      pixel, so the light *stopped dead* at the window's edge; the shape
      therefore carried an `edge` term whose only purpose was to extinguish the
      outward half before it arrived there, because a hard cut across a glow
      reads worse than no glow at all. Ten iterations were spent on the
      profile, the blur, the colour ramp and the supersampling, and none of
      them was the fault: the light was being killed to hide a boundary.
      `chrome/layered.py` removes the boundary instead, and this module now
      builds *both* halves — the inward one for the canvas, the outward one for
      the companion surface that has no edge to stop at.
    * Where a pixel is *round* the ring, which is what carries the rotating
      colour. That is closed-form too: the arc length of the nearest point on
      the path. Better than the old one, in fact — the old field was drawn as
      thick line segments and needed a disc at every joint to fill the wedges
      two rectangles leave on the outside of a turn, and it counted position by
      *point index*, so the head sped up over the densely sampled corners. Arc
      length has no wedges and no joints, and the head travels at one speed.

Everything above is static: it depends on the geometry and on nothing else.
What the music and the palette do is a lookup table from "where round the ring"
to (colour, opacity), so painting a frame is four gathers and a multiply, and
rotating the gradient is a rotation *of the table*, which costs nothing at all.

The ring is four images round the edge rather than one covering the panel. That
is not a memory economy first — it is a damage one. Replacing a picture that
covers the whole panel makes Tk redraw everything under it, which is every lyric
on screen; four edge strips damage only the edge, where nothing else is drawn.
Measured on the default panel: 12.5 ms a swap against 7.5 ms for all four strips
and 2.1 ms for one.
"""
import math
from collections import OrderedDict
from itertools import accumulate
from typing import NamedTuple


class Shape(NamedTuple):
    """How wide the light is and how it falls off, in physical pixels.

    Asymmetric, and that is the point of describing it here rather than as one
    radius. Inward there is panel to spread across and words not to drown, so
    the field carries `reach`. Outward there is desktop, so it carries `spill`
    — which is free to be the larger of the two now that the window's edge is
    no longer where the light dies.
    """
    inset: float        # how far the ridge sits inside the panel's own edge
    core: float         # how wide the brightest part of the light is
    ridge: float        # how far the near falloff carries
    reach: float        # how far the wide field carries inward
    spill: float        # and how far it carries outward, onto the desktop

# Entries in the table that turns "how far round the ring" into a colour. One
# byte's worth, because that is what the `where` field can hold and what a
# per-pixel gather reads.
LUT_SIZE = 256

# Byte 0 of `where` means "no light reaches this pixel", so it is reserved and
# never handed to a position on the ring. Anything the light does not touch is
# therefore transparent whatever the table says, and — more usefully — a table
# that changed only where the ring is dark cannot force a repaint.
OFF_RING = 0

# How the light falls off across the border. The narrow lobe is the ridge that
# gives the panel an edge to end at; the wide one is the weak field that lets it
# survive a textured cover wash. Screened together rather than added, so the
# ridge keeps its opacity where the two overlap.
OUTER_KEEP = 0.55

# How many standard deviations of the wide lobe fit inside `reach` and inside
# `spill`. Three, so the field has faded to about a hundredth of its peak by the
# distance the caller asked for.
SIGMAS = 3.0

# Below this the light is not merely dim, it is off. It is what makes the tail
# *end* rather than trail a level or two forever, and what keeps the `where`
# field honest about which pixels have no ring near them.
#
# It is also what sizes the companion surface. A gaussian is still worth
# something at two and three sigmas, so "as far as `spill` says" is not far
# enough — `pad_of` asks the profile itself where it reaches nothing, which is
# this threshold, and the bitmap is cut there rather than at a number somebody
# chose. Cut short of it and the spill acquires a straight edge in mid-air,
# which is the same defect as the window's own boundary wearing a different
# hat.
DIM = 0.4 / 255.0

# How finely the cross-section is sampled before it is interpolated per pixel.
# The profile is evaluated once per shape into a one-dimensional table and read
# from it with linear interpolation, because `erf` is not vectorised: 0.02 px
# leaves an interpolation error under a hundredth of a level, which is four
# orders of magnitude below the thing being measured.
SAMPLE = 0.02

# How much further than the light carries a strip reaches, in pixels. A strip
# boundary is a straight line and a straight line across a glow is exactly the
# artefact this module exists to remove, so the far tail of the wide field has
# to be inside a strip rather than cut off by one.
BAND_SLACK = 4

# How many strips may be repainted in one call. This is the cap that turns "as
# often as the music changes" into a bounded per-frame cost, and one is enough:
# the ring closes on a new colour within four frames, and four frames is 67 ms
# against a gradient that takes eleven seconds to go round.
PER_CALL = 1

# Fields are cached because a collapse animation asks for the same twenty-one
# sizes on the way back out that it asked for on the way in. Bounded by bytes
# rather than by count: a panel at 2.0 scale has four times the pixels of one at
# 1.0, so the same entry count would mean four times the memory.
#
# One entry is five bytes a pixel of edge band — four for the profile, which is
# kept in floating point precisely so that the multiply by the table's opacity
# happens before the rounding rather than after it, and one for the position.
#
# It was 24 MiB while an entry was the inward band alone. An entry now carries
# the outward band as well, which is wider and runs round the outside of the
# panel rather than the inside, and it measured 2.4 MiB against the 0.7 MiB it
# used to be. At 24 MiB a collapse of the default panel kept fourteen of its
# twenty-one sizes, so the unfold — which asks for exactly the same twenty-one
# — started paying for a third of them again: measured, a warm re-fold went
# from 4.7 ms a frame to 9.8. 48 MiB holds the whole animation with room over,
# which is the property the cache was put here for.
FIELD_BYTES = 48 * 1024 * 1024


class _Fields(OrderedDict):
    """Built strips of `(box, profile, where, keys)`, least recently used out."""

    def __init__(self):
        super().__init__()
        self._weight: dict = {}
        self.total_bytes = 0

    def take(self, key):
        if key not in self:
            return None
        self.move_to_end(key)
        return self[key]

    def keep(self, key, value) -> None:
        weight = sum(profile.nbytes + where.nbytes
                     for half in value
                     for _box, profile, where, _keys in half)
        self[key] = value
        self._weight[key] = weight
        self.total_bytes += weight
        # Never the entry just kept: the caller is about to draw with it.
        while len(self) > 1 and self.total_bytes > FIELD_BYTES:
            del self[next(iter(self))]

    # `OrderedDict.popitem` is implemented in C and does not route through a
    # subclass's `__delitem__`, so eviction spells the deletion out above.
    def __delitem__(self, key) -> None:
        self.total_bytes -= self._weight.pop(key, 0)
        super().__delitem__(key)

    def clear(self) -> None:
        super().clear()
        self._weight.clear()
        self.total_bytes = 0


_cache = _Fields()
_profiles: dict = {}
_bayer = None


def boxes(width: int, height: int, band: int) -> list[tuple]:
    """Four rectangles covering the edge, or two when the panel is too short.

    They tile rather than overlap, and that is load-bearing: two images with
    partial opacity laid over each other compose twice, so an overlap would
    read as a bright seam straight across the light.
    """
    top = min(band, (height + 1) // 2)
    bottom = min(band, height - top)
    left = min(band, (width + 1) // 2)
    right = min(band, width - left)
    made = [(0, 0, width, top), (0, height - bottom, width, height)]
    if height - top - bottom > 0:
        made.append((0, top, left, height - bottom))
        if width - left - right > 0:
            made.append((width - right, top, width, height - bottom))
    return [box for box in made if box[2] > box[0] and box[3] > box[1]]


def outer_boxes(width: int, height: int, pad: int, corner: int) -> list[tuple]:
    """Four rectangles covering everything outside the panel, in panel coordinates.

    Negative coordinates, because the companion surface begins `pad` pixels
    above and to the left of the panel's own origin and every field in this
    module is solved about the panel's centre. Keeping one coordinate system
    for both halves is what makes the dither line up across the boundary: it is
    indexed by the pixel's place on the panel, so a strip on either side of the
    edge agrees with its neighbour instead of crawling against it.

    Same tiling as `boxes` and the same reason — they must not overlap — with
    one difference. The top and bottom bands reach `corner` further in than the
    sides do, because the pixels between a square corner and a rounded one are
    *inside* the panel's rectangle and *outside* its clip region, so nothing on
    the canvas can ever draw them: they belong to this half, and they sit
    beside the corner rather than beyond it.
    """
    deep = min(max(0, corner), height // 2)
    top = (-pad, -pad, width + pad, deep)
    bottom = (-pad, max(deep, height - deep), width + pad, height + pad)
    made = [top, bottom]
    if bottom[1] > top[3]:
        made.append((-pad, top[3], 0, bottom[1]))
        made.append((width, top[3], width + pad, bottom[1]))
    return [box for box in made if box[2] > box[0] and box[3] > box[1]]


def profile_of(shape: Shape):
    """The light's cross-section: `(distances, brightness)`, both ascending in d.

    `d` is the signed distance to the ring's path, negative inside the panel.
    Everything the border looks like is in this one curve, and it is written as
    arithmetic rather than as a blur so that it is exact at every pixel instead
    of at every byte.

    Two terms:

      * a near lobe and a wide one, each the *exact* profile of a line of width
        `core` convolved with a gaussian — which is what the old blur was
        approximating in eight bits, spelled out as a difference of error
        functions and evaluated in double precision. Screened together, and each
        normalised to its own peak first, so `OUTER_KEEP` still means what it
        says however wide the two are.
      * the asymmetry, which is now a single number rather than a term: the
        wide lobe is given one standard deviation inside the path and a larger
        one outside it. Both sides still peak at one on the path, so the curve
        is continuous there and the brightest point is the path itself.

    There used to be a third, an outward gate — the exact profile of a blurred
    half-plane, one inside and falling to nothing outside — and it was there
    only because the window's boundary was `inset` pixels out and light that
    reached it was light the clip would cut in a straight line. The companion
    surface has no boundary, so the gate has gone and with it the reason the
    border looked like a sticker.
    """
    import numpy as np

    key = tuple(round(value, 3) for value in shape)
    made = _profiles.get(key)
    if made is not None:
        return made

    erf = np.vectorize(math.erf, otypes=[np.float64])
    half = max(0.05, shape.core / 2.0)

    def lobe(distance, sigma):
        """A line of width `core` seen through a gaussian of `sigma`, exactly."""
        root = sigma * math.sqrt(2.0)
        return 0.5 * (erf((half + distance) / root) + erf((half - distance) / root))

    # Far enough each way that the wide lobe has died. Anything beyond either
    # end reads the end's value, which is zero on both sides by construction.
    inward = shape.reach + shape.core + 8.0
    outward = shape.spill + shape.core + 8.0
    grid = np.arange(-inward, outward + SAMPLE, SAMPLE)
    away = np.abs(grid)

    near = max(0.35, shape.ridge / SIGMAS)
    # One sigma per side, selected per sample rather than solved twice. Both
    # sides are normalised to their own peak, which is one at `d` = 0 either
    # way, so the join at the path is a value and not a step.
    wide = np.where(grid < 0.0, max(0.60, shape.reach / SIGMAS),
                    max(0.60, shape.spill / SIGMAS))
    ridge = lobe(away, near) / lobe(0.0, near)
    field = OUTER_KEEP * (lobe(away, wide) / lobe(0.0, wide))
    values = ridge + field - ridge * field
    values /= values.max()
    values[values < DIM] = 0.0
    made = (grid, values.astype(np.float32))
    # Keyed on the shape alone, and there are five of them in a session at most
    # — one per window scale anyone stops at — so this never needs a bound.
    _profiles[key] = made
    return made


def pad_of(shape: Shape) -> int:
    """How much room outside the panel the light needs, in physical pixels.

    Asked of the profile rather than of `spill`, because `spill` is where the
    wide lobe has faded to about a hundredth and a hundredth of a bright border
    is still four or five levels of 255. What the surface has to contain is
    everywhere the profile is not *zero*, and the profile knows where that is:
    `DIM` is what put the zeros there.

    The companion's own bitmap edge is a cliff exactly like the window's, so
    getting this wrong is not a dimmer glow, it is a straight line drawn across
    one in mid-air. `BAND_SLACK` is added for the same reason it is added to a
    strip boundary.
    """
    grid, values = profile_of(shape)
    lit = values.nonzero()[0]
    reach = float(grid[lit[-1]]) if len(lit) else 0.0
    return max(1, math.ceil(reach - shape.inset) + BAND_SLACK)


def _around(np, x, y, qx, qy, ax, by, radius, starts):
    """How far round the path the nearest point to each pixel is, in pixels.

    Arc length from the top-left corner, clockwise, which is the order
    `beam._rounded_path` walks and therefore the order the colour table is
    written in. Closed form, in the same regions the distance itself is defined
    by: `qx > 0 and qy > 0` is the wedge belonging to a corner arc, and
    everywhere else the nearest edge is the one whose `q` is larger — which is
    exactly what `min(max(q.x, q.y), 0)` says in the distance.

    The straight edges are answered for every pixel and the corners are then
    written over the top, rather than both being computed and selected between.
    An `arctan2` costs about as much as everything else in this module put
    together, and the four corner wedges of the default panel are 1,764 pixels
    of the band's 82,656 — so asking for it everywhere and throwing 98 % of the
    answer away was measured at 4 ms of a 12 ms build.
    """
    right, lower = x > 0.0, y > 0.0
    place = np.where(
        qx >= qy,
        np.where(right, starts[2] + (y + by), starts[6] + (by - y)),
        np.where(lower, starts[4] + (ax - x), starts[0] + (x + ax)))
    corner = (qx > 0.0) & (qy > 0.0)
    if corner.any():
        shape = place.shape
        pick = np.broadcast_to
        angle = np.arctan2(pick(qy, shape)[corner], pick(qx, shape)[corner])
        near, below = pick(right, shape)[corner], pick(lower, shape)[corner]
        # Two of the four arcs are entered at their zero-angle end and two at
        # their right-angle end, because the ring is walked clockwise and the
        # angle is not: top-right and bottom-left run against it.
        turn = math.pi / 2
        place[corner] = np.where(
            near,
            np.where(below, starts[3] + angle * radius,
                     starts[1] + (turn - angle) * radius),
            np.where(below, starts[5] + (turn - angle) * radius,
                     starts[7] + angle * radius))
    return place


def _dither(np, box):
    """A stable sub-level offset per pixel, mean one half.

    Eight bits is enough for a border only if the last bit is used. A falloff
    that spans two hundred levels over thirty pixels has stretches where it
    moves by less than one level a pixel, and rounding those gives plateaus two
    and three pixels wide with a visible contour between them — the exact defect
    the eight-bit blur used to produce for a different reason, arriving by the
    honest route instead. Adding this before truncating turns each plateau into
    an interleave whose *average* is the true value, which is what the eye
    integrates. It is ordered rather than random and indexed by the pixel's
    place on the panel rather than in the strip, so a repaint reproduces it
    exactly and neighbouring strips agree across their shared border: a glow
    that re-dithered every frame would crawl.
    """
    global _bayer
    if _bayer is None:
        cell = np.array([[0, 2], [3, 1]])
        while cell.shape[0] < 8:
            cell = np.block([[4 * cell, 4 * cell + 2],
                             [4 * cell + 3, 4 * cell + 1]])
        _bayer = (cell + 0.5) / cell.size
    x0, y0, x1, y1 = box
    rows = np.arange(y0, y1) % 8
    columns = np.arange(x0, x1) % 8
    return _bayer[np.ix_(rows, columns)].astype(np.float32)


def _rounded_distance(np, x, y, a: float, b: float, r: float):
    """Signed distance from `(x, y)` to a rounded rectangle centred on zero.

    Half-extents `a` and `b`, corner radius `r`, negative inside. The same
    closed form the light is built on, kept as a function because it is now
    solved twice for two different rectangles — the ring's own path, and the
    panel's outline, which is where the two halves of the light meet.
    """
    qx, qy = np.abs(x) - (a - r), np.abs(y) - (b - r)
    wide, tall = np.maximum(qx, 0.0), np.maximum(qy, 0.0)
    return (np.sqrt(wide * wide + tall * tall)
            + np.minimum(np.maximum(qx, qy), 0.0) - r)


def _built(width: int, height: int, radius: float, shape: Shape,
           band: int, pad: int) -> tuple[list, list]:
    """The static fields for one geometry: `(inward, outward)`.

    Only the edge band is evaluated. The interior of the panel is where most of
    its pixels are and none of them are ever lit, so the cost is a function of
    the perimeter rather than of the area — which is why a closed form at full
    resolution is affordable where a full-resolution blur was not.

    The two halves are the same light cut at the panel's own outline, because
    that is where one window stops and the other starts, and neither can draw a
    pixel on the other's side. Only the outward half is masked. The canvas half
    is left whole and the region clip cuts it, which is deliberate: the clip is
    one bit and rasterised by GDI, and no mask computed here is guaranteed to
    agree with it to the pixel. Masking both would make a disagreement a *gap* —
    a dark notch round the corner, which is exactly the artefact all of this
    exists to remove. Leaving the canvas half whole makes a disagreement an
    overlap instead, and an overlap is one pixel of light composed twice.
    """
    import numpy as np

    grid, values = profile_of(shape)
    cx, cy = width / 2.0, height / 2.0
    a, b = cx - shape.inset, cy - shape.inset
    r = max(0.0, min(float(radius), a, b))
    ax, by = a - r, b - r
    quarter = math.pi * r / 2.0
    # Where each of the eight stretches begins, and how long the whole is: two
    # straights and two arcs each way, in the order the path is walked.
    starts = list(accumulate((2 * ax, quarter, 2 * by, quarter,
                              2 * ax, quarter, 2 * by, quarter), initial=0.0))
    total = starts[-1] or 1.0

    # The panel's own outline, which is what the window is clipped to and
    # therefore where the light changes hands. Its centre is the true middle of
    # the pixel grid rather than the field's `width / 2`, so that the mask lands
    # where `CreateRoundRectRgn` puts the region rather than half a pixel off it.
    edge_a, edge_b = (width - 1) / 2.0, (height - 1) / 2.0
    edge_r = max(0.0, min(float(radius), edge_a, edge_b))

    # The cross-section is sampled on an even grid, so it is read by arithmetic
    # rather than by `np.interp`'s search: two gathers and a lerp against a
    # binary search per pixel, over eighty thousand of them.
    first, step = float(grid[0]), float(grid[1] - grid[0])
    last = len(values) - 2

    def field(box, outside: bool):
        x0, y0, x1, y1 = box
        x = (np.arange(x0, x1, dtype=np.float32) - cx)[None, :]
        y = (np.arange(y0, y1, dtype=np.float32) - cy)[:, None]
        qx, qy = np.abs(x) - ax, np.abs(y) - by
        wide, tall = np.maximum(qx, 0.0), np.maximum(qy, 0.0)
        distance = (np.sqrt(wide * wide + tall * tall)
                    + np.minimum(np.maximum(qx, qy), 0.0) - r)
        at = (distance - first) * (1.0 / step)
        np.clip(at, 0.0, last, out=at)
        below = at.astype(np.int32)
        rest = at - below
        low = values[below]
        profile = low + (values[below + 1] - low) * rest
        if outside:
            panel = _rounded_distance(np, x + (cx - edge_a), y + (cy - edge_b),
                                      edge_a, edge_b, edge_r)
            profile = profile * (panel > 0.0)

        place = _around(np, x, y, qx, qy, ax, by, r, starts)
        # Position 0 is reserved for "no light here", so the ring is written
        # into 1 .. LUT_SIZE - 1 and a pixel the light never reaches keeps 0
        # however close to the path it is.
        place *= (LUT_SIZE - 2) / total
        np.rint(place, out=place)
        np.clip(place, 0, LUT_SIZE - 2, out=place)
        where = (place.astype(np.uint8) + 1) * (profile > 0.0)
        # Which positions round the ring this strip actually contains, so a
        # frame can ask whether the table moved *here* without looking at the
        # pixels.
        counted = np.bincount(where.reshape(-1), minlength=LUT_SIZE)
        keys = tuple(int(value) for value in counted.nonzero()[0])
        return (box, np.ascontiguousarray(profile), where, keys)

    inward = [field(box, False) for box in boxes(width, height, band)]
    corner = math.ceil(edge_r) + 1
    outward = [field(box, True)
               for box in outer_boxes(width, height, pad, corner)]
    return inward, outward


class _Strip:
    """One stretch of the edge: its two fields, its canvas item, its last paint."""

    __slots__ = ("box", "dither", "item", "keys", "photo", "profile", "shown",
                 "where")

    def __init__(self, item=None):
        self.item = item
        self.box = self.profile = self.where = self.photo = self.shown = None
        self.dither = None
        self.keys: tuple = ()


def _lay(strips: list[_Strip], built: list, np) -> None:
    """Give a set of strips the fields just built for them.

    Shared by both halves, because what a strip carries is the same on either
    side of the window's edge — a box, the two fields over it, and a dither
    keyed on where the box sits on the panel rather than where it sits in the
    strip, so that a repaint reproduces it exactly and two strips agree across
    the border they share.
    """
    for strip, made in zip(strips, built, strict=False):
        box, profile, where, keys = made
        if strip.box != box:
            strip.dither = _dither(np, box)
        strip.box, strip.profile, strip.where = box, profile, where
        strip.keys = keys
        # The fields under it changed, so whatever it is showing no longer
        # describes it. Left alone, a strip whose colours happened not to move
        # would keep the picture drawn for the previous panel size.
        strip.shown = None


class Spill:
    """The half of the light that falls outside the window.

    It has no canvas and no `PhotoImage`: its pixels are written straight into
    the surface a `chrome.layered.Layered` presents, which is the only place in
    this process with real per-pixel alpha and no boundary for the light to
    stop at. The panel's own footprint is a hole in it — the overlay is
    translucent, so light left under the panel would show *through* it, doubly
    composed and at the wrong brightness — and that hole is also why the
    companion cannot cover a word even if it lost its place in the z-order.

    Written premultiplied, because `UpdateLayeredWindow` demands it, and that
    costs nothing: the opacity a pixel gets is `profile * opacity(where)`, so
    its premultiplied red is `red(where) * opacity(where) * profile`, and
    `red * opacity` is a function of `where` alone. It folds into the table.
    Done the obvious way instead — a second pass over every pixel multiplying
    the three colour bands by the alpha band — it was measured at 8.53 ms a
    frame, which is half the budget for arithmetic that need never happen.
    """

    def __init__(self, glow):
        self.glow = glow
        self.strips = [_Strip() for _ in range(4)]
        self.pad = 0
        self.size = (0, 0)
        self.at = (0, 0)

    def reshape(self, built: list, pad: int, width: int, height: int) -> None:
        """Take the outward fields for a panel of this size, and clear the surface.

        The surface persists between frames — that is what lets a frame rewrite
        one strip of the edge and hand the whole bitmap over — so a panel that
        changed size leaves the previous one's light lying in it. Cleared here
        rather than per strip, because the strips no longer cover the same
        pixels they did.
        """
        import numpy as np

        self.pad = pad
        self.size = (width + 2 * pad, height + 2 * pad)
        self.glow.reserve(*self.size)
        self.glow.frame()[:self.size[1], :self.size[0]] = 0
        _lay(self.strips, built, np)
        for strip in self.strips[len(built):]:
            strip.box, strip.shown = None, None

    def place(self, x: int, y: int) -> None:
        """Where the *panel's* top-left corner is. The surface sits `pad` outside it."""
        self.at = (int(x) - self.pad, int(y) - self.pad)

    def move(self) -> None:
        """Follow a drag, without handing over a bitmap that has not changed.

        Both windows move by the same asynchronous `SetWindowPos`, which is the
        point: they queue on the one thread that owns them and are applied in
        the order they were asked for. Presenting the surface instead was tried
        and is not wrong — the light is unchanged, so the handover is pure cost
        — but it puts a synchronous call between two asynchronous ones, which
        is the arrangement most likely to let them interleave. Photographed
        mid-drag either way, the panel sits exactly in the middle of its light.
        """
        self.glow.move(*self.at)

    def present(self) -> None:
        self.glow.present(self.size[0], self.size[1], self.at)

    def behind(self, hwnd: int) -> None:
        self.glow.behind(hwnd)

    def visible(self, shown: bool) -> None:
        self.glow.visible(shown)

    def paint(self, index: int, channels: tuple) -> bool:
        """Redraw one strip into the surface. True if it needed redrawing.

        Nothing is presented here. The handover is one call for the whole
        bitmap whatever is in it — 0.12 ms, measured — so it belongs to the
        frame rather than to the strip.
        """
        strip = self.strips[index] if index < len(self.strips) else None
        if strip is None or strip.box is None:
            return False
        want = bytes(table[key] for key in strip.keys for table in channels)
        if want == strip.shown:
            return False
        self._draw(strip, channels)
        strip.shown = want
        return True

    def _draw(self, strip: _Strip, channels: tuple) -> None:
        import numpy as np

        red, green, blue, opacity = (
            np.asarray(table, dtype=np.float32) for table in channels)
        # The premultiply, folded into the table where it is free. Alpha is
        # last, and the three colours can never exceed it because no entry of
        # `red` exceeds 255 — including after the dither, which is added to all
        # four equally.
        tables = (blue * opacity * (1.0 / 255.0), green * opacity * (1.0 / 255.0),
                  red * opacity * (1.0 / 255.0), opacity)
        x0, y0, x1, y1 = strip.box
        pad = self.pad
        out = self.glow.frame()[y0 + pad:y1 + pad, x0 + pad:x1 + pad]
        for band, table in enumerate(tables):
            value = strip.profile * table[strip.where]
            value += strip.dither
            np.clip(value, 0.0, 255.0, out=value)
            # Truncated rather than rounded, because the dither already carries
            # the half. See `_dither`.
            out[..., band] = value.astype(np.uint8)

    def destroy(self) -> None:
        self.strips.clear()
        self.glow.destroy()


class Ring:
    """The border, as four images that only ever have their colours looked up again.

    The canvas items are created once and never again. That is not tidiness:
    the overlay lays the beam before the card and the lyrics precisely so it can
    never cover a word, and an item created later lands on top of the display
    list. A ring that rebuilt its items on a resize would climb over the words
    it was laid under, once, and stay there.

    It owns the outward half as well, when there is a surface to put it on.
    Two halves of one border cut by a window's boundary are not two effects:
    they are laid out from one geometry and repainted from one loop and one
    strip index, because the thing that would show is the two of them
    disagreeing across the very line the split runs along.
    """

    def __init__(self, canvas, glow=None):
        self.canvas = canvas
        self.strips = [_Strip(canvas.create_image(0, 0, anchor="nw",
                                                  state="hidden"))
                       for _ in range(4)]
        self.spill = Spill(glow) if glow is not None else None
        self._next = 0

    def reshape(self, width: int, height: int, radius: float,
                shape: Shape) -> None:
        """Rebuild the fields for a panel of this size.

        Everything a frame needs is derived from these two fields, so this is
        where the whole cost of the effect lives. It is paid on every frame of a
        collapse, which is why the results are cached — the unfold asks for the
        same twenty-one sizes in the other order and pays for none of them.

        The geometry arrives as numbers rather than as the list of points the
        blurred version was handed, because nothing is drawn any more: the
        rounded rectangle is *solved*, not rasterised, so what it needs is the
        rectangle and not a sampling of it. The caller still has the points —
        they are the path the light is centred on — and nothing here reads them.
        """
        import numpy as np

        band = max(1, round(shape.inset + shape.reach + shape.core + BAND_SLACK))
        pad = pad_of(shape)
        key = (width, height, round(float(radius), 2),
               tuple(round(value, 2) for value in shape), band, pad)
        built = _cache.take(key)
        if built is None:
            built = _built(width, height, radius, shape, band, pad)
            _cache.keep(key, built)
        inward, outward = built
        boxed = [strip.box for strip in self.strips]
        _lay(self.strips, inward, np)
        for strip, was in zip(self.strips[:len(inward)], boxed, strict=False):
            if was != strip.box:
                # A `PhotoImage` cannot change size, so a strip that did needs a
                # new one. The canvas item does not: it is the item's position
                # in the display list that has to survive.
                #
                # The item is let go of the old image *before* the last
                # reference to it is dropped, and that order is not tidiness.
                # `ImageTk.PhotoImage.__del__` deletes the Tk image, and an item
                # still naming a deleted image answers `image "pyimage1" doesn't
                # exist` to the next thing asked of it — including the
                # `itemconfigure` two lines below, which only wanted to unhide
                # it.
                self.canvas.itemconfigure(strip.item, image="")
                strip.photo = None
            self.canvas.coords(strip.item, strip.box[0], strip.box[1])
            self.canvas.itemconfigure(strip.item, state="normal")
        for strip in self.strips[len(inward):]:
            self.canvas.itemconfigure(strip.item, state="hidden")
            strip.box, strip.shown = None, None
        if self.spill is not None:
            self.spill.reshape(outward, pad, width, height)

    def paint(self, channels: tuple) -> int:
        """Show the ring lit by `channels`, at most `PER_CALL` strips a call.

        `channels` is four tables of `LUT_SIZE` bytes — red, green, blue and
        opacity — indexed by the byte `where` holds. Rotating the gradient round
        the ring is a rotation of those tables and costs nothing here at all.

        A strip is repainted only when the tables have actually changed over the
        positions that strip contains, which is what makes a travelling head
        affordable: a comet lights a seventh of the ring, so the other strips
        are told most frames that nothing happened to them. Returns how many
        were repainted, which is the unit a frame's cost is measured in.
        """
        painted = 0
        for _ in range(len(self.strips)):
            if painted >= PER_CALL:
                break
            index = self._next % len(self.strips)
            strip = self.strips[index]
            self._next += 1
            if strip.box is None:
                continue
            want = bytes(table[key] for key in strip.keys for table in channels)
            inward = want != strip.shown
            # Asked unconditionally, and before the early exit, because the two
            # halves are one stretch of edge: skipping the outward one whenever
            # the inward one happened not to move is how a border ends up lit to
            # one colour inside the panel and another outside it.
            outward = (self.spill is not None
                       and self.spill.paint(index, channels))
            if not (inward or outward):
                continue
            if inward:
                self._show(strip, channels)
                strip.shown = want
            painted += 1
        if painted and self.spill is not None:
            # One handover for the whole surface, once, however many strips of
            # it were rewritten.
            self.spill.present()
        return painted

    def place(self, x: int, y: int) -> None:
        """Tell the outward half where the panel's top-left corner is now."""
        if self.spill is not None:
            self.spill.place(x, y)
            self.spill.present()

    def follow(self, x: int, y: int) -> None:
        """The same, for a drag: move the surface without handing it over again."""
        if self.spill is not None:
            self.spill.place(x, y)
            self.spill.move()

    def image(self, strip: _Strip, channels: tuple):
        """What one strip looks like under `channels`, as a PIL image.

        The opacity a pixel finally gets is how much light reaches it *times*
        how bright that stretch of ring is, and the first of those two is kept
        in floating point until this line so that the product is rounded once
        rather than twice. Keeping the second factor out of the colour is what
        lets the unlit part of the ring be transparent rather than a flat patch
        of backdrop laid over the cover wash.
        """
        import numpy as np
        from PIL import Image

        red, green, blue, opacity = (
            np.asarray(table, dtype=np.uint8) for table in channels)
        where = strip.where
        alpha = strip.profile * opacity[where].astype(np.float32)
        alpha += strip.dither
        np.clip(alpha, 0.0, 255.0, out=alpha)
        out = np.empty((*where.shape, 4), dtype=np.uint8)
        out[..., 0] = red[where]
        out[..., 1] = green[where]
        out[..., 2] = blue[where]
        # Truncated rather than rounded, because the dither above already
        # carries the half: `floor(v + u)` for `u` uniform on the unit interval
        # is the round with the remainder spread over neighbouring pixels
        # instead of thrown away.
        out[..., 3] = alpha.astype(np.uint8)
        return Image.fromarray(out, "RGBA")

    def _show(self, strip: _Strip, channels: tuple) -> None:
        from PIL import ImageTk

        image = self.image(strip, channels)
        if strip.photo is None:
            # `master` explicitly, rather than letting it find tkinter's default
            # root. An `ImageTk.PhotoImage` belongs to whichever interpreter was
            # default when it was made, and a process with two of them — which
            # is any test session that builds an overlay beside a shared root —
            # then hands one canvas an image the other owns: `image "pyimage5"
            # doesn't exist`, from a line that only asked to show it.
            strip.photo = ImageTk.PhotoImage(image, master=self.canvas)
            self.canvas.itemconfigure(strip.item, image=strip.photo)
        else:
            # In place, rather than a fresh `PhotoImage` every time. Each one is
            # a GDI bitmap and a process is given ten thousand handles; `bloom`
            # documents what running out of them does, which is a `Tcl_Panic`
            # with nothing to catch and no chance to log a word.
            strip.photo.paste(image)

    def destroy(self) -> None:
        # The companion first, and that order is load-bearing. Deleting a canvas
        # item throws if the interpreter has already gone, and one of the ways
        # this is reached is a root that was destroyed from under the loop — so
        # the half that would be left on screen is taken down before the half
        # that can raise on the way out.
        if self.spill is not None:
            self.spill.destroy()
            self.spill = None
        for strip in self.strips:
            self.canvas.delete(strip.item)
            strip.photo = None
        self.strips.clear()
