"""The panel's border as light, with no line anywhere in it.

The border used to be two Tk strokes: a five-pixel core at full colour and a
fifteen-pixel halo at a flat mix of 42 % toward the backdrop. Both are solid
and both have ends, so the cross-section of the border was a step function with
exactly one step in it — bright, then a plateau, then nothing. Real light falls
off continuously, and no choice of the two widths or of the 42 % changes the
shape of that graph. It is a property of `create_line`, so the fix is to stop
using one.

This is `bloom.py`'s trade applied to the border rather than to a glyph: draw
the shape into a mask, blur the *opacity* rather than the colour, tint
afterwards, hand Tk an image and let the canvas do nothing per frame but show a
different one. `bloom` measured that as twenty times cheaper than the four-copy
fake it replaced. Here it replaces 352 `itemconfigure` calls a frame with at
most one image swap.

What it costs is the ability to say "this pixel is a different colour from that
one" for free, which is what a per-segment line ring gave away. The colour
moves round the ring, and one image tinted a single colour cannot carry a
gradient that rotates. It is split in two instead:

    * `profile` — how much light reaches a pixel. Depends only on the geometry,
      so it is built once per panel size and never again.
    * `where` — how far round the ring a pixel is, as one byte. Also depends
      only on the geometry.

Everything the music and the palette do is then a lookup table from `where` to
(colour, opacity), and painting a frame is `Image.point` three times plus a
multiply. The rotation is a rotation *of the table*, which costs nothing at
all, and the blurred field it is applied to never has to be blurred again.

Two consequences of that split are worth stating, because they are the whole
reason it is affordable:

    * The blur happens once per size, at a third of the resolution. Diffuse
      light has no detail to lose to that, and it is the only expensive step
      here — measured 4.7 ms for one full-resolution gaussian on the default
      panel against 0.54 ms at a third.
    * The dark part of the ring is genuinely transparent rather than painted in
      the backdrop colour. The line ring had to draw its unlit segments as
      opaque `palette.backdrop`, which is a flat patch laid over a textured
      cover wash; opacity lets the wash through.

The ring is four images round the edge rather than one covering the panel. That
is not a memory economy first — it is a damage one. Replacing a picture that
covers the whole panel makes Tk redraw everything under it, which is every
lyric on screen; four edge strips damage only the edge, where nothing else is
drawn. Measured on the default panel: 12.5 ms a swap against 7.5 ms for all
four strips and 2.1 ms for one.
"""
from collections import OrderedDict
from typing import NamedTuple


class Shape(NamedTuple):
    """How wide the light is and how it falls off, in physical pixels.

    Asymmetric, and that is the point of describing it here rather than as one
    radius. Inward there is panel to spread across, so the field carries
    `reach`. Outward there is the window's own boundary a few pixels away — the
    overlay is clipped to a rounded rectangle and the desktop starts on the
    other side of it — so the field has to be gone before it arrives, or the
    clip cuts a straight line across a glow and hands back the hard edge this
    whole module exists to remove.
    """
    inset: float        # how far the ridge sits inside the panel's own edge
    core: float         # the ink drawn into the mask, before any blur
    ridge: float        # how far that ink is smeared: the near falloff
    reach: float        # how far the wide field carries inward
    edge: float         # how sharply the light is cut off at the panel's edge

# How many physical pixels one pixel of the blurred fields covers. The light is
# diffuse by construction, so there is no detail in it for a third-resolution
# blur to lose, and the blur is the only expensive step: measured 4.7 ms for a
# full-resolution gaussian on the default panel against 0.54 ms at a third.
# The upscale that follows is itself part of the softness — a one-pixel line
# resampled bilinearly by three is already a six-pixel ramp with no step in it.
DETAIL = 3

# Entries in the table that turns "how far round the ring" into a colour. One
# byte's worth, because that is what the `where` field can hold and what
# `Image.point` reads.
LUT_SIZE = 256

# Byte 0 of `where` means "no part of the ring is near this pixel", so it is
# reserved and never handed to a segment. Anything the band missed is therefore
# transparent rather than wrongly coloured, whatever the table says.
OFF_RING = 0

# How the light falls off across the border. The narrow blur is the ridge that
# gives the panel an edge to end at; the wide one is the weak field that lets it
# survive a textured cover wash. Screened together rather than added, so the
# ridge keeps its opacity where the two overlap — the same combination
# `bloom._rendered_halo` uses, and for the same reason.
OUTER_KEEP = 0.55

# How many standard deviations of the wide blur fit inside `reach`. Three, so
# the field has faded to about a hundredth of its peak by the distance the
# caller asked for, which is what lets the ring sit that far inside the panel
# edge and still not be cut off by it.
SIGMAS = 3.0

# How much further than the light carries a strip reaches, in pixels. A strip
# boundary is a straight line and a straight line across a glow is exactly the
# artefact this module exists to remove, so the far tail of the wide field has
# to be inside a strip rather than cut off by one.
BAND_SLACK = 4

# How many strips may be repainted in one call. This is the cap that turns "as
# often as the music changes" into a bounded per-frame cost, and one is enough.
# Measured on the default panel with eight lines of lyrics on it, at 60 Hz with
# the redraw forced every frame: 1.4 to 2.4 ms a frame across the three styles,
# against 26.4 for the shine's 352 recoloured line items and 28.3 for the
# aurora's. Four at once was measured at 7.5 ms, which is half a frame for
# nothing: the ring closes on a new colour within four frames either way, and
# four frames is 67 ms against a gradient that takes eleven seconds to go round.
PER_CALL = 1

# Fields are cached because a collapse animation asks for the same twenty-one
# sizes on the way back out that it asked for on the way in — measured 4.19 ms
# a frame on the way in and 1.11 on the way back. Bounded by bytes rather than
# by count, for the reason `bloom` bounds its cache that way: a panel at 2.0
# scale has four times the pixels of one at 1.0, so the same entry count would
# mean four times the memory.
#
# What it actually holds is small. Two greyscale images over four strips is
# 161 KiB for the default panel and 612 KiB at 2.0 scale, so a whole collapse
# is 2.7 MiB of the 24 allowed and only a session that resized the window
# through dozens of distinct sizes ever reaches the bound. The Tk photos are
# held separately by the strips themselves, at 323 KiB for the default panel
# and 1.2 MiB at 2.0 scale — four GDI handles in total, against the 800 a song
# of lyrics asks `bloom` for.
FIELD_BYTES = 24 * 1024 * 1024


class _Fields(OrderedDict):
    """Built strips of `(box, profile, where)`, least recently used evicted."""

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
        # One byte a pixel each, for two greyscale images per strip.
        weight = sum(profile.width * profile.height * 2
                     for _box, profile, _where, _keys in value)
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


def _normalised(image, keep: float = 1.0):
    """`image` scaled so its brightest pixel is `keep` of full opacity.

    A blurred one-pixel line peaks far below 255 — the wider the blur the
    dimmer, since the same ink is spread over more pixels — so without this the
    ridge's opacity would depend on the window scale, and the border would
    quietly fade as the panel grew.
    """
    peak = image.getextrema()[1]
    if not peak:
        return image
    gain = 255.0 * keep / peak
    return image.point([min(255, round(value * gain)) for value in range(256)])


def _built(points: list[tuple], width: int, height: int, shape: Shape,
           band: int) -> list[tuple]:
    """The static fields for one geometry, cropped to the strips that use them.

    Both are drawn at `DETAIL`-reduced resolution and resampled up, and the two
    are resampled differently on purpose. `profile` is interpolated, because
    interpolating it is half of what makes it smooth. `where` is not: it holds a
    position round a closed ring, so it wraps from 255 back to 1 somewhere, and
    interpolating across that step invents every colour in the gradient in a
    band a few pixels wide. Nearest-neighbour invents nothing, and the
    three-pixel cells it leaves are invisible in a field that takes a sixth of
    the perimeter to change colour appreciably.
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    k = DETAIL
    small = (max(1, -(-width // k)), max(1, -(-height // k)))
    # Resampled to a whole number of low-resolution cells and cropped back, so
    # every strip is a crop of one image rather than a resize of its own. Two
    # neighbouring strips resampled separately can disagree by half a pixel,
    # and half a pixel of disagreement along a shared border is a line.
    full = (small[0] * k, small[1] * k)
    low = [(x / k, y / k) for x, y in points]

    mask = Image.new("L", small, 0)
    ImageDraw.Draw(mask).line([*low, low[0]], fill=255,
                              width=max(1, round(shape.core / k)), joint="curve")
    ridge = _normalised(mask.filter(
        ImageFilter.GaussianBlur(max(0.7, shape.ridge / (k * SIGMAS)))))
    field = _normalised(mask.filter(
        ImageFilter.GaussianBlur(max(1.0, shape.reach / (k * SIGMAS)))), OUTER_KEEP)
    # Both of those are symmetric about the path, because a blur is. The panel
    # is not: this is the half of the falloff that has somewhere to go. Filling
    # the ring's own polygon and softening its border by `edge` gives a mask
    # that is one well inside the path and nothing at the window's boundary, so
    # multiplying by it leaves the inward tail whole and takes the outward one
    # away before the clip can cut it.
    inside = Image.new("L", small, 0)
    ImageDraw.Draw(inside).polygon(low, fill=255)
    inside = inside.filter(ImageFilter.GaussianBlur(max(0.6, shape.edge / k)))
    profile = _normalised(
        ImageChops.multiply(ImageChops.screen(ridge, field), inside))
    profile = profile.resize(full, Image.BICUBIC)

    where = Image.new("L", small, OFF_RING)
    pen = ImageDraw.Draw(where)
    count = len(low)
    # Wider than the light reaches, so every pixel the profile lights has a
    # position to look its colour up by. A pixel the band missed keeps byte 0,
    # which the caller's table always answers with no opacity at all.
    thick = max(1, round(2 * (shape.reach + shape.core) / k))
    for index, start in enumerate(low):
        end = low[(index + 1) % count]
        pen.line([start, end], fill=1 + round((LUT_SIZE - 2) * index / count),
                 width=thick)
    where = where.resize(full, Image.NEAREST)

    made = []
    for box in boxes(width, height, band):
        cut = where.crop(box)
        # Which positions round the ring this strip actually contains, so a
        # frame can ask whether the table moved *here* without looking at the
        # pixels. Counted once, with the field it belongs to, rather than on
        # every resize: it is a histogram of thirty thousand pixels.
        counts = cut.getcolors(LUT_SIZE + 1) or []
        keys = tuple(sorted(value for _n, value in counts))
        made.append((box, profile.crop(box), cut, keys))
    return made


class _Strip:
    """One stretch of the edge: its two fields, its canvas item, its last paint."""

    __slots__ = ("box", "item", "keys", "photo", "profile", "shown", "where")

    def __init__(self, item: int):
        self.item = item
        self.box = self.profile = self.where = self.photo = self.shown = None
        self.keys: tuple = ()

    @property
    def size(self) -> tuple:
        return (self.box[2] - self.box[0], self.box[3] - self.box[1])


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

    def reshape(self, points: list[tuple], width: int, height: int,
                shape: Shape) -> None:
        """Rebuild the fields for a panel of this size.

        Everything a frame needs is derived from these two images, so this is
        where the whole cost of the effect lives: measured 3.4 ms on the default
        panel against 0.7 ms for relaying 352 lines. It is paid on every frame
        of a collapse, which is why the results are cached — the unfold asks for
        the same twenty-one sizes in the other order and pays for none of them.
        """
        band = max(1, round(shape.inset + shape.reach + shape.core + BAND_SLACK))
        key = (width, height, tuple(round(v, 2) for v in shape), band,
               len(points), tuple(points[0]))
        built = _cache.take(key)
        if built is None:
            built = _built(points, width, height, shape, band)
            _cache.keep(key, built)
        for strip, made in zip(self.strips, built, strict=False):
            box, profile, where, keys = made
            resized = strip.box is None or strip.size != profile.size
            strip.box, strip.profile, strip.where = box, profile, where
            strip.keys = keys
            # The fields under it changed, so whatever it is showing no longer
            # describes it. Left alone, a strip whose colours happened not to
            # move would keep the picture drawn for the previous panel size.
            strip.shown = None
            if resized:
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
        """What one strip looks like under `channels`, as a PIL image."""
        from PIL import Image, ImageChops

        red, green, blue, opacity = channels
        bands = [strip.where.point(red), strip.where.point(green),
                 strip.where.point(blue)]
        # The opacity a pixel finally gets is how much light reaches it *times*
        # how bright that stretch of ring is. Keeping the second factor out of
        # the colour is what lets the unlit part of the ring be transparent
        # rather than a flat patch of backdrop laid over the cover wash.
        bands.append(ImageChops.multiply(strip.profile,
                                         strip.where.point(opacity)))
        return Image.merge("RGBA", bands)

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
