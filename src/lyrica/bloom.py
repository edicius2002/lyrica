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
# is subtle, but subtle has a floor here that is not a matter of taste: at 6 %
# a narrow letter gained nine tenths of a pixel across the whole growth, and
# four of the nine steps rendered to the same integer size as their neighbour,
# so a third of the frames showed an identical picture. Measured, every step
# earns a different image from about 10 % up.
GROWTH = 0.11          # replaced at startup from the environment

# Room for the blur to fall off inside the image, or it is cut square at the
# edges and the halo has corners.
PAD = 12

_cache: dict = {}
_fonts: dict = {}
_missing: set = set()


def _font_file(family: str, weight: str, slant: str = "roman") -> Path | None:
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
    italic = slant.lower() == "italic"
    # Windows names the four faces by one letter. Tried in order of how well
    # each matches, so a family missing its italic falls back to its roman
    # rather than to nothing at all.
    endings = {(True, True): ["z", "bi", "b", "i", ""],
               (True, False): ["b", "bd", ""],
               (False, True): ["i", ""],
               (False, False): [""]}[(bold, italic)]
    for suffix in endings:
        path = root / f"{stem}{suffix}.ttf"
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
        path = _font_file(actual["family"], actual.get("weight", "normal"),
                          actual.get("slant", "roman"))
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
    """`char` grown by `step` of `SCALES`, as (image, dx, dy), or None.

    `dx`/`dy` place the image relative to the letter's own top-left corner, and
    they come back with it because they can only be right if they are derived
    from the size the image actually came out — which is whole pixels, not the
    fraction that was asked for. Working from the fraction instead leaves the
    placement and the picture disagreeing by up to half a pixel, and the letter
    trembles as it grows.

    The image carries `PAD` of transparent margin so the halo has room to fall
    off, and that margin is resampled with everything else, so the letter sits
    `PAD * scale` inside rather than `PAD`. On top of that it has to move back
    by half of what it gained, or it swells rightwards instead of in place.

    Step 0 is the letter at its own size, which an ordinary text item already
    draws, so it returns nothing and the caller shows the text. Nothing is ever
    scaled down: a word only grows.
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

        asked = 1.0 + GROWTH * min(step, SCALES) / SCALES
        img = _rendered(char, font, colour, asked)
        width = max(1, int(font.getlength(char)))
        height = sum(font.getmetrics())
        # What the resampling actually delivered, per axis.
        got_x = img.width / (width + PAD * 2)
        got_y = img.height / (height + PAD * 2)
        placed = (width / 2 - PAD * got_x - width * got_x / 2,
                  height / 2 - PAD * got_y - height * got_y / 2)
        made = (ImageTk.PhotoImage(img), *placed)
    except Exception:
        logger.debug("could not grow %r", char, exc_info=True)
        return None
    _cache[key] = made
    return made


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
