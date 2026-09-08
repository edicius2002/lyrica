"""Capture real overlay geometry with synthetic lyrics and no running services.

Run with this checkout's src on PYTHONPATH. --baseline loads only app.py from
96da0d0 using git show; it does not switch or modify any checkout. Images use
the opaque, bounded capture convention of render_visual_baselines.py.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

from PIL import Image, ImageGrab
from render_visual_baselines import _rounded

from lyrica import app
from lyrica.chrome import CORNER_RADIUS
from lyrica.lyrics import Lyrics

SHORT = "Stay with me"
WRAPPED = ("I need you all night long and every single morning after when the "
           "city is still asleep and nothing else")


def _timed(text, start):
    parts = text.split()
    step = 2.0 / len(parts)
    return [(start + i * step, start + (i + 1) * step, word)
            for i, word in enumerate(parts)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("research/shots/adlib-layout"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    module = app
    if args.baseline:
        git = shutil.which("git")
        if git is None:
            parser.error("git is required for the local baseline")
        # Fixed arguments read local trusted code; no shell or fetched content.
        source = subprocess.check_output(  # noqa: S603
            [git, "show", "96da0d0:src/lyrica/app.py"],
            text=True, encoding="utf-8")
        module = types.ModuleType("lyrica._adlib_baseline")
        sys.modules[module.__name__] = module
        # Fixed trusted repository revision, never fetched or supplied text.
        exec(compile(source, "96da0d0/app.py", "exec"), module.__dict__)  # noqa: S102
    panel = module.Overlay()
    records = []
    try:
        panel._drop_beam()
        panel._lyrics_state = module.LYRICS_PRESENT
        for scale in (0.75, 1.0):
            for lead, below in ((SHORT, SHORT), (SHORT, WRAPPED),
                                (WRAPPED, SHORT), (WRAPPED, WRAPPED)):
                panel._clear_views()
                panel._dpi_scale, panel._size = scale, 1.0
                panel._apply_scale()
                panel.lyrics = Lyrics(
                    lines=[(0.0, lead), (3.0, below)], synced=True,
                    words=[_timed(lead, 0.0), _timed(below, 3.0)],
                    backing=["(You)", ""], backing_words=[[(1.0, 2.0, "(You)")], []])
                panel._compact = False
                panel._resize_window(panel.chrome.px(app.WIDTH), panel.chrome.px(app.HEIGHT))
                panel._go_to_line(0, panel.lyrics, animate=False)
                panel._incoming_fades.clear()
                panel._advance_glides()
                panel._restyle()
                panel._views[0].show_sweep(1, 0.5)
                panel._show_backing(panel.lyrics, 1.3, effects=False)
                panel._present_incoming_preview()
                panel._lay_out_card("Synthetic ad-lib geometry", "Offline visual probe")
                panel.canvas.itemconfigure(panel._title_item, text="Synthetic ad-lib geometry")
                panel.canvas.itemconfigure(panel._artist_item, text="Offline visual probe")
                panel.canvas.itemconfigure(panel._thumb_item, fill="#324454")
                panel.root.attributes("-transparentcolor", "")
                panel.root.attributes("-alpha", 1.0)
                panel.root.geometry("+50+50")
                panel.root.update()
                time.sleep(0.1)
                left, top = panel.canvas.winfo_rootx(), panel.canvas.winfo_rooty()
                shot = ImageGrab.grab((left, top, left + panel.width, top + panel.height))
                # Never retain pixels outside the rounded panel footprint.
                ground = Image.new("RGB", shot.size, panel.chrome.background)
                ground.paste(shot, mask=_rounded(*shot.size, panel.chrome.px(CORNER_RADIUS)))
                counts = [int(v.height / v.line_height) for v in panel._views.values()]
                name = f"{'before' if args.baseline else 'after'}-{scale}-{counts[0]}x{counts[1]}"
                ground.save(args.output / f"{name}.png")
                echo = panel._echo
                records.append({
                    "name": name, "size": [panel.width, panel.height],
                    "card_floor": panel._content_top,
                    "rows": {i: {"y": v.y, "ink": v.glyph_vertical_span(),
                                 "visual": v.visual_vertical_span()}
                             for i, v in panel._views.items()},
                    "echo": None if echo is None else {
                        "font": echo._font, "ink": echo.glyph_vertical_span(),
                        "visual": echo.visual_vertical_span()},
                })
    finally:
        panel._clear_views()
        panel.meter.close()
        panel.root.destroy()
    path = args.output / ("before.json" if args.baseline else "after.json")
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
