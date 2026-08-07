"""What one frame of the border effect costs, and how evenly they land.

Sampling the process from outside measures the player as much as the overlay —
a cover arriving mid-window moved the reading further than the effect does. This
times the work itself instead: how long `advance()` takes, and what interval the
loop actually achieves against the one it asks for.

Smoothness is the second half. A beam is smooth when frames land evenly, not
when they land often, so the spread matters more than the mean.

    python research/viability/probe_beam_frames.py
"""
import itertools
import statistics
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lyrica import palette as pal_mod
from lyrica.beam import Beam, _rounded_path
from lyrica.chrome import Chrome, ChromeMode
from lyrica.glass import PANEL
from lyrica.meter import create_meter
from lyrica.songcolour import SongColour

SIZES = [("compacto", 325, 114), ("normal", 1125, 375), ("grande", 1575, 525)]
FRAMES = 300


def main() -> int:
    root = tk.Tk()
    root.geometry("1x1+0+0")
    chrome = Chrome(ChromeMode.PANEL, "#000000", PANEL, 1.25)
    palette = pal_mod.for_song(
        chrome, SongColour(38, 0.8, 0.45, 38, False, (0, 0, 0)), (29, 24, 14))
    meter = create_meter()

    print(f"{'panel':10s} {'segmentos':>10s} {'advance()':>22s} "
          f"{'una lectura':>13s}")
    for label, width, height in SIZES:
        canvas = tk.Canvas(root, width=width, height=height)
        canvas.pack()
        beam = Beam(canvas, width, height, 18, 1.25)
        root.update()

        costs, meter_costs = [], []
        for _ in range(FRAMES):
            t0 = time.perf_counter()
            level = meter.level(1 / 30)
            t1 = time.perf_counter()
            beam.advance(1 / 30, level, palette)
            root.update_idletasks()
            costs.append((time.perf_counter() - t1) * 1000)
            meter_costs.append((t1 - t0) * 1000)

        segments = len(_rounded_path(width, height, 18, 2.5))
        print(f"{label:10s} {segments:10d} "
              f"{statistics.median(costs):7.2f} ms  p95 {sorted(costs)[int(FRAMES * .95)]:6.2f} ms "
              f"{statistics.median(meter_costs):10.3f} ms")
        beam.destroy()
        canvas.destroy()

    # --- how evenly the loop actually ticks -------------------------------
    print("\nintervalos reales del bucle, pidiendo 33 ms (30 Hz)")
    canvas = tk.Canvas(root, width=1125, height=375)
    canvas.pack()
    beam = Beam(canvas, 1125, 375, 18, 1.25)
    stamps = []

    def tick():
        stamps.append(time.perf_counter())
        beam.advance(1 / 30, meter.level(1 / 30), palette)
        if len(stamps) < 200:
            root.after(33, tick)
        else:
            root.quit()

    root.after(33, tick)
    root.mainloop()

    gaps = [(b - a) * 1000 for a, b in itertools.pairwise(stamps)]
    gaps.sort()
    print(f"  mediana {statistics.median(gaps):6.2f} ms   "
          f"p95 {gaps[int(len(gaps) * .95)]:6.2f} ms   "
          f"peor {gaps[-1]:6.2f} ms")
    print(f"  desviacion {statistics.pstdev(gaps):5.2f} ms  "
          f"-> {'estable' if statistics.pstdev(gaps) < 4 else 'IRREGULAR'}")

    meter.close()
    root.destroy()
    return 0




def measure_live() -> int:
    """The cadence the *running* overlay achieves, from its own process.

    Imported and driven here rather than sampled from outside, because an
    external sampler measures the media player as much as the overlay — a cover
    arriving mid-window moved an earlier reading further than the effect does.
    """
    import itertools as it

    from lyrica import app as A
    from lyrica import config

    config.load()
    overlay = A.Overlay()
    stamps = []
    real_tick = overlay._tick

    def traced():
        stamps.append(time.perf_counter())
        real_tick()

    overlay._tick = traced
    overlay.hotkeys.start()
    from lyrica import chrome as chrome_mod
    chrome_mod.hold_timer_resolution(True)
    overlay._tick()
    overlay.root.after(8000, overlay.root.destroy)
    overlay.root.mainloop()
    chrome_mod.hold_timer_resolution(False)
    overlay.hotkeys.stop()

    gaps = sorted((b - a) * 1000 for a, b in it.pairwise(stamps))
    print(f"\noverlay real, {len(stamps)} ticks en 8 s")
    print(f"  mediana {statistics.median(gaps):6.2f} ms "
          f"({1000 / statistics.median(gaps):5.1f} Hz)  "
          f"desviacion {statistics.pstdev(gaps):5.2f} ms")
    return 0


if __name__ == "__main__":
    # `--live` drives the real overlay; without it the beam is timed in
    # isolation, which is the number that is not affected by what is playing.
    raise SystemExit(measure_live() if "--live" in sys.argv else main())
