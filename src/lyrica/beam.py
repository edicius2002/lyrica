"""A light that travels the panel's edge, brightened by what is playing.

The head advances at a fixed rate and the *brightness* is what the music moves.
That split is deliberate. Tying the speed to the beat needs a beat, and the
endpoint meter this is driven by has no spectrum to find one in — it reports
loudness and nothing else. A beam that always moves and breathes with the music
says everything a loudness signal honestly can; one that tried to lurch on each
kick would be guessing.

There is no line in it. The border used to be laid as segments of `create_line`
and recoloured in place — a crisp core with a wider, flatter halo under it — and
what that looks like is plastic, for a reason no number could fix. Both strokes
are solid and of fixed width, so the light's cross-section was bright, then a
plateau at 42 % of the way to the backdrop, then nothing: one step, where real
light has none. Thickening the strokes, adding ripples to the gradient,
interpolating in linear light and rounding the ramp's corner all move where the
step is and none of them removes it, because it is a property of the primitive
and not of the parameters.

So the ring is an image now, all of it, `halo.py` — no core, no arista, only a
falloff. What that costs is a per-segment colour, which is what carried the
rotation. `halo` buys it back by splitting the picture into a static field of
*how much* light reaches each pixel and a static field of *where round the ring*
each pixel is, leaving everything the music and the palette do as a lookup table
between them. Rotating the gradient is then rotating the table.

The one thing the module here still owns is that table: what colour the ring is
at each point of its circumference, and how much of it there is.
"""
import colorsys
import math

from lyrica import halo
from lyrica.glass import delta_e, rgb_of

# Spacing is deliberately uneven, and evenly spacing it is what looked wrong
# first. The default panel's perimeter is about 2900 px while a corner arc is
# 28, so at any segment count cheap enough to walk at 30 Hz a corner gets less
# than one segment and reads as a chamfer rather than a curve. The corners are
# therefore given a fixed budget of their own and the straights share what is
# left by length.
CORNER_POINTS = 9
STRAIGHT_SPACING = 16.0

# How finely the light's own path is sampled, against that spacing. Half, and
# free to be: these points are walked once, when the panel changes size, rather
# than once a frame as the line ring's were.
#
# Halving is also as far as it is worth going. The colour is looked up through
# one byte, so the circumference is quantised to 255 positions however many
# points are drawn into it — about ten pixels each round the default panel — and
# 316 points is what saturates that where the unhalved 176 would not. The
# consequence is the one quantisation left in the picture: the colour steps
# every ten pixels *along* the border, by one or two levels of 255 at the
# gradient's steepest. The line ring stepped every sixteen, by the same amount.
LIGHT_SPACING = 0.5

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

# The shape of the light across the border, in design pixels before the window
# scale, and asymmetric because the panel is.
#
# `CORE_WIDTH` is the ink drawn into the mask and `CORE_SOFT` is how far it is
# smeared — together they are the near falloff, the part that still gives the
# panel somewhere to end. `HALO_REACH` is how far the wide field carries inward
# after that. The old halo was a flat 9-pixel stroke reaching 4.5 px each way
# and stopping dead; this carries six times as far inward and never stops.
#
# `EDGE_INSET` and `EDGE_SOFT` are the other side. The window is clipped to its
# own rounded rectangle, so light that runs past the panel's edge meets a hard
# boundary — and a hard boundary across a glow is the defect all of this
# replaced. The ridge therefore sits `EDGE_INSET` inside, and the outward field
# is taken away over `EDGE_SOFT` so that what reaches the boundary is under a
# hundredth of the peak. Measured on the default panel: 3 of 255 at the edge,
# against a peak of 255 nine pixels in.
CORE_WIDTH = 3.0
CORE_SOFT = 7.0
HALO_REACH = 22.0
EDGE_INSET = 7.0
EDGE_SOFT = 3.4

# What the music does to the light's presence. The line ring pulsed its stroke
# *widths*, which an image cannot do without rebuilding the blur; scaling the
# whole ring's opacity moves the same thing, because the distance at which a
# falloff stops being visible moves with how bright it started. Quantised to
# quarters, as the widths were, so the pulse is not a new reason to repaint.
#
# The floor is high because the border's quiet state is what it protects. The
# line ring's silent core measured 93 of 255 against a backdrop of 38; the
# ridge measures 84 at 0.86 and 87 at this, which is the same light — the
# difference is that it is now four times as wide and correspondingly softer,
# so the peak has nothing to spare.
PRESENCE_FLOOR = 0.90

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

# How finely the state that decides the table is quantised. Nothing below a step
# reaches the canvas, so between them these decide how often the ring is asked
# to repaint — measured 42 strip repaints a second for the shine in silence and
# 59 with a beat under it, against a ceiling of 60 that `halo.PER_CALL` sets
# whatever these say.
#
# The phase counts differ by style because what a step *does* differs. The shine
# rotates a smooth gradient, so a step moves every point on the ring by a
# fraction of the swing — 128 of them puts each step under two entries of the
# 96-step gradient, which is below what an eye finds in a soft glow. The comet
# is a head with a place, and a step moves it: 256 puts it 11 px along the
# default panel's perimeter, against the 8 px a frame it moves anyway.
PHASE_STEPS = {COMET: 256, SHINE: 128, AURORA: 128}

# And the same for what the music does. The level is the one that moves every
# frame, so it is the one that decides whether an idle beam is idle: 24 bands
# over the shine's own strength range is a shade under three of the gradient's
# 96 entries a band, which is a step in a soft glow rather than a change.
LEVEL_STEPS = 24
DYNAMICS_STEPS = 16


def _rounded_path(width: int, height: int, radius: int, inset: float,
                  spacing: float = STRAIGHT_SPACING) -> list[tuple[float, float]]:
    """Points around a rounded rectangle, clockwise from the top left corner.

    Corners get a fixed number of points and straights get one every
    `spacing`, so a curve is always drawn as a curve however long the
    edges beside it are.

    `spacing` is in physical pixels and the caller scales it, so that the
    density is constant in design units rather than on the glass. Left at the
    unscaled default a Ctrl+Alt+plus bought points nobody asked for: the default
    panel went from 176 to 316 at 2.0, and every one of them is a line drawn
    into the light's fields when the panel changes size.
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


def _lerp(a: tuple, b: tuple, amount: float) -> tuple:
    amount = max(0.0, min(1.0, amount))
    return tuple(x + (y - x) * amount for x, y in zip(a, b, strict=True))


def _beam_colour(palette) -> tuple:
    """The song's colour, moved only as far as visibility against the wash needs.

    Palette roles are solved for text contrast, so a cover whose accent sits
    close to its own wash can hand the border a colour that vanishes into it.
    The correction walks toward the already-safe sung role and stops at the
    first step that clears the floor, so the hue survives and only a
    disappearing accent is changed.
    """
    dark = tuple(palette.backdrop)
    mid = rgb_of(palette.beam)
    if delta_e(dark, mid) >= MIN_BEAM_DE:
        return mid
    head = rgb_of(palette.sung)
    for k in range(1, 21):
        candidate = _lerp(mid, head, k / 20)
        if delta_e(dark, candidate) >= MIN_BEAM_DE:
            return candidate
    return head


def _ramp(palette, steps: int = GRADIENT_STEPS) -> list[tuple[tuple, float]]:
    """From invisible, through the song's colour, to the head — as light.

    Each entry is a colour and how much of it there is, and the split matters.
    The line ring had to say the same thing in one opaque colour, so "invisible"
    was spelled as *the backdrop colour, painted*: a flat patch laid over a
    textured cover wash, which is only invisible where the wash happens to agree
    with the palette's idea of it. Here the dim end of the ramp is the song's
    colour at no opacity, so the wash comes through it untouched.

    The bright end stays at full opacity and moves in hue instead, up to the
    sung colour, because that end is a light and not an absence of one.
    """
    mid = _beam_colour(palette)
    head = rgb_of(palette.sung)
    out = []
    for i in range(steps):
        t = i / (steps - 1)
        if t < COLOUR_STOP:
            out.append((mid, t / COLOUR_STOP))
        else:
            k = (t - COLOUR_STOP) / (1 - COLOUR_STOP)
            out.append((_lerp(mid, head, k), 1.0))
    return out


def _aurora_colours(palette, steps: int = GRADIENT_STEPS) -> list[tuple]:
    """Neighbouring hues from the song colour, closed into a seamless ring."""
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
    """The ring of light, and the state that decides what colour it is where."""

    def __init__(self, canvas, width: int, height: int, radius: int,
                 scale: float = 1.0, style: str = COMET,
                 intensity: float = 1.0):
        self.canvas = canvas
        self.style = style
        self.intensity = max(0.5, min(2.0, intensity))
        self._phase = 0.0
        self._points: list[tuple] = []
        self._ramp: list[tuple] = []
        self._aurora: list[tuple] = []
        self._palette = None
        self._state = None
        self._tables: tuple = ()
        self.light = halo.Ring(canvas)
        self.set_scale(scale)
        self.reshape(width, height, radius)

    def set_scale(self, scale: float) -> None:
        """Re-derive the light's shape for a window whose scale changed.

        Deliberately not folded into `reshape`, and not called from it: only the
        caller knows whether the scale moved. `reshape` runs once a frame
        through the collapse animation, where the geometry changes every frame
        and the scale never does.

        Until this existed the widths were fixed at construction, so after a
        Ctrl+Alt+plus the ring carried the new geometry at the old thickness —
        half as thick as it should be at 2.0, nearly twice at 0.6 — and since
        the path is inset by a share of the reach, the ring also sat wrong
        against a corner radius that had scaled properly.
        """
        self.scale = scale
        weight = scale * self.intensity
        self.shape = halo.Shape(inset=max(2.0, EDGE_INSET * weight),
                                core=max(1.0, CORE_WIDTH * weight),
                                ridge=max(1.0, CORE_SOFT * weight),
                                reach=max(2.0, HALO_REACH * weight),
                                edge=max(0.8, EDGE_SOFT * weight))

    def reshape(self, width: int, height: int, radius: int) -> None:
        """Lay the light out again, for a window that changed size.

        The whole cost of the border now lives here rather than in `advance`,
        which is the trade `halo` is built on: the blur, the two fields and the
        four strips are derived once per size and then only looked up.

        That is the one place this variant is dearer than the line ring, and it
        is paid on every frame of a collapse. Measured over the twenty-one
        frames of the default panel folding to compact, reshape plus the frame's
        own advance: 4.19 ms a frame against 1.78 for relaying and recolouring
        352 line items. The unfold asks for the same twenty-one sizes in the
        other order, so `halo`'s cache answers all of it and the same measurement
        comes back 1.11 ms — cheaper than the ring it replaced, in the direction
        that is asked for twice.
        """
        self._points = _rounded_path(width, height, radius, self.shape.inset,
                                     STRAIGHT_SPACING * self.scale * LIGHT_SPACING)
        self.light.reshape(self._points, width, height, self.shape)

    def destroy(self) -> None:
        self.light.destroy()
        self._points = []

    def advance(self, dt: float, music, palette) -> None:
        """Move the phase and, if anything visible moved with it, repaint.

        `music` carries the level and, for the shine, what the music has been
        doing around it. The ramp is derived from the palette, so the beam wears
        the cover's colour, and rebuilt only when the palette changes.
        """
        if not self.light.strips:
            return
        if palette is not self._palette:
            self._palette = palette
            self._ramp = _ramp(palette)
            self._aurora = _aurora_colours(palette)
            self._state = None

        level = max(0.0, min(1.0, getattr(music, "level", music)))
        dynamics = max(0.0, min(1.0, getattr(music, "dynamics", 0.0)))
        rate = max(0.0, min(1.0, getattr(music, "rate", 0.0)))

        if self.style == AURORA:
            period = AURORA_PERIOD_S / (1.0 + SHINE_SPEED_GAIN * rate)
        elif self.style == SHINE:
            # Busier music turns it faster; nothing here claims to know a beat.
            period = SHINE_PERIOD_S / (1.0 + SHINE_SPEED_GAIN * rate)
        else:
            period = PERIOD_S
        self._phase = (self._phase + dt / period) % 1.0

        # The phase keeps moving continuously and only the *table* is
        # quantised, so the rate still reaches the rotation between two steps
        # of it — a beam whose phase itself were rounded would stand still
        # under any dt small enough.
        steps = PHASE_STEPS.get(self.style, PHASE_STEPS[SHINE])
        state = (int(self._phase * steps) % steps, round(level * LEVEL_STEPS),
                 round(dynamics * DYNAMICS_STEPS))
        if state != self._state:
            self._state = state
            self._tables = self._lit(state[0] / steps, level, dynamics)
        self.light.paint(self._tables)

    def _lit(self, phase: float, level: float, dynamics: float) -> tuple:
        """Red, green, blue and opacity round the ring, as four byte tables.

        Indexed by how far round the circumference a pixel is, which is what
        `halo` stores per pixel and never has to store again. Entry 0 is the
        byte that field keeps for "no ring near here", so it is left at no
        opacity whatever the style decides — a pixel the band missed is
        invisible rather than wrongly coloured.
        """
        size = halo.LUT_SIZE
        red, green, blue = [0] * size, [0] * size, [0] * size
        opacity = [0] * size
        # Quantised to quarters, as the line ring's stroke widths were, so the
        # pulse cannot be a reason to repaint that the phase was not already.
        pulse = round((level * 0.7 + dynamics * 0.3) * 4) / 4
        presence = PRESENCE_FLOOR + (1.0 - PRESENCE_FLOOR) * pulse
        span = size - 1                       # positions 1 .. size - 1
        top = len(self._ramp) - 1

        if self.style == AURORA:
            wheel = len(self._aurora)
            strength = min(1.0, (0.56 + 0.44 * level) * self.intensity)
        elif self.style == SHINE:
            strength = SHINE_FLOOR + (1.0 - SHINE_FLOOR) * level
            swing = SHINE_SWING_FLAT + (SHINE_SWING_OPEN - SHINE_SWING_FLAT) * dynamics
            base = 1.0 - swing
        else:
            strength = FLOOR + GAIN * level

        for index in range(1, size):
            turn = (index - 1) / span
            if self.style == AURORA:
                colour = self._aurora[int(((turn + phase) % 1.0) * wheel) % wheel]
                # A cosine over the ring's own circumference, so the wave is
                # carried round by the hue rather than standing still under it.
                amount = strength * (0.72 + 0.28 * math.cos(2 * math.pi * turn))
            elif self.style == SHINE:
                # A cosine rather than a sawtooth: the ring closes on itself, so
                # a gradient that ran end to end would show a seam where it
                # wrapped. This one has no ends.
                wave = 0.5 + 0.5 * math.cos(
                    2 * math.pi * (turn * SHINE_CYCLES + phase))
                colour, amount = self._ramp[
                    int(top * (base + swing * wave) * strength)]
            else:
                # Distance behind the head, once round the ring.
                behind = (phase - turn) % 1.0
                glow = 0.0 if behind > TAIL else (1.0 - behind / TAIL) ** 2
                colour, amount = self._ramp[int(top * glow * strength)]
            red[index] = min(255, max(0, round(colour[0])))
            green[index] = min(255, max(0, round(colour[1])))
            blue[index] = min(255, max(0, round(colour[2])))
            opacity[index] = min(255, max(0, round(255 * amount * presence)))
        return (red, green, blue, opacity)
