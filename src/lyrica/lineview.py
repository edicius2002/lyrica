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
import math
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

# A struck word moves on a fixed clock rather than borrowing the bloom's. When
# the clock followed word duration, a short word rose in one frame and a held
# word stayed swollen for a second. The light may honestly follow duration; the
# physical gesture has to remain recognisable from one word to the next.
GROW_ATTACK_S = 0.105
GROW_SETTLE_S = 0.270
GROW_SPAN_S = GROW_ATTACK_S + GROW_SETTLE_S
STRIKE_ATTACK = GROW_ATTACK_S / GROW_SPAN_S


def _strike_shape(age: float) -> float:
    """How far into its strike a word stands, 0..1, `age` through its light.

    The two halves are shaped separately because being struck and relaxing are
    not the same gesture: nearly there at once, then a long way back.
    """
    if age <= 0.0:
        return 0.0
    if age < STRIKE_ATTACK:
        return cubic_bezier(age / STRIKE_ATTACK, STRIKE_UP)
    fall = (age - STRIKE_ATTACK) / (1.0 - STRIKE_ATTACK)
    return 1.0 - cubic_bezier(min(1.0, fall), STRIKE_DOWN)


# Whether a word may *begin* building sizes it does not have yet, rather than how
# many letters of it may. A word is built whole or not at all — see
# `_grow_piece` — so this gates how many words start in one frame, and a word
# that starts is allowed to overshoot to stay in one piece.
#
# Measured at 0.329 ms per image, not the 0.7 ms an earlier estimate assumed, so
# one whole step of a twelve-letter word costs 3.95 ms of a 16 ms frame and even
# a long word finishes inside one. At three, a second word waits a frame rather
# than sharing the cost of the first, which is what keeps the worst frame bounded
# by the longest word on the row instead of by the whole row.
NEW_SIZES_PER_FRAME = 3

# The halo counts separately. Sharing one pool, growth was served first and the
# light went hungry: measured over twelve cold frames, the halo got new images in
# six of them, in bursts of three and one, so a decay that is smooth in time was
# shown in irregular jumps.
NEW_HALOS_PER_FRAME = 3


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
                 growth: float = 0.14, lean: float = 0.0):
        self.canvas = canvas
        self.palette = palette
        self.words = words
        self.feather = feather * scale
        self.bloom = bloom
        self.growth = max(0.0, float(growth))
        self._scale = scale
        self.y = float(y)
        self._cx = float(cx)
        # How far this line's voice wants to stand from the column, and how far
        # it actually does. The two differ whenever the line is too long to move
        # the whole way, which is the point: see `fit`.
        self.lean = float(lean)
        self._shift = 0.0
        self._tag = f"lyrica-line-{id(self)}"
        # Separate tags let the frame-boundary visibility contract repair the
        # real Tk presentation in two Tcl calls, independent of the renderer's
        # cached target state.  Doing this per glyph on every frame would make
        # a long wrapped preview needlessly expensive.
        self._text_tag = f"{self._tag}-text"
        self._outline_tag = f"{self._tag}-outline"
        self._items: list = []      # [centre_x, row, item, colour]
        self._char_widths: list[float] = []
        self._char_piece: list[int] = []   # character -> the word it belongs to
        # The letters themselves, so nothing has to ask Tk what it is drawing.
        # Every lit character used to be read back from the canvas once a frame
        # for its halo and again for its size, which is a round trip apiece for
        # something settled when the line was laid out.
        self._chars: list[str] = []
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
        self._grown: dict = {}      # char index -> its scaled stand-in
        self._reached: list = []    # char index -> how far the front is past it
        self._showing: dict = {}    # char index -> (step, colour) on screen
        # How far each halo has been carried from its resting place to stay with
        # the letter it belongs to. Kept so the move can be recomputed from the
        # letter rather than applied again on top of itself.
        self._glow_dx: dict = {}    # char index -> offset now applied
        self._halo_shown: dict = {}  # char index -> halo level on screen
        self._budget = 0            # words still allowed to build sizes
        self._halo_budget = 0       # new halo levels still allowed this frame
        self._blooming = False
        self._active = False
        self._visible = True
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
                piece_tag = self._piece_tag(_token_index)
                for ch in word_text:
                    adv = measure(font_obj, key, ch)
                    # Only in keyed mode, the one mode with nothing drawn behind
                    # the text to keep it off a bright background. Elsewhere the
                    # cover wash is the contrast, and it is capped to stay that
                    # way whatever the cover started at.
                    outline = [canvas.create_text(x + dx, row_y + dy, text=ch,
                                                  anchor="nw", font=font,
                                                  fill=OUTLINE_COLOUR,
                                                  tags=(self._tag, piece_tag,
                                                        self._outline_tag))
                               for dx, dy in ring_offsets(palette.outline)]
                    item = canvas.create_text(x, row_y, text=ch, anchor="nw",
                                              font=font, fill=palette.side,
                                              tags=(self._tag, piece_tag,
                                                    self._text_tag))
                    self._items.append([x + adv / 2, r, item, palette.side])
                    self._char_widths.append(adv)
                    self._char_piece.append(_token_index)
                    self._chars.append(ch)
                    self._outline.extend(outline)
                    x += adv

        # The other way round, once: which characters make up each word. The
        # bloom is keyed to words and the sweep to characters, so both readings
        # are needed and neither is worth rebuilding per frame.
        self._piece_chars: dict[int, list[int]] = {}
        for index, piece in enumerate(self._char_piece):
            self._piece_chars.setdefault(piece, []).append(index)
        self._piece_centres = {
            piece: (
                self._items[chars[0]][0] - self._char_widths[chars[0]] / 2
                + self._items[chars[-1]][0] + self._char_widths[chars[-1]] / 2
            ) / 2
            for piece, chars in self._piece_chars.items() if chars
        }
        self._piece_widths = {
            piece: sum(self._char_widths[i] for i in chars)
            for piece, chars in self._piece_chars.items()
        }
        self._row_pieces: dict[int, list[int]] = {}
        for piece, chars in self._piece_chars.items():
            if chars:
                self._row_pieces.setdefault(self._items[chars[0]][1], []).append(piece)
        self._piece_growth: dict[int, float] = {}
        self._piece_layout_shift = {piece: 0.0 for piece in self._piece_chars}

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

    @property
    def effect_padding(self) -> int:
        """Maximum ink outside the resting line box, in canvas pixels.

        A line's nominal height does not include either the bloom or the half
        of its own height gained by growing about its centre. Geometry that
        ignores both lets a transition show a perfectly laid-out text item and
        still cuts the visible glyph image at the window edge.
        """
        halo = bloom.OUTER_RADIUS if self.bloom > 0 and self.palette.glow else 0
        growth = self.line_height * self.growth / 2
        return math.ceil(max(self.palette.outline, halo) + growth)

    @property
    def glyph_padding(self) -> int:
        """Ink outside the resting box, excluding the deliberately soft halo.

        Adjacent lyric rows may let their light meet, just as two lit words in
        one row do. Their actual glyphs, outlines and maximum growth may not.
        Keeping this separate from ``effect_padding`` prevents a harmless halo
        intersection from turning into a sudden geometric correction.
        """
        growth = self.line_height * self.growth / 2
        return math.ceil(max(self.palette.outline, growth))

    def visual_vertical_span(self) -> tuple[float, float]:
        """Top and bottom of every possible visible pixel in this line."""
        pad = self.effect_padding
        return self.y - pad, self.y + self.height + pad

    def glyph_vertical_span(self) -> tuple[float, float]:
        """Top and bottom that another lyric's actual ink must not cross."""
        pad = self.glyph_padding
        return self.y - pad, self.y + self.height + pad

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
        if delta:
            self._cx += delta
            self._slide(delta)

    def fit(self, left: float, right: float) -> None:
        """Stand this line's voice aside, as far as the box between the margins
        allows.

        The offset is a fixed step rather than an alignment, and it is clipped
        here rather than granted. Aligning outright is what the reference does,
        but the reference is not working inside a panel whose width comes from
        its own longest line: there, a short line ends up against the edge and
        the eye crosses the whole panel every time the singers trade. A step
        that a long line simply cannot take in full says the same thing about
        who is singing without ever putting a word near the margin — and the
        lines that keep it whole are the short ones, which are the ones a duet
        actually alternates on.

        Idempotent, so it can be called again whenever the box changes without
        the line creeping further each time.
        """
        want = self.lean
        # Leave enough room for every word on a row to complete its strike.
        # Usually only one or two overlap, but reserving the full possible
        # width makes a fast phrase no less safe than a slow one.
        guards = {
            row: sum(self._piece_widths[piece] for piece in pieces)
            * self.growth / 2
            for row, pieces in self._row_pieces.items()
        }
        lo = min((a - guards.get(row, 0.0)
                  for row, (a, _b) in enumerate(self._row_spans)),
                 default=0.0) - self._shift
        hi = max((b + guards.get(row, 0.0)
                  for row, (_a, b) in enumerate(self._row_spans)),
                 default=0.0) - self._shift
        if want > 0:
            want = max(0.0, min(want, right - hi))
        elif want < 0:
            want = min(0.0, max(want, left - lo))
        delta = round(want - self._shift)
        if delta:
            self._shift += delta
            self._slide(delta)

    def _slide(self, delta: int) -> None:
        """Move every part of the line sideways, geometry included."""
        for item in self.item_ids():
            self.canvas.move(item, delta, 0)
        for entry in self._items:
            entry[0] += delta
        for piece in self._piece_centres:
            self._piece_centres[piece] += delta
        self._row_spans = [(a + delta, b + delta) for a, b in self._row_spans]

    def _set_piece_growth(self, piece: int, shape: float) -> None:
        """Give an expanding word room without changing the resting layout.

        The row grows about its own centre. Words before a strike breathe left,
        words after it breathe right, and simultaneous strikes share the same
        calculation. Because the stored geometry remains at rest, reducing to
        zero returns to the exact original pixels instead of accumulating
        rounding drift frame after frame.
        """
        shape = max(0.0, min(1.0, float(shape)))
        if abs(self._piece_growth.get(piece, 0.0) - shape) < 1e-6:
            return
        if shape:
            self._piece_growth[piece] = shape
        else:
            self._piece_growth.pop(piece, None)
        self._reflow_growth()

    def _piece_tag(self, piece: int) -> str:
        return f"{self._tag}-piece-{piece}"

    def _reflow_growth(self) -> None:
        desired = {piece: 0.0 for piece in self._piece_layout_shift}
        for pieces in self._row_pieces.values():
            extra = {
                piece: self._piece_widths[piece] * self.growth
                * self._piece_growth.get(piece, 0.0)
                for piece in pieces
            }
            cursor = -sum(extra.values()) / 2
            for piece in pieces:
                desired[piece] = cursor + extra[piece] / 2
                cursor += extra[piece]

        for piece, target in desired.items():
            current = self._piece_layout_shift[piece]
            delta = target - current
            if abs(delta) < 1e-6:
                continue
            # One Tcl round trip for the word, regardless of how many letters,
            # outlines, halo layers and stand-ins it currently carries.
            self.canvas.move(self._piece_tag(piece), delta, 0)
            self._piece_layout_shift[piece] = target

    def item_ids(self):
        yield from self._outline
        for entry in self._items:
            yield entry[2]
        for items in self._glow.values():
            yield from items
        # The grown stand-ins travel with the line too: one holding still at a
        # constant size while its line glided would come adrift of its own word.
        yield from self._grown.values()

    def raise_layer(self) -> None:
        """Raise the complete line while preserving its internal item order."""
        self.canvas.tag_raise(self._tag)

    def set_visible(self, visible: bool) -> None:
        """Actually hide a spent line instead of painting opaque background."""
        visible = bool(visible)
        if visible == self._visible:
            return
        self._visible = visible
        state = "normal" if visible else "hidden"
        for item in self._outline:
            self.canvas.itemconfigure(item, state=state)
        for index, entry in enumerate(self._items):
            text_state = "hidden" if visible and index in self._showing else state
            self.canvas.itemconfigure(entry[2], state=text_state)
        if not visible:
            for items in self._glow.values():
                for item in items:
                    self.canvas.itemconfigure(item, state="hidden")
            for item in self._grown.values():
                self.canvas.itemconfigure(item, state="hidden")
            return
        # Coming back, the stand-in of a letter whose text is held hidden above
        # has to be put back on screen and the halo asked for again. Neither
        # happened, and both are only invisible while nothing else disturbs
        # them: a line hidden mid-strike and shown again had hidden text behind
        # a hidden image, so its grown letters were simply not drawn.
        for index in self._showing:
            self.canvas.itemconfigure(self._grown[index], state="normal")
        self._halo_shown.clear()

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
        if self._glow:
            return
        self._blurred = bloom.available(self._font)
        for index, entry in enumerate(self._items):
            ch = self._chars[index]
            x, y = self.canvas.coords(entry[2])
            if self._blurred and self.growth > 0 and index not in self._grown:
                # Growth is independent of the halo. Keyed composition cannot
                # safely draw light behind text, but it can still let the word
                # itself move.
                stand_in = self.canvas.create_image(x, y, anchor="nw",
                                                    state="hidden",
                                                    tags=(self._tag, self._piece_tag(
                                                        self._char_piece[index])))
                self._grown[index] = stand_in
            if not self.palette.glow or self.bloom <= 0:
                continue
            if self._blurred:
                # One image, swapped rather than recoloured. The blur is real,
                # so a curve does not come out doubled.
                item = self.canvas.create_image(x - bloom.PAD, y - bloom.PAD,
                                                anchor="nw", state="hidden",
                                                tags=(self._tag, self._piece_tag(
                                                    self._char_piece[index])))
                self.canvas.tag_lower(item, entry[2])
                self._glow[index] = [item]
                continue
            items = [self.canvas.create_text(x + dx, y + dy, text=ch, anchor="nw",
                                             font=self._font,
                                             fill=self.palette.bloom(0.0),
                                             tags=(self._tag, self._piece_tag(
                                                 self._char_piece[index])))
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

    def present_inactive(self, colour: str) -> None:
        """Reassert a readable inactive row on the actual Tk canvas.

        Scene fades write presentation colours directly without changing the
        cached target colour, and Tk item state can likewise outlive a geometry
        change.  A normal ``show_inactive`` then legitimately sees its target
        already cached and does no work.  This is the frame-boundary repair for
        the one row whose presence is a hard UI contract: the upcoming lyric.

        `colour` is a presentation colour, so it obeys the same rule as those
        scene fades: the renderer's cached target survives it untouched. Writing
        the presented colour into the cache instead made every following
        ``show_inactive`` miss — the restyle then repainted the whole row glyph
        by glyph, once per frame, only for the two calls below to overwrite the
        lot. Measured on a wrapped preview: 100 item updates a frame, replaced
        by none.
        """
        self.set_active(False)
        self._visible = True
        self.canvas.itemconfigure(self._outline_tag, state="normal")
        self.canvas.itemconfigure(
            self._text_tag, state="normal", fill=colour)

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
            if (front_here and piece not in self._struck
                    and (self.bloom > 0 or self.growth > 0)):
                self._struck.add(piece)
                if self.bloom > 0:
                    span = self._piece_time.get(piece, BLOOM_FLOOR_S) * self.bloom
                    span = max(BLOOM_FLOOR_S, min(BLOOM_CEILING_S, span))
                else:
                    span = 0.0
                for index in chars:
                    self._hit[index] = (struck, span)
            elif not front_here and reached[chars[0]] <= 0.05:
                # Re-armed only when the front falls back behind the word, so a
                # seek gives a line its strike again and nothing else does.
                self._struck.discard(piece)
                for index in chars:
                    self._hit.pop(index, None)

    def _group_dx(self, index: int, scale: float) -> float:
        """How far this letter stands from where it rests, at `scale`.

        A word swells about its own centre, so its letters travel apart as well
        as thicken. Derived from the resting geometry every time rather than
        accumulated, so returning to `scale` 1.0 returns to the original pixel.
        """
        piece = self._char_piece[index]
        centre = self._piece_centres.get(piece, self._items[index][0])
        return (self._items[index][0] - centre) * (scale - 1.0)

    def _grow_piece(self, piece: int, chars: list[int], shape: float) -> None:
        """Give a whole word one scale, or leave it the one it already has.

        Atomic on purpose, and that is the whole point of the method. Budgeting
        per letter instead let a word wear several scales in the same frame:
        measured on a cold cache, the eight letters of one word showed steps 1,
        2, 4 and 7 at once — a tenth of the growth in spread — and which letters
        led changed every frame, so the word deformed differently each time
        rather than swelling. That is what read as trembling.

        So the budget decides whether this word may *start* building sizes it
        lacks, never how far it gets. A word that starts finishes, because
        stopping half way is exactly the tear being prevented; a word that may
        not start keeps the size it is already showing for one more frame, which
        at an eighth of a fourteen-per-cent growth nobody sees.

        While a word grows, its letters are either sung or unsung with nothing
        between: the ramp lives in the front's own width, which is under one
        character, so quantising it for the length of the strike costs a frame
        of a boundary and buys the whole cache. Sixty-four ramp steps would have
        been 21,504 images and a quarter of a gigabyte.
        """
        step = round(shape * bloom.SCALES)
        if step <= 0:
            self._set_piece_growth(piece, 0.0)
            for index in chars:
                self._retire_grown(index)
            return
        wants = {}
        for index in chars:
            if index not in self._grown:
                return          # this line carries no stand-ins to grow
            sung = (self._reached[index] >= 0.5
                    if index < len(self._reached) else True)
            colour = rgb_of(self.palette.sung if sung else self.palette.unsung)
            wants[index] = (step, colour, self._chars[index])
        if all(self._showing.get(index) == (want[0], want[1])
               for index, want in wants.items()):
            return
        missing = [index for index, (level, colour, char) in wants.items()
                   if not bloom.ready(char, self._font, level, colour,
                                      self.growth)]
        if missing:
            if self._budget <= 0:
                return      # hold this word whole; the next frame may build
            # Charged for every image it takes, so a word that has just spent
            # the frame's worth stops the next word starting rather than adding
            # its own cost on top. The overshoot is bounded by one word.
            self._budget -= len(missing)
            for index in missing:
                level, colour, char = wants[index]
                if bloom.grown(char, self._font, level, colour,
                               self.growth) is None:
                    return      # no images to be had; leave the text as it is
        # The layout moves only now that every letter's size exists, so the
        # neighbours never breathe for a growth that is not on screen yet.
        self._set_piece_growth(piece, step / bloom.SCALES)
        scale = 1.0 + self.growth * step / bloom.SCALES
        for index in chars:
            level, colour, char = wants[index]
            if self._showing.get(index) == (level, colour):
                continue
            made = bloom.grown(char, self._font, level, colour, self.growth)
            if made is None:
                continue
            image, dx, dy = made
            group_dx = self._group_dx(index, scale)
            text = self._items[index][2]
            x, y = self.canvas.coords(text)
            self.canvas.coords(self._grown[index], x + dx + group_dx, y + dy)
            if index in self._showing:
                # Already standing in for its letter: the picture changes, the
                # two states do not. A whole word's worth of redundant state
                # changes a frame is what the atomic swap cannot afford.
                self.canvas.itemconfigure(self._grown[index], image=image)
            else:
                self.canvas.itemconfigure(self._grown[index], image=image,
                                          state="normal")
                self.canvas.itemconfigure(text, state="hidden")
            self._showing[index] = (level, colour)
            # The light belongs to this letter, so it goes where the letter
            # goes. Left behind, it sat up to twelve pixels away at full growth
            # and slid back, which is the halo that appeared to drift about
            # behind the illumination.
            self._place_halo(index, group_dx, at=(x, y))

    def _retire_grown(self, index: int) -> None:
        """Put a letter back to its own size, drawn as text again."""
        item = self._grown.get(index)
        if item is None:
            return
        if self._showing.pop(index, None) is not None:
            self.canvas.itemconfigure(item, state="hidden")
            self.canvas.itemconfigure(self._items[index][2], state="normal")
        self._place_halo(index, 0.0)

    def _place_halo(self, index: int, dx: float, at=None) -> None:
        """Carry this character's halo `dx` from where its letter rests.

        `at` is the letter's own corner when the caller has just read it, which
        saves asking the canvas for the same two numbers twice in a frame.
        """
        items = self._glow.get(index)
        if not items:
            return
        if abs(self._glow_dx.get(index, 0.0) - dx) < 1e-6:
            return
        self._glow_dx[index] = dx
        x, y = at if at is not None else self.canvas.coords(self._items[index][2])
        if self._blurred:
            self.canvas.coords(items[0], x - bloom.PAD + dx, y - bloom.PAD)
            return
        for item, (ox, oy) in zip(items, GLOW_OFFSETS, strict=True):
            self.canvas.coords(item, x + ox + dx, y + oy)

    def _settle_grown(self) -> None:
        """Every letter back to its own size, and the row back to its own place.

        Both halves matter. Putting the letters back without releasing the
        growth left the neighbours of a word that was mid-strike standing aside
        for an expansion that had already gone — six and a half pixels, held for
        as long as the line lived.
        """
        for index in list(self._showing):
            self._retire_grown(index)
        self._showing.clear()
        for piece in list(self._piece_growth):
            self._set_piece_growth(piece, 0.0)

    def advance_bloom(self, now: float) -> bool:
        """Fade each struck character's halo. True while any is still alight.

        Time rather than position, which is the whole difference. A halo keyed
        to where the front is travels with it like a carried lamp; one keyed to
        when the front arrived stays behind, so each word is *struck* and the
        light it leaves drains away.

        Walked word by word rather than letter by letter, because a word is the
        unit that is struck: its letters share one clock, and they have to share
        one size in any given frame or the word trembles instead of swelling.
        """
        if not self._hit:
            self._settle_grown()
            return False
        alight = False
        self._budget = NEW_SIZES_PER_FRAME
        self._halo_budget = NEW_HALOS_PER_FRAME
        for piece, chars in self._piece_chars.items():
            struck = [index for index in chars if index in self._hit]
            if not struck:
                continue
            # One clock for the word, read from it once. Every letter of a word
            # is struck in the same instant by `show_sweep`, and taking the time
            # per letter only invited them to disagree.
            when, span = self._hit[struck[0]]
            elapsed = max(0.0, now - when)
            bloom_age = elapsed / span if span > 0 else 1.0
            grow_age = elapsed / GROW_SPAN_S
            level = (GLOW_PEAK * (1.0 - bloom_age)
                     if self.bloom > 0 and bloom_age < 1.0 else 0.0)
            growing = self.growth > 0 and grow_age < 1.0
            if level <= 0.0 and not growing:
                for index in struck:
                    del self._hit[index]
            else:
                alight = True
            # The letters themselves, not only the light behind them: a halo
            # behind text that is not reacting reads as an effect laid over the
            # words rather than as the words being sung.
            self._grow_piece(piece, struck,
                             _strike_shape(grow_age) if growing else 0.0)
            if not self._glow:
                continue
            self._fade_halo(struck, level)
        self._blooming = alight
        return alight

    def _fade_halo(self, chars: list[int], level: float) -> None:
        """Show the halo of every letter in this word at `level`."""
        if not self._blurred:
            shade = self.palette.bloom(level)
            for index in chars:
                for item in self._glow.get(index, ()):
                    self.canvas.itemconfigure(item, fill=shade)
            return
        # The light is quantised to `LEVELS`, so it holds the same picture for
        # four or five frames at a time. Told again each frame, that was a whole
        # word's worth of Tcl for an unchanged image.
        step = int(level / GLOW_PEAK * bloom.LEVELS + 0.5)
        for index in chars:
            if self._halo_shown.get(index) == step:
                continue
            char = self._chars[index]
            if not bloom.blurred_ready(char, self._font, step):
                if self._halo_budget <= 0:
                    continue    # keep the halo it has for one more frame
                self._halo_budget -= 1
            image = bloom.glyph(char, self._font, step)
            for item in self._glow.get(index, ()):
                if image is None:
                    self.canvas.itemconfigure(item, state="hidden")
                else:
                    self.canvas.itemconfigure(item, image=image, state="normal")
            self._halo_shown[index] = step

    def _clear_glow(self) -> None:
        self._blooming = False
        # The carried offsets and shown levels go with the items they described.
        # Kept, a rebuilt halo would be recorded as already moved and already
        # lit, and would never travel or change again.
        self._glow_dx.clear()
        self._halo_shown.clear()
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
