"""One lyric line on screen, kept alive as it moves through the roles.

The previous design rebuilt every row whenever the line changed and then slid
the new items into place. That looks wrong for a reason worth stating: the line
below never *rose* to become the current one — it was destroyed and a different
object was born where it landed. Motion built that way reads as a slideshow no
matter how well it is eased, because nothing on screen actually travelled.

Here a line is created once, when it first comes into view, and afterwards only
moves and changes brightness. The row that was next becomes the row that is
current by being the same items in a new place.

All lines share one font size. That is what makes the reuse real — a role
change that also changed size would force a relayout, which is a rebuild
wearing a different name — and it matches the reference, where lines differ by
brightness rather than by scale.
"""
import tkinter as tk
from tkinter import font as tkfont

from lyrica.overlay_text import (
    EDGE_MARGIN,
    OUTLINE_COLOUR,
    measure,
    ring_offsets,
    wrap_words,
)
from lyrica.palette import GLOW_LEVELS

# The sweep front is softened over this many pixels. Fixed rather than a
# fraction of each word: at display sizes a tenth of a short word is under one
# character, which reads as a step instead of a ramp.
FEATHER_PX = 40.0
GLOW_OFFSETS = ((-2, 0), (2, 0), (0, -2), (0, 2))
GLOW_PEAK = 60


class LineView:
    """The canvas items for one lyric line, and the state to colour them."""

    def __init__(self, canvas: tk.Canvas, cx: int, y: float, text: str, words: list,
                 *, font, wrap: int, palette, scale: float = 1.0):
        self.canvas = canvas
        self.palette = palette
        self.words = words
        self.feather = FEATHER_PX * scale
        self.y = float(y)
        self._items: list = []      # [centre_x, row, item, colour]
        self._outline: list = []
        self._word_chars: list[int] = []   # timed token -> its first character
        self._glow: dict = {}       # char index -> [items], built only while active
        self._active = False
        self._state = None

        font_obj = tkfont.Font(font=font)
        key = tuple(sorted(font_obj.actual().items()))
        space = measure(font_obj, key, " ")
        self.line_height = font_obj.metrics("linespace")
        self._font = font

        # The line is laid out from its own text, never from the word tokens.
        # Sources tokenise below the word — richsync in particular splits on
        # syllables — so joining tokens with spaces would render a split word as
        # two. Timings are mapped onto the text afterwards instead, which makes
        # what appears on screen exactly what the line says.
        self.text = text or " ".join(w[2] for w in words)
        tokens = [(0.0, 0.0, part) for part in self.text.split()]
        self._word_chars = self._map_words_to_text(words)
        rows = wrap_words(tokens, font_obj, key, space, wrap)
        self._row_spans: list[tuple[float, float]] = []

        for r, row in enumerate(rows):
            total = sum(w for _, _, w in row) + space * (len(row) - 1)
            # Never started off the left edge: a word too wide to break would
            # otherwise lose its beginning, which is worse than losing its end.
            x = max(EDGE_MARGIN, cx - total / 2)
            self._row_spans.append((x, x + total))
            row_y = y + r * self.line_height
            for _token_index, word_text, _ in row:
                for ch in word_text:
                    adv = measure(font_obj, key, ch)
                    # Only in keyed mode, where colour replaces what is behind
                    # it and nothing else keeps text off a bright background.
                    # Glass composites additively and the tinted plate is the
                    # contrast, so an outline there would be invisible anyway.
                    outline = [canvas.create_text(x + dx, row_y + dy, text=ch,
                                                  anchor="nw", font=font,
                                                  fill=OUTLINE_COLOUR)
                               for dx, dy in ring_offsets(palette.outline)]
                    item = canvas.create_text(x, row_y, text=ch, anchor="nw",
                                              font=font, fill=palette.side)
                    self._items.append([x + adv / 2, r, item, palette.side])
                    self._outline.extend(outline)
                    x += adv
                x += space

        self.height = len(rows) * self.line_height

    # --- lifecycle ---
    def move_to(self, y: float) -> None:
        """Shift the whole line, keeping the items that are already there."""
        delta = round(y - self.y)
        if delta:
            for item in self.item_ids():
                self.canvas.move(item, 0, delta)
            self.y += delta

    def item_ids(self):
        yield from self._outline
        for entry in self._items:
            yield entry[2]
        for items in self._glow.values():
            yield from items

    def destroy(self) -> None:
        for item in self.item_ids():
            self.canvas.delete(item)
        self._items.clear()
        self._outline.clear()
        self._glow.clear()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._state = None
        if not active:
            self._clear_glow()

    # --- colouring ---
    def show_inactive(self, colour: str) -> None:
        """Flat brightness, for a line that is not the one being sung."""
        if self._state == colour:
            return
        self._state = colour
        for entry in self._items:
            if entry[3] != colour:
                self.canvas.itemconfigure(entry[2], fill=colour)
                entry[3] = colour

    def show_sweep(self, word_index: int, fraction: float) -> None:
        """The active line, swept character by character."""
        front = self._front_at(word_index, fraction)
        if front == self._state:
            return
        self._state = front
        active_row, front_x = front or (-1, 0.0)
        palette = self.palette

        for i, entry in enumerate(self._items):
            centre, row, item, last = entry
            # Rows share a horizontal range, so x alone cannot say whether a
            # character has been sung: without the row test a wrapped line
            # lights both of its rows at once.
            if front is None or row > active_row:
                t = 0.0
            elif row < active_row:
                t = 1.0
            else:
                t = max(0.0, min(1.0, (front_x - centre) / self.feather + 0.5))
            colour = palette.at(t)
            if colour != last:
                self.canvas.itemconfigure(item, fill=colour)
                entry[3] = colour
            if palette.glow:
                self._glow_char(i, entry, t)

    def _glow_char(self, index: int, entry: list, t: float) -> None:
        """Light behind a character as the front crosses it.

        Glow items are made when first needed and dropped when the line stops
        being active, so a line waiting its turn carries none.
        """
        level = int(GLOW_PEAK * (1 - abs(t - 0.5) * 2)) if 0.06 < t < 0.94 else 0
        if level <= 4:
            for item in self._glow.pop(index, ()):
                self.canvas.delete(item)
            return
        items = self._glow.get(index)
        if items is None:
            ch = self.canvas.itemcget(entry[2], "text")
            x, y = self.canvas.coords(entry[2])
            items = [self.canvas.create_text(x + dx, y + dy, text=ch, anchor="nw",
                                             font=self._font, fill="#000000")
                     for dx, dy in GLOW_OFFSETS]
            for item in items:
                self.canvas.tag_lower(item, entry[2])
            self._glow[index] = items
        shade = GLOW_LEVELS[min(len(GLOW_LEVELS) - 1, level)]
        for item in items:
            self.canvas.itemconfigure(item, fill=shade)

    def _clear_glow(self) -> None:
        for items in self._glow.values():
            for item in items:
                self.canvas.delete(item)
        self._glow.clear()

    # --- mapping timings onto the text ---
    def _map_words_to_text(self, words: list) -> list[int]:
        """Where each timed token begins in the laid-out characters.

        Sources tokenise inconsistently — richsync splits on syllables, and
        tokens carry their own spacing — so a token is matched into the line's
        own text rather than trusted to be a word. When a token cannot be found
        at all, which means the source disagrees with the text it supplied, the
        cursor simply advances: a slightly wrong highlight beats a crash or a
        line rendered as gibberish.
        """
        flat = "".join(self.text.split())
        starts: list[int] = []
        cursor = 0
        for entry in words:
            needle = "".join(str(entry[2]).split())
            if not needle:
                starts.append(min(cursor, max(0, len(flat) - 1)))
                continue
            found = flat.find(needle, cursor)
            if found < 0:
                found = cursor
            starts.append(found)
            cursor = found + len(needle)
        return starts

    # --- geometry ---
    def _front_at(self, word_index: int, fraction: float) -> tuple[int, float] | None:
        """The row being sung and where the front sits along it.

        A word whose successor has wrapped aims at the end of its own row.
        Aiming at the successor would send the front back across the line,
        since the next row restarts at the left margin.
        """
        if not self.words or word_index < 0 or word_index >= len(self._word_chars):
            return None
        index = self._word_chars[word_index]
        row = self._items[max(0, min(index, len(self._items) - 1))][1]
        here = self._centre(index)

        after = word_index + 1
        if after < len(self._word_chars):
            next_index = self._word_chars[after]
            next_row = self._items[max(0, min(next_index, len(self._items) - 1))][1]
            target = (self._centre(next_index) if next_row == row
                      else self._row_spans[row][1] + self.feather)
        else:
            target = self._row_spans[row][1] + self.feather
        return row, here + (target - here) * fraction - self.feather * 0.5

    def _centre(self, index: int) -> float:
        return self._items[max(0, min(index, len(self._items) - 1))][0]
