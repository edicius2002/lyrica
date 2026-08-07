"""Frosted glass and rounded corners for a tkinter window on Windows.

The blur comes from `SetWindowCompositionAttribute`, which is undocumented but
is the only way to frost what is *behind* a window. Nothing else reaches it: a
Qt blur effect blurs the widget's own content, and a webview's backdrop filter
blurs the page. Every toolkit that appears to do this is making this same call.

Two composition modes exist and they are mutually exclusive:

- **Keyed** — a layered window with a transparent colour key. Binary
  transparency, so black outlines survive, but the accent plate is always a
  rectangle: `SetWindowRgn` is ignored on a layered window.
- **Glass** — no colour key. Rounded corners work, but the desktop compositor
  blends the surface *additively*, because GDI leaves the alpha byte at zero.

Additive blending sounds like a limitation and is closer to a gift: brightness
becomes opacity, so a dimmed line is just a darker fill with no alpha channel
anywhere, and a glow is genuinely additive light rather than an imitation of it.
The cost is that pure black is invisible, which is why glass mode draws no
outline — the tinted plate supplies the contrast instead.
"""
import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19

# Alpha is the whole translucency dial. Lower lets more of the desktop through;
# higher makes a more solid slab. This can go lower than a normal panel would
# dare because the surface composites additively: the text adds its own light
# regardless of what the plate is doing, so thinning the plate costs background
# separation but not legibility. Measured: 0xCC reads ~(35,35,42) over a
# saturated backdrop, 0xA0 visibly drifts light over bright video.
TINT_RGBA = (12, 12, 16, 132)
CORNER_RADIUS = 22


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),   # 0xAABBGGRR, not RGBA
        ("AnimationId", ctypes.c_int),
    ]


class _CompositionAttribData(ctypes.Structure):
    _fields_ = [
        ("Attrib", ctypes.c_int),
        ("pvData", ctypes.c_void_p),
        ("cbData", ctypes.c_size_t),
    ]


def _abgr(r: int, g: int, b: int, a: int) -> int:
    """Pack to the byte order the accent policy expects."""
    return (a << 24) | (b << 16) | (g << 8) | r


def set_dpi_awareness() -> float:
    """Opt out of DPI virtualisation and return the current scale factor.

    Without this the window is rendered at 96 dpi and then bitmap-stretched by
    Windows, so on a scaled display every glyph is resampled — which reads as
    soft, slightly blurred text and no amount of font tuning fixes it.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            logger.debug("could not set DPI awareness", exc_info=True)
            return 1.0
    try:
        dc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, dc)
        return (dpi or 96) / 96.0
    except (AttributeError, OSError):
        return 1.0


def _hwnd_of(root) -> int:
    """The real top-level handle for a Tk window.

    Tk wraps toplevels, so `winfo_id()` is the inner window and the composition
    attribute has to go on the outer one.
    """
    root.update_idletasks()
    hwnd = int(root.winfo_id())
    parent = ctypes.windll.user32.GetParent(wintypes.HWND(hwnd))
    return int(parent) if parent else hwnd


def _set_accent(hwnd: int, state: int, tint: int) -> bool:
    try:
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
    except AttributeError:
        return False
    policy = _AccentPolicy(state, 0, tint, 0)
    data = _CompositionAttribData(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    try:
        return bool(fn(wintypes.HWND(hwnd), ctypes.byref(data)))
    except OSError:
        return False


def round_corners(root, width: int, height: int, radius: int = CORNER_RADIUS) -> bool:
    """Clip the window to a rounded rectangle.

    Only works on a non-layered window; a layered one ignores the region
    entirely. The region is in window coordinates, so it has to be reapplied
    whenever the window is resized.
    """
    try:
        hwnd = _hwnd_of(root)
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2)
        if not rgn:
            return False
        ctypes.windll.user32.SetWindowRgn(
            ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
        return True
    except (AttributeError, OSError):
        logger.debug("could not round the window corners", exc_info=True)
        return False


def apply_glass(root) -> bool:
    """Frost the desktop behind the window. False if the system refuses."""
    try:
        hwnd = _hwnd_of(root)
    except (AttributeError, OSError, ValueError):
        return False
    tint = _abgr(*TINT_RGBA)
    # Acrylic is the wider, saturated blur. Plain blur-behind is the fallback on
    # builds that predate it.
    for state in (ACCENT_ENABLE_ACRYLICBLURBEHIND, ACCENT_ENABLE_BLURBEHIND):
        if _set_accent(hwnd, state, tint):
            logger.info("glass enabled (accent state %d)", state)
            return True
    logger.info("the compositor refused the accent policy")
    return False
