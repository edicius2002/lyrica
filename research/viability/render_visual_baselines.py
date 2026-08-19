"""Render the five deterministic visual-review frames committed under docs/.

Run on Windows from the repository root:

    py research/viability/render_visual_baselines.py

The window is opaque and contains only synthetic lyrics, so a capture cannot
include the desktop or real listening history.
"""
from __future__ import annotations

import argparse
import time
import tkinter as tk
from pathlib import Path

import numpy as np
from PIL import ImageColor, ImageGrab

from lyrica import bloom, halo
from lyrica.app import (
    ECHO_CORNER_INSET,
    ECHO_KEEP,
    ECHO_SAFE_MARGIN,
    ECHO_VERTICAL_GAP,
    ROW_GAP,
    VOICE_SAFE_MARGIN,
)
from lyrica.beam import SHINE, Beam, shape_at
from lyrica.chrome import prepare
from lyrica.lineview import GROW_ATTACK_S, LineView
from lyrica.meter import Character
from lyrica.palette import DEFAULT

WIDTH, HEIGHT = 900, 320
BACKGROUND = "#10131a"
FONT = ("Segoe UI", -30, "bold")
SMALL = ("Segoe UI", -20, "italic")


class _Surface:
    """The companion window, in memory: the five calls `halo.Spill` makes on it.

    Not a mock — it reproduces the one property the code leans on here, a
    surface that survives between frames, so the composite can read back what
    the outward strips actually wrote.
    """

    def __init__(self):
        import numpy as np

        self._np = np
        self.capacity = (0, 0)
        self._frame = None

    def reserve(self, width, height):
        if width <= self.capacity[0] and height <= self.capacity[1]:
            return False
        width, height = max(width, self.capacity[0]), max(height, self.capacity[1])
        self._frame = self._np.zeros((height, width, 4), self._np.uint8)
        self.capacity = (width, height)
        return True

    def frame(self):
        return self._frame

    def present(self, *_a): pass
    def move(self, *_a): pass
    def behind(self, *_a): pass
    def visible(self, *_a): pass
    def destroy(self): pass


def _straight(surface):
    """The surface's premultiplied BGRA as the straight RGBA PIL composites with.

    `UpdateLayeredWindow` wants the colours already multiplied by their alpha
    and `halo.Spill` folds that into the lookup table, where it is free. PIL
    wants them unmultiplied, so this is the one place in the repository that
    ever divides it back out.
    """
    from PIL import Image

    alpha = surface[..., 3:4].astype(np.float32)
    colour = np.where(alpha > 0,
                      surface[..., 2::-1].astype(np.float32) * 255.0
                      / np.maximum(alpha, 1e-6), 0.0)
    out = np.concatenate([np.clip(colour, 0, 255), alpha], axis=2)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def _rounded(width: int, height: int, radius: int):
    """The panel's own footprint, the way `SetWindowRgn` cuts it."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1),
                                           radius=radius, fill=255)
    return mask


def words(text: str, start: float = 0.0) -> list:
    made = []
    for index, word in enumerate(text.split()):
        made.append((start + index * 0.45, start + (index + 1) * 0.45, word))
    return made


class Renderer:
    def __init__(self, output: Path):
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        prepare()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{WIDTH}x{HEIGHT}+20+20")
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT,
                                bg=BACKGROUND, highlightthickness=0)
        self.canvas.pack()

    def clear(self) -> None:
        self.canvas.delete("all")
        bloom._cache.clear()

    def capture(self, name: str) -> None:
        self.root.update_idletasks()
        self.root.update()
        self.root.lift()
        time.sleep(0.08)
        left, top = self.root.winfo_rootx(), self.root.winfo_rooty()
        image = ImageGrab.grab((left, top, left + WIDTH, top + HEIGHT))
        image.save(self.output / f"{name}.png")

    def word_strike(self) -> None:
        self.clear()
        view = LineView(self.canvas, WIDTH // 2, 118, "Feel the light move",
                        words("Feel the light move"), font=FONT, wrap=800,
                        palette=DEFAULT, growth=0.14, bloom=1.0)
        view.set_active(True)
        view.show_sweep(2, 0.25)
        when = min(hit for hit, _span in view._hit.values())
        for _ in range(6):
            view.advance_bloom(when + GROW_ATTACK_S)
        self.capture("word-strike")
        view.destroy()

    def duet_lanes(self) -> None:
        self.clear()
        left = LineView(self.canvas, WIDTH // 2, 78, "Tell me where you are",
                        words("Tell me where you are"), font=FONT, wrap=800,
                        palette=DEFAULT, lean=-160)
        right = LineView(self.canvas, WIDTH // 2, 164, "I am waiting here",
                         words("I am waiting here"), font=FONT, wrap=800,
                         palette=DEFAULT, lean=160)
        for view in (left, right):
            view.fit(VOICE_SAFE_MARGIN, WIDTH - VOICE_SAFE_MARGIN)
            view.show_inactive(DEFAULT.side)
        right.set_active(True)
        right.show_sweep(1, 0.7)
        self.capture("duet-lanes")
        left.destroy()
        right.destroy()

    def backing_vocal(self) -> None:
        self.clear()
        lead = LineView(self.canvas, 290, 94, "Stay with me tonight",
                        words("Stay with me tonight"), font=FONT, wrap=520,
                        palette=DEFAULT)
        # The response no longer hangs inside the lead's own box. It occupies
        # the lane reserved between the two lyric rows, which is what `ROW_GAP`
        # is sized for — see `Overlay._place_backing_y`.
        echo_y = lead.y + lead.height + ECHO_VERTICAL_GAP
        echo = LineView(self.canvas, WIDTH // 2, echo_y, "(right here)",
                        words("right here"), font=SMALL, wrap=300,
                        palette=DEFAULT.dimmed(ECHO_KEEP))
        echo_width = max(b - a for a, b in echo._row_spans)
        corner = max(b for _a, b in lead._row_spans)
        echo.lean = (corner - ECHO_CORNER_INSET + echo_width / 2
                     - WIDTH / 2)
        echo.fit(ECHO_SAFE_MARGIN, WIDTH - ECHO_SAFE_MARGIN)
        following = LineView(
            self.canvas, 610, lead.y + lead.height + ROW_GAP,
            "Don't let it fade", words("Don't let it fade"), font=FONT,
            wrap=520, palette=DEFAULT)
        lead.set_active(True)
        lead.show_sweep(2, 0.35)
        echo.set_active(True)
        echo.show_sweep(0, 0.65)
        following.show_inactive(DEFAULT.side)
        # The same order `Overlay._order_text_layers` applies, and it has to be
        # applied here too: creation order alone would put the echo above the
        # lead, where the renderer deliberately keeps it underneath. Its halo is
        # allowed to reach into a neighbour's box precisely because no part of
        # it can ever cover a main lyric glyph.
        for view in (echo, following, lead):
            view.raise_layer()
        self.capture("backing-vocal")
        lead.destroy()
        echo.destroy()
        following.destroy()

    def beam(self, name: str, character: Character) -> None:
        """Composed in PIL rather than grabbed off the glass.

        The border is images now, so its frame can be assembled from the exact
        pixels both windows would be handed — which is both more faithful than a
        screen capture and free of the two things a capture costs: a visible
        window that something else can land on top of, and a dependence on
        whatever the compositor did to the panel on the way to the screen.

        **Both** windows. The panel no longer carries the light: it occludes it,
        and what a reviewer has to see is what escapes round the outside. So the
        panel is drawn inset by the light's own reach inside a frame of the
        committed size, with a companion surface underneath it, rather than
        filling the frame with the canvas half alone — which since the light
        went behind the panel would be a picture of four dark edges.
        """
        from PIL import Image

        self.clear()
        surface = _Surface()
        pad = halo.pad_of(shape_at(1.0))
        inner = (WIDTH - 2 * pad, HEIGHT - 2 * pad)
        ring = Beam(self.canvas, inner[0], inner[1], 18, 1.0, SHINE,
                    glow=surface)
        ring.advance(0.7, character, DEFAULT)
        # Every strip. `halo.PER_CALL` bounds what a *frame* may repaint; a
        # still of one strip's worth of border is a still of the animation's
        # worst moment rather than of the border.
        for _ in range(len(ring.light.strips) * 2):
            ring.light.paint(ring._tables)

        ground = (*ImageColor.getrgb(BACKGROUND), 255)
        frame = Image.new("RGBA", (WIDTH, HEIGHT), ground)
        frame.alpha_composite(_straight(surface.frame()[:HEIGHT, :WIDTH]))
        plate = Image.new("RGBA", inner, ground)
        for strip in ring.light.strips:
            if strip.box is not None:
                plate.alpha_composite(ring.light.image(strip, ring._tables),
                                      (strip.box[0], strip.box[1]))
        plate.putalpha(_rounded(*inner, 18))
        frame.alpha_composite(plate, (pad, pad))
        frame.convert("RGB").save(self.output / f"{name}.png")
        ring.destroy()

    def close(self) -> None:
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("docs/visual-baselines"))
    args = parser.parse_args()
    renderer = Renderer(args.output)
    try:
        renderer.word_strike()
        renderer.duet_lanes()
        renderer.backing_vocal()
        renderer.beam("beam-quiet", Character(level=0.0, dynamics=0.0, rate=0.0))
        renderer.beam("beam-loud", Character(level=1.0, dynamics=1.0, rate=0.8))
    finally:
        renderer.close()


if __name__ == "__main__":
    main()
