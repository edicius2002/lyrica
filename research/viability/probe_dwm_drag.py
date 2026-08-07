"""Probe: what does the compositor pay while an acrylic window is dragged?

The app-side cost of a move is already down to ~1 ms, so if dragging still
feels heavy the work is happening outside our process. The blur is recomputed
for every new position, and that is dwm.exe's bill rather than ours.

Measures dwm.exe while the window is stationary and while it is moving, with
acrylic, with plain blur, and with none — and checks whether turning the effect
off for the duration of a drag is quick enough to be worth doing.
"""
import ctypes
import statistics
import sys
import time
import tkinter as tk
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Work\research\lyrica\src")

import psutil

from lyrica.chrome import windows as win

user32 = ctypes.WinDLL("user32", use_last_error=True)
MOVES = 240


def dwm_process():
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower() == "dwm.exe":
            return proc
    return None


def build(width: int, height: int):
    scale = win.set_dpi_awareness()
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#000000")
    w, h = int(width * scale), int(height * scale)
    root.geometry(f"{w}x{h}+300+300")
    canvas = tk.Canvas(root, width=w, height=h, bg="#000000", highlightthickness=0)
    canvas.pack()
    for i in range(3):
        canvas.create_text(w // 2, 60 + i * 60, text="placeholder row of text",
                           font=("Segoe UI", int(-30 * scale), "bold"), fill="#bbbbbb")
    root.update_idletasks()
    root.update()
    return root, w, h


def drag(root, hwnd, seconds: float) -> tuple[float, float]:
    """Move the window steadily. Returns median and worst move cost in ms."""
    samples = []
    end = time.perf_counter() + seconds
    i = 0
    while time.perf_counter() < end:
        i += 1
        x, y = 300 + (i % 60), 300 + (i % 23)
        t0 = time.perf_counter()
        user32.SetWindowPos(wintypes.HWND(hwnd), None, x, y, 0, 0,
                            win.SWP_NOSIZE | win.SWP_NOZORDER
                            | win.SWP_NOACTIVATE | win.SWP_ASYNCWINDOWPOS)
        root.update()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), max(samples)


def measure(label: str, state: int | None, width: int = 900, height: int = 300) -> None:
    dwm = dwm_process()
    root, w, h = build(width, height)
    hwnd = win._hwnd_of(root)
    if state is not None:
        win._set_accent(hwnd, state, win._abgr(*win.TINT_RGBA))
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, 44, 44)
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
    root.update()

    if dwm:
        dwm.cpu_percent(None)
    time.sleep(1.0)
    idle = dwm.cpu_percent(None) if dwm else 0.0

    if dwm:
        dwm.cpu_percent(None)
    median, worst = drag(root, hwnd, 2.0)
    moving = dwm.cpu_percent(None) if dwm else 0.0

    print(f"    {label:26s} dwm idle {idle:5.1f}%  dragging {moving:5.1f}%"
          f"  move {median:5.2f} / {worst:6.2f} ms")
    root.destroy()


def measure_toggle_cost() -> None:
    """How long it takes to turn the effect off and on again."""
    root, _, _ = build(900, 300)
    hwnd = win._hwnd_of(root)
    tint = win._abgr(*win.TINT_RGBA)
    win._set_accent(hwnd, win.ACCENT_ENABLE_ACRYLICBLURBEHIND, tint)
    root.update()

    off_times, on_times = [], []
    for _ in range(30):
        t0 = time.perf_counter()
        win._set_accent(hwnd, 0, 0)          # ACCENT_DISABLED
        off_times.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        win._set_accent(hwnd, win.ACCENT_ENABLE_ACRYLICBLURBEHIND, tint)
        on_times.append((time.perf_counter() - t0) * 1000)
    print(f"\n    turning the effect off: {statistics.median(off_times):.3f} ms"
          f"   back on: {statistics.median(on_times):.3f} ms")
    root.destroy()


if __name__ == "__main__":
    print("=== compositor cost while dragging ===")
    measure("acrylic, 900x300", win.ACCENT_ENABLE_ACRYLICBLURBEHIND)
    measure("plain blur, 900x300", win.ACCENT_ENABLE_BLURBEHIND)
    measure("no effect, 900x300", None)
    print()
    measure("acrylic, 600x220 (smaller)", win.ACCENT_ENABLE_ACRYLICBLURBEHIND, 600, 220)
    measure("acrylic, 1400x460 (larger)", win.ACCENT_ENABLE_ACRYLICBLURBEHIND, 1400, 460)
    measure_toggle_cost()
