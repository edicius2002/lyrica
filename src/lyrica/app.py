"""Lyrica — always-on-top synced lyrics overlay for Windows.

Reads whatever is playing (Spotify app, Chrome with YouTube / YT Music /
SoundCloud, etc.) from the Windows global media session and shows lyrics
line by line in a floating transparent window.

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

from lyrica import chrome as chrome_mod
from lyrica import palette as palette_mod
from lyrica.lyrics import Lyrics
from lyrica.overlay_text import SweepLine, WordLine, draw_outlined
from lyrica.providers import fetch_for_candidates
from lyrica.sessions import Snapshot, create_reader

# Logical pixels at 96 dpi; scaled by the chrome's DPI factor at startup.
# Tall enough that a line which wraps to two rows still fits with its
# neighbours: a clipped bottom row is the same failure as a clipped right edge.
WIDTH, HEIGHT = 900, 280
WRAP = 800
ROW_GAP = 6

# Negative sizes are device pixels rather than points. Points re-quantise under
# DPI scaling, which is what makes text drift a pixel between machines.
FONT_HEADER = ("Segoe UI", -15)
FONT_SIDE = ("Segoe UI Semibold", -22)
FONT_CURRENT = ("Segoe UI", -38, "bold")

SLOW_TICK_MS = 100
SWEEP_TICK_MS = 16   # 60 Hz; measured at ~1% of this machine with stable items

# Lyrics feel late when they land exactly on the beat: you read a line as it
# begins, so it has to be there fractionally before the voice. better-lyrics
# settled on the same correction — 0.115 s for lines, 0.150 s where word timing
# is driving the highlight — and those numbers are adopted rather than guessed.
LINE_LEAD_S = 0.115
WORD_LEAD_S = 0.150


def _scaled_font(spec: tuple, scale: float) -> tuple:
    family, size, *rest = spec
    return (family, round(size * scale), *rest)


class Overlay:
    def __init__(self):
        self.reader = create_reader(interval=0.5)
        self.lyrics: Lyrics | None = None
        self.track_key = ""
        self.fetch_gen = 0
        self.status_msg = "Waiting for music…"
        self.offset = 0.0
        self.last_render = None
        self.line_index = -1
        self.word_line = None
        self._dragging = False
        self._drag_at = (None, None)

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
        self.f_side = _scaled_font(FONT_SIDE, scale)
        self.f_current = _scaled_font(FONT_CURRENT, scale)

        # Clamped rather than computed and trusted: screen metrics and window
        # size can disagree about whether they are logical or device pixels, and
        # the failure mode is the overlay sitting half off the bottom of the
        # screen. Clamping is right under either reading.
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

        for widget in (self.root, self.canvas):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Button-3>", lambda e: self.root.destroy())
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<plus>", lambda e: self._nudge(+0.25))
        self.root.bind("<minus>", lambda e: self._nudge(-0.25))

    # --- interaction ---
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._dragging = True

    def _drag_move(self, e):
        x, y = e.x_root - self._dx, e.y_root - self._dy
        if (x, y) == self._drag_at:
            return          # motion events repeat; moving to where we already are stutters
        self._drag_at = (x, y)
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        self._dragging = False

    def _nudge(self, dt: float):
        self.offset += dt

    # --- lyrics ---
    def _start_fetch(self, snap: Snapshot):
        self.fetch_gen += 1
        gen = self.fetch_gen
        self.lyrics = None
        # No "searching" text: a status message where a lyric belongs reads as
        # part of the song for the half second before the real line replaces it.
        # Silence is a truer answer than a progress report.
        self.status_msg = ""

        def work():
            lyr = fetch_for_candidates(snap.lookup_candidates(), snap.duration, snap.album)
            if gen == self.fetch_gen:
                self.lyrics = lyr
                self.status_msg = "" if lyr else "No lyrics found"

        threading.Thread(target=work, daemon=True).start()

    def _rows(self, snap: Snapshot) -> tuple[str, str, str, str]:
        """Header plus the previous, current and next lines."""
        artist, title = snap.norm_artist_title() if snap.ok else ("", "")
        header = f"{artist} – {title}" if snap.ok else "Waiting for music…"
        if self.offset:
            header += f"   [offset {self.offset:+.2f}s]"
        prev_t = curr_t = next_t = ""
        self.line_index = -1

        lyr = self.lyrics
        if not snap.ok:
            curr_t = "♪"
        elif lyr is None:
            curr_t = self.status_msg or "♪"
        elif lyr.instrumental:
            curr_t = "♪"
        elif lyr.synced:
            pos = snap.live_position() + self.offset + LINE_LEAD_S
            i = lyr.line_index_at(pos)
            n = len(lyr.lines)
            self.line_index = i
            prev_t = lyr.lines[i - 1][1] if i > 0 else ""
            curr_t = (lyr.lines[i][1] if i >= 0 else "") or "♪"
            next_t = lyr.lines[i + 1][1] if -1 <= i < n - 1 else ""
        elif lyr.plain:
            # Unsynced: approximate paging by playback progress
            lines = [ln for ln in lyr.plain.splitlines() if ln.strip()]
            if snap.duration > 0 and lines:
                i = min(int(snap.live_position() / snap.duration * len(lines)), len(lines) - 1)
                prev_t = lines[i - 1] if i > 0 else ""
                curr_t = lines[i] + "  (approx, unsynced)"
                next_t = lines[i + 1] if i < len(lines) - 1 else ""
            else:
                curr_t = "Lyrics available but not synced"

        return header, prev_t, curr_t, next_t

    def _active_words(self) -> list:
        """Word timings for the line on screen, if it has any."""
        lyr = self.lyrics
        if lyr is None or self.line_index < 0:
            return []
        return lyr.words_at(self.line_index)

    def _draw_sheen(self) -> None:
        """A one-pixel highlight along the top edge of the glass.

        Additive, so three fading lines read as light catching an edge. In keyed
        mode there is no plate for it to catch on, so it is skipped.
        """
        if not self.chrome.additive:
            return
        inset = self.chrome.px(22)
        for i, level in enumerate((0x2C, 0x1C, 0x0C)):
            self.canvas.create_line(inset, 1 + i, self.width - inset, 1 + i,
                                    fill=f"#{level:02x}{level:02x}{level + 4:02x}")

    # --- render ---
    def _repaint(self, rows: tuple[str, str, str, str], words: list) -> None:
        header, prev_t, curr_t, next_t = rows
        self.canvas.delete("all")
        self.word_line = None
        self._draw_sheen()

        p = self.palette
        x, y = self.width // 2, self.chrome.px(14)

        for text, font, colour in ((header, self.f_header, p.header),
                                   (prev_t, self.f_side, p.side)):
            used = draw_outlined(self.canvas, x, y, text, font=font, fill=colour,
                                 wrap=self.wrap, outline=p.outline)
            if used:
                y += used + self.row_gap

        if words:
            if self.chrome.additive:
                self.word_line = SweepLine(self.canvas, x, y, words,
                                           font=self.f_current, wrap=self.wrap,
                                           palette=p, scale=self.chrome.scale)
            else:
                # Without additive light a per-character ramp would need an
                # outline per character, which is far too many canvas items.
                self.word_line = WordLine(self.canvas, x, y, words,
                                          font=self.f_current, wrap=self.wrap,
                                          sung=p.sung, active=p.sung,
                                          unsung=p.unsung, outline=p.outline)
            y += self.word_line.height + self.row_gap
        else:
            used = draw_outlined(self.canvas, x, y, curr_t, font=self.f_current,
                                 fill=p.sung, wrap=self.wrap, outline=p.outline)
            if used:
                y += used + self.row_gap

        draw_outlined(self.canvas, x, y, next_t, font=self.f_side, fill=p.side,
                      wrap=self.wrap, outline=p.outline)

    def _tick(self):
        snap = self.reader.snapshot
        if snap.ok and snap.track_key() != self.track_key:
            self.track_key = snap.track_key()
            self._start_fetch(snap)

        rows = self._rows(snap)
        words = self._active_words()
        # The line itself is the layout; its word colours are not. Rebuilding on
        # a colour change would tear the line down mid-sweep.
        layout = (rows, len(words))
        if layout != self.last_render and not self._dragging:
            # Rebuilding the canvas mid-drag competes with the window moving,
            # and the drag is the thing the hand is watching. It repaints on
            # release, a fraction of a second later.
            self.last_render = layout
            self._repaint(rows, words)

        interval = SLOW_TICK_MS
        if self.word_line is not None:
            pos = snap.live_position() + self.offset + WORD_LEAD_S
            index, fraction = self.lyrics.word_progress_at(self.line_index, pos)
            self.word_line.update(index, fraction)
            # A faster tick only while a word is actually lit; the rest of the
            # time the overlay costs nothing.
            if 0 <= index < len(words):
                interval = SWEEP_TICK_MS

        self.root.after(interval, self._tick)

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
