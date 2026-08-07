"""Lyrica — an always-on-top synced lyrics overlay.

Reads whatever is playing from the platform's media session and shows the
lyrics over everything else, swept word by word.

Run:      python -m lyrica   (or the `lyrica` console script)
Keys:     Esc = quit | right click = quit | drag with mouse = move
          +/- = nudge sync offset by ±0.25 s
"""
import logging
import logging.handlers
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from lyrica import artwork, motion
from lyrica import chrome as chrome_mod
from lyrica import palette as palette_mod
from lyrica.lineview import LineView
from lyrica.lyrics import Lyrics
from lyrica.providers import fetch_for_candidates
from lyrica.sessions import Snapshot, create_reader

# Logical pixels at 96 dpi; scaled by the chrome's DPI factor at startup.
#
# The height is not a taste decision. Three lines have to sit fully lit — the
# one before, the one being sung, and the one coming — with a fade band above
# and below for lines arriving and leaving, and the header clear of all of it.
# Anything shorter and the previous line is already half faded while you are
# still hearing it.
WIDTH, HEIGHT = 900, 400
WRAP = 800
ROW_GAP = 10

# The band at each edge where a line fades away. Deep enough to read as a fade
# rather than a cut, shallow enough that three lines still fit lit between the
# two bands.
FADE_ZONE = 64

# Where the line being sung sits, as a fraction of the window height. Just
# above centre: the line coming matters more than the one just gone, so it gets
# the larger share of the room.
ANCHOR = 0.45

FONT_HEADER = ("Segoe UI", -15)
# One size for every lyric line. A role change that also changed size would
# force a relayout, which is a rebuild wearing a different name — and lines
# reading as louder or quieter rather than bigger or smaller is what the
# reference does anyway.
FONT_LINE = ("Segoe UI", -30, "bold")

SLOW_TICK_MS = 100
FAST_TICK_MS = 16   # 60 Hz; measured at ~1% of this machine with stable items

# Lyrics feel late when they land exactly on the beat: you read a line as it
# begins, so it has to be there fractionally before the voice. better-lyrics
# settled on the same correction, and those numbers are adopted, not guessed.
LINE_LEAD_S = 0.115
WORD_LEAD_S = 0.150

# How many lines either side of the current one are kept on screen. Two, not
# one, so the outermost pair can sit dim: a line then arrives already faint and
# brightens as it approaches, rather than appearing at full strength.
CONTEXT = 2

# How far the pointer may travel and still count as a click rather than a drag.
CLICK_SLACK = 4

logger = logging.getLogger(__name__)


def _scaled_font(spec: tuple, scale: float) -> tuple:
    family, size, *rest = spec
    return (family, round(size * scale), *rest)


class Overlay:
    def __init__(self):
        self.reader = create_reader(interval=0.5)
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
        self._pending_backdrop = None
        self._backdrop_item = None
        self._backdrop_photo = None

        # Before Tk exists: Tk reads the display metrics when the root window is
        # created, so declaring DPI awareness afterwards leaves it holding
        # virtualised numbers and every geometry it reports is off by the scale.
        scale_hint = chrome_mod.prepare()

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
        self.f_header = _scaled_font(FONT_HEADER, scale)
        self.f_line = _scaled_font(FONT_LINE, scale)
        self.anchor_y = self.height * ANCHOR

        # Clamped rather than computed and trusted: screen metrics and window
        # size can disagree about whether they are logical or device pixels, and
        # the failure is the overlay sitting half off the bottom of the screen.
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, (sw - self.width) // 2)
        y = max(0, min(sh - self.height - self.chrome.px(80), sh - self.height))
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

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

    def _build_frame(self):
        """The parts that never move: the sheen and the header."""
        if self.chrome.additive:
            # Additive, so three fading lines read as light catching an edge.
            # In keyed mode there is no plate for it to catch on.
            inset = self.chrome.px(22)
            for i, level in enumerate((0x2C, 0x1C, 0x0C)):
                self.canvas.create_line(inset, 1 + i, self.width - inset, 1 + i,
                                        fill=f"#{level:02x}{level:02x}{level + 4:02x}")
        self._header_y = self.chrome.px(12)
        self._header = self.canvas.create_text(
            self.width // 2, self._header_y, text="", anchor="n",
            font=self.f_header, fill=self.palette.header)
        # Lyrics must be gone before they reach the header. Without this the
        # outermost line arrives at the top still faintly visible and overlaps
        # the title, which is the one thing on screen that never moves.
        self._content_top = self._header_y + tkfont.Font(
            font=self.f_header).metrics("linespace") + self.chrome.px(10)

    # --- interaction ---
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._press_at = (e.x_root, e.y_root)
        self._press_y = e.y
        self._moved = False
        self._dragging = True

    def _drag_move(self, e):
        x, y = e.x_root - self._dx, e.y_root - self._dy
        if (x, y) == self._drag_at:
            return          # motion events repeat; moving to where we already are stutters
        self._drag_at = (x, y)
        # A hand never holds perfectly still, so a few pixels of travel is still
        # a click. Without the slack, seeking would almost never fire.
        px, py = self._press_at
        if abs(e.x_root - px) > CLICK_SLACK or abs(e.y_root - py) > CLICK_SLACK:
            self._moved = True
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        self._dragging = False
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
                return

    def _nudge(self, dt: float):
        self.offset += dt

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
        self._start_artwork(gen)

    def _start_artwork(self, gen: int) -> None:
        """Fetch and prepare the backdrop off the render thread.

        Decoding and blurring an image takes long enough to drop frames if it
        happened inline, and it only needs doing once a track.
        """
        if not self.chrome.additive or not artwork.available():
            return

        def work():
            data = self.reader.read_artwork()
            image = artwork.make_backdrop(data, self.width, self.height) if data else None
            if gen == self.fetch_gen:
                # Handing the image over rather than drawing it: Tk objects
                # belong to the thread that owns the widget.
                self._pending_backdrop = image

        threading.Thread(target=work, daemon=True).start()

    def _apply_backdrop(self) -> None:
        """Put a prepared backdrop on the canvas, on the render thread."""
        image = self._pending_backdrop
        if image is None:
            return
        self._pending_backdrop = None
        try:
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(image)
        except Exception:
            logger.debug("could not convert the backdrop", exc_info=True)
            return
        if self._backdrop_item is not None:
            self.canvas.delete(self._backdrop_item)
        self._backdrop_item = self.canvas.create_image(0, 0, image=photo, anchor="nw")
        # Behind everything, and held: Tk keeps only a weak claim on the image,
        # so dropping the reference blanks it.
        self.canvas.tag_lower(self._backdrop_item)
        self._backdrop_photo = photo

    def _header_for(self, snap: Snapshot) -> str:
        if not snap.ok:
            return ""
        artist, title = snap.norm_artist_title()
        text = f"{artist} – {title}" if artist else title
        if self.offset:
            text += f"   [{self.offset:+.2f}s]"
        return self._fit_header(text)

    def _fit_header(self, text: str) -> str:
        """Shorten a title that will not fit, rather than letting it overflow.

        Truncated, not wrapped: a two-line title would push the lyrics down and
        change the layout depending on the song, and the title is the one thing
        on screen that should stay put.
        """
        if not text:
            return text
        font_obj = tkfont.Font(font=self.f_header)
        limit = self.wrap
        if font_obj.measure(text) <= limit:
            return text
        ellipsis = "…"
        room = limit - font_obj.measure(ellipsis)
        cut = len(text)
        while cut > 0 and font_obj.measure(text[:cut]) > room:
            cut -= 1
        return text[:cut].rstrip() + ellipsis

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
        snap = self.reader.snapshot
        if snap.ok and snap.track_key() != self.track_key:
            self.track_key = snap.track_key()
            self.line_index = -1
            self._start_fetch(snap)

        self._apply_backdrop()

        header = self._header_for(snap)
        if header != self._header_text:
            self._header_text = header
            self.canvas.itemconfigure(self._header, text=header)

        interval = SLOW_TICK_MS
        lyr = self.lyrics
        if lyr is not None and lyr.synced and lyr.lines and not self._dragging:
            pos = snap.live_position() + self.offset
            index = lyr.line_index_at(pos + LINE_LEAD_S)
            stepped = index == self.line_index + 1 and self.line_index >= 0
            if index != self.line_index:
                self.line_index = index
                indices = self._visible_indices(len(lyr.lines))
                self._ensure_views(indices, lyr)
                # Only a step to the next line animates. A seek or a new track
                # should simply be there; animating a discontinuity is how a
                # view ends up chasing itself.
                self._retarget(indices, animate=stepped)
                self._restyle(indices)

            if self._advance_glides():
                # Brightness follows position, so it has to be recomputed while
                # anything is still moving — otherwise a line would travel out
                # of the frame at full strength and be clipped rather than fade.
                self._restyle()
                interval = FAST_TICK_MS
            active = self._views.get(self.line_index)
            if active is not None and active.words:
                word, fraction = lyr.word_progress_at(self.line_index,
                                                      pos + WORD_LEAD_S)
                active.show_sweep(word, fraction)
                if 0 <= word:
                    interval = FAST_TICK_MS

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
        self._tick()
        self.root.mainloop()
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
    Overlay().run()


if __name__ == "__main__":
    main()
