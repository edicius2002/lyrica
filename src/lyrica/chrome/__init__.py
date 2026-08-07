"""Window chrome: how the overlay sits on the desktop, per platform.

Three modes, chosen by what the platform allows rather than by preference:

- **PANEL** — a uniformly alpha-blended window with rounded corners. The
  desktop behind shows through sharp rather than frosted, and everything drawn
  is translucent to the same degree, text included. Composition is ordinary
  replacement inside the surface and a plain blend on the way out.
- **FROSTED** — the desktop behind is blurred by the compositor and the surface
  composites *additively*, so brightness doubles as opacity and pure black is
  invisible. Corners are square and cannot be otherwise: DWM draws the accent
  plate over the whole window rectangle and ignores the clip region.
- **KEYED** — a transparent colour key. Square corners, no translucency, and
  text needs a dark outline to survive whatever is behind it.

Frosting and rounded corners are mutually exclusive on Windows 10, measured
both ways (`research/viability/probe_corners.py`). PANEL is what this app
chooses; FROSTED is kept working behind `PREFERRED` so the choice can be undone
without re-deriving anything.

The renderer reads the mode's composition law and derives a palette from it;
nothing else in the app knows the difference.
"""
import logging
import sys
import tkinter as tk
from dataclasses import dataclass
from enum import Enum

from lyrica import config, glass

logger = logging.getLogger(__name__)

# The colour key: any pixel drawn in it becomes a hole through the window. An
# unusual near-black so no real content lands on it by accident.
KEY_COLOUR = "#010203"

# Both translucent modes paint on black. Under frosting that is the absence of
# light and the plate shows through; under a blended panel it is the darkest
# the panel can be, which is what makes it read as smoked glass.
DARK_BACKGROUND = "#000000"


class ChromeMode(Enum):
    PANEL = "panel"
    FROSTED = "frosted"
    KEYED = "keyed"


# Which look to attempt first. Set this to FROSTED to trade the rounded corners
# back for the blur.
PREFERRED = ChromeMode.PANEL

# Rounded enough to read as deliberate at a glance, not so round the top line
# has to be inset to clear it.
CORNER_RADIUS = 14

# Composition for the colour-keyed fallback: whatever is drawn arrives exactly,
# over a background this mode knows nothing about.
KEYED_COMPOSITION = glass.Composition("keyed", 1.0, 0.0, 0.0)


@dataclass(frozen=True)
class Chrome:
    mode: ChromeMode
    background: str
    composition: glass.Composition
    scale: float = 1.0

    @property
    def washed(self) -> bool:
        """True where a cover wash is drawn behind the text.

        Which is also the question "can a line fade by dissolving into what is
        behind it" — over a colour key there is nothing behind it to dissolve
        into but the desktop, and aiming a glyph at that makes a silhouette.
        """
        return self.mode is not ChromeMode.KEYED

    def px(self, value: float) -> int:
        """A logical measurement in real device pixels."""
        return round(value * self.scale)


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


def _keyed(root: tk.Tk, scale: float) -> Chrome:
    root.configure(bg=KEY_COLOUR)
    root.attributes("-transparentcolor", KEY_COLOUR)
    return Chrome(ChromeMode.KEYED, KEY_COLOUR, KEYED_COMPOSITION, scale)


def _panel(root: tk.Tk, scale: float) -> Chrome | None:
    """A uniformly translucent window. Rounded corners follow, where they can.

    Nothing undocumented here — `-alpha` is Tk's own attribute and works on both
    platforms — which is why this is tried before the accent policy rather than
    after it.
    """
    root.configure(bg=DARK_BACKGROUND)
    alpha = config.opacity()
    try:
        root.attributes("-alpha", alpha)
    except tk.TclError:
        logger.info("the display does not support window alpha")
        return None
    # Built from the same number the window wears, not from the default. The
    # palette solves against this composition, so the two drifting apart would
    # mean colours derived for a panel that is not the one on screen.
    return Chrome(ChromeMode.PANEL, DARK_BACKGROUND, glass.alpha_panel(alpha),
                  scale)


def _frosted(root: tk.Tk, scale: float) -> Chrome | None:
    if sys.platform != "win32":
        return None
    from lyrica.chrome import windows as win

    root.configure(bg=DARK_BACKGROUND)
    root.update_idletasks()
    root.update()
    if win.apply_glass(root):
        return Chrome(ChromeMode.FROSTED, DARK_BACKGROUND, glass.ACRYLIC, scale)
    return None


def setup(root: tk.Tk, scale: float = 1.0) -> Chrome:
    """Prepare the window and report which mode was achieved.

    Ordered so a machine that refuses the preferred look still gets a working
    overlay rather than an invisible one. The keyed mode is last because it is
    the only one that gives up translucency entirely.
    """
    order = ([_panel, _frosted] if PREFERRED is ChromeMode.PANEL
             else [_frosted, _panel])
    for attempt in order:
        chrome = attempt(root, scale)
        if chrome is not None:
            logger.info("chrome: %s", chrome.mode.value)
            return chrome

    logger.info("falling back to colour-key transparency")
    return _keyed(root, scale)


def hold_timer_resolution(hold: bool) -> None:
    """Ask the platform for even frame scheduling while the overlay draws.

    Windows hands out timers on a 15.625 ms quantum unless asked otherwise, and
    everything animated here inherits it. Released when nothing is being drawn,
    because the finer resolution costs a little power.
    """
    if sys.platform != "win32":
        return
    from lyrica.chrome import windows as win
    win.hold_timer_resolution(hold)


def suspend_effects(root: tk.Tk, chrome: Chrome, suspended: bool) -> None:
    """Drop the expensive part of the window effect while it is being moved.

    Only frosting has one. The compositor recomputes a blur for every position
    a moving window passes through; a uniform alpha costs the same wherever the
    window is, so a blended panel has nothing to give up and this does nothing.
    """
    if chrome.mode is not ChromeMode.FROSTED or sys.platform != "win32":
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


def shape(root: tk.Tk, chrome: Chrome, width: int, height: int) -> bool:
    """Clip the window to rounded corners. **Call after the geometry is set.**

    The region is in device pixels and does not track the window, so applying it
    before the final size clips the window to whatever it was at the time —
    which shows up as content mysteriously cut off at the right and bottom, not
    as a missing corner. It has to be reapplied on every resize for the same
    reason.

    Frosted mode gets nothing from this and says so: the region is accepted,
    and then the accent plate is drawn over the whole rectangle anyway.
    """
    if (chrome.mode is not ChromeMode.PANEL or sys.platform != "win32"
            or CORNER_RADIUS <= 0):
        return False
    from lyrica.chrome import windows as win
    return win.round_corners(root, width, height, chrome.px(CORNER_RADIUS))


__all__ = ["CORNER_RADIUS", "DARK_BACKGROUND", "KEY_COLOUR", "Chrome",
           "ChromeMode", "setup"]
