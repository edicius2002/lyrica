"""Photograph the real overlay, on the real glass, with the real companion window.

Everything in `probe_backlit.py` is composed in PIL, which is exact and proves
nothing about the compositor. This one builds an actual `app.Overlay` — the
same acrylic plate, the same `SetWindowRgn` clip, the same `WS_EX_LAYERED`
companion, the same monitor scale — puts it over a backdrop, and grabs the
screen. What it can show that a composite cannot: whether DWM's own handling of
the two windows leaves a seam, whether the region clip and the field's mask
agree to the pixel, and what the light looks like at the corner at six times
life size.

The backdrop is a window of this script's own rather than the desktop, so the
photograph is reproducible and carries nothing of whoever ran it: five vertical
greys, a soft warm gradient behind them and a line of body text, which between
them cover the two cases a glow fails in — black, where a bloom that should be
invisible is not, and white, where any wash left in the light goes grey.

The lyric text is fabricated because no track is playing; it is laid with the
same `LineView` the app lays every line with, on the overlay's own canvas.

    py research/viability/shoot_backlit.py

Quit any running Lyrica first. `instance.claim` is not called here and two
overlays is two panels.
"""
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, __file__.rsplit("research", 1)[0] + "src")

from PIL import Image, ImageGrab

from lyrica import app as A
from lyrica import config
from lyrica.lineview import LineView
from lyrica.meter import Character

OUT = Path(__file__).resolve().parents[1] / "shots"

# Where the panel is put, and how much desktop is photographed around it. The
# margin has to be wider than the light reaches or the picture crops the thing
# it is of.
AT = (240, 200)
MARGIN = 90

LINES = ("hold the line, love isn't always on time",
         "and the night is not as long as it seems")


def cover_palette(overlay):
    """A palette off a real cover, because the default one has no colour in it.

    The border wears whatever the album art gave it, and a photograph taken
    against `palette.DEFAULT` is a photograph of a grey light — which flatters
    a white-on-white failure and hides a coloured one. This is the warm amber a
    dark sleeve produces.
    """
    from lyrica import palette as pal_mod
    from lyrica.songcolour import SongColour

    return pal_mod.for_song(overlay.chrome,
                            SongColour(34.0, 0.78, 0.52, 34.0, False, (0, 0, 0)),
                            (34, 26, 16))


def backdrop(root: tk.Tk) -> tk.Toplevel:
    """A window under the overlay with the five bands and a warm ground."""
    made = tk.Toplevel(root)
    made.overrideredirect(True)
    # Topmost, like the overlay, so the two are in the same band and lift order
    # decides. Left ordinary it sinks under whatever the desktop already had on
    # it, and the photograph becomes a photograph of that instead.
    made.attributes("-topmost", True)
    made.geometry("1500x900+80+80")
    canvas = tk.Canvas(made, width=1500, height=900, highlightthickness=0,
                       bg="#000000")
    canvas.pack()
    for index, grey in enumerate((0, 64, 128, 192, 255)):
        canvas.create_rectangle(index * 300, 0, (index + 1) * 300, 900,
                                fill=f"#{grey:02x}{grey:02x}{grey:02x}",
                                outline="")
    # A warm band across the middle, so the light has something other than
    # neutral grey to sit on — a cover wash is never neutral and a border that
    # only works on grey is a border that works nowhere.
    for step in range(120):
        shade = int(40 + step * 0.7)
        canvas.create_rectangle(0, 380 + step * 2, 1500, 382 + step * 2,
                                fill=f"#{shade:02x}{max(0, shade - 26):02x}"
                                     f"{max(0, shade - 44):02x}", outline="")
    canvas.create_text(60, 840, anchor="w", font=("Segoe UI", 18),
                       fill="#808080",
                       text="the quick brown fox jumps over the lazy dog")
    return made


def dress(overlay) -> list:
    """Lay two lines on the overlay's own canvas, the way the app lays them."""
    views = []
    for index, text in enumerate(LINES):
        words = [(i * 0.4, (i + 1) * 0.4, word)
                 for i, word in enumerate(text.split())]
        view = LineView(overlay.canvas, overlay.width // 2,
                        overlay.height // 2 + (index - 0.5) * overlay.chrome.px(52),
                        text, words, font=overlay.f_line,
                        wrap=overlay.chrome.px(A.WRAP), palette=overlay.palette)
        if index == 0:
            view.set_active(True)
            view.show_sweep(3, 0.55)
        else:
            view.show_inactive(overlay.palette.side)
        views.append(view)
    return views


def shoot(overlay, name: str, character: Character, seconds: float = 1.2) -> None:
    """Let the border settle on this state, then grab the panel and its light."""
    # `halo.PER_CALL` repaints one strip a frame, so a state the border has just
    # been given is a border three quarters of which still shows the last one.
    # That is the rendering defect the whole brief turns on; a still of it would
    # be a still of the bug.
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        overlay.beam.advance(1 / 60, character, overlay.palette)
        overlay.root.update()
        time.sleep(1 / 60)
    overlay.root.update()
    time.sleep(0.15)

    left, top = overlay.root.winfo_rootx(), overlay.root.winfo_rooty()
    box = (left - MARGIN, top - MARGIN,
           left + overlay.width + MARGIN, top + overlay.height + MARGIN)
    whole = ImageGrab.grab(box, all_screens=True)
    whole.save(OUT / f"{name}.png")
    print(f"wrote {OUT / name}.png  {whole.size}")

    # The top-left corner at six times life size, nearest-neighbour, so what is
    # photographed is the pixels and not an interpolation of them.
    corner = ImageGrab.grab((left - 46, top - 46, left + 74, top + 74),
                            all_screens=True)
    corner.resize((corner.width * 6, corner.height * 6),
                  Image.NEAREST).save(OUT / f"{name}-corner-x6.png")
    print(f"wrote {OUT / name}-corner-x6.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    config.load()
    overlay = A.Overlay()
    ground = backdrop(overlay.root)
    overlay.root.update()
    if overlay.beam is None:
        print("no beam; nothing to photograph")
        raise SystemExit(1)
    if overlay.beam.light.spill is None:
        print("warning: no companion surface, so this is the inward half only")

    from lyrica import chrome as chrome_mod

    chrome_mod.move(overlay.root, *AT)
    overlay.beam.place(*AT)
    overlay._adopt_palette(cover_palette(overlay))
    views = dress(overlay)
    overlay.root.update()
    # Backdrop, then the companion, then the panel. Asserted after the backdrop
    # is mapped, because mapping a topmost window puts it above everything else
    # in the band — including the light this is a photograph of.
    ground.lower(overlay.root)
    overlay.root.lift()
    overlay.beam.behind(chrome_mod.window_handle(overlay.root))
    overlay.root.update()
    print(f"panel {overlay.width}x{overlay.height} at scale "
          f"{overlay.chrome.scale}, pad {overlay.beam.pad} px")

    shoot(overlay, "in-use-backlit-rest",
          Character(level=0.0, dynamics=0.0, rate=0.0))
    shoot(overlay, "in-use-backlit-lit",
          Character(level=0.95, dynamics=0.8, rate=0.6))

    for view in views:
        view.destroy()
    ground.destroy()
    overlay._drop_beam()
    overlay.root.destroy()


if __name__ == "__main__":
    main()
