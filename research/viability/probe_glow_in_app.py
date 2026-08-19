"""What the border costs *inside Lyrica*, now that half of it is another window.

`probe_layered_glow.py` answered whether a companion `WS_EX_LAYERED` window can
carry the outward half of the light at all, and measured it against a synthetic
rectangle. This one measures the same thing against the real `Overlay` — the
real panel size, the real palette, the real four strips — because the numbers
that decide whether the effect ships are the ones paid on the app's own frame.

Three questions, none of which the earlier probe could close:

    frame     What one frame of the border costs with the companion against
              what it costs without it. The border's budget is 16 ms and it
              cost 1.4-2.4 ms before any of this; a spill that doubles a frame
              is a spill that has to be argued for.
    collapse  The panel folds to its card and back over twenty-one frames, and
              every frame is a different size. `Layered.reserve` rebuilds the
              DIB section when the surface has to grow, and *that* was never
              measured under animation. If a rebuild per frame is too dear the
              answer is to allocate at the largest size the panel reaches and
              present a sub-rectangle — but from a measurement, not from taste.
    dpi       Two monitors at different scales. The overlay follows the scale
              of the screen it is dropped on, which changes the panel, the
              light's widths and therefore the surface. This checks that the
              companion arrives at the same size the panel did, on both.

Run with `PYTHONPATH=src`. It builds a real overlay, so quit any running one
first — `instance.claim` is not called here, and two overlays is two panels.
"""
import statistics
import sys
import time

sys.path.insert(0, __file__.rsplit("research", 1)[0] + "src")

from lyrica import app as A
from lyrica import chrome as chrome_mod
from lyrica import config, halo
from lyrica.meter import Character

FRAMES = 240


def _timed(label, work, rounds=FRAMES):
    work(0)
    times = []
    for i in range(1, rounds + 1):
        start = time.perf_counter()
        work(i)
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    # The minimum is reported alongside the median because this is a desktop
    # and not a bench: anything else running steals whole milliseconds from a
    # sample, and the cheapest frame is the one that was not preempted. Compare
    # minima when comparing two builds; read the median as what a busy machine
    # actually pays.
    print(f"{label:<52} {times[0]:6.2f} ms min"
          f"   {times[int(len(times) * 0.1)]:6.2f} ms p10"
          f"   {statistics.median(times):6.2f} ms median"
          f"   {times[-1]:6.2f} ms worst")
    return times


def _music(i):
    # A level that actually moves, so the tables are rebuilt as often as they
    # are in a song rather than never.
    return Character(level=0.35 + 0.3 * ((i % 20) / 20.0),
                     dynamics=0.5, rate=0.4)


def frame_cost(overlay):
    beam = overlay.beam
    print(f"panel {overlay.width}x{overlay.height} at scale "
          f"{overlay.chrome.scale}, pad {beam.pad} px, "
          f"surface {beam.light.spill.size if beam.light.spill else None}")

    def border(i):
        beam.advance(1 / 60, _music(i), overlay.palette)

    def border_and_flush(i):
        beam.advance(1 / 60, _music(i), overlay.palette)
        overlay.root.update()

    # The border's own work, which is the number the effect is budgeted in.
    # `root.update` is measured separately because it is Tk repainting the
    # whole canvas — every word on screen, not only the strip that changed —
    # and it is paid whether the border moved or not.
    _timed("the border alone, both halves", border)
    spill, beam.light.spill = beam.light.spill, None
    beam._state = None
    _timed("the border alone, the canvas half only", border)
    beam.light.spill = spill
    beam._state = None
    _timed("the border and the flush that follows it", border_and_flush)


def collapse_cost(overlay):
    """Every frame of a fold and an unfold, with the DIB rebuilds counted."""
    glow = overlay.beam.light.spill.glow
    rebuilds = []
    real = glow.reserve

    def counted(width, height):
        grew = real(width, height)
        if grew:
            rebuilds.append((width, height))
        return grew

    glow.reserve = counted
    full = (overlay.chrome.px(A.WIDTH), overlay.chrome.px(A.HEIGHT))
    compact = (overlay.chrome.px(A.COMPACT_MIN_WIDTH),
               overlay._card_y * 2 + overlay._thumb_size)

    for name, (start, end) in (("fold", (full, compact)),
                               ("unfold", (compact, full))):
        halo._cache.clear()
        times = []
        for step in range(21):
            t = step / 20
            width = round(start[0] + (end[0] - start[0]) * t)
            height = round(start[1] + (end[1] - start[1]) * t)
            began = time.perf_counter()
            overlay._resize_window(width, height, settling=step == 20)
            overlay.beam.advance(1 / 60, _music(step), overlay.palette)
            overlay.root.update()
            times.append((time.perf_counter() - began) * 1000)
        times.sort()
        print(f"{name:<52} {statistics.median(times):6.2f} ms median"
              f"   {times[-1]:6.2f} ms worst")
    print(f"DIB rebuilt {len(rebuilds)} times over 42 frames: {rebuilds}")
    glow.reserve = real
    overlay._resize_window(*full)


def dpi_check(overlay):
    """Land the panel on each monitor and see what the companion did."""
    from lyrica.chrome import windows as win

    left, top, width, _height = chrome_mod.desktop_bounds(overlay.root)
    for name, point in (("left screen", (left + 200, top + 200)),
                        ("right screen", (left + width - 900, top + 200))):
        chrome_mod.move(overlay.root, *point)
        overlay.beam.follow(*point)
        overlay.root.update()
        scale = win.dpi_for_window(overlay.root)
        overlay._dpi_scale = scale or overlay._dpi_scale
        overlay._apply_scale()
        overlay.root.update()
        panel = (overlay.root.winfo_x(), overlay.root.winfo_y(),
                 overlay.width, overlay.height)
        spill = overlay.beam.light.spill
        pad = spill.pad
        print(f"{name}: dpi scale {scale}, panel {panel}, pad {pad}, "
              f"surface {spill.size} at {spill.at}")
        centred = (panel[0] - spill.at[0], panel[1] - spill.at[1],
                   spill.at[0] + spill.size[0] - (panel[0] + panel[2]),
                   spill.at[1] + spill.size[1] - (panel[1] + panel[3]))
        print(f"    the panel sits {centred} inside its light "
              f"(all four must equal {pad})")


if __name__ == "__main__":
    config.load()
    overlay = A.Overlay()
    overlay.root.update()
    if overlay.beam is None or overlay.beam.light.spill is None:
        print("no beam or no companion surface; nothing to measure")
        raise SystemExit(1)
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "frame"):
        frame_cost(overlay)
    if what in ("all", "collapse"):
        collapse_cost(overlay)
    if what in ("all", "dpi"):
        dpi_check(overlay)
    overlay._drop_beam()
    overlay.root.destroy()
