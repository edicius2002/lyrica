"""Blurred glyphs, so the light around a sung word is light and not four copies.

The first attempt drew the halo as four copies of the character offset two
pixels apart, which is what a canvas can do unaided. It reads as exactly what it
is: hard-edged letters stacked slightly apart, doubling every curve. What it is
imitating is a gaussian bloom, and a canvas cannot blur.

PIL can, and it is already here for the cover wash. A glyph is drawn once,
blurred, given an alpha ramp and handed to Tk as an image; the canvas then only
has to swap which image an item shows. Measured against the four-copy version:
generating one costs 1.0 ms and is done once per character per level for the
whole session, while showing a different one costs 0.005 ms against 0.21 for
recolouring four items. The honest version is twenty times cheaper per frame
than the fake one.

Images are cached and held here on purpose: Tk keeps only a weak claim on an
image, so anything dropped goes blank on screen.
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# How many strengths a bloom is quantised to. The eye cannot see a step of an
# eighth of a fade, and each step is an image that has to be built and kept.
LEVELS = 8

# How far the light spreads, in pixels at the designed size. Wide enough to read
# as a glow around the letter rather than as a thicker letter.
RADIUS = 4

# How much larger a word gets at the peak of its strike. What is being imitated
# is subtle — past this it stops reading as emphasis and starts reading as a
# word jumping — and it is small enough that the overlap with the neighbouring
# words stays at a pixel or two.
GROWTH = 0.06

# Room for the blur to fall off inside the image, or it is cut square at the
# edges and the halo has corners.
PAD = 12

_cache: dict = {}
_fonts: dict = {}
_missing: set = set()


def _font_file(family: str, weight: str) -> Path | None:
    """The TrueType file behind a Tk font family, or None if it cannot be found.

    Only Windows is answered directly. Elsewhere the caller falls back to the
    offset copies, which look worse and always work — the wrong trade for the
    platform this is written for and the right one everywhere else.
    """
    if sys.platform != "win32":
        return None
    root = Path("C:/Windows/Fonts")
    stem = family.lower().replace(" ", "")
    bold = weight.lower() == "bold"
    for name in ([f"{stem}b.ttf", f"{stem}bd.ttf"] if bold else []) + [f"{stem}.ttf"]:
        path = root / name
        if path.exists():
            return path
    return None


def _pil_font(spec: tuple):
    """A PIL font matching a Tk font spec, or None."""
    if spec in _fonts:
        return _fonts[spec]
    try:
        from tkinter import font as tkfont

        from PIL import ImageFont

        measured = tkfont.Font(font=spec)
        actual = measured.actual()
        path = _font_file(actual["family"], actual.get("weight", "normal"))
        if path is None:
            loaded = None
        else:
            # Sized by matching ascents rather than by copying a number. Tk
            # reports `actual()["size"]` in points where the font was asked for
            # in pixels, and passing that straight to PIL — which counts in
            # pixels — drew the halo at 22 where the glyph was 30, and from a
            # different vertical origin: measured, the light sat 13 px above the
            # letter it belonged to. Ascent is the quantity that has to agree,
            # because it is what positions a glyph under `anchor="nw"`, and it
            # scales linearly with size, so one correction is exact.
            want = measured.metrics("ascent")
            trial = ImageFont.truetype(str(path), abs(int(actual["size"])))
            got = trial.getmetrics()[0]
            size = max(1, round(trial.size * want / got)) if got else trial.size
            loaded = ImageFont.truetype(str(path), size)
    except Exception:
        logger.debug("could not load a font for the bloom", exc_info=True)
        loaded = None
    if loaded is None and spec not in _missing:
        _missing.add(spec)
        logger.info("no blurred bloom for %r; falling back to offset copies", spec)
    _fonts[spec] = loaded
    return loaded


def available(spec: tuple) -> bool:
    """Whether blurred glyphs can be made for this font."""
    return _pil_font(spec) is not None


# How many sizes a growing word is quantised to. PIL resamples, so the steps are
# only a cache limit and not the staircase that changing a Tk font size is: a
# 4 % growth there gives two integer sizes and nothing between them.
SCALES = 8


def _rendered(char: str, font, colour: tuple, scale: float):
    """The glyph drawn at `colour`, grown by `scale` about its own centre."""
    from PIL import Image, ImageDraw

    width = max(1, int(font.getlength(char)))
    ascent, descent = font.getmetrics()
    height = ascent + descent
    img = Image.new("RGBA", (width + PAD * 2, height + PAD * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((PAD, PAD), char, font=font, fill=(*colour, 255))
    if scale != 1.0:
        big = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(big, Image.LANCZOS)
    return img


def ready(char: str, spec: tuple, step: int, colour: tuple) -> bool:
    """Whether this image already exists, so a caller can budget the building.

    Each one costs about 0.7 ms and a frame is 16, so a word of seven letters
    reaching for a new size every frame overran on its first play — measured at
    24 ms. Nothing has to be built *this* frame, though: the size before it is
    already on screen and a step of a sixth of six per cent is not a thing
    anyone sees held for one frame longer.
    """
    return ("grown", char, spec, min(step, SCALES), colour) in _cache


def grown(char: str, spec: tuple, step: int, colour: tuple):
    """`char` at `step` of `SCALES` through the growth, as a Tk image, or None.

    Step 0 is the letter at its own size, which is what an ordinary text item
    already draws — so it returns nothing and the caller shows the text instead.
    Nothing is scaled down: a word only ever grows.
    """
    if step <= 0:
        return None
    key = ("grown", char, spec, min(step, SCALES), colour)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    font = _pil_font(spec)
    if font is None:
        return None
    try:
        from PIL import ImageTk

        scale = 1.0 + GROWTH * min(step, SCALES) / SCALES
        photo = ImageTk.PhotoImage(_rendered(char, font, colour, scale))
    except Exception:
        logger.debug("could not grow %r", char, exc_info=True)
        return None
    _cache[key] = photo
    return photo


def offset(char: str, spec: tuple, step: int) -> tuple:
    """Where a grown glyph's *image* goes, relative to the letter's own corner.

    Two things have to be undone at once and the first attempt did neither
    properly, which put every growing word twelve pixels down and to the right
    instead of swelling in place.

    The image carries `PAD` of transparent margin so the halo has room to fall
    off, and that margin is resampled along with everything else — so the glyph
    sits `PAD * scale` inside the image, not `PAD`. And the glyph should swell
    about its own centre, which means moving it back by half of what it gained;
    that half is of the *letter's* width, not of the padded image's.
    """
    font = _pil_font(spec)
    if font is None or step <= 0:
        return (0.0, 0.0)
    ascent, descent = font.getmetrics()
    width = max(1, int(font.getlength(char)))
    height = ascent + descent
    scale = 1.0 + GROWTH * min(step, SCALES) / SCALES
    return (-width * (scale - 1) / 2 - PAD * scale,
            -height * (scale - 1) / 2 - PAD * scale)


def blurred_ready(char: str, spec: tuple, level: int,
                  colour: tuple = (255, 255, 255)) -> bool:
    """Whether this halo already exists. Budgeted for the same reason sizes are."""
    return (char, spec, min(level, LEVELS), colour) in _cache


def glyph(char: str, spec: tuple, level: int, colour: tuple = (255, 255, 255)):
    """A blurred `char` at `level` of `LEVELS`, as a Tk image, or None.

    Level 0 is nothing at all rather than a transparent image, so a spent bloom
    costs the canvas no work beyond hiding its item.
    """
    if level <= 0:
        return None
    key = (char, spec, min(level, LEVELS), colour)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    font = _pil_font(spec)
    if font is None:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageTk

        width = int(font.getlength(char)) + PAD * 2
        height = int(font.size * 1.6) + PAD * 2
        img = Image.new("RGBA", (max(width, 1), height), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((PAD, PAD), char, font=font,
                                 fill=(*colour, 255))
        img = img.filter(ImageFilter.GaussianBlur(RADIUS))
        strength = min(level, LEVELS) / LEVELS
        img.putalpha(img.split()[3].point(lambda v: int(v * strength)))
        photo = ImageTk.PhotoImage(img)
    except Exception:
        logger.debug("could not build a bloom for %r", char, exc_info=True)
        return None
    _cache[key] = photo
    return photo
