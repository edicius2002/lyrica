"""The measured composition law for glass mode, and contrast under it.

Measured on Windows 10 19045 with the acrylic tint this app uses:

    desktop   0 -> plate (18, 18, 19)
    desktop 128 -> plate (67, 67, 69)
    desktop 255 -> plate (117, 117, 118)

Linear to within a unit, per channel: ``plate = 18 + 0.388 * desktop``. The
blend is additive with a clamp, confirmed both ways: a red surface over a plate
of 117 lands on 255, and a black surface over a plate of 18 lands on 17.

The consequence that matters is not "black is invisible". Text is drawn over
the backdrop image *inside* the surface, where composition is ordinary
replacement; only the finished surface is added to the plate. So the plate is a
pedestal common to the text and its background:

    screen_text = min(255, plate + text)
    screen_bg   = min(255, plate + backdrop)

While neither clamps, the difference is exactly ``text - backdrop`` — the
desktop cancels out entirely. Legibility is lost only where the clamp bites,
and it bites from above. That gives one hard budget:

    HEADROOM = 255 - 117 = 138

A colour whose largest channel is at or below 138 keeps its full separation and
its full hue over every possible desktop. Anything brighter is progressively
flattened toward white as the desktop brightens — which is why the sung word
and the unsung tail can end up identical on a white background.
"""
import colorsys
from dataclasses import dataclass

PLATE_FLOOR = 18.0
PLATE_GAIN = 0.388
P_BRIGHT = (117, 117, 118)      # what a white desktop puts under everything
HEADROOM = 255 - 117            # 138: the largest channel that never clamps


@dataclass(frozen=True)
class Composition:
    """How a drawn surface reaches the screen, for one window mode.

    Both modes this app has are the same shape — a pedestal from the desktop
    plus a share of what was drawn — and differ only in their constants:

        screen = min(255, gain * surface + floor + slope * desktop)

    Acrylic passes the whole surface through (gain 1) on top of a bright plate.
    A uniformly alpha-blended panel passes only `a` of it, over a pedestal of
    `1 - a` of the desktop. Writing it once means the palette solver does not
    care which one it is running against.
    """
    name: str
    gain: float
    floor: float
    slope: float
    # True where the finished surface *adds* to what is behind it rather than
    # replacing it — which is what makes pure black invisible, brightness double
    # as opacity, and an offset copy read as light rather than as a smear.
    additive: bool = False

    def pedestal(self, desktop: tuple) -> tuple:
        return tuple(min(255, round(self.floor + self.slope * d)) for d in desktop)

    def compose(self, desktop: tuple, surface: tuple) -> tuple:
        return tuple(min(255, round(self.gain * s + self.floor + self.slope * d))
                     for s, d in zip(surface, desktop, strict=True))

    @property
    def headroom(self) -> float:
        """The largest channel that still arrives without being flattened.

        This is a physical limit, not a design one: above it the clamp eats
        chroma. Where it lands at 255 there is effectively no ceiling at all.
        """
        return min(255.0, (255 - (self.floor + self.slope * 255)) / self.gain)


# Measured on this machine; the numbers in the docstring above.
ACRYLIC = Composition("acrylic", 1.0, PLATE_FLOOR, PLATE_GAIN, additive=True)

# 0.75 is the most translucent value that clears 3:1 at all, and 0.82 the most
# translucent one that clears it with margin — but "as translucent as legibility
# allows" turned out to be more translucent than anyone wanted to look at, with
# the desktop reading clearly through the panel. 0.90 measures 4.14:1 and puts
# 25 of a white desktop through instead of 46. `LYRICA_OPACITY` moves it, since
# where in that range to sit is taste rather than measurement.
#
# Unlike acrylic this barely clamps — the pedestal tops out at 46, so a surface
# has 255 of room — which is why the sung word and the unsung tail keep their
# separation over a white desktop here without having to buy it back.
PANEL_ALPHA = 0.90


def alpha_panel(alpha: float = PANEL_ALPHA) -> Composition:
    return Composition("panel", alpha, 0.0, 1.0 - alpha)


PANEL = alpha_panel()

# Blue-yellow separation is the weakest the eye has, and weaker still at the
# spatial frequency of a glyph. Measured consequence: a yellow tail scoring 20
# by the unweighted formula still read as one flat bar, where a rose one at the
# same 20 was obvious.
B_WEIGHT = 0.5


def plate(desktop: tuple) -> tuple:
    return tuple(min(255, round(PLATE_FLOOR + PLATE_GAIN * d)) for d in desktop)


def screen(p: tuple, surface: tuple) -> tuple:
    """What the compositor actually puts on the glass."""
    return tuple(min(255, p[i] + surface[i]) for i in range(3))


def _lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _byte(v: float) -> int:
    """Linear light back to a byte, the inverse of `_lin`."""
    v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    return max(0, min(255, round(v * 255)))


def blend_light(a: tuple, b: tuple, amount: float) -> tuple:
    """Mix two colours the way light mixes, rather than the way bytes do.

    sRGB is not a quantity of light; it is light bent through a gamma curve so
    that dark tones get more of the byte range than their share of photons.
    Averaging two of them therefore lands nowhere physical: it under-represents
    the light through the middle of a ramp, which comes out as the muddy,
    over-saturated midtones that make a ramp from a dark ground to a lit colour
    read as coloured plastic instead of as something lit.

    Undoing the curve, mixing, and putting it back is what light does. Measured
    from (16,16,20) to (235,180,120): a quarter of the way along, bytes give
    (71,57,45) and light gives (127,96,65). The ramp reaches its colour sooner
    and spends far less of its length in the dirty part.
    """
    return tuple(_byte(_lin(x) + (_lin(y) - _lin(x)) * amount)
                 for x, y in zip(a, b, strict=True))


def luminance(rgb: tuple) -> float:
    return 0.2126 * _lin(rgb[0]) + 0.7152 * _lin(rgb[1]) + 0.0722 * _lin(rgb[2])


def contrast(a: tuple, b: tuple) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _f(t: float) -> float:
    return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116


def lab(rgb: tuple) -> tuple:
    r, g, b = (_lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    fx, fy, fz = _f(x), _f(y), _f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: tuple, b: tuple) -> float:
    """Perceived difference, with blue-yellow discounted.

    Contrast ratio cannot see this: over a bright desktop two colours that clamp
    to the same white have a ratio of 1.00 while being plainly different when
    the desktop is dark. Chroma is the half of the design that survives, so the
    measurement has to be able to see it.
    """
    la, aa, ba = lab(a)
    lb, ab, bb = lab(b)
    return ((la - lb) ** 2 + (aa - ab) ** 2 + (B_WEIGHT * (ba - bb)) ** 2) ** 0.5


def tint(level: float, hue: float, sat: float) -> tuple:
    """A colour whose *largest channel* is `level`.

    Parameterised by the largest channel rather than by luminance because the
    largest channel is what clamps: level <= 138 is the promise that this colour
    reaches the screen intact on any desktop.
    """
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360.0, 0.5, 1.0)
    mixed = [255 - sat * (255 - c * 255) for c in (r, g, b)]
    k = level / 255.0
    return tuple(max(0, min(255, round(c * k))) for c in mixed)


def chroma(rgb: tuple) -> int:
    return max(rgb) - min(rgb)


def hex_of(rgb: tuple) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def rgb_of(value: str) -> tuple:
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
