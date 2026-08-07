"""What each accent state actually looks like, and what it costs.

Dragging currently switches the accent off entirely, which is why the panel
jumps to a flat dark rectangle the moment a hand touches it. The question this
answers is whether some cheaper state exists whose plate is close enough to
acrylic's that the switch is invisible.

Measures, for each state, the plate colour over a controlled backdrop at
several brightnesses — the same method that produced the composition law in
`glass` — and then the wall-clock cost of a run of moves under each.

    python research/viability/probe_accent_states.py
"""
import ctypes
import statistics
import sys
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from PIL import ImageGrab

from lyrica.chrome import windows as win

STATES = {
    0: "disabled (what dragging does today)",
    2: "transparent gradient (tint, no blur)",
    3: "blur behind (the older, cheaper blur)",
    4: "acrylic (what the panel normally is)",
}

DESKTOPS = (0, 64, 128, 200, 255)
PANEL = (420, 220)


def sample(box) -> tuple:
    """The mean colour of a small patch, so one stray pixel cannot decide."""
    grab = ImageGrab.grab(box).convert("RGB")
    raw = grab.tobytes()
    n = len(raw) // 3
    return tuple(round(sum(raw[c::3]) / n) for c in range(3))


def main() -> int:
    # Before Tk exists, or Tk reports logical pixels while ImageGrab returns
    # physical ones and every sample lands somewhere other than the panel.
    scale = win.set_dpi_awareness()
    root = tk.Tk()
    root.withdraw()

    # The controlled desktop: a fullscreen window we own, so the measurement
    # does not depend on whatever wallpaper happens to be up.
    back = tk.Toplevel(root)
    back.overrideredirect(True)
    back.attributes("-topmost", True)
    sw, sh = back.winfo_screenwidth(), back.winfo_screenheight()
    back.geometry(f"{sw}x{sh}+0+0")

    panel = tk.Toplevel(root)
    panel.overrideredirect(True)
    panel.attributes("-topmost", True)
    px, py = (sw - PANEL[0]) // 2, (sh - PANEL[1]) // 2
    panel.geometry(f"{PANEL[0]}x{PANEL[1]}+{px}+{py}")
    panel.configure(bg="#000000")
    root.update()

    hwnd = win._hwnd_of(panel)
    tint = win._abgr(*win.TINT_RGBA)
    # Asked of Windows rather than derived from Tk's numbers. Tk's coordinates
    # are logical or physical depending on the DPI-awareness that was declared
    # before it started, and guessing wrong samples the backdrop instead of the
    # panel — which reads as "every state is identical" rather than as an error.
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    patch = (rect.left + 60, rect.top + 60, rect.left + 160, rect.top + 160)
    # Well clear of the panel and of the blur it bleeds outward.
    outside = (rect.left - 260, rect.top, rect.left - 160, rect.top + 100)
    print(f"display scale {scale:g}, panel at "
          f"({rect.left},{rect.top})-({rect.right},{rect.bottom}), "
          f"sampling {patch}\n")

    print("plate colour, by accent state and desktop brightness\n")
    print(f"{'state':>5s}  {'name':38s} " + "  ".join(f"d={d:<3d}" for d in DESKTOPS))
    plates = {}
    for state in STATES:
        win._set_accent(hwnd, state, tint if state else 0)
        row = []
        for level in DESKTOPS:
            back.configure(bg=f"#{level:02x}{level:02x}{level:02x}")
            panel.lift()              # both are topmost; the last raised wins
            root.update()
            time.sleep(0.25)          # let the compositor settle before grabbing
            root.update()
            got = sample(patch)
            control = sample(outside)
            if max(abs(a - level) for a in control) > 6:
                print(f"  !! backdrop reads {control} at level {level}")
            row.append(got)
        plates[state] = row
        cells = "  ".join(f"{c[0]:3d},{c[1]:3d}" for c in row)
        print(f"{state:5d}  {STATES[state]:38s} {cells}")

    print("\ndistance from acrylic, per channel mean (lower is a less visible switch)")
    for state in STATES:
        if state == 4:
            continue
        gaps = [abs(a[i] - b[i])
                for a, b in zip(plates[state], plates[4], strict=True)
                for i in range(3)]
        print(f"  state {state}: mean {statistics.mean(gaps):5.1f}   "
              f"worst {max(gaps):3d}   {STATES[state]}")

    # --- can the gradient be calibrated onto acrylic's line? ----------------
    #
    # The gradient blends plainly: plate = (1-a)*desktop + a*tint. Acrylic was
    # measured at plate = 0.388*desktop + 18. Setting (1-a) = 0.388 and
    # a*tint = 18 gives the alpha and tint that put the two on the same line,
    # so the switch changes the blur and nothing else.
    print("\ncalibrated gradient candidates")
    for alpha, level in ((156, 29), (156, 32), (150, 30), (160, 28)):
        win._set_accent(hwnd, 2, win._abgr(level, level, level + 4, alpha))
        row = []
        for d in DESKTOPS:
            back.configure(bg=f"#{d:02x}{d:02x}{d:02x}")
            panel.lift()
            root.update()
            time.sleep(0.25)
            root.update()
            row.append(sample(patch))
        gaps = [abs(a[i] - b[i]) for a, b in zip(row, plates[4], strict=True)
                for i in range(3)]
        cells = "  ".join(f"{c[0]:3d},{c[1]:3d}" for c in row)
        print(f"  tint {level:3d} alpha {alpha:3d}: {cells}   "
              f"mean {statistics.mean(gaps):4.1f}  worst {max(gaps):3d}")

    # --- what a run of moves costs under each state -------------------------
    print("\ncost of 120 moves, ms per move")
    user32 = ctypes.windll.user32
    flags = win.SWP_NOSIZE | win.SWP_NOZORDER | win.SWP_NOACTIVATE
    for state in STATES:
        win._set_accent(hwnd, state, tint if state else 0)
        root.update()
        time.sleep(0.3)
        times = []
        for i in range(120):
            x = px + (i % 40) - 20
            start = time.perf_counter()
            user32.SetWindowPos(ctypes.wintypes.HWND(hwnd), None, x, py, 0, 0,
                                flags | win.SWP_ASYNCWINDOWPOS)
            root.update()
            times.append((time.perf_counter() - start) * 1000)
        times.sort()
        print(f"  state {state}: median {times[len(times)//2]:5.2f}   "
              f"p95 {times[int(len(times)*0.95)]:5.2f}   worst {times[-1]:6.2f}   "
              f"{STATES[state]}")

    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
