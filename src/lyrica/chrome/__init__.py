"""Window chrome: how the overlay sits on the desktop, per platform.

Two modes, chosen by what the platform actually allows rather than by
preference:

- **GLASS** — the desktop behind is frosted, corners are rounded, and the
  surface composites additively. Brightness is opacity, so nothing needs an
  alpha channel and pure black is invisible.
- **KEYED** — a transparent colour key. Square corners and no frosting, but
  text can be any colour and needs a dark outline to stay legible over whatever
  happens to be behind it.

The renderer reads the mode and picks a palette; nothing else in the app knows
the difference.
"""
import logging
import sys
import tkinter as tk
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# The colour key: any pixel drawn in it becomes a hole through the window. An
# unusual near-black so no real content lands on it by accident.
KEY_COLOUR = "#010203"

# In glass mode black is not a colour, it is the absence of light — the plate
# shows through untouched.
GLASS_BACKGROUND = "#000000"


class ChromeMode(Enum):
    GLASS = "glass"
    KEYED = "keyed"


@dataclass(frozen=True)
class Chrome:
    mode: ChromeMode
    background: str
    scale: float = 1.0

    @property
    def additive(self) -> bool:
        """True when the surface adds light rather than replacing it."""
        return self.mode is ChromeMode.GLASS

    def px(self, value: float) -> int:
        """A logical measurement in real device pixels."""
        return round(value * self.scale)


def _keyed(root: tk.Tk, scale: float = 1.0) -> Chrome:
    root.configure(bg=KEY_COLOUR)
    root.attributes("-transparentcolor", KEY_COLOUR)
    return Chrome(ChromeMode.KEYED, KEY_COLOUR, scale)


def prepare() -> float:
    """Declare DPI awareness and return the display scale. **Call before Tk.**

    Order matters and the failure is silent: Tk reads the system metrics when
    the root window is created, so declaring awareness afterwards leaves it
    holding virtualised numbers. Every geometry it reports is then wrong by the
    scale factor, and the window disagrees with the canvas drawn inside it.
    """
    if sys.platform != "win32":
        return 1.0
    from lyrica.chrome import windows as win
    return win.set_dpi_awareness()


def setup(root: tk.Tk, scale: float = 1.0) -> Chrome:
    """Prepare the window and report which mode was achieved.

    Glass is attempted first and the keyed mode is the fallback, so a machine
    whose compositor refuses the accent policy still gets a working overlay
    rather than an invisible one.
    """
    if sys.platform != "win32":
        # macOS has its own vocabulary for this (a visual-effect view) that Tk
        # does not expose, so the keyed path is what there is for now. It is
        # honest rather than pretending: `-transparentcolor` is Windows-only,
        # so on a Mac this yields a plain opaque window.
        return _keyed(root, scale)

    from lyrica.chrome import windows as win

    root.configure(bg=GLASS_BACKGROUND)
    root.update_idletasks()
    root.update()

    if win.apply_glass(root):
        return Chrome(ChromeMode.GLASS, GLASS_BACKGROUND, scale)

    logger.info("falling back to colour-key transparency")
    return _keyed(root, scale)


def suspend_effects(root: tk.Tk, chrome: Chrome, suspended: bool) -> None:
    """Drop the blur while the window is being moved, restore it after."""
    if chrome.mode is not ChromeMode.GLASS or sys.platform != "win32":
        return
    from lyrica.chrome import windows as win
    win.suspend_glass(root, suspended)


def move(root: tk.Tk, x: int, y: int) -> None:
    """Move the window, by the quickest route the platform offers."""
    if sys.platform == "win32":
        from lyrica.chrome import windows as win
        if win.move_window(root, x, y):
            return
    root.geometry(f"+{int(x)}+{int(y)}")


# Rounded corners are off, and the earlier reason given here was wrong. It said
# the clip region's binary coverage made the curve a stair-step. Photographing
# the corner showed something simpler: with the frosting on, the corner is not
# rounded at all. DWM draws the accent plate over the whole window rectangle and
# ignores the region, so the curve never reaches the screen to be jagged. See
# `chrome.windows` for the measurement and what was ruled out.
#
# Setting a radius here is therefore only meaningful in the modes that have no
# accent plate.
CORNER_RADIUS = 0


def shape(root: tk.Tk, chrome: Chrome, width: int, height: int) -> bool:
    """Clip the window to rounded corners. **Call after the geometry is set.**

    The region is in device pixels and does not track the window, so applying
    it before the final size clips the window to whatever it was at the time —
    which shows up as content mysteriously cut off at the right and bottom, not
    as a missing corner. It has to be reapplied on every resize for the same
    reason.
    """
    if (chrome.mode is not ChromeMode.GLASS or sys.platform != "win32"
            or CORNER_RADIUS <= 0):
        # A layered window ignores the region entirely, so in keyed mode this
        # would silently do nothing.
        return False
    from lyrica.chrome import windows as win
    return win.round_corners(root, width, height, chrome.px(CORNER_RADIUS))


__all__ = ["GLASS_BACKGROUND", "KEY_COLOUR", "Chrome", "ChromeMode", "setup"]
