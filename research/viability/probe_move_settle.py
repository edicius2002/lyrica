"""Probe: how long until the window is actually *there*?

The earlier probe timed how quickly a move call returns, which is the wrong
question. What a hand feels is when the window arrives, and those are different
numbers the moment a call is asynchronous — asynchronous means queued, and a
queue can be slower to land while being faster to return.

Measures settle time: issue a move, then poll the real window rectangle until
it matches, and time that.
"""
import ctypes
import statistics
import sys
import time
import tkinter as tk
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Work\research\lyrica\src")

from lyrica.chrome import windows as win

user32 = ctypes.WinDLL("user32", use_last_error=True)
SAMPLES = 120
TIMEOUT_S = 0.25


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def window_x(hwnd: int) -> int:
    rect = RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect.left


def build(acrylic: bool):
    scale = win.set_dpi_awareness()
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#000000")
    w, h = int(900 * scale), int(300 * scale)
    root.geometry(f"{w}x{h}+300+300")
    canvas = tk.Canvas(root, width=w, height=h, bg="#000000", highlightthickness=0)
    canvas.pack()
    for i in range(3):
        canvas.create_text(w // 2, 60 + i * 60, text="placeholder row of text",
                           font=("Segoe UI", int(-30 * scale), "bold"), fill="#bbbbbb")
    root.update_idletasks()
    root.update()
    hwnd = win._hwnd_of(root)
    if acrylic:
        win._set_accent(hwnd, win.ACCENT_ENABLE_ACRYLICBLURBEHIND,
                        win._abgr(*win.TINT_RGBA))
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, 44, 44)
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
    root.update()
    return root, hwnd


def settle(root, hwnd, mover, count: int = SAMPLES) -> tuple[float, float, int]:
    """Median and worst milliseconds until the window really moved."""
    times, missed = [], 0
    for i in range(count):
        target = 300 + (i % 50) * 4
        if window_x(hwnd) == target:
            continue
        start = time.perf_counter()
        mover(target)
        deadline = start + TIMEOUT_S
        while window_x(hwnd) != target:
            root.update()
            if time.perf_counter() > deadline:
                missed += 1
                break
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times), max(times), missed


def run(label: str, acrylic: bool) -> None:
    root, hwnd = build(acrylic)
    flags_sync = win.SWP_NOSIZE | win.SWP_NOZORDER | win.SWP_NOACTIVATE

    def sync(x):
        user32.SetWindowPos(wintypes.HWND(hwnd), None, x, 300, 0, 0, flags_sync)

    def asynchronous(x):
        user32.SetWindowPos(wintypes.HWND(hwnd), None, x, 300, 0, 0,
                            flags_sync | win.SWP_ASYNCWINDOWPOS)

    def by_tk(x):
        root.geometry(f"+{x}+300")

    print(f"--- {label}")
    for name, mover in (("SetWindowPos sync", sync),
                        ("SetWindowPos async", asynchronous),
                        ("tk geometry", by_tk)):
        median, worst, missed = settle(root, hwnd, mover)
        note = f"  ({missed} never landed)" if missed else ""
        print(f"    {name:20s} settles in {median:6.2f} ms  worst {worst:7.2f} ms{note}")
    root.destroy()


if __name__ == "__main__":
    print("=== time until the window has actually moved ===")
    run("acrylic on", acrylic=True)
    run("acrylic off", acrylic=False)
