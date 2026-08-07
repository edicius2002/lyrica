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

from lyrica.lyrics import Lyrics
from lyrica.overlay_text import WordLine, draw_outlined
from lyrica.providers import fetch_for_candidates
from lyrica.sessions import Snapshot, create_reader

TRANSPARENT = "#010203"
WIDTH, HEIGHT = 920, 210
WRAP = 860
OUTLINE = 2

FONT_HEADER = ("Segoe UI", 10)
FONT_SIDE = ("Segoe UI", 14)
FONT_CURRENT = ("Segoe UI", 24, "bold")

COLOUR_HEADER = "#c9cfda"
COLOUR_SIDE = "#b6bdc9"
COLOUR_CURRENT = "#ffffff"

# Word states on the current line. Unsung stays legible rather than faint: it is
# the line you are about to sing, and the outline already separates it from the
# background, so brightness carries the timing instead of visibility.
COLOUR_SUNG = "#ffffff"
COLOUR_UNSUNG = "#9aa3b2"

SLOW_TICK_MS = 100
SWEEP_TICK_MS = 33

# Muted grey reads as "dimmed" on a dark background and as "nearly invisible"
# on a bright one. The side lines are therefore only lightly dimmed and lean on
# the outline for separation instead.
ROW_GAP = 6


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
        self.word_line: WordLine | None = None

        self.root = tk.Tk()
        self.root.title("Lyrica")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{(sw - WIDTH) // 2}+{sh - HEIGHT - 80}")

        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, bg=TRANSPARENT,
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)

        for widget in (self.root, self.canvas):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Button-3>", lambda e: self.root.destroy())
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<plus>", lambda e: self._nudge(+0.25))
        self.root.bind("<minus>", lambda e: self._nudge(-0.25))

    # --- interaction ---
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _nudge(self, dt: float):
        self.offset += dt

    # --- lyrics ---
    def _start_fetch(self, snap: Snapshot):
        self.fetch_gen += 1
        gen = self.fetch_gen
        self.lyrics = None
        artist, title = snap.norm_artist_title()
        self.status_msg = f"Searching lyrics: {artist} – {title}"

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
            curr_t = self.status_msg or "…"
        elif lyr.instrumental:
            curr_t = "♪ Instrumental ♪"
        elif lyr.synced:
            pos = snap.live_position() + self.offset
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

    # --- render ---
    def _repaint(self, rows: tuple[str, str, str, str], words: list) -> None:
        header, prev_t, curr_t, next_t = rows
        self.canvas.delete("all")
        self.word_line = None
        x, y = WIDTH // 2, 4

        for text, font, colour in ((header, FONT_HEADER, COLOUR_HEADER),
                                   (prev_t, FONT_SIDE, COLOUR_SIDE)):
            used = draw_outlined(self.canvas, x, y, text, font=font, fill=colour,
                                 wrap=WRAP, outline=OUTLINE)
            if used:
                y += used + ROW_GAP

        if words:
            self.word_line = WordLine(self.canvas, x, y, words, font=FONT_CURRENT,
                                      wrap=WRAP, sung=COLOUR_SUNG,
                                      active=COLOUR_CURRENT, unsung=COLOUR_UNSUNG)
            y += self.word_line.height + ROW_GAP
        else:
            used = draw_outlined(self.canvas, x, y, curr_t, font=FONT_CURRENT,
                                 fill=COLOUR_CURRENT, wrap=WRAP, outline=OUTLINE)
            if used:
                y += used + ROW_GAP

        draw_outlined(self.canvas, x, y, next_t, font=FONT_SIDE, fill=COLOUR_SIDE,
                      wrap=WRAP, outline=OUTLINE)

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
        if layout != self.last_render:
            self.last_render = layout
            self._repaint(rows, words)

        interval = SLOW_TICK_MS
        if self.word_line is not None:
            pos = snap.live_position() + self.offset
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
