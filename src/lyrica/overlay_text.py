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

from lyrica.palette import GLOW_LEVELS

OUTLINE_COLOUR = "#000000"

# Nothing is laid out closer than this to the window edge.
EDGE_MARGIN = 8

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


class SweepLine:
    """The active line under additive composition, swept character by character.

    A gradient front travels along the line and each character takes its colour
    from where it sits relative to it. The front is what makes the highlight
    look like it is following a voice rather than clicking between words.

    Per-character is safe because Tk applies no kerning: the widths of a
    string's characters sum to the width of the string exactly, so this is
    pixel-identical to drawing the words whole.

    The feather is a fixed width rather than a fraction of each word. At display
    sizes a tenth of a short word is under one character, which would read as a
    hard step — the point of the ramp is that it spans two or three.
    """

    FEATHER_PX = 40.0
    OVERSHOOT_PX = 60.0
    GLOW_OFFSETS = ((-2, 0), (2, 0), (0, -2), (0, 2))
    GLOW_PEAK = 60

    def __init__(self, canvas: tk.Canvas, cx: int, y: int, words: list, *,
                 font, wrap: int, palette, scale: float = 1.0):
        self.canvas = canvas
        self.words = words
        self.palette = palette
        self.feather = self.FEATHER_PX * scale
        self.overshoot = self.OVERSHOOT_PX * scale
        self._chars: list = []      # [centre_x, row, item, glow_items, colour, glowing]
        self._word_starts: list[int] = []   # word index -> its first character
        self._front = None
        self._line_end = float(cx)

        font_obj = tkfont.Font(font=font)
        key = tuple(sorted(font_obj.actual().items()))
        space = measure(font_obj, key, " ")
        line_height = font_obj.metrics("linespace")
        rows = wrap_words(words, font_obj, key, space, wrap)

        self._row_spans: list[tuple[float, float]] = []
        for r, row in enumerate(rows):
            total = sum(w for _, _, w in row) + space * (len(row) - 1)
            # Centred, but never started off the left edge. A single word wider
            # than the line cannot be broken, and letting it centre would push
            # its beginning out of view — losing the start of a word is worse
            # than losing its end.
            x = max(EDGE_MARGIN, cx - total / 2)
            self._row_spans.append((x, x + total))
            row_y = y + r * line_height
            for word_index, text, _ in row:
                while len(self._word_starts) <= word_index:
                    self._word_starts.append(len(self._chars))
                for ch in text:
                    adv = measure(font_obj, key, ch)
                    glow = []
                    if palette.glow:
                        glow = [canvas.create_text(x + dx, row_y + dy, text=ch,
                                                   anchor="nw", font=font,
                                                   fill="#000000", state="hidden")
                                for dx, dy in self.GLOW_OFFSETS]
                    item = canvas.create_text(x, row_y, text=ch, anchor="nw",
                                              font=font, fill=palette.unsung)
                    self._chars.append([x + adv / 2, r, item, glow,
                                        palette.unsung, False])
                    x += adv
                x += space
            self._line_end = max(self._line_end, x)

        self.height = len(rows) * line_height

    def _front_at(self, word_index: int, fraction: float) -> float | None:
        """Where the gradient front sits, in canvas x, or None before the start.

        The front advances from the active word's first character towards the
        next word's, so it tracks the voice word by word rather than sweeping
        the whole line at one constant rate.
        """
        if word_index < 0 or word_index >= len(self._word_starts):
            return None
        here = self._char_centre(self._word_starts[word_index])
        after = word_index + 1
        target = (self._char_centre(self._word_starts[after])
                  if after < len(self._word_starts)
                  else self._line_end + self.feather)
        return here + (target - here) * fraction - self.feather * 0.5

    def _char_centre(self, index: int) -> float:
        index = max(0, min(index, len(self._chars) - 1))
        return self._chars[index][0]

    def update(self, word_index: int, fraction: float) -> None:
        front = self._front_at(word_index, fraction)
        if front == self._front:
            return
        self._front = front
        canvas, palette = self.canvas, self.palette

        for entry in self._chars:
            centre, _row, item, glow, last, glowing = entry
            if front is None:
                t = 0.0
            else:
                t = (front - centre) / self.feather + 0.5
                t = max(0.0, min(1.0, t))
            colour = palette.at(t)
            if colour != last:
                canvas.itemconfigure(item, fill=colour)
                entry[4] = colour
            if not glow:
                continue
            # Brightest as the front crosses the character, nothing either side.
            level = int(self.GLOW_PEAK * (1 - abs(t - 0.5) * 2)) if 0.06 < t < 0.94 else 0
            want = level > 4
            if want:
                shade = GLOW_LEVELS[min(len(GLOW_LEVELS) - 1, level)]
                for g in glow:
                    canvas.itemconfigure(g, fill=shade, state="normal")
            elif glowing:
                for g in glow:
                    canvas.itemconfigure(g, state="hidden")
            entry[5] = want


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
