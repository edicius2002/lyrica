"""The path the border light travels, and the meter that drives it (offline)."""
import math
import sys

import pytest

from lyrica import meter as meter_mod
from lyrica.beam import CORNER_POINTS, STRAIGHT_SPACING, _rounded_path


def path(width=1125, height=375, radius=18, inset=2.0):
    return _rounded_path(width, height, radius, inset)


def test_the_ring_closes_on_itself():
    points = path()
    # Last point back to first must be a short hop, not a leap across the panel.
    gap = math.dist(points[-1], points[0])
    assert gap <= STRAIGHT_SPACING + 1


def test_every_point_lies_on_the_panel():
    for x, y in path():
        assert -1 <= x <= 1126 and -1 <= y <= 376


def test_the_corners_are_drawn_as_curves_not_cut_off():
    # The defect this spacing exists for. The default panel's perimeter is about
    # 2900 px and a corner arc is 28, so evenly spaced segments cheap enough to
    # recolour at 30 Hz gave a corner less than one segment — a chamfer.
    points = path()
    corner = [(x, y) for x, y in points if x < 30 and y < 30]
    assert len(corner) >= CORNER_POINTS - 1, "the corner reads as a cut"


def test_corner_points_sit_on_the_radius():
    radius, inset = 18, 2.0
    centre = (inset + radius, inset + radius)
    on_arc = [p for p in path(radius=radius, inset=inset)
              if p[0] < centre[0] and p[1] < centre[1]]
    for point in on_arc:
        assert math.dist(point, centre) == pytest.approx(radius, abs=1.0)


def test_a_longer_edge_gets_more_points_but_the_corner_does_not():
    short, long = path(width=400), path(width=1600)
    assert len(long) > len(short)
    for points in (short, long):
        corner = [(x, y) for x, y in points if x < 30 and y < 30]
        assert len(corner) >= CORNER_POINTS - 1


def test_a_panel_too_small_for_its_radius_still_produces_a_ring():
    # The compact panel is 114 px tall; the radius must not exceed half of it.
    points = path(width=120, height=60, radius=40)
    assert len(points) >= 4
    for x, y in points:
        assert -1 <= x <= 121 and -1 <= y <= 61


# --- the meter --------------------------------------------------------------

def test_a_platform_without_a_meter_reports_silence():
    null = meter_mod.NullMeter()
    assert not null.available
    assert null.level() == 0.0
    null.close()


def test_the_platform_picks_the_meter_it_can_use():
    made = meter_mod.create_meter()
    if sys.platform == "win32":
        assert isinstance(made, meter_mod.WindowsMeter)
    else:
        assert isinstance(made, meter_mod.NullMeter)
    made.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only meter")
def test_the_level_rises_at_once_and_falls_gently(monkeypatch):
    # A peak meter reads nothing between beats. Following it raw strobes; this
    # keeps the attack and turns the gaps into a decay.
    made = meter_mod.WindowsMeter.__new__(meter_mod.WindowsMeter)
    made._meter, made._value, made.available = None, 0.0, True
    monkeypatch.setattr(made, "raw", lambda: 0.8)
    assert made.level(1 / 60) == pytest.approx(0.8), "a rise must land at once"

    monkeypatch.setattr(made, "raw", lambda: 0.0)
    after_one = made.level(1 / 60)
    assert 0.0 < after_one < 0.8, "a fall must take time"
    assert made.level(1.0) == 0.0, "and must reach silence eventually"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only timer")
def test_holding_the_timer_resolution_is_idempotent():
    # Every begin has to be matched by exactly one end, so asking twice for a
    # state already held must not leave the resolution raised on the way out.
    from lyrica.chrome import windows as win

    assert win.hold_timer_resolution(True) is True
    assert win.hold_timer_resolution(True) is True
    assert win.hold_timer_resolution(False) is False
    assert win.hold_timer_resolution(False) is False


def test_asking_for_even_frames_is_safe_on_any_platform():
    from lyrica import chrome as chrome_mod

    chrome_mod.hold_timer_resolution(True)
    chrome_mod.hold_timer_resolution(False)
