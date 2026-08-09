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
import time
import tkinter as tk
from tkinter import font as tkfont

from lyrica import bloom
from lyrica.glass import rgb_of
from lyrica.motion import STRIKE_DOWN, STRIKE_UP, cubic_bezier
from lyrica.overlay_text import (
    EDGE_MARGIN,
    OUTLINE_COLOUR,
    measure,
    ring_offsets,
    split_for_wrapping,
    wrap_words,
)

# The sweep front is softened over this many pixels. Fixed rather than a
# fraction of each word: at display sizes a tenth of a short word is under one
# character, which reads as a step instead of a ramp.
#
# Narrowed from 40, which spread the transition over 2.6 characters at the
# designed size — wide enough that there was never a boundary, only a cloud. The
# lit word had to be told apart from the unlit one by brightness alone, and
# brightness is exactly what it lost when the unlit line was raised for
# legibility: the sweep's own separation fell from 49.5 to 24.1 in delta E. This
# gives back an edge without spending a unit of that luminance, and it stays
# above one character, so it is a tight ramp rather than the step the note above
# is warning about.
FEATHER_PX = 16.0
GLOW_OFFSETS = ((-2, 0), (2, 0), (0, -2), (0, 2))

# How bright the halo gets at the instant of the strike, as a fraction of the
# way from the wash behind the words to the colour of the word itself.
GLOW_PEAK = 0.85

# How far a struck word rises, in pixels at the designed size, before settling
# back. Without it the letters themselves do nothing — a halo appears behind
# text that is not reacting to anything, which reads as an effect laid over the
# words rather than as the words being sung. There is no room to brighten them
# instead: the sung colour is already at 253 of 255.
LIFT_PX = 3.0

# How much of a word's light is spent rising rather than settling. The rise used
# to take no time at all — one frame from nothing to the top, which is a
# teleport, not a movement.
LIFT_ATTACK = 0.16

# How many grown letters may be drawn for the first time in one frame. Each is
# about 0.7 ms against a 16 ms budget, and a word reaching for a new size every
# frame overran on its first play. A letter that cannot be built waits at the
# size it already has, which is a sixth of six per cent away.
NEW_SIZES_PER_FRAME = 3


def _lift_shape(age: float) -> float:
    """How high a struck word stands, 0..1, `age` through its light.

    Measured on the machine this was tuned on: three designed pixels came to
    3.4 real ones, so a linear fall over eighteen frames visited four positions
    and read as a staircase. The travel is wider now and the two halves are
    shaped separately, because being struck and relaxing are not the same
    gesture.
    """
    if age <= 0.0:
        return 0.0
    if age < LIFT_ATTACK:
        return cubic_bezier(age / LIFT_ATTACK, STRIKE_UP)
    fall = (age - LIFT_ATTACK) / (1.0 - LIFT_ATTACK)
    return 1.0 - cubic_bezier(min(1.0, fall), STRIKE_DOWN)

# How long a character keeps its bloom after the front reaches it. The effect
# being copied blooms at the onset and relaxes; the old glow instead peaked
# *at* the front and was symmetric about it, which reads as a lamp carried
# along the line rather than as each word being struck.
#
# Tuned against singing rather than taste: a character lasts about 29 ms in fast
# delivery, so a quarter of a second leaves a tail of eight or nine characters
# lit — long enough to see, short enough that a whole line is never alight at
# once.
BLOOM_S = 1.0

# What a word's own length is allowed to become. A word sung in a tenth of a
# second still has to be seen, and one held for eight has to stop glowing before
# the next line arrives.
BLOOM_FLOOR_S, BLOOM_CEILING_S = 0.14, 1.1


class LineView:
    """The canvas items for one lyric line, and the state to colour them."""

    def __init__(self, canvas: tk.Canvas, cx: int, y: float, text: str, words: list,
                 *, font, wrap: int, palette, scale: float = 1.0,
                 feather: float = FEATHER_PX, bloom: float = BLOOM_S,
                 lift: float = 1.0):
        self.canvas = canvas
        self.palette = palette
        self.words = words
        self.feather = feather * scale
        self.bloom = bloom
        self._scale = scale
        self._lift_scale = scale * lift
        self.y = float(y)
        self._cx = float(cx)
        self._items: list = []      # [centre_x, row, item, colour]
        self._char_piece: list[int] = []   # character -> the word it belongs to
        self._outline: list = []
        self._word_chars: list[int] = []   # timed token -> its first character
        self._glow: dict = {}       # char index -> [items], built only while active
        self._hit: dict = {}        # char index -> when the front reached it
        # Which characters the front has already passed. Separate from `_hit`
        # because a bloom ends while the front stays where it is: without this,
        # a character whose light had drained looked unstruck again, was struck
        # again on the next frame, and every sung letter pulsed for ever.
        self._struck: set = set()
        self._blurred = False
        self._lift: dict = {}       # char index -> pixels it is currently raised
        self._grown: dict = {}      # char index -> its scaled stand-in
        self._reached: list = []    # char index -> how far the front is past it
        self._showing: dict = {}    # char index -> (step, colour) on screen
        self._budget = 0            # new sizes still allowed this frame
        self._blooming = False
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
        pieces = split_for_wrapping(self.text)
        tokens = [(0.0, 0.0, piece) for piece, _glued in pieces]
        glued = [g for _piece, g in pieces]
        self._word_chars = self._map_words_to_text(words)
        rows = wrap_words(tokens, font_obj, key, space, wrap, glued)
        self._row_spans: list[tuple[float, float]] = []

        for r, row in enumerate(rows):
            # Per piece, because a script written without spaces gets none: the
            # gap belongs to the boundary, not to every boundary alike.
            gaps = [0 if k == 0 or glued[i] else space
                    for k, (i, _text, _w) in enumerate(row)]
            total = sum(w for _, _, w in row) + sum(gaps)
            # Never started off the left edge: a word too wide to break would
            # otherwise lose its beginning, which is worse than losing its end.
            x = max(EDGE_MARGIN * scale, cx - total / 2)
            self._row_spans.append((x, x + total))
            row_y = y + r * self.line_height
            for (_token_index, word_text, _), gap in zip(row, gaps, strict=True):
                x += gap
                for ch in word_text:
                    adv = measure(font_obj, key, ch)
                    # Only in keyed mode, the one mode with nothing drawn behind
                    # the text to keep it off a bright background. Elsewhere the
                    # cover wash is the contrast, and it is capped to stay that
                    # way whatever the cover started at.
                    outline = [canvas.create_text(x + dx, row_y + dy, text=ch,
                                                  anchor="nw", font=font,
                                                  fill=OUTLINE_COLOUR)
                               for dx, dy in ring_offsets(palette.outline)]
                    item = canvas.create_text(x, row_y, text=ch, anchor="nw",
                                              font=font, fill=palette.side)
                    self._items.append([x + adv / 2, r, item, palette.side])
                    self._char_piece.append(_token_index)
                    self._outline.extend(outline)
                    x += adv

        # The other way round, once: which characters make up each word. The
        # bloom is keyed to words and the sweep to characters, so both readings
        # are needed and neither is worth rebuilding per frame.
        self._piece_chars: dict[int, list[int]] = {}
        for index, piece in enumerate(self._char_piece):
            self._piece_chars.setdefault(piece, []).append(index)

        # And how long each word is sung for. The light drains on that clock
        # rather than on a fixed number of seconds, because the sweep crossing
        # the word is already on it: a constant made the two disagree, the glow
        # gone before a long word was half lit and still burning after a short
        # one had finished.
        self._piece_time: dict[int, float] = {}
        for piece, chars in self._piece_chars.items():
            timed = [w for k, w in enumerate(words)
                     if k < len(self._word_chars)
                     and chars[0] <= self._word_chars[k] <= chars[-1]]
            if timed:
                self._piece_time[piece] = (max(e for _s, e, _x in timed)
                                           - min(s for s, _e, _x in timed))

        self.height = len(rows) * self.line_height

    # --- lifecycle ---
    def move_to(self, y: float) -> None:
        """Shift the whole line, keeping the items that are already there."""
        delta = round(y - self.y)
        if delta:
            for item in self.item_ids():
                self.canvas.move(item, 0, delta)
            self.y += delta

    def recentre(self, cx: float) -> None:
        """Shift the whole line to a new horizontal centre.

        Every character's x is computed once, from the centre the window had
        when the line was built, and moving vertically is all that ever happens
        to a line afterwards. So a window that changes width leaves its lines
        centred on a box that no longer exists — which is what put the lyrics
        off to one side after the panel came back out of its compact size.
        """
        delta = round(cx - self._cx)
        if not delta:
            return
        for item in self.item_ids():
            self.canvas.move(item, delta, 0)
        self._cx += delta
        for entry in self._items:
            entry[0] += delta
        self._row_spans = [(a + delta, b + delta) for a, b in self._row_spans]

    def item_ids(self):
        yield from self._outline
        for entry in self._items:
            yield entry[2]
        for items in self._glow.values():
            yield from items
        # The grown stand-ins travel with the line too: one holding still at a
        # constant size while its line glided would come adrift of its own word.
        yield from self._grown.values()

    def destroy(self) -> None:
        for item in self.item_ids():
            self.canvas.delete(item)
        self._items.clear()
        self._outline.clear()
        self._glow.clear()

    def set_palette(self, palette) -> None:
        """Adopt a new palette without rebuilding the line.

        The cover arrives a second or two after the lyrics are already on
        screen, so recreating every glyph to recolour it would show as a flicker
        in the middle of a song. Dropping the cached state is enough: the next
        restyle or sweep repaints each character it finds out of date.
        """
        if palette is self.palette:
            return
        self.palette = palette
        self._state = None

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._state = None
        if active:
            self._build_glow()
        else:
            self._settle_grown()
            self._settle_lifts()
            self._clear_glow()
            self._hit.clear()
            self._struck.clear()

    def _build_glow(self) -> None:
        """Make every character's halo up front, once.

        Measured: making and dropping four copies as each character is reached
        costs 4.8 ms of a 16 ms frame when nine are alight, where recolouring
        copies that already exist costs 1.9. Building the line's worth in one go
        costs 6 ms, and it happens when the line changes rather than while it is
        being sung.
        """
        if self._glow or not self.palette.glow or self.bloom <= 0:
            return
        self._blurred = bloom.available(self._font)
        for index, entry in enumerate(self._items):
            ch = self.canvas.itemcget(entry[2], "text")
            x, y = self.canvas.coords(entry[2])
            if self._blurred:
                # One image, swapped rather than recoloured. The blur is real,
                # so a curve does not come out doubled.
                item = self.canvas.create_image(x - bloom.PAD, y - bloom.PAD,
                                                anchor="nw", state="hidden")
                self.canvas.tag_lower(item, entry[2])
                self._glow[index] = [item]
                # And a stand-in for the letter itself while it is growing. Tk
                # cannot scale a text item: its font sizes are integers, and a
                # 6 % growth lands on two of them, which is the staircase this
                # is trying to avoid. An image resamples to any fraction.
                stand_in = self.canvas.create_image(x, y, anchor="nw",
                                                    state="hidden")
                self._grown[index] = stand_in
                continue
            items = [self.canvas.create_text(x + dx, y + dy, text=ch, anchor="nw",
                                             font=self._font,
                                             fill=self.palette.bloom(0.0))
                     for dx, dy in GLOW_OFFSETS]
            for item in items:
                self.canvas.tag_lower(item, entry[2])
            self._glow[index] = items

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

    def show_lit(self) -> None:
        """The whole line at full strength.

        For an active line with no word timing. Leaving it at the unsung level
        would be saying "none of this has been sung" about the line you are
        listening to — the sweep is unavailable, not the knowledge that this is
        the line.
        """
        self.show_inactive(self.palette.sung)

    def show_sweep(self, word_index: int, fraction: float) -> None:
        """The active line, swept character by character."""
        front = self._front_at(word_index, fraction)
        if front == self._state:
            return
        self._state = front
        active_row, front_x = front or (-1, 0.0)
        palette = self.palette
        struck = time.monotonic()
        reached = [0.0] * len(self._items)

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
            reached[i] = t
        self._reached = reached

        # A whole word at once, not each of its letters in turn. Lighting them
        # one by one made the line ripple, where what is being imitated strikes
        # the word and lets it ring — and a word's letters singing together is
        # also what a word is.
        for piece, chars in self._piece_chars.items():
            front_here = reached[chars[0]] >= 0.5
            if front_here and piece not in self._struck:
                self._struck.add(piece)
                span = self._piece_time.get(piece, BLOOM_FLOOR_S) * self.bloom
                span = max(BLOOM_FLOOR_S, min(BLOOM_CEILING_S, span))
                for index in chars:
                    self._hit[index] = (struck, span)
            elif not front_here and reached[chars[0]] <= 0.05:
                # Re-armed only when the front falls back behind the word, so a
                # seek gives a line its strike again and nothing else does.
                self._struck.discard(piece)
                for index in chars:
                    self._hit.pop(index, None)

    def _raise_char(self, index: int, to: float) -> None:
        """Hold a character `to` pixels above where it was laid out."""
        current = self._lift.get(index, 0.0)
        delta = round(to - current)
        if not delta:
            return
        self._lift[index] = current + delta
        self.canvas.move(self._items[index][2], 0, -delta)
        for item in self._glow.get(index, ()):
            self.canvas.move(item, 0, -delta)
        stand_in = self._grown.get(index)
        if stand_in is not None:
            self.canvas.move(stand_in, 0, -delta)

    def _grow_char(self, index: int, shape: float) -> None:
        """Show a grown stand-in for a letter, or the letter itself at rest.

        While a word grows, its letters are either sung or unsung with nothing
        between: the ramp lives in the front's own width, which is under one
        character, so quantising it for the length of the strike costs a frame
        of a boundary and buys the whole cache. Sixty-four ramp steps would have
        been 21,504 images and a quarter of a gigabyte.
        """
        item = self._grown.get(index)
        if item is None:
            return
        step = round(shape * bloom.SCALES)
        text = self._items[index][2]
        if step <= 0:
            if self._showing.pop(index, None) is not None:
                self.canvas.itemconfigure(item, state="hidden")
                self.canvas.itemconfigure(text, state="normal")
            return
        sung = self._reached[index] >= 0.5 if index < len(self._reached) else True
        colour = rgb_of(self.palette.sung if sung else self.palette.unsung)
        want = (step, colour)
        if self._showing.get(index) == want:
            return
        char = self.canvas.itemcget(text, "text")
        if not bloom.ready(char, self._font, step, colour):
            if self._budget <= 0:
                return          # hold the size it has; the next frame may build
            self._budget -= 1
        image = bloom.grown(char, self._font, step, colour)
        if image is None:
            return
        x, y = self.canvas.coords(text)
        dx, dy = bloom.offset(char, self._font, step)
        self.canvas.coords(item, x + dx + bloom.PAD, y + dy + bloom.PAD)
        self.canvas.itemconfigure(item, image=image, state="normal")
        self.canvas.itemconfigure(text, state="hidden")
        self._showing[index] = want

    def _settle_grown(self) -> None:
        for index in list(self._showing):
            self._grow_char(index, 0.0)
        self._showing.clear()

    def _settle_lifts(self) -> None:
        for index in list(self._lift):
            self._raise_char(index, 0.0)
        self._lift.clear()

    def advance_bloom(self, now: float) -> bool:
        """Fade each struck character's halo. True while any is still alight.

        Time rather than position, which is the whole difference. A halo keyed
        to where the front is travels with it like a carried lamp; one keyed to
        when the front arrived stays behind, so each word is *struck* and the
        light it leaves drains away.
        """
        if self.bloom <= 0 or not self._hit:
            self._settle_grown()
            self._settle_lifts()
            return False
        alight = False
        self._budget = NEW_SIZES_PER_FRAME
        for index, (when, span) in list(self._hit.items()):
            age = (now - when) / span
            level = GLOW_PEAK * (1.0 - age) if age < 1.0 else 0.0
            if level <= 0.0:
                del self._hit[index]
            else:
                alight = True
            # The letters themselves, not only the light behind them. Highest
            # at the strike and settling as the light drains, so a word visibly
            # answers rather than being decorated.
            shape = _lift_shape(age)
            self._raise_char(index, LIFT_PX * self._lift_scale * shape)
            self._grow_char(index, shape)
            if not self._glow:
                continue
            if self._blurred:
                char = self.canvas.itemcget(self._items[index][2], "text")
                step = int(level / GLOW_PEAK * bloom.LEVELS + 0.5)
                if not bloom.blurred_ready(char, self._font, step):
                    if self._budget <= 0:
                        continue    # keep the halo it has for one more frame
                    self._budget -= 1
                image = bloom.glyph(char, self._font, step)
                for item in self._glow.get(index, ()):
                    if image is None:
                        self.canvas.itemconfigure(item, state="hidden")
                    else:
                        self.canvas.itemconfigure(item, image=image,
                                                  state="normal")
                continue
            shade = self.palette.bloom(level)
            for item in self._glow.get(index, ()):
                self.canvas.itemconfigure(item, fill=shade)
        self._blooming = alight
        return alight

    def _clear_glow(self) -> None:
        self._blooming = False
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
