"""Probe: what actually makes the window lag while it is being dragged?

Four suspects, measured rather than argued about:

  1. Tk's geometry() versus calling SetWindowPos directly.
  2. The 60 Hz render tick competing with the drag.
  3. The acrylic blur, which costs the compositor real work per move.
  4. Redrawing the canvas while the window moves.

Reports how long a move takes to issue and how many can be sustained per
second, which is what decides whether the window keeps up with a hand.
"""
import ctypes
import statistics
import sys
import time
import tkinter as tk
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, r"D:\Work\research\lyrica\src")
from lyrica import chrome as chrome_mod

user32 = ctypes.WinDLL("user32", use_last_error=True)

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOREDRAW = 0x0008
SWP_ASYNCWINDOWPOS = 0x4000

MOVES = 300


def hwnd_of(root) -> int:
    root.update_idletasks()
    handle = int(root.winfo_id())
    parent = user32.GetParent(wintypes.HWND(handle))
    return int(parent) if parent else handle


def time_moves(fn, count: int = MOVES) -> tuple[float, float]:
    """Median and worst milliseconds for one move."""
    samples = []
    for i in range(count):
        x = 200 + (i % 40)
        y = 200 + (i % 17)
        start = time.perf_counter()
        fn(x, y)
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), max(samples)


def run(label: str, *, acrylic: bool, ticking: bool, redraw: bool) -> None:
    scale = chrome_mod.prepare()
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    chrome = chrome_mod.setup(root, scale) if acrylic else None
    if chrome is None:
        root.configure(bg="#000000")
    width, height = 900, 300
    root.geometry(f"{width}x{height}+200+200")
    canvas = tk.Canvas(root, width=width, height=height,
                       bg=(chrome.background if chrome else "#000000"),
                       highlightthickness=0)
    canvas.pack()
    items = [canvas.create_text(450, 40 + i * 40, text="placeholder text row",
                                font=("Segoe UI", -30, "bold"), fill="#cccccc")
             for i in range(3)]
    root.update()

    state = {"n": 0, "stop": False}

    def tick():
        if state["stop"]:
            return
        state["n"] += 1
        if redraw:
            shade = 0x60 + (state["n"] % 40)
            for item in items:
                canvas.itemconfigure(item, fill=f"#{shade:02x}{shade:02x}{shade:02x}")
        root.after(16, tick)

    if ticking:
        tick()

    handle = hwnd_of(root)

    def by_tk(x, y):
        root.geometry(f"+{x}+{y}")
        root.update()

    def by_api(x, y):
        user32.SetWindowPos(wintypes.HWND(handle), None, x, y, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        root.update()

    def by_api_async(x, y):
        user32.SetWindowPos(wintypes.HWND(handle), None, x, y, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                            | SWP_ASYNCWINDOWPOS)
        root.update()

    print(f"--- {label}")
    for name, fn in (("tk geometry", by_tk),
                     ("SetWindowPos", by_api),
                     ("SetWindowPos async", by_api_async)):
        median, worst = time_moves(fn)
        rate = 1000 / median if median else 0
        print(f"    {name:20s} median {median:6.2f} ms  worst {worst:7.2f} ms"
              f"  ~{rate:5.0f} moves/s")

    state["stop"] = True
    root.destroy()


if __name__ == "__main__":
    run("acrylic on, 60 Hz tick redrawing", acrylic=True, ticking=True, redraw=True)
    run("acrylic on, 60 Hz tick idle", acrylic=True, ticking=True, redraw=False)
    run("acrylic on, no tick", acrylic=True, ticking=False, redraw=False)
    run("acrylic off, no tick", acrylic=False, ticking=False, redraw=False)
