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
import colorsys
import math

from lyrica.glass import delta_e, hex_of, rgb_of

# Spacing is deliberately uneven, and evenly spacing it is what looked wrong
# first. The default panel's perimeter is about 2900 px while a corner arc is
# 28, so at any segment count cheap enough to recolour at 30 Hz a corner gets
# less than one segment and reads as a chamfer rather than a curve. The corners
# are therefore given a fixed budget of their own and the straights share what
# is left by length.
CORNER_POINTS = 9
STRAIGHT_SPACING = 16.0

# Two ways to light the edge.
#
# COMET is a short bright head travelling a dark ring — movement you follow.
# SHINE lights the whole border at once and rotates a gradient through it, so
# every edge is lit all the time and what moves is the colour rather than a
# spot. The second is the quieter of the two to sit beside while reading.
COMET, SHINE, AURORA = "comet", "shine", "aurora"

# How long a circuit takes. The shine turns more slowly: a gradient sweeping the
# whole border at the comet's rate reads as a wash sloshing about, where the
# comet at the shine's rate barely appears to move at all.
PERIOD_S = 6.0
SHINE_PERIOD_S = 11.0
AURORA_PERIOD_S = 8.0

# How far apart the two ends of the shine's gradient sit, in whole cycles round
# the ring. One, so opposite edges are opposite colours and the seam where the
# gradient closes is exactly where it began.
SHINE_CYCLES = 1.0

# The shine never drops to the backdrop — that is the point of it — so its floor
# is what keeps the dimmest part of the border plainly lit.
SHINE_FLOOR = 0.34

# How far the gradient swings between its light and dark parts, and what the
# music's own dynamics do to that. A compressed wall of sound gets an almost
# even border; something with air between its hits gets a border with the same
# air in it. This is where the *style* of the music shows, and it needs no beat
# to be found — measured at 0.05 for a heavily compressed master against 0.94
# for an open one.
SHINE_SWING_FLAT = 0.16
SHINE_SWING_OPEN = 0.62

# How much busier music turns the gradient faster. Driven by the onset rate,
# which is honest, rather than by a tempo, which is not: which multiple of the
# beat that rate counts cannot be recovered from loudness, so a beam that spun
# once per beat would spin at half or double speed about half the time. A beam
# that is merely *more agitated* when the music is cannot be wrong that way.
SHINE_SPEED_GAIN = 0.55

# The old ring was a single two-pixel line. It moved, but against a textured
# artwork wash it had no spatial presence. A crisp core carries the colour and
# a quieter wide line underneath gives it a field without covering the words.
CORE_WIDTH = 3.0
HALO_WIDTH = 9.0
HALO_KEEP = 0.42

# Palette roles guarantee text contrast, not border contrast. The beam gets its
# own perceptual floor so a cover whose accent resembles its wash cannot make
# the ring disappear.
MIN_BEAM_DE = 18.0

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
GRADIENT_STEPS = 96


def _rounded_path(width: int, height: int, radius: int, inset: float,
                  spacing: float = STRAIGHT_SPACING) -> list[tuple[float, float]]:
    """Points around a rounded rectangle, clockwise from the top left corner.

    Corners get a fixed number of points and straights get one every
    `spacing`, so a curve is always drawn as a curve however long the
    edges beside it are.

    `spacing` is in physical pixels and the caller scales it, so that the
    density is constant in design units rather than on the glass. Left at the
    unscaled default a Ctrl+Alt+plus bought segments nobody asked for and paid
    for them on every frame for the rest of the session: the default panel went
    from 176 segments to 316 at 2.0, and `advance` walks every one of them at
    60 Hz whether or not the window is moving — measured 5.3 ms a frame for the
    comet against 3.3 once the spacing scales, on a 16 ms budget.

    It cuts the other way at 0.6, and deliberately: that panel goes from 120
    segments to the same 176, taking the comet from 2.0 ms a frame to 2.9 —
    which is what the default panel already costs. Constant density is the
    point of it rather than a smaller number in every direction. A
    small window was drawing a *coarser* ring than the default one in the units
    the design is written in, and the corners already worked this way — they
    have a fixed point budget of their own and never read the spacing at all.
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
        steps = max(1, int(length / spacing))
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
    # The beam's own mid, not the unsung line's. They were the same number until
    # the unlit text was brightened for legibility, at which point the ring lost
    # the dark ground the head needs to travel through.
    head = rgb_of(palette.sung)
    mid = rgb_of(palette.beam)
    if delta_e(dark, mid) < MIN_BEAM_DE:
        # Move only as far toward the already-safe sung role as visibility
        # requires. The hue survives; only a disappearing accent is corrected.
        for k in range(1, 21):
            amount = k / 20
            candidate = tuple(a + (b - a) * amount
                              for a, b in zip(mid, head, strict=True))
            if delta_e(dark, candidate) >= MIN_BEAM_DE:
                mid = candidate
                break
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


def _lerp(a: tuple, b: tuple, amount: float) -> tuple:
    amount = max(0.0, min(1.0, amount))
    return tuple(x + (y - x) * amount for x, y in zip(a, b, strict=True))


def _mix(a: tuple, b: tuple, amount: float) -> str:
    return hex_of(_lerp(a, b, amount))


def _aurora_colours(palette, steps: int = GRADIENT_STEPS) -> list[tuple]:
    """Neighbouring hues from the song colour, closed into a seamless ring.

    Kept as RGB tuples rather than hex. These are never drawn as they stand —
    every frame mixes them against the backdrop — so formatting them here only
    bought a `rgb_of` per segment per frame to parse them straight back, which
    is 210 round trips a frame on the default panel for no gain.
    """
    base = tuple(channel / 255 for channel in rgb_of(palette.beam))
    hue, saturation, value = colorsys.rgb_to_hsv(*base)
    saturation = max(0.28, saturation)
    value = max(0.60, value)
    anchors = [colorsys.hsv_to_rgb((hue + shift) % 1.0, saturation, value)
               for shift in (-0.12, 0.0, 0.12)]
    anchors = [tuple(channel * 255 for channel in colour) for colour in anchors]
    out = []
    for index in range(steps):
        turn = index / steps * len(anchors)
        left = int(turn) % len(anchors)
        amount = turn - int(turn)
        out.append(_lerp(anchors[left], anchors[(left + 1) % len(anchors)],
                         amount))
    return out


class Beam:
    """The ring of segments, and the state to colour them."""

    def __init__(self, canvas, width: int, height: int, radius: int,
                 scale: float = 1.0, style: str = COMET,
                 intensity: float = 1.0):
        self.canvas = canvas
        self.style = style
        self.intensity = max(0.5, min(2.0, intensity))
        self._phase = 0.0
        self._items: list[int] = []
        self._halo_items: list[int] = []
        self._shades: list[str] = []
        self._halo_shades: list[str] = []
        self._core_tag = f"beam-core-{id(self)}"
        self._halo_tag = f"beam-halo-{id(self)}"
        self._gradient: list[str] = []
        self._aurora: list[tuple] = []
        self._halos: dict[str, str] = {}
        self._palette = None
        self.set_scale(scale)
        self.reshape(width, height, radius)

    def set_scale(self, scale: float) -> None:
        """Re-derive the line widths for a window whose scale changed.

        Deliberately not folded into `reshape`, and not called from it: only
        the caller knows whether the scale moved. `reshape` runs once a frame
        through the collapse animation, where the geometry changes every frame
        and the scale never does.

        Until this existed the widths were fixed at construction, so after a
        Ctrl+Alt+plus the ring carried the new geometry at the old thickness —
        half as thick as it should be at 2.0, nearly twice at 0.6 — and since
        `reshape` insets the path by `_halo_width / 2`, the ring also sat wrong
        against a corner radius that had scaled properly.
        """
        self.scale = scale
        self._core_width = max(1.0, CORE_WIDTH * scale * self.intensity)
        self._halo_width = max(self._core_width, HALO_WIDTH * scale * self.intensity)
        # The pulsed width only reaches the canvas when the level crosses a
        # quartile, so without this the new base widths would wait for the
        # music to happen to change band before showing up.
        self._width_state = None

    def reshape(self, width: int, height: int, radius: int) -> None:
        """Lay the ring out again, for a window that changed size.

        A pool, not a rebuild. The geometry is a pure function of the size, but
        the *number* of segments barely moves between consecutive frames of a
        collapse — measured 176, 176, 174, 172, 172, 168 … 114 across the
        twenty-one frames of the default panel folding to compact. Tearing the
        ring down and laying it again spent 352 `delete` plus 352 `create_line`
        on every one of those frames to end up with items in the same places;
        moving the ones already there costs 352 `coords` and creates or deletes
        only the handful the count actually moved by — four `create_line` on a
        frame of the unfold, none at all on a frame of the collapse. Measured
        704 canvas calls a frame down to 352, and 3.0 ms a frame down to 0.7 on
        this machine, in both directions, against a resize budget of 16 ms that
        the neighbouring work had already taken to 30.8.
        """
        points = _rounded_path(width, height, radius, self._halo_width / 2,
                               STRAIGHT_SPACING * self.scale)
        segments = [(start, points[(i + 1) % len(points)])
                    for i, start in enumerate(points)]
        kept = min(len(self._items), len(segments))
        for index in range(kept):
            (x0, y0), (x1, y1) = segments[index]
            self.canvas.coords(self._halo_items[index], x0, y0, x1, y1)
            self.canvas.coords(self._items[index], x0, y0, x1, y1)
        # `_shades` and `_halo_shades` are deliberately left as they are for
        # these. Since `f1d4928` they are `_paint`'s only proof of what the
        # canvas is actually carrying, and a moved item keeps its fill — so the
        # entry still describes it truthfully, which is the only property the
        # early return needs. Reseeding them with the sentinel would not be
        # wrong, just wasteful: it would force all 352 items to be rewritten on
        # the very next frame, which is the cost this pool exists to avoid.
        # Whether the colour is still *appropriate* is a different question and
        # not this one's: `advance` recomputes every segment's shade from
        # `i / count` regardless, and writes wherever the answer differs, so a
        # ring whose count changed repaints exactly the segments that moved
        # through the gradient.
        for index in range(kept, len(self._items)):
            self.canvas.delete(self._halo_items[index])
            self.canvas.delete(self._items[index])
        del self._halo_items[kept:], self._halo_shades[kept:]
        del self._items[kept:], self._shades[kept:]

        # Every halo first, then every core. Interleaving them lets the wide
        # halo of segment N+1 cover the end of core N, turning a continuous
        # gradient into a dashed line at every join.
        for start, end in segments[kept:]:
            halo = self.canvas.create_line(*start, *end, width=self._halo_width,
                                           fill="#000000", capstyle="round",
                                           tags=(self._halo_tag,))
            self._halo_items.append(halo)
            # The empty string, not the fill just given, because `_paint` now
            # takes an unchanged core shade as proof the halo is unchanged too.
            # Seeding both with "#000000" made that a lie for any backdrop that
            # is not black: the core would match on the first frame and the
            # halo would be left at the creation fill for good.
            self._halo_shades.append("")
        for start, end in segments[kept:]:
            core = self.canvas.create_line(*start, *end, width=self._core_width,
                                           fill="#000000", capstyle="round",
                                           tags=(self._core_tag,))
            self._items.append(core)
            self._shades.append("")
        if len(segments) > kept:
            # New items land on top of the display list, so a ring that grew
            # would have its fresh halos sitting over the cores that were
            # already there — the dashed-join defect above, but only on the
            # part of the ring that is new. One tag-wide lower puts the whole
            # halo group back under the whole core group and preserves the
            # order within each, so it is enough to say it once.
            self.canvas.tag_lower(self._halo_tag, self._core_tag)
            # Fresh segments are created at the base width, so the pulse has to
            # be reasserted; otherwise a ring rebuilt mid-song stays unpulsed
            # until the level next crosses a quartile. Only when there are
            # fresh segments, though: the reused ones are still carrying the
            # width the last frame gave them, and reasserting costs two
            # tag-wide `itemconfigure`s that touch every item on the ring.
            self._width_state = None

    def destroy(self) -> None:
        for item in (*self._items, *self._halo_items):
            self.canvas.delete(item)
        self._items.clear()
        self._halo_items.clear()
        self._shades.clear()
        self._halo_shades.clear()

    def advance(self, dt: float, music, palette) -> None:
        """Move the head and recolour the ring.

        `music` carries the level and, for the shine, what the music has been
        doing around it. The gradient is derived from the palette, so the beam
        wears the cover's colour, and rebuilt only when the palette changes.
        """
        if not self._items:
            return
        if palette is not self._palette:
            self._palette = palette
            self._gradient = _gradient(palette)
            self._aurora = _aurora_colours(palette)
            # The halo memo is keyed on the core shade alone, so it is only
            # valid for one backdrop. It dies with the gradient it was built
            # against.
            self._halos = {}
            self._shades = [""] * len(self._items)   # force a full repaint
            self._halo_shades = [""] * len(self._items)

        level = max(0.0, min(1.0, getattr(music, "level", music)))
        dynamics = max(0.0, min(1.0, getattr(music, "dynamics", 0.0)))
        rate = max(0.0, min(1.0, getattr(music, "rate", 0.0)))
        top = len(self._gradient) - 1
        count = len(self._items)

        width_state = round((level * 0.7 + dynamics * 0.3) * 4)
        if width_state != self._width_state:
            self._width_state = width_state
            pulse = width_state / 4
            self.canvas.itemconfigure(
                self._core_tag, width=self._core_width * (1.0 + 0.22 * pulse))
            self.canvas.itemconfigure(
                self._halo_tag, width=self._halo_width * (0.82 + 0.38 * pulse))

        if self.style == AURORA:
            period = AURORA_PERIOD_S / (1.0 + SHINE_SPEED_GAIN * rate)
            self._phase = (self._phase + dt / period) % 1.0
            strength = min(1.0, (0.56 + 0.44 * level) * self.intensity)
            for i, item in enumerate(self._items):
                turn = (i / count + self._phase) % 1.0
                colour = self._aurora[int(turn * len(self._aurora))
                                      % len(self._aurora)]
                wave = 0.72 + 0.28 * math.cos(2 * math.pi * (turn - self._phase))
                shade = _mix(tuple(palette.backdrop), colour, strength * wave)
                self._paint(i, item, shade, palette)
            return

        if self.style == SHINE:
            # Busier music turns it faster; nothing here claims to know a beat.
            period = SHINE_PERIOD_S / (1.0 + SHINE_SPEED_GAIN * rate)
            self._phase = (self._phase + dt / period) % 1.0
            strength = SHINE_FLOOR + (1.0 - SHINE_FLOOR) * level
            swing = SHINE_SWING_FLAT + (SHINE_SWING_OPEN - SHINE_SWING_FLAT) * dynamics
            base = 1.0 - swing
            for i, item in enumerate(self._items):
                # A cosine rather than a sawtooth: the ring closes on itself, so
                # a gradient that ran end to end would show a seam where it
                # wrapped. This one has no ends.
                turn = (i / count) * SHINE_CYCLES + self._phase
                wave = 0.5 + 0.5 * math.cos(2 * math.pi * turn)
                shade = self._gradient[int(top * (base + swing * wave) * strength)]
                self._paint(i, item, shade, palette)
            return

        self._phase = (self._phase + dt / PERIOD_S) % 1.0

        strength = FLOOR + GAIN * level
        for i, item in enumerate(self._items):
            # Distance behind the head, once round the ring.
            behind = (self._phase - i / count) % 1.0
            glow = 0.0 if behind > TAIL else (1.0 - behind / TAIL) ** 2
            shade = self._gradient[int(top * glow * strength)]
            self._paint(i, item, shade, palette)

    def _paint(self, index: int, item: int, shade: str, palette) -> None:
        """Paint the crisp ring and its quieter field only when either changed."""
        if shade == self._shades[index]:
            # The halo is a pure function of the core shade and the backdrop,
            # and a backdrop only changes with the palette, which forces a full
            # repaint above — so an unchanged shade cannot want a changed halo.
            # The Tcl writes were already guarded; the *arithmetic* was not, and
            # a comet leaves 86% of the ring alone (TAIL = 0.14). Those segments
            # each paid rgb_of + a three-channel mix + a str.format every frame
            # to arrive back at the string already on the canvas: about 10,500
            # discarded formats a second at 60 Hz on the default panel, and it
            # grows with the perimeter.
            return
        self.canvas.itemconfigure(item, fill=shade)
        self._shades[index] = shade
        halo = self._halos.get(shade)
        if halo is None:
            # Bounded by construction for the comet and the shine, which only
            # ever hand over one of the gradient's 96 entries — measured 95 and
            # 69 distinct shades across five minutes at 60 Hz. The aurora mixes
            # its own shade per segment and per level and has no such ceiling:
            # 13,307 over the same five minutes. So the memo is dropped whole
            # rather than left to creep. A clear every couple of minutes costs
            # the aurora a few hundred rebuilt entries and nothing else; the
            # other two styles never reach it.
            if len(self._halos) > 4096:
                self._halos.clear()
            halo = self._halos[shade] = _mix(tuple(palette.backdrop),
                                             rgb_of(shade), HALO_KEEP)
        if halo != self._halo_shades[index]:
            self.canvas.itemconfigure(self._halo_items[index], fill=halo)
            self._halo_shades[index] = halo
