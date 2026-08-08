"""Easing, and the glide that carries lines between positions.

Lines that jump between positions read as a slideshow. Animating them is what
produces the trailing feel of a good lyrics view — and the trail comes from the
rows moving at slightly *different* speeds, not from the block sliding as one
piece. A row further from the active line takes longer, so the group arrives
staggered rather than in lockstep.
"""
import time

# The curve better-lyrics scrolls on. Steep in the middle, slow at both ends,
# which is what makes a move read as weight rather than as a slide.
SCROLL_CURVE = (0.86, 0.0, 0.2, 1.0)

# The panel resizing is not a line scrolling, and borrowing the curve above for
# it was a mistake worth naming. That one is steep on purpose — over the 50 px a
# row travels, the sharp middle reads as weight. Over the 800 px the window
# changes width by when it collapses, the same curve put 89 % of the change into
# 40 % of the frames, which reads as a snap rather than a shrink. This is an
# ordinary ease-in-out: no sudden middle, and both ends still settle.
RESIZE_CURVE = (0.42, 0.0, 0.58, 1.0)

DURATION_MS = 460
# Each row further from the active line takes this much longer, which is what
# fans the movement out instead of moving the block rigidly.
STAGGER_MS = 45


def _bezier_x(t: float, x1: float, x2: float) -> float:
    u = 1 - t
    return 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t


def _bezier_y(t: float, y1: float, y2: float) -> float:
    u = 1 - t
    return 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t


def cubic_bezier(progress: float, curve: tuple = SCROLL_CURVE) -> float:
    """Eased 0..1 for a CSS-style cubic-bezier.

    The curve is given as x(t), y(t); what is wanted is y for a given x, so t
    is solved for by bisection. Twenty passes puts the error far below a pixel
    at these distances, and this runs a few times per frame at most.
    """
    if progress <= 0:
        return 0.0
    if progress >= 1:
        return 1.0
    x1, y1, x2, y2 = curve
    lo, hi = 0.0, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2
        if _bezier_x(mid, x1, x2) < progress:
            lo = mid
        else:
            hi = mid
    return _bezier_y((lo + hi) / 2, y1, y2)


class Glide:
    """One row's journey from where it was to where it belongs.

    Holds the remaining offset rather than an absolute position, so a move that
    starts while another is still running simply adds to it — the row keeps
    going instead of snapping to a new start.
    """

    def __init__(self, distance: float, duration_ms: float):
        self.distance = distance
        self.duration = max(1.0, duration_ms) / 1000.0
        self.started = time.monotonic()

    @property
    def done(self) -> bool:
        return time.monotonic() - self.started >= self.duration

    def offset(self) -> float:
        """How far the row still has to travel, easing to zero."""
        elapsed = (time.monotonic() - self.started) / self.duration
        if elapsed >= 1.0:
            return 0.0
        return self.distance * (1.0 - cubic_bezier(elapsed))


def row_duration(row_index: int, active_row: int) -> float:
    """Longer the further a row sits from the active one."""
    return DURATION_MS + abs(row_index - active_row) * STAGGER_MS
