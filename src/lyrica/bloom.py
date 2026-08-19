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
image, so anything dropped goes blank on screen. Held, though, is not held
forever — see `_Images` for what bounds the cache and why it has to be bounded.
"""
import logging
import sys
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

# How many strengths a bloom is quantised to. The eye cannot see a step of an
# eighth of a fade, and each step is an image that has to be built and kept.
LEVELS = 8

# A tight core makes the strike legible; a wider, weaker field lets it survive
# the artwork wash. A single four-pixel blur read as a softened outline at the
# designed thirty-pixel lyric size.
INNER_RADIUS = 3
OUTER_RADIUS = 11
OUTER_STRENGTH = 0.34

# How much larger a word gets at the peak of its strike. What is being imitated
# is subtle, but subtle has a floor here that is not a matter of taste: at 6 %
# a narrow letter gained nine tenths of a pixel across the whole growth, and
# four of the nine steps rendered to the same integer size as their neighbour,
# so a third of the frames showed an identical picture. Measured, every step
# earns a different image from about 10 % up.
GROWTH = 0.14          # default for direct users; the overlay passes its setting

# Room for the blur to fall off inside the image, or it is cut square at the
# edges and the halo has corners.
PAD = 20

# --- how many of them are kept ----------------------------------------------
# Every image in here is a Windows GDI bitmap from the moment it is first drawn:
# measured at exactly one handle and 29.6 KiB each. A process is given 10,000 GDI
# handles. The keys carry the palette colour, and the palette follows the cover,
# so each song asks for images of its own and none of the previous song's are
# ever asked for again — which made an unbounded cache a leak on the one axis
# that never stops moving. Reproduced: the quota was reached after about fifteen
# songs, and Tk ended the process from `Tk_GetPixmap` with "Fail to allocate
# bitmap". That is a `Tcl_Panic`, so there is nothing to catch and no chance to
# log a word: the only defence is never to hold that many.
#
# One song's whole appetite is two colours — sung and unsung — times the distinct
# characters in its lyrics times `SCALES`, which is about 800 images for a Latin
# lyric. Halos are white whatever the song, so they are shared by every song in
# the font. Three songs' worth is kept: 2,400 of the 10,000 handles, and about
# 70 MB.
LIMIT = 2400

# Count alone does not bound the memory. A Korean or Japanese lyric has hundreds
# of distinct characters and draws each of them larger, so the same number of
# images is several times the bytes. Whichever bound is reached first evicts.
BYTE_LIMIT = 72 * 1024 * 1024


class _Images(OrderedDict):
    """The kept images, bounded by count and by bytes, least recently used out.

    Eviction is by last use rather than by age or by song, and that is what
    makes it safe. Tk keeps only a weak claim on an image, so dropping one a
    canvas item is still showing blanks that letter — but a letter on screen is
    by definition one that was just used, and it takes `LIMIT` other images
    being used to age it out. One line on screen is at most about forty of them
    against a bound of 2,400, and a song builds 800 in its entirety, so the
    least recently used entry belongs to a song that stopped playing.

    Dropping the reference is what frees the handle: `ImageTk.PhotoImage`
    deletes the Tk image in `__del__`, and this cache holds the only strong
    reference to it. So `del` here is not bookkeeping, it is the release.
    """

    def __init__(self):
        super().__init__()
        self._weight: dict = {}
        self.total_bytes = 0

    def take(self, key):
        """What is held for `key`, counted as freshly used, or None."""
        if key not in self:
            return None
        self.move_to_end(key)
        return self[key]

    def keep(self, key, value, image) -> None:
        """Hold `value`, then drop the least recently used until it fits.

        `image` is the PIL original rather than the Tk one because it is what
        knows its own size in pixels; four bytes each is what both copies of it
        cost, and the two are within a few per cent of the 29.6 KiB measured.
        """
        weight = image.width * image.height * 4
        self[key] = value
        self._weight[key] = weight
        self.total_bytes += weight
        # Never the entry just kept, however small the bounds are set: a caller
        # that is handed an image expects it to still be there next frame.
        while len(self) > 1 and (len(self) > LIMIT
                                 or self.total_bytes > BYTE_LIMIT):
            del self[next(iter(self))]

    # `OrderedDict.popitem` is implemented in C and does not route through
    # `__delitem__` on a subclass, so eviction spells the deletion out above
    # rather than trusting it to keep the total honest.
    def __delitem__(self, key) -> None:
        self.total_bytes -= self._weight.pop(key, 0)
        super().__delitem__(key)

    def clear(self) -> None:
        super().clear()
        self._weight.clear()
        self.total_bytes = 0


_cache = _Images()
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


def _growth_key(growth: float | None) -> float:
    return round(GROWTH if growth is None else growth, 4)


def ready(char: str, spec: tuple, step: int, colour: tuple,
          growth: float | None = None) -> bool:
    """Whether this image already exists, so a caller can budget the building.

    Each one costs about 0.7 ms and a frame is 16, so a word of seven letters
    reaching for a new size every frame overran on its first play — measured at
    24 ms. Nothing has to be built *this* frame, though: the size before it is
    already on screen and a step of a sixth of six per cent is not a thing
    anyone sees held for one frame longer.
    """
    return ("grown", char, spec, min(step, SCALES), colour,
            _growth_key(growth)) in _cache


def grown(char: str, spec: tuple, step: int, colour: tuple,
          growth: float | None = None):
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
    amount = _growth_key(growth)
    key = ("grown", char, spec, min(step, SCALES), colour, amount)
    hit = _cache.take(key)
    if hit is not None:
        return hit
    font = _pil_font(spec)
    if font is None:
        return None
    try:
        from PIL import ImageTk

        asked = 1.0 + amount * min(step, SCALES) / SCALES
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
    _cache.keep(key, made, img)
    return made


def blurred_ready(char: str, spec: tuple, level: int,
                  colour: tuple = (255, 255, 255)) -> bool:
    """Whether this halo already exists. Budgeted for the same reason sizes are."""
    return (char, spec, min(level, LEVELS), colour) in _cache


def _rendered_halo(char: str, font, level: int, colour: tuple):
    """Render a coloured halo by blurring opacity, never RGB channels."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    width = int(font.getlength(char)) + PAD * 2
    height = int(font.size * 1.6) + PAD * 2
    mask = Image.new("L", (max(width, 1), height), 0)
    ImageDraw.Draw(mask).text((PAD, PAD), char, font=font, fill=255)

    # Blurring a transparent RGBA glyph interpolates its RGB towards the
    # transparent black background. Blur opacity alone, combine both fields as
    # alpha-over, and colour afterwards so even the faintest pixel stays light.
    outer = mask.filter(ImageFilter.GaussianBlur(OUTER_RADIUS)).point(
        lambda value: int(value * OUTER_STRENGTH))
    inner = mask.filter(ImageFilter.GaussianBlur(INNER_RADIUS))
    alpha = ImageChops.screen(inner, outer)
    strength = min(level, LEVELS) / LEVELS
    alpha = alpha.point(lambda value: int(value * strength))

    image = Image.new("RGBA", mask.size, (*colour, 0))
    image.putalpha(alpha)
    return image


def glyph(char: str, spec: tuple, level: int, colour: tuple = (255, 255, 255)):
    """A blurred `char` at `level` of `LEVELS`, as a Tk image, or None.

    Level 0 is nothing at all rather than a transparent image, so a spent bloom
    costs the canvas no work beyond hiding its item.
    """
    if level <= 0:
        return None
    key = (char, spec, min(level, LEVELS), colour)
    hit = _cache.take(key)
    if hit is not None:
        return hit
    font = _pil_font(spec)
    if font is None:
        return None
    try:
        from PIL import ImageTk

        halo = _rendered_halo(char, font, level, colour)
        photo = ImageTk.PhotoImage(halo)
    except Exception:
        logger.debug("could not build a bloom for %r", char, exc_info=True)
        return None
    _cache.keep(key, photo, halo)
    return photo
