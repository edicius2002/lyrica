# -*- coding: utf-8 -*-
"""Lyrica — always-on-top synced lyrics overlay for Windows.

Reads whatever is playing (Spotify app, Chrome with YouTube / YT Music /
SoundCloud, etc.) from the Windows global media session and shows lyrics
line by line in a floating transparent window.

Run:      python -m lyrica   (or the `lyrica` console script)
Keys:     Esc = quit | right click = quit | drag with mouse = move
          +/- = nudge sync offset by ±0.25 s
"""
import threading
import tkinter as tk
from typing import Optional

from lyrica.lyrics import Lyrics
from lyrica.providers import fetch_for_candidates
from lyrica.smtc import SmtcReader, Snapshot

TRANSPARENT = "#010203"
WRAP = 880


class Overlay:
    def __init__(self):
        self.reader = SmtcReader(interval=0.5)
        self.lyrics: Optional[Lyrics] = None
        self.track_key = ""
        self.fetch_gen = 0
        self.status_msg = "Waiting for music…"
        self.offset = 0.0
        self.last_render = None

        self.root = tk.Tk()
        self.root.title("Lyrica")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = 920, 190
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{sh - h - 80}")

        self.lbl_track = tk.Label(self.root, text="", font=("Segoe UI", 10),
                                  fg="#8b93a1", bg=TRANSPARENT)
        self.lbl_prev = tk.Label(self.root, text="", font=("Segoe UI", 14),
                                 fg="#7a8290", bg=TRANSPARENT, wraplength=WRAP)
        self.lbl_curr = tk.Label(self.root, text="", font=("Segoe UI", 24, "bold"),
                                 fg="#ffffff", bg=TRANSPARENT, wraplength=WRAP)
        self.lbl_next = tk.Label(self.root, text="", font=("Segoe UI", 14),
                                 fg="#7a8290", bg=TRANSPARENT, wraplength=WRAP)
        for lbl in (self.lbl_track, self.lbl_prev, self.lbl_curr, self.lbl_next):
            lbl.pack(pady=1)

        for widget in (self.root, self.lbl_curr, self.lbl_prev, self.lbl_next, self.lbl_track):
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

    # --- render ---
    def _tick(self):
        snap = self.reader.snapshot
        if snap.ok and snap.track_key() != self.track_key:
            self.track_key = snap.track_key()
            self._start_fetch(snap)

        artist, title = snap.norm_artist_title() if snap.ok else ("", "")
        header = f"{artist} – {title}" if snap.ok else "Waiting for music…"
        if self.offset:
            header += f"   [offset {self.offset:+.2f}s]"
        prev_t = curr_t = next_t = ""

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
            prev_t = lyr.lines[i - 1][1] if i > 0 else ""
            curr_t = lyr.lines[i][1] if i >= 0 else "♪"
            next_t = lyr.lines[i + 1][1] if -1 <= i < n - 1 else ""
            curr_t = curr_t or "♪"
        elif lyr.plain:
            # Unsynced: approximate paging by playback progress
            lines = [l for l in lyr.plain.splitlines() if l.strip()]
            if snap.duration > 0 and lines:
                i = min(int(snap.live_position() / snap.duration * len(lines)), len(lines) - 1)
                prev_t = lines[i - 1] if i > 0 else ""
                curr_t = lines[i] + "  (approx, unsynced)"
                next_t = lines[i + 1] if i < len(lines) - 1 else ""
            else:
                curr_t = "Lyrics available but not synced"

        render = (header, prev_t, curr_t, next_t)
        if render != self.last_render:
            self.last_render = render
            self.lbl_track.config(text=header)
            self.lbl_prev.config(text=prev_t)
            self.lbl_curr.config(text=curr_t)
            self.lbl_next.config(text=next_t)

        self.root.after(100, self._tick)

    def run(self):
        self.reader.start()
        self._tick()
        self.root.mainloop()
        self.reader.stop()


def main():
    Overlay().run()


if __name__ == "__main__":
    main()
