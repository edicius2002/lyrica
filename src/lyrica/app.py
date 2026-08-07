"""Lyrica — an always-on-top synced lyrics overlay.

Reads whatever is playing from the platform's media session and shows the
lyrics over everything else, swept word by word.

Run:      python -m lyrica   (or the `lyrica` console script)
Keys:     Esc = quit | right click = quit | drag with mouse = move
          +/- = nudge sync offset by ±0.25 s  (needs the overlay focused)
Global:   Ctrl+Alt+plus / Ctrl+Alt+minus = resize | Ctrl+Alt+0 = designed size
"""
import logging
import logging.handlers
import os
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import font as tkfont
from typing import ClassVar

from lyrica import artwork, config, hotkeys, motion, songcolour
from lyrica import chrome as chrome_mod
from lyrica import palette as palette_mod
from lyrica.lineview import LineView
from lyrica.lyrics import Lyrics
from lyrica.providers import fetch_for_candidates
from lyrica.sessions import Snapshot, create_reader

# Logical pixels at 96 dpi; scaled by the chrome's DPI factor at startup.
#
# Three lines: the one before, the one being sung, and the one coming. The
# height follows from that plus the card and a fade band at each edge — it is
# derived, not chosen, and shrinking the count is what shrinks the window.
WIDTH, HEIGHT = 900, 300
WRAP = 800
ROW_GAP = 10

# The band at each edge where a line fades away. Shallower now that only one
# line sits either side: deep enough to read as a fade rather than a cut,
# shallow enough that the neighbours are still properly legible.
FADE_ZONE = 42

# Where the line being sung sits, as a fraction of the window height. Derived
# rather than chosen: low enough that the line above clears the card and its
# fade band, high enough that the line below clears the bottom one.
ANCHOR = 0.55

FONT_TITLE = ("Segoe UI Semibold", -20)
FONT_ARTIST = ("Segoe UI", -16)
THUMB_SIZE = 62

# One size for every lyric line. A role change that also changed size would
# force a relayout, which is a rebuild wearing a different name — and lines
# reading as louder or quieter rather than bigger or smaller is what the
# reference does anyway.
FONT_LINE = ("Segoe UI", -30, "bold")

SLOW_TICK_MS = 100
FAST_TICK_MS = 16   # 60 Hz; measured at ~1% of this machine with stable items
DRAG_TICK_MS = 120  # while the window is being moved, the hand comes first

# Drop the blur while dragging. Everything measurable inside the process came
# back clean — the move lands in ~2.4 ms, the loop stalls twice in eight
# seconds — which leaves the compositor's own work, outside this process and
# not measurable from here. Switching the effect costs 0.006 ms, so this is
# cheap to try and cheap to turn off if it makes no difference.
SUSPEND_GLASS_WHILE_DRAGGING = True

# Lyrics feel late when they land exactly on the beat: you read a line as it
# begins, so it has to be there fractionally before the voice. better-lyrics
# settled on the same correction, and those numbers are adopted, not guessed.
LINE_LEAD_S = 0.115
WORD_LEAD_S = 0.150

# How many lines either side of the current one are kept on screen. One, so the
# view is the line before, the line now, and the line next — and nothing else
# competing for the glance.
CONTEXT = 1

# How much one press of the resize keys moves the size. A tenth: coarse enough
# that a couple of presses is a visible change, fine enough that the range is
# not crossed by accident.
SIZE_STEP = 0.1

# How far the pointer may travel and still count as a click rather than a drag.
CLICK_SLACK = 4

# How close the reported position must come before a seek is considered landed.
# Generous, because the player moves to roughly where it was asked rather than
# exactly, and because playback keeps running while the request travels.
SEEK_SETTLED_S = 2.5

logger = logging.getLogger(__name__)


def _scaled_font(spec: tuple, scale: float) -> tuple:
    family, size, *rest = spec
    return (family, round(size * scale), *rest)


class Overlay:
    def __init__(self):
        self.reader = create_reader(interval=0.5)
        self.hotkeys = hotkeys.create_listener()
        self.lyrics: Lyrics | None = None
        self.track_key = ""
        self.fetch_gen = 0
        self.offset = 0.0
        self.line_index = -1

        self._views: dict[int, LineView] = {}
        self._glides: dict[int, motion.Glide] = {}
        self._targets: dict[int, float] = {}
        self._header_text = None
        self._dragging = False
        self._drag_at = (None, None)
        self._press_at = (0, 0)
        self._press_y = 0
        self._moved = False
        self._pending_art = None
        self._cover_data = None     # kept so a resize can re-derive both images
        self._backdrop_item = None
        self._backdrop_photo = None
        self._thumb_image = None
        self._thumb_photo = None
        self._card_text = None
        self._card_raw = None
        self._awaiting_seek = None

        # Before Tk exists: Tk reads the display metrics when the root window is
        # created, so declaring DPI awareness afterwards leaves it holding
        # virtualised numbers and every geometry it reports is off by the scale.
        # The display's own scale, times whatever size the user asked for. They
        # multiply because they are the same kind of quantity: both say how many
        # device pixels a designed unit is worth, and folding them together here
        # means every measurement downstream is scaled once, by one number.
        self._dpi_scale = chrome_mod.prepare()
        self._size = config.size_scale()
        scale_hint = self._dpi_scale * self._size

        self.root = tk.Tk()
        self.root.title("Lyrica")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Chrome decides how the window composites, and everything visual
        # follows from that: glass adds light and needs no outline, keyed
        # replaces colour and cannot survive without one.
        self.chrome = chrome_mod.setup(self.root, scale_hint)
        self.palette = palette_mod.for_chrome(self.chrome)
        scale = self.chrome.scale

        self.width = self.chrome.px(WIDTH)
        self.height = self.chrome.px(HEIGHT)
        self.wrap = self.chrome.px(WRAP)
        self.row_gap = self.chrome.px(ROW_GAP)
        self.f_title = _scaled_font(FONT_TITLE, scale)
        self.f_artist = _scaled_font(FONT_ARTIST, scale)
        self.f_line = _scaled_font(FONT_LINE, scale)
        # Made once. Creating a Font is a round trip into Tk, and these were
        # being rebuilt on every tick just to measure a string.
        self._title_font = tkfont.Font(font=self.f_title)
        self._artist_font = tkfont.Font(font=self.f_artist)
        self.anchor_y = self.height * ANCHOR

        # Clamped rather than computed and trusted: screen metrics and window
        # size can disagree about whether they are logical or device pixels, and
        # the failure is the overlay sitting half off the bottom of the screen.
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self._start_x = max(0, (sw - self.width) // 2)
        self._start_y = max(0, min(sh - self.height - self.chrome.px(80),
                                   sh - self.height))
        self.root.geometry(
            f"{self.width}x{self.height}+{self._start_x}+{self._start_y}")

        self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                bg=self.chrome.background,
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)

        # Only now that the window is its final size: the region is in device
        # pixels and clips whatever it was applied to.
        self.root.update_idletasks()
        chrome_mod.shape(self.root, self.chrome, self.width, self.height)

        self._build_frame()
        self._bind()

    def _bind(self):
        for widget in (self.root, self.canvas):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Button-3>", lambda e: self.root.destroy())
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<plus>", lambda e: self._nudge(+0.25))
        self.root.bind("<minus>", lambda e: self._nudge(-0.25))
        # Size. These are the fallback: they need the overlay focused, which
        # means clicking it, which seeks. The global shortcuts are what anyone
        # actually uses on Windows; this is what a platform without them gets.
        # Several spellings per direction because which one arrives depends on
        # the keyboard — `plus` needs Shift on most layouts, so `equal` is the
        # key under the finger, and the numeric pad sends its own names again.
        for sequence in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.root.bind(sequence, lambda e: self._resize(+SIZE_STEP))
        for sequence in ("<Control-minus>", "<Control-underscore>",
                         "<Control-KP_Subtract>"):
            self.root.bind(sequence, lambda e: self._resize(-SIZE_STEP))
        self.root.bind("<Control-0>", lambda e: self._resize_to(1.0))

    HOTKEY_ACTIONS: ClassVar[dict] = {
        "bigger": lambda self: self._resize(+SIZE_STEP),
        "smaller": lambda self: self._resize(-SIZE_STEP),
        "reset": lambda self: self._resize_to(1.0),
    }

    def _drain_hotkeys(self) -> None:
        """Act on global shortcuts pressed while something else had the focus.

        Drained on the tick rather than delivered from the listener thread:
        everything a resize touches is a Tk widget, and Tk is only safe on the
        thread that made it.
        """
        for action in self.hotkeys.poll():
            handler = self.HOTKEY_ACTIONS.get(action)
            if handler:
                handler(self)

    def _build_frame(self):
        """The parts that never move: the card.

        There is no edge highlight. It was built on the belief that this
        surface only ever adds light, so a dim grey could not darken anything.
        Sampling the actual pixels disproved that: against a plate reading 58,
        the highlight's own rows measured 67, 50 and 35 — the lower two are
        darker than what they sit on, which is why the top edge looked like a
        badly drawn black line rather than a soft one.

        Correcting the levels would have kept a decoration that has now caused
        two visible faults. Removing it is the smaller thing on screen and the
        smaller thing to explain.
        """
        # The card: cover, title beside it, artists under the title. Laid out
        # left to right but centred as a group, so it stays put while its own
        # contents change width from song to song.
        self._card_y = self.chrome.px(14)
        self._thumb_size = self.chrome.px(THUMB_SIZE)
        self._thumb_item = self.canvas.create_rectangle(
            0, 0, 0, 0, outline="", fill="")
        self._title_item = self.canvas.create_text(
            0, 0, text="", anchor="w", font=self.f_title, fill=self.palette.title)
        self._artist_item = self.canvas.create_text(
            0, 0, text="", anchor="w", font=self.f_artist, fill=self.palette.artist)

        # Lyrics must be gone before they reach the card. Without this the
        # outermost line arrives at the top still faintly visible and overlaps
        # it, and the card is the one thing on screen that never moves.
        self._content_top = self._card_y + self._thumb_size + self.chrome.px(12)

    def _lay_out_card(self, title: str, artists: str) -> None:
        """Place the card's parts and centre the group."""
        gap = self.chrome.px(10)
        title_font, artist_font = self._title_font, self._artist_font
        text_width = max(title_font.measure(title), artist_font.measure(artists))
        # The cover's space is reserved whether or not it has arrived, so the
        # card does not shuffle sideways the moment it does.
        cover = self._thumb_size + gap
        block = cover + text_width

        left = max(self.chrome.px(12), (self.width - block) // 2)
        top = self._card_y

        self.canvas.coords(self._thumb_item, left, top,
                           left + self._thumb_size, top + self._thumb_size)
        text_x = left + cover
        # Both text rows share the cover's vertical centre, so the pair reads as
        # one block rather than as two lines that happen to sit near a square.
        mid = top + self._thumb_size / 2
        self.canvas.coords(self._title_item, text_x,
                           mid - artist_font.metrics("linespace") / 2)
        self.canvas.coords(self._artist_item, text_x,
                           mid + title_font.metrics("linespace") / 2)

    # --- interaction ---
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._press_at = (e.x_root, e.y_root)
        self._press_y = e.y
        self._moved = False
        self._dragging = True
        # The blur is *not* dropped here. Doing it on the press meant every
        # click flashed the panel, including clicks meant only to seek — the
        # window changed colour for the length of a tap. It waits until the
        # pointer has actually travelled.

    def _drag_move(self, e):
        x, y = e.x_root - self._dx, e.y_root - self._dy
        if (x, y) == self._drag_at:
            return          # motion events repeat; moving to where we already are stutters
        self._drag_at = (x, y)
        # A hand never holds perfectly still, so a few pixels of travel is still
        # a click. Without the slack, seeking would almost never fire.
        px, py = self._press_at
        if not self._moved and (abs(e.x_root - px) > CLICK_SLACK
                                or abs(e.y_root - py) > CLICK_SLACK):
            self._moved = True
            # Now it is a drag rather than a click, so the blur can go.
            if SUSPEND_GLASS_WHILE_DRAGGING:
                chrome_mod.suspend_effects(self.root, self.chrome, True)
        chrome_mod.move(self.root, x, y)

    def _drag_end(self, _e):
        was_dragging = self._moved
        self._dragging = False
        if was_dragging and SUSPEND_GLASS_WHILE_DRAGGING:
            chrome_mod.suspend_effects(self.root, self.chrome, False)
        if not self._moved:
            self._seek_to_line_at(self._press_y)

    def _seek_to_line_at(self, y: int) -> None:
        """Jump to whichever line was clicked, if the player will allow it."""
        lyr = self.lyrics
        if lyr is None or not lyr.synced:
            return
        for index, view in self._views.items():
            if view.y <= y <= view.y + view.height and index < len(lyr.lines):
                target = lyr.lines[index][0] - self.offset
                # The lead exists so a line appears before it is sung; seeking
                # to a line means starting where it starts, so it comes back off.
                if self.reader.seek(max(0.0, target)):
                    logger.info("seeking to line %d at %.2fs", index, target)
                    # Move now rather than waiting for the next poll, and ignore
                    # the position until it catches up. Without the guard the
                    # view snapped straight back: the reader is still reporting
                    # where playback was, so the very next frame recomputed the
                    # old line and undid the jump, then jumped again half a
                    # second later. That bounce is the delay, not the seek.
                    self._awaiting_seek = (target, time.monotonic())
                    self._go_to_line(index, lyr)
                return

    def _go_to_line(self, index: int, lyr: Lyrics) -> None:
        """Make `index` the current line, animating when the move is small."""
        step = abs(index - self.line_index) if self.line_index >= 0 else None
        self.line_index = index
        indices = self._visible_indices(len(lyr.lines))
        self._ensure_views(indices, lyr)
        # A move within the visible column animates, in either direction — going
        # back a line should travel just as the next one does. A longer jump is
        # a discontinuity, and animating one is how a view ends up chasing
        # itself across a song.
        self._retarget(indices, animate=step is not None and 0 < step <= CONTEXT)
        self._restyle(indices)

    def _nudge(self, dt: float):
        self.offset += dt

    # --- size ---
    def _resize(self, delta: float) -> None:
        self._resize_to(self._size + delta)

    def _resize_to(self, size: float) -> None:
        size = round(config.clamp_size(size), 2)
        if abs(size - self._size) < 1e-6:
            return          # already at the limit; rebuilding would only flicker
        self._size = size
        config.save_size(size)
        self._apply_scale()

    def _apply_scale(self) -> None:
        """Rebuild every measurement against the new scale.

        Everything the layout knows is derived from `chrome.px()` and the
        scaled fonts, so this recomputes exactly the same things `__init__`
        did — which is the reason it can be this short, and the reason to keep
        the two lists next to each other if either ever grows.
        """
        scale = self._dpi_scale * self._size
        self.chrome = replace(self.chrome, scale=scale)

        was = (self.root.winfo_x() + self.width // 2,
               self.root.winfo_y() + self.height // 2)
        self.width = self.chrome.px(WIDTH)
        self.height = self.chrome.px(HEIGHT)
        self.wrap = self.chrome.px(WRAP)
        self.row_gap = self.chrome.px(ROW_GAP)
        self.anchor_y = self.height * ANCHOR
        self.f_title = _scaled_font(FONT_TITLE, scale)
        self.f_artist = _scaled_font(FONT_ARTIST, scale)
        self.f_line = _scaled_font(FONT_LINE, scale)
        self._title_font = tkfont.Font(font=self.f_title)
        self._artist_font = tkfont.Font(font=self.f_artist)
        self._card_y = self.chrome.px(14)
        self._thumb_size = self.chrome.px(THUMB_SIZE)
        self._content_top = self._card_y + self._thumb_size + self.chrome.px(12)

        # Grown or shrunk about its own middle, so the window stays where the
        # eye left it instead of walking up-left as it grows. Clamped, because
        # a window centred near an edge would otherwise grow off the screen.
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, min(was[0] - self.width // 2, sw - self.width))
        y = max(0, min(was[1] - self.height // 2, sh - self.height))
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self.canvas.configure(width=self.width, height=self.height)
        # After the geometry, never before: the clip region is in device pixels
        # and does not track the window, so applying it early clips the window
        # to whatever size it used to be.
        self.root.update_idletasks()
        chrome_mod.shape(self.root, self.chrome, self.width, self.height)

        self.canvas.itemconfigure(self._title_item, font=self.f_title)
        self.canvas.itemconfigure(self._artist_item, font=self.f_artist)
        # Both card caches, and the second one is not optional. The card is
        # only laid out again when its *text* changes, so invalidating the
        # fitted text alone leaves a resize where the text happens to come out
        # identical — which is most of them — holding its old coordinates while
        # the cover is rebuilt at the new size and placed into them. Measured at
        # 1.4x: the title started 20 px inside the cover.
        self._card_raw = None
        self._card_text = None

        # The lines cannot be resized in place: a LineView lays its characters
        # out once, at the font and wrap width it was built with. Dropping them
        # and forgetting which line was current makes the next tick rebuild and
        # place them without animating, which is what a resize should look like.
        for view in self._views.values():
            view.destroy()
        self._views.clear()
        self._glides.clear()
        self._targets.clear()
        self.line_index = -1

        # The cover has to be re-derived: both images were built for the old
        # window. Inline rather than on a thread — it measures ~18 ms, and this
        # is a keypress rather than a track change, so there is a hand waiting
        # for it and nothing else in flight.
        if self._cover_data:
            self._pending_art = self._build_art(self._cover_data)
        logger.info("overlay size %.2f (%dx%d)", self._size, self.width, self.height)

    # --- lyrics ---
    def _start_fetch(self, snap: Snapshot):
        self.fetch_gen += 1
        gen = self.fetch_gen
        self.lyrics = None
        self._clear_views()

        def work():
            lyr = fetch_for_candidates(snap.lookup_candidates(), snap.duration, snap.album)
            if gen == self.fetch_gen:
                self.lyrics = lyr

        threading.Thread(target=work, daemon=True).start()
        self._start_artwork(gen, snap)

    def _start_artwork(self, gen: int, snap: Snapshot) -> None:
        """Fetch and prepare the cover off the render thread.

        Decoding an image takes long enough to drop frames if it happened
        inline, and it only needs doing once a track.
        """
        if not artwork.available():
            return

        artist, title = snap.norm_artist_title()
        album = snap.album

        def work():
            # One cover, shown once. Fetching the good one first and only
            # falling back to the player's own means nothing is ever replaced on
            # screen. Showing the local thumbnail immediately was faster, but
            # the swap to the sharp one was visible — and a cover that changes
            # under you reads worse than one that arrives a moment late.
            wanted = max(600, self._thumb_size * 4)
            data = (artwork.best_cover(artist, title, album, size=wanted)
                    or self.reader.read_artwork())
            if data and gen == self.fetch_gen:
                self._cover_data = data
                self._pending_art = self._build_art(data)

        threading.Thread(target=work, daemon=True).start()

    def _build_art(self, data: bytes) -> tuple:
        """Everything derived from one cover, at the current size.

        Kept as a method rather than a closure because a resize needs it too:
        both images are built for a particular window, so growing the window
        means deriving them again from the same bytes.
        """
        return (
            artwork.make_thumbnail(data, self._thumb_size),
            # The backdrop only makes sense where the panel has a body to wash;
            # over a colour key it would be a dark rectangle.
            artwork.make_backdrop(data, self.width, self.height)
            if self.chrome.washed else None,
            # Measured here rather than on the render thread: it costs a couple
            # of milliseconds, and whichever thread calls this has them.
            songcolour.extract(data),
        )

    def _apply_art(self) -> None:
        """Put prepared images on the canvas, on the render thread."""
        if self._pending_art is None or self._dragging:
            # Measured at 1.0 ms for the backdrop and 0.02 ms for the cover, so
            # this is not the guard against stutter it was once written as —
            # the old comment claimed 15 ms. It stays because it costs nothing:
            # a cover that waits for the hand to stop is never noticed, and the
            # decoding it waits on already happened off this thread.
            return
        thumb, backdrop, song = self._pending_art
        self._pending_art = None
        try:
            from PIL import ImageTk
        except ImportError:
            return

        # Held, not just drawn: Tk keeps only a weak claim on an image, so
        # dropping the reference blanks it.
        if backdrop is not None:
            photo = ImageTk.PhotoImage(backdrop.image)
            if self._backdrop_item is not None:
                self.canvas.delete(self._backdrop_item)
            self._backdrop_item = self.canvas.create_image(0, 0, image=photo, anchor="nw")
            self.canvas.tag_lower(self._backdrop_item)
            self._backdrop_photo = photo

        # The palette follows the cover, and the fade follows the wash the cover
        # actually left on the glass — a line dims into what is behind it rather
        # than toward a level guessed once and applied to every song.
        self._adopt_palette(palette_mod.for_song(
            self.chrome, song, backdrop.colour if backdrop else (0, 0, 0)))

        if thumb is not None:
            photo = ImageTk.PhotoImage(thumb)
            if self._thumb_image is not None:
                self.canvas.delete(self._thumb_image)
            self._thumb_image = self.canvas.create_image(0, 0, image=photo, anchor="nw")
            self._thumb_photo = photo
        else:
            if self._thumb_image is not None:
                self.canvas.delete(self._thumb_image)
            self._thumb_image = None
            self._thumb_photo = None
        # Put the cover where the layout already says it goes. Waiting for the
        # card text to change would leave it at the origin whenever a new cover
        # arrives for the same song — which is most of the time, since the
        # cover is fetched after the title is already on screen.
        self._place_thumb()

    def _adopt_palette(self, pal) -> None:
        """Switch every coloured thing over to a new palette.

        The glyphs snap rather than cross-fading. A fade would have to run for
        several hundred milliseconds over every character on screen, and it
        would land in the middle of a sweep — where a colour drifting under the
        front is far more noticeable than one that simply changed while the
        cover was appearing anyway.
        """
        if pal is self.palette:
            return
        self.palette = pal
        logger.debug("palette hue=%.0f strength=%.2f sweep dE=%.1f",
                     pal.hue, pal.strength, pal.sweep_de)
        self.canvas.itemconfigure(self._title_item, fill=pal.title)
        self.canvas.itemconfigure(self._artist_item, fill=pal.artist)
        for view in self._views.values():
            view.set_palette(pal)
        self._restyle()

    def _place_thumb(self) -> None:
        """Move the cover image onto the slot the layout reserved for it."""
        if self._thumb_image is None:
            return
        box = self.canvas.coords(self._thumb_item)
        if len(box) >= 2:
            self.canvas.coords(self._thumb_image, box[0], box[1])

    def _card_for(self, snap: Snapshot) -> tuple[str, str]:
        # Short-circuited on the raw inputs. Measuring text costs a round trip
        # into Tk, and this runs on every tick where the song changes a few
        # times an hour — a millisecond spent re-deriving an unchanged string
        # is a millisecond mouse events spend queued.
        raw = (snap.ok, snap.artist, snap.title, round(self.offset, 2))
        if raw == self._card_raw:
            return self._card_text or ("", "")
        self._card_raw = raw

        if not snap.ok:
            return "", ""
        artist, title = snap.norm_artist_title()
        if self.offset:
            title += f"   [{self.offset:+.2f}s]"
        limit = self.wrap - self._thumb_size
        return (self._fit(title, self._title_font, limit),
                self._fit(artist, self._artist_font, limit))

    def _fit(self, text: str, font_obj: tkfont.Font, limit: int) -> str:
        """Shorten text that will not fit, rather than letting it overflow.

        Truncated, not wrapped: a second line would change the card's height
        from song to song, and the card is what everything else is measured
        from.
        """
        if not text:
            return text
        if font_obj.measure(text) <= limit:
            return text
        room = limit - font_obj.measure("…")
        cut = len(text)
        while cut > 0 and font_obj.measure(text[:cut]) > room:
            cut -= 1
        return text[:cut].rstrip() + "…"

    # --- the line pool ---
    def _clear_views(self):
        for view in self._views.values():
            view.destroy()
        self._views.clear()
        self._glides.clear()
        self._targets.clear()

    def _visible_indices(self, count: int) -> list[int]:
        if self.line_index < 0:
            return [0] if count else []
        lo = max(0, self.line_index - CONTEXT)
        hi = min(count - 1, self.line_index + CONTEXT)
        return list(range(lo, hi + 1))

    def _ensure_views(self, indices: list[int], lyr: Lyrics) -> None:
        """Create what has come into view, drop what has left it.

        Everything already on screen is left alone — that is the whole point.
        A line that was next becomes the line that is current by being the same
        items in a new place, not by being replaced by a copy of itself.
        """
        for index in list(self._views):
            if index not in indices:
                self._views.pop(index).destroy()
                self._glides.pop(index, None)
                self._targets.pop(index, None)

        for index in indices:
            if index in self._views:
                continue
            text = lyr.lines[index][1] if index < len(lyr.lines) else ""
            words = lyr.words_at(index)
            # Born off the bottom when it is arriving from below, so its first
            # movement is the same rise as the lines already on screen.
            start_y = self.height if index > self.line_index else -self.chrome.px(60)
            self._views[index] = LineView(
                self.canvas, self.width // 2, start_y, text, words,
                font=self.f_line, wrap=self.wrap, palette=self.palette,
                scale=self.chrome.scale)

    def _retarget(self, indices: list[int], animate: bool) -> None:
        """Place the active line at the anchor and stack the rest around it."""
        if self.line_index not in self._views:
            return
        y = self.anchor_y
        for index in sorted(indices):
            if index < self.line_index:
                y -= self._views[index].height + self.row_gap
        for index in sorted(indices):
            view = self._views[index]
            # A view seen for the first time starts from wherever it was born,
            # so it travels in with the rest. Snapping it into place was what
            # made arriving lines land on top of lines still moving.
            previous = self._targets.get(index, view.y)
            self._targets[index] = y
            if not animate:
                view.move_to(y)
                self._glides.pop(index, None)
            elif abs(previous - y) >= 1:
                # Displacement decaying, not a position being driven: a change
                # landing mid-glide adds to the journey instead of restarting it.
                remaining = self._glides[index].offset() if index in self._glides else 0.0
                self._glides[index] = motion.Glide(
                    previous - y + remaining,
                    motion.row_duration(index, self.line_index))
            y += view.height + self.row_gap

    def _advance_glides(self) -> bool:
        moving = False
        for index, glide in list(self._glides.items()):
            view = self._views.get(index)
            if view is None:
                del self._glides[index]
                continue
            offset = glide.offset()
            view.move_to(self._targets[index] + offset)
            if glide.done:
                view.move_to(self._targets[index])
                del self._glides[index]
            else:
                moving = True
        return moving

    # --- render ---
    def _tick(self):
        self._drain_hotkeys()
        snap = self.reader.snapshot
        if snap.ok and snap.track_key() != self.track_key:
            self.track_key = snap.track_key()
            self.line_index = -1
            self._start_fetch(snap)

        self._apply_art()

        card = self._card_for(snap)
        if card != self._card_text:
            self._card_text = card
            title, artists = card
            self.canvas.itemconfigure(self._title_item, text=title)
            self.canvas.itemconfigure(self._artist_item, text=artists)
            self._lay_out_card(title, artists)
            self._place_thumb()

        interval = SLOW_TICK_MS
        lyr = self.lyrics
        if lyr is not None and lyr.synced and lyr.lines and not self._dragging:
            pos = snap.live_position() + self.offset
            if self._awaiting_seek is not None:
                target, since = self._awaiting_seek
                # Keep counting from the jump rather than sitting on it. Holding
                # the position still was what made the highlight freeze after a
                # click: the sweep had nothing to advance along until the poll
                # caught up, and then resumed with a lurch.
                assumed = target + (time.monotonic() - since)
                if abs(pos - assumed) <= SEEK_SETTLED_S:
                    self._awaiting_seek = None      # the player caught up
                else:
                    pos = assumed                   # trust the jump, not the poll
            index = lyr.line_index_at(pos + LINE_LEAD_S)
            if index != self.line_index:
                self._go_to_line(index, lyr)

            if self._advance_glides():
                # Brightness follows position, so it has to be recomputed while
                # anything is still moving — otherwise a line would travel out
                # of the frame at full strength and be clipped rather than fade.
                self._restyle()
                interval = FAST_TICK_MS
            if self._dragging:
                # Nobody reads the lyrics while dragging the window they are on.
                # Measured: repainting at 60 Hz pushes a move's worst case from
                # 2.6 ms to 14.7 ms, which is most of a frame — the animation
                # costs the drag far more than the drag costs the animation.
                interval = DRAG_TICK_MS
            active = self._views.get(self.line_index)
            if active is not None and active.words:
                word, fraction = lyr.word_progress_at(self.line_index,
                                                      pos + WORD_LEAD_S)
                active.show_sweep(word, fraction)
                if word >= 0:
                    interval = FAST_TICK_MS
            elif active is not None:
                # No word timing for this line: light all of it. Leaving it dim
                # would say none of it has been sung, about the line playing.
                active.show_lit()

        self.root.after(interval, self._tick)

    def _visibility(self, view: LineView) -> float:
        """How present a line is, from where it sits rather than from its index.

        A line fades as it nears either edge and is gone before it reaches one.
        That is what stops a line ever being seen half-clipped by the frame: it
        has already faded out by the time the edge would cut it.
        """
        fade = max(1, self.chrome.px(FADE_ZONE))
        top, bottom = view.y, view.y + view.height
        room = min((top - self._content_top) / fade,
                   (self.height - bottom) / fade, 1.0)
        return max(0.0, room)

    def _restyle(self, indices: list[int] | None = None) -> None:
        for index in (indices if indices is not None else list(self._views)):
            view = self._views.get(index)
            if view is None:
                continue
            active = index == self.line_index
            view.set_active(active)
            if not active:
                view.show_inactive(self.palette.faded(
                    abs(index - self.line_index), self._visibility(view)))

    def run(self):
        self.reader.start()
        self.hotkeys.start()
        self._tick()
        self.root.mainloop()
        self.hotkeys.stop()
        self.reader.stop()


def setup_logging() -> Path:
    """Send logs to a rotating file and return its path.

    The overlay has no console: started from the Start menu or a packaged
    executable, anything written to stderr goes nowhere. Without a file, the
    handled exceptions would be logged into the void, which is worse than not
    logging them because the code reads as though failures are being recorded.
    """
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Lyrica"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "lyrica.log"
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=512_000, backupCount=2, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path


def main():
    setup_logging()
    # Before anything reads a token: the .env is the fallback for values that
    # must not be committed, and an exported variable still wins over it.
    config.load()
    Overlay().run()


if __name__ == "__main__":
    main()
