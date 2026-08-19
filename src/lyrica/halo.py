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
      light carries `reach`. Outward the window's own boundary is `inset` pixels
      away — the overlay is clipped to a rounded rectangle with `SetWindowRgn`,
      and the desktop starts on the other side of it — so the light has to be
      *gone* before it arrives, or the clip cuts a straight line across a glow
      and hands back the hard edge all of this exists to remove. A blur is
      symmetric by definition and the old code corrected for that with a second
      mask; here the two sides are simply two different functions of `d`.
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
    radius. Inward there is panel to spread across, so the field carries
    `reach`. Outward there is the window's own boundary `inset` pixels away, so
    the field has to be gone before it arrives.
    """
    inset: float        # how far the ridge sits inside the panel's own edge
    core: float         # how wide the brightest part of the light is
    ridge: float        # how far the near falloff carries
    reach: float        # how far the wide field carries inward
    edge: float         # how sharply the light is taken away outward

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

# How many standard deviations of the wide lobe fit inside `reach`. Three, so
# the field has faded to about a hundredth of its peak by the distance the
# caller asked for.
SIGMAS = 3.0

# And how many of the outward gate's fit inside `inset`. The gate is the half of
# the asymmetry that matters: at `inset` the window is clipped, so whatever the
# caller asked for as `edge`, the light is given no more room than the clip
# allows. Measured on the default panel — `inset` 7, `edge` 3.4 — this holds the
# gate to sigma 2.5 and leaves 0.35 of 255 at the boundary, which rounds to
# nothing and is cut by nothing.
EDGE_SIGMAS = 2.8

# Below this the light is not merely dim, it is off. It is what makes the outer
# tail *end* rather than trail a level or two into the clip, and what keeps the
# `where` field honest about which pixels have no ring near them.
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
FIELD_BYTES = 24 * 1024 * 1024


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
                     for _box, profile, where, _keys in value)
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


def profile_of(shape: Shape):
    """The light's cross-section: `(distances, brightness)`, both ascending in d.

    `d` is the signed distance to the ring's path, negative inside the panel.
    Everything the border looks like is in this one curve, and it is written as
    arithmetic rather than as a blur so that it is exact at every pixel instead
    of at every byte.

    Three terms:

      * a near lobe and a wide one, each the *exact* profile of a line of width
        `core` convolved with a gaussian — which is what the old blur was
        approximating in eight bits, spelled out as a difference of error
        functions and evaluated in double precision. Screened together, and each
        normalised to its own peak first, so `OUTER_KEEP` still means what it
        says however wide the two are.
      * an outward gate, the exact profile of a blurred half-plane, which is the
        asymmetry. It is one at every pixel inside the path and falls away
        outside it, and its width is capped by `inset` rather than taken from
        `edge` alone: the clip is at `inset`, so light that would still be there
        is light the clip would cut.

    The whole is normalised to peak at one — which the gate puts a pixel or two
    *inside* the path rather than on it, and that is right: the brightest part
    of a border that has to end at a boundary is not the part nearest it.
    """
    import numpy as np

    key = tuple(round(value, 3) for value in shape)
    made = _profiles.get(key)
    if made is not None:
        return made

    erf = np.vectorize(math.erf, otypes=[np.float64])
    erfc = np.vectorize(math.erfc, otypes=[np.float64])
    half = max(0.05, shape.core / 2.0)

    def lobe(distance, sigma):
        """A line of width `core` seen through a gaussian of `sigma`, exactly."""
        root = sigma * math.sqrt(2.0)
        return 0.5 * (erf((half + distance) / root) + erf((half - distance) / root))

    # Far enough inward that the wide lobe has died, and far enough outward that
    # the gate has. Anything beyond either end reads the end's value, which is
    # zero on both sides by construction.
    inward = shape.reach + shape.core + 8.0
    outward = shape.inset + 2.0
    grid = np.arange(-inward, outward + SAMPLE, SAMPLE)
    away = np.abs(grid)

    near = max(0.35, shape.ridge / SIGMAS)
    wide = max(0.60, shape.reach / SIGMAS)
    ridge = lobe(away, near) / lobe(0.0, near)
    field = OUTER_KEEP * (lobe(away, wide) / lobe(0.0, wide))
    lit = ridge + field - ridge * field

    gate = 0.5 * erfc(grid / (max(0.35, min(shape.edge,
                                            shape.inset / EDGE_SIGMAS))
                              * math.sqrt(2.0)))
    values = lit * gate
    values /= values.max()
    values[values < DIM] = 0.0
    made = (grid, values.astype(np.float32))
    # Keyed on the shape alone, and there are five of them in a session at most
    # — one per window scale anyone stops at — so this never needs a bound.
    _profiles[key] = made
    return made


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


def _built(width: int, height: int, radius: float, shape: Shape,
           band: int) -> list[tuple]:
    """The static fields for one geometry, over the strips that use them.

    Only the edge band is evaluated. The interior of the panel is where most of
    its pixels are and none of them are ever lit, so the cost is a function of
    the perimeter rather than of the area — which is why a closed form at full
    resolution is affordable where a full-resolution blur was not.
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

    # The cross-section is sampled on an even grid, so it is read by arithmetic
    # rather than by `np.interp`'s search: two gathers and a lerp against a
    # binary search per pixel, over eighty thousand of them.
    first, step = float(grid[0]), float(grid[1] - grid[0])
    last = len(values) - 2

    made = []
    for box in boxes(width, height, band):
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
        made.append((box, np.ascontiguousarray(profile), where, keys))
    return made


class _Strip:
    """One stretch of the edge: its two fields, its canvas item, its last paint."""

    __slots__ = ("box", "dither", "item", "keys", "photo", "profile", "shown",
                 "where")

    def __init__(self, item: int):
        self.item = item
        self.box = self.profile = self.where = self.photo = self.shown = None
        self.dither = None
        self.keys: tuple = ()


class Ring:
    """The border, as four images that only ever have their colours looked up again.

    The canvas items are created once and never again. That is not tidiness:
    the overlay lays the beam before the card and the lyrics precisely so it can
    never cover a word, and an item created later lands on top of the display
    list. A ring that rebuilt its items on a resize would climb over the words
    it was laid under, once, and stay there.
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self.strips = [_Strip(canvas.create_image(0, 0, anchor="nw",
                                                  state="hidden"))
                       for _ in range(4)]
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
        band = max(1, round(shape.inset + shape.reach + shape.core + BAND_SLACK))
        key = (width, height, round(float(radius), 2),
               tuple(round(value, 2) for value in shape), band)
        built = _cache.take(key)
        if built is None:
            built = _built(width, height, radius, shape, band)
            _cache.keep(key, built)
        for strip, made in zip(self.strips, built, strict=False):
            box, profile, where, keys = made
            resized = strip.box is None or strip.box != box
            strip.box, strip.profile, strip.where = box, profile, where
            strip.keys = keys
            # The fields under it changed, so whatever it is showing no longer
            # describes it. Left alone, a strip whose colours happened not to
            # move would keep the picture drawn for the previous panel size.
            strip.shown = None
            if resized:
                import numpy as np

                strip.dither = _dither(np, box)
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
            self.canvas.coords(strip.item, box[0], box[1])
            self.canvas.itemconfigure(strip.item, state="normal")
        for strip in self.strips[len(built):]:
            self.canvas.itemconfigure(strip.item, state="hidden")
            strip.box, strip.shown = None, None

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
            strip = self.strips[self._next % len(self.strips)]
            self._next += 1
            if strip.box is None:
                continue
            want = bytes(table[key] for key in strip.keys for table in channels)
            if want == strip.shown:
                continue
            self._show(strip, channels)
            strip.shown = want
            painted += 1
        return painted

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
        for strip in self.strips:
            self.canvas.delete(strip.item)
            strip.photo = None
        self.strips.clear()
