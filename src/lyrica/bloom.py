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
