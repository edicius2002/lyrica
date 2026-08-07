"""Text measurement and layout shared by the line renderer.

Two things live here because they are needed before anything can be drawn:
how wide a piece of text is, and how a line breaks into rows.

Measurements are memoised. The same words are measured on every rebuild, and
`font.measure` is a round trip into Tk — cheap once, wasteful thousands of times.

Outlines ring the centre rather than filling a square. A square of radius 2 is
24 draws per glyph where the ring is 8, and the extra 16 land underneath the
fill where nothing can see them. They are only used in keyed mode, which is the
one mode with nothing drawn behind the text; everywhere else the cover wash is
capped dark enough to be the contrast by itself.
"""
from tkinter import font as tkfont

OUTLINE_COLOUR = "#000000"

# Nothing is laid out closer than this to the window edge. In designed units:
# callers multiply by the display scale, or a line at 2x sits twice as close to
# an edge that moved twice as far away.
EDGE_MARGIN = 8

_measure_cache: dict[tuple, int] = {}


def ring_offsets(width: int) -> list[tuple[int, int]]:
    """The eight compass directions at `width` pixels, plus the diagonals at
    the step inside it so the corners do not thin out."""
    if width <= 0:
        return []
    w = width
    offsets = [(-w, 0), (w, 0), (0, -w), (0, w), (-w, -w), (-w, w), (w, -w), (w, w)]
    if w > 1:
        i = w - 1
        offsets += [(-i, -i), (-i, i), (i, -i), (i, i)]
    return offsets


def measure(font_obj: tkfont.Font, key: tuple, text: str) -> int:
    """Width of `text`, memoised — the same words are measured on every rebuild."""
    cache_key = (key, text)
    width = _measure_cache.get(cache_key)
    if width is None:
        width = font_obj.measure(text)
        _measure_cache[cache_key] = width
    return width


def wrap_words(words: list, font_obj: tkfont.Font, key: tuple, space: int,
               wrap: int) -> list:
    """Greedily group words into rows no wider than `wrap`.

    Returns rows of (word_index, text, width). The index survives wrapping
    because the renderer colours by word, and a wrapped row still has to know
    which word each of its entries was.

    A word wider than the limit gets a row of its own rather than being
    dropped — it will overflow, but a line that is present and too wide beats a
    line that is missing.
    """
    rows: list = []
    row: list = []
    row_width = 0
    for i, entry in enumerate(words):
        text = entry[2]
        w = measure(font_obj, key, text)
        extra = w if not row else w + space
        if row and row_width + extra > wrap:
            rows.append(row)
            row, row_width = [], 0
            extra = w
        row.append((i, text, w))
        row_width += extra
    if row:
        rows.append(row)
    return rows
