"""Outlined text rendering for the overlay canvas.

tkinter has no text outline, and the overlay needs one: over a browser the
background behind it is whatever the page is showing, so flat white text
disappears against a bright video. The usual trick applies — draw the string
several times in a dark colour, offset around the centre, then draw the fill on
top.

Offsets ring the centre rather than filling a square. A square of radius 2 is
24 draws per line where the ring is 8, and the extra 16 land underneath the
fill where nothing can see them.
"""
import tkinter as tk
from tkinter import font as tkfont

OUTLINE_COLOUR = "#000000"

# Word rendering rebuilds canvas items only when the line changes and then just
# recolours them, so the outline can stay generous without costing frame rate.
WORD_OUTLINE = 2

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


def draw_outlined(canvas: tk.Canvas, x: int, y: int, text: str, *, font,
                  fill: str, wrap: int, outline: int = 2) -> int:
    """Draw centred, outlined, top-anchored text. Returns the height it used.

    Zero for empty text, so a caller stacking rows can simply add the result
    and let absent rows take no space.
    """
    if not text:
        return 0
    common = {"text": text, "font": font, "width": wrap, "anchor": "n",
              "justify": "center"}
    for dx, dy in ring_offsets(outline):
        canvas.create_text(x + dx, y + dy, fill=OUTLINE_COLOUR, **common)
    item = canvas.create_text(x, y, fill=fill, **common)
    bbox = canvas.bbox(item)
    return (bbox[3] - bbox[1]) if bbox else 0


def measure(font_obj: tkfont.Font, key: tuple, text: str) -> int:
    """Width of `text`, memoised — the same words are measured every frame."""
    cache_key = (key, text)
    width = _measure_cache.get(cache_key)
    if width is None:
        width = font_obj.measure(text)
        _measure_cache[cache_key] = width
    return width


def wrap_words(words: list, font_obj: tkfont.Font, key: tuple, space: int,
               wrap: int) -> list:
    """Greedily group words into rows no wider than `wrap`.

    Returns rows of (word_index, text, width). A word wider than the limit
    still gets a row of its own rather than being dropped.
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


class WordLine:
    """One word-timed line, drawn once and afterwards only recoloured.

    Rebuilding the canvas every frame is what makes a sweep look stepped: at
    word level the highlight moves continuously, so the line would be torn down
    and rebuilt many times a second. Instead the items are created when the line
    changes, and each frame reconfigures only the few whose colour actually
    differs — which is what keeps the motion smooth without the overlay costing
    real CPU.
    """

    def __init__(self, canvas: tk.Canvas, cx: int, y: int, words: list, *,
                 font, wrap: int, sung: str, active: str, unsung: str,
                 outline: int = WORD_OUTLINE):
        self.canvas = canvas
        self.words = words
        self.sung, self.active, self.unsung = sung, active, unsung
        self._items: list[list[int]] = [[] for _ in words]
        self._colours: list[str | None] = [None] * len(words)
        self._state: tuple = (-2, -1.0)

        font_obj = tkfont.Font(font=font)
        key = tuple(sorted(font_obj.actual().items()))
        space = measure(font_obj, key, " ")
        line_height = font_obj.metrics("linespace")

        rows = wrap_words(words, font_obj, key, space, wrap)
        offsets = ring_offsets(outline)

        for r, row in enumerate(rows):
            total = sum(w for _, _, w in row) + space * (len(row) - 1)
            x = cx - total // 2
            row_y = y + r * line_height
            for word_index, text, w in row:
                for dx, dy in offsets:
                    canvas.create_text(x + dx, row_y + dy, text=text, font=font,
                                       fill=OUTLINE_COLOUR, anchor="nw")
                item = canvas.create_text(x, row_y, text=text, font=font,
                                          fill=unsung, anchor="nw")
                self._items[word_index].append(item)
                x += w + space

        self.height = len(rows) * line_height

    def update(self, active_index: int, fraction: float) -> None:
        """Recolour for the current word and how far through it playback is.

        The active word flips to sung at its midpoint. Below word granularity
        that is the sweep: a word is short enough that switching it whole
        halfway through tracks the voice closely, at one reconfigure instead of
        one per character.
        """
        state = (active_index, round(fraction, 2))
        if state == self._state:
            return
        self._state = state

        for i, items in enumerate(self._items):
            if i < active_index:
                colour = self.sung
            elif i > active_index:
                colour = self.unsung
            else:
                colour = self.sung if fraction >= 0.5 else self.active
            if colour != self._colours[i]:
                self._colours[i] = colour
                for item in items:
                    self.canvas.itemconfig(item, fill=colour)
