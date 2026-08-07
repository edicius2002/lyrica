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

PLATE_FLOOR = 18.0
PLATE_GAIN = 0.388
P_BRIGHT = (117, 117, 118)      # what a white desktop puts under everything
HEADROOM = 255 - 117            # 138: the largest channel that never clamps

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
