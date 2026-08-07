"""The colour a cover reads as, extracted from its bytes.

Quantisation alone answers the wrong question. "Which colour covers the most
pixels" is nearly always a black border, a white sleeve or a grey sky — of the
sixteen covers measured while designing this, six return a near-black as their
most populous swatch. What a song's colour *means* is "which hue does this
cover read as", which needs the swatches ranked, not merely counted.

So: quantise cheaply to sixteen swatches with their populations, score each by
how much colour it carries and how much of the cover it is, and take the best.
Neutral covers score nothing and say so, which is the honest answer for a
black-and-white sleeve — the palette then stays grey rather than inventing a hue
out of JPEG noise.

Measured at 1.75 ms mean over sixteen 600x600 JPEGs, so it can run inline when
a cover arrives rather than needing a thread.
"""
import colorsys
import io
import logging
from dataclasses import dataclass

from PIL import Image

logger = logging.getLogger(__name__)

# Decoded straight to a thumbnail through the JPEG DCT scaler, so the full
# bitmap is never built — which is where most of the time would otherwise go.
SAMPLE = 96
SWATCHES = 16

# Below this saturation a swatch has no hue worth reporting: it is a grey with a
# cast, and treating a cast as the song's colour is how a black sleeve ends up
# faintly green.
MIN_SAT = 0.15
# Two gates that sound like one but measure different things. Below MIN_SIGNAL
# there is not enough light in the swatch for its hue to be anything but JPEG
# noise in the shadows, and that is a question about the largest channel. Above
# MAX_LIGHT the swatch is paper, and that is a question about lightness — asking
# it of the largest channel instead rejects every fully saturated colour there
# is, because pure red's largest channel reads 1.0 exactly as white's does.
MIN_SIGNAL = 0.10
MAX_LIGHT = 0.97
# How much of the cover must carry colour before the track counts as coloured.
COLOURED_FRACTION = 0.06

# Two hues have to differ by this much before the second is worth keeping as an
# accent rather than being the same colour twice.
ACCENT_SEPARATION = 40.0


@dataclass(frozen=True)
class SongColour:
    hue: float            # 0..360
    sat: float            # 0..1, of the winning swatch
    weight: float         # 0..1, how much of the cover carries colour at all
    accent_hue: float     # a second hue, or == hue when the cover has only one
    neutral: bool         # True when there is no colour worth using
    dominant: tuple       # the most populous swatch, for reference


NEUTRAL = SongColour(0.0, 0.0, 0.0, 0.0, True, (0, 0, 0))


def _thumb(data: bytes, box: int = SAMPLE) -> Image.Image:
    src = Image.open(io.BytesIO(data))
    src.draft("RGB", (box, box))          # free on JPEG, a no-op elsewhere
    im = src.convert("RGB")
    im.thumbnail((box, box), Image.BILINEAR)
    return im


def _swatches(im: Image.Image, k: int = SWATCHES) -> list:
    pal = im.quantize(colors=k, method=Image.FASTOCTREE)
    table = pal.getpalette()
    total = im.width * im.height
    out = [(count / total, tuple(table[i * 3:i * 3 + 3]))
           for count, i in pal.getcolors()]
    out.sort(reverse=True)
    return out


def _score(share: float, rgb: tuple) -> float:
    _h, lightness, sat = colorsys.rgb_to_hls(*[c / 255 for c in rgb])
    if sat < MIN_SAT or max(rgb) / 255.0 < MIN_SIGNAL or lightness > MAX_LIGHT:
        return 0.0
    # Population under a square root: a colour on a fifth of the cover is not
    # five times the song's colour that one on a twentieth is, and without the
    # compression a large dull region always beats a small vivid one.
    pop = share ** 0.5
    # Saturation squared, so the gap between 0.3 and 0.6 counts for more than
    # the gap between 0.6 and 0.9 — that is where "has a hue" actually lives.
    colour = sat * sat
    # Mid-lightness swatches carry their hue most reliably; the extremes are
    # where quantisation error and clipping manufacture false ones.
    room = 1.0 - abs(lightness - 0.5) * 1.4
    return pop * colour * max(0.05, room)


def _hue_distance(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def extract(data: bytes) -> SongColour:
    """The song's colour, or NEUTRAL when the cover has none to give."""
    try:
        swatches = _swatches(_thumb(data))
    except Exception:
        logger.debug("could not read cover for colour", exc_info=True)
        return NEUTRAL
    if not swatches:
        return NEUTRAL

    scored = sorted(((_score(sh, rgb), sh, rgb) for sh, rgb in swatches),
                    reverse=True)
    coloured = sum(sh for sh, rgb in swatches if _score(sh, rgb) > 0)
    dominant = swatches[0][1]

    if scored[0][0] <= 0 or coloured < COLOURED_FRACTION:
        return SongColour(0.0, 0.0, coloured, 0.0, True, dominant)

    _best, _share, rgb = scored[0]
    h, _lightness, sat = colorsys.rgb_to_hls(*[c / 255 for c in rgb])
    hue = h * 360.0

    accent = hue
    for score, _sh, other in scored[1:]:
        if score <= 0:
            break
        other_hue = colorsys.rgb_to_hls(*[c / 255 for c in other])[0] * 360.0
        if _hue_distance(other_hue, hue) >= ACCENT_SEPARATION:
            accent = other_hue
            break

    return SongColour(hue, sat, coloured, accent, False, dominant)
