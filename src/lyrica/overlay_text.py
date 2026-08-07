# -*- coding: utf-8 -*-
"""Outlined text rendering for the overlay canvas.

tkinter has no text outline, and the overlay needs one: over a browser the
background behind it is whatever the page is showing, so flat white text
disappears against a bright video. The usual trick applies — draw the string
several times in a dark colour, offset around the centre, then draw the fill on
top.

Offsets ring the centre rather than filling a square. A square of radius 2 is
24 draws per line where the ring is 8, and the extra 16 land underneath the
fill where nothing can see them.
"""
import tkinter as tk

OUTLINE_COLOUR = "#000000"


def ring_offsets(width: int) -> list[tuple[int, int]]:
    """The eight compass directions at `width` pixels, plus the diagonals at
    the step inside it so the corners do not thin out."""
    if width <= 0:
        return []
    w = width
    offsets = [(-w, 0), (w, 0), (0, -w), (0, w), (-w, -w), (-w, w), (w, -w), (w, w)]
    if w > 1:
        i = w - 1
        offsets += [(-i, -i), (-i, i), (i, -i), (i, i)]
    return offsets


def draw_outlined(canvas: tk.Canvas, x: int, y: int, text: str, *, font,
                  fill: str, wrap: int, outline: int = 2) -> int:
    """Draw centred, outlined, top-anchored text. Returns the height it used.

    Zero for empty text, so a caller stacking rows can simply add the result
    and let absent rows take no space.
    """
    if not text:
        return 0
    common = {"text": text, "font": font, "width": wrap, "anchor": "n",
              "justify": "center"}
    for dx, dy in ring_offsets(outline):
        canvas.create_text(x + dx, y + dy, fill=OUTLINE_COLOUR, **common)
    item = canvas.create_text(x, y, fill=fill, **common)
    bbox = canvas.bbox(item)
    return (bbox[3] - bbox[1]) if bbox else 0
