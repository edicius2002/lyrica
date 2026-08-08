"""Frosted glass and rounded corners for a tkinter window on Windows.

The blur comes from `SetWindowCompositionAttribute`, which is undocumented but
is the only way to frost what is *behind* a window. Nothing else reaches it: a
Qt blur effect blurs the widget's own content, and a webview's backdrop filter
blurs the page. Every toolkit that appears to do this is making this same call.

This is only the frosted path and the Windows-specific plumbing. The mode the
app actually wears — a uniformly alpha-blended panel with rounded corners —
needs nothing undocumented and lives in `chrome/__init__`.

**Rounded corners and frosting cannot both be had on this build**, and the
reason is not the one it looks like. `SetWindowRgn` is accepted and does clip
the window — measured, with the accent off the desktop shows through at the
corner, and it clips a layered window and its child widgets just as well. But
DWM draws the accent plate over the whole window *rectangle* and ignores the
region, under acrylic and under the gradient state alike, whichever order the
two are applied in and whether or not the frame is invalidated afterwards. So
the corner is not jagged, it is simply not there: the plate squares it off
again. The attribute that would fix this, `DWMWA_WINDOW_CORNER_PREFERENCE`,
needs Windows 11 and is refused here.

`research/viability/probe_corners.py` photographs the corner under each
combination, for when this is worth re-testing on another machine.

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

ACCENT_DISABLED = 0
ACCENT_ENABLE_TRANSPARENTGRADIENT = 2
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

# What the panel wears while it is being dragged, calibrated so the switch
# changes the blur and nothing else.
#
# The gradient state blends plainly — plate = (1-a)*desktop + a*tint — while
# acrylic was measured at plate = 0.388*desktop + 18. Setting (1-a) = 0.388 and
# a*tint = 18 puts the two on the same line: alpha 156, tint 29. Measured
# against acrylic over five desktop brightnesses, that lands 18/43/68/96/117
# where acrylic gives 18/42/67/95/116 — a mean error of 0.9 and a worst of 1,
# which is measurement noise.
#
# The blue is carried up in the same proportion as the acrylic tint's, so the
# faint cool cast survives the switch too.
DRAG_TINT_RGBA = (29, 29, 33, 156)
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


# Windows schedules timers on a 15.625 ms quantum by default, and Tk's `after`
# inherits it. Measured: a 33 ms request lands on 46.17 ms — 22 Hz for something
# asking for 30 — and a 16 ms request, while it averages 16.42, does so with a
# standard deviation of 6.89 ms. That second number is the one that shows: at
# 60 Hz with frames arriving 7 ms apart from where they should, motion stutters
# however high the average rate looks.
#
# One millisecond is the finest the API offers. It measured 33.09 ms and 16.10 ms
# with deviations of 0.36 and 0.35 — twenty times more even.
#
# It is not free: a higher timer interrupt rate costs a little power, which is
# why Windows does not default to it. So it is held only while the overlay is
# actually drawing, and released when it is hidden.
TIMER_RESOLUTION_MS = 1
_timer_held = False


def hold_timer_resolution(hold: bool) -> bool:
    """Raise or release the scheduler's granularity. Idempotent."""
    global _timer_held
    if hold == _timer_held:
        return _timer_held
    try:
        winmm = ctypes.windll.winmm
        if hold:
            ok = winmm.timeBeginPeriod(TIMER_RESOLUTION_MS) == 0
        else:
            ok = winmm.timeEndPeriod(TIMER_RESOLUTION_MS) == 0
    except (AttributeError, OSError):
        logger.debug("could not change the timer resolution", exc_info=True)
        return _timer_held
    if ok:
        _timer_held = hold
    return _timer_held


# The virtual desktop: every monitor together, in one coordinate space whose
# origin can be negative when a screen sits left of or above the primary one.
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def desktop_bounds() -> tuple | None:
    """(left, top, width, height) across all monitors, or None if unavailable."""
    try:
        metric = ctypes.windll.user32.GetSystemMetrics
        bounds = (metric(SM_XVIRTUALSCREEN), metric(SM_YVIRTUALSCREEN),
                  metric(SM_CXVIRTUALSCREEN), metric(SM_CYVIRTUALSCREEN))
    except (AttributeError, OSError):
        return None
    return bounds if bounds[2] > 0 and bounds[3] > 0 else None


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

    The region is in window coordinates and does not track the window, so it
    has to be reapplied on every resize.

    Succeeding here is not the same as the corner appearing: an accent plate is
    drawn over the full rectangle regardless (see the module docstring), so this
    shows only where there is no accent — which is the blended panel, the mode
    that asks for it.

    The clip is binary, so the curve is a stair-step of single pixels. Measured
    at radius 18 against a bright desktop, that is invisible at 100 % and only
    resolves into steps past about 4x.
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


SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000


def move_window(root, x: int, y: int) -> bool:
    """Move the window by asking Windows directly.

    Measured at roughly half the cost of Tk's own geometry call — 1.0 ms
    against 1.8 ms, and a worst case of 2.6 ms against 14.7 ms while the
    overlay is animating. The worst case is what a hand notices: 14 ms is
    almost a whole frame, so a drag built on the slower path stutters exactly
    when the lyrics are moving.

    Asynchronous, because a drag issues these faster than the compositor needs
    to finish one, and waiting for each is what turns a stream of moves into a
    queue.
    """
    try:
        hwnd = _hwnd_of(root)
        return bool(ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(hwnd), None, int(x), int(y), 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS))
    except (AttributeError, OSError, ValueError):
        return False


def suspend_glass(root, suspended: bool) -> bool:
    """Drop the blur for the duration of a drag, and restore it after.

    The compositor recomputes the blur for every position a moving window
    passes through, and that work happens outside this process where it cannot
    be measured from here. Everything inside the process measured clean — the
    move lands in ~2.4 ms, the event loop stalls twice in eight seconds — so
    this is where the cost is, by elimination rather than by direct sighting.

    **What it must not do is switch the accent off.** That was the first
    version, and it dropped the plate entirely: measured at 0 over every
    desktop brightness, against acrylic's 18 to 116, so the panel snapped to a
    flat black rectangle the moment a hand touched it. The blur is the
    expensive part; the tint costs nothing and there is no reason to lose it.

    So it switches to the gradient state instead, carrying a tint calibrated
    onto acrylic's own line. The colour is unchanged to within a level and only
    the frosting stops — which, on a panel that is at that moment sliding
    across the screen, is the half nobody is looking at.
    """
    try:
        hwnd = _hwnd_of(root)
    except (AttributeError, OSError, ValueError):
        return False
    if suspended:
        return _set_accent(hwnd, ACCENT_ENABLE_TRANSPARENTGRADIENT,
                           _abgr(*DRAG_TINT_RGBA))
    return _set_accent(hwnd, ACCENT_ENABLE_ACRYLICBLURBEHIND, _abgr(*TINT_RGBA))


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
