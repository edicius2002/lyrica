"""Easing and the glide that carries rows between positions (offline)."""
import itertools
import time

import pytest

from lyrica import motion
from lyrica.motion import Glide, cubic_bezier, row_duration

# --- easing -----------------------------------------------------------------

def test_easing_is_pinned_at_both_ends():
    assert cubic_bezier(0.0) == 0.0
    assert cubic_bezier(1.0) == 1.0


def test_easing_clamps_outside_its_range():
    assert cubic_bezier(-1.0) == 0.0
    assert cubic_bezier(2.0) == 1.0


def test_easing_never_goes_backwards():
    values = [cubic_bezier(i / 40) for i in range(41)]
    assert all(b >= a - 1e-9 for a, b in itertools.pairwise(values))


def test_the_curve_is_steep_in_the_middle():
    # What makes a move read as weight rather than a constant slide: most of the
    # distance is covered in the middle third.
    middle = cubic_bezier(0.66) - cubic_bezier(0.33)
    assert middle > 0.5


def test_a_linear_curve_is_the_identity():
    for x in (0.25, 0.5, 0.75):
        assert cubic_bezier(x, (1 / 3, 1 / 3, 2 / 3, 2 / 3)) == pytest.approx(x, abs=0.01)


# --- stagger ----------------------------------------------------------------

def test_rows_further_from_the_active_one_take_longer():
    near = row_duration(2, active_row=2)
    far = row_duration(0, active_row=2)
    assert far > near, "identical durations would move the block rigidly"


def test_the_stagger_is_symmetric_about_the_active_row():
    assert row_duration(1, active_row=2) == row_duration(3, active_row=2)


# --- glide ------------------------------------------------------------------

def test_a_glide_starts_at_its_full_distance():
    assert Glide(50, 400).offset() == pytest.approx(50, abs=1)


def test_a_glide_decays_towards_zero():
    g = Glide(50, 60)
    first = g.offset()
    time.sleep(0.03)
    second = g.offset()
    assert abs(second) < abs(first)


def test_a_finished_glide_reports_no_offset():
    g = Glide(50, 10)
    time.sleep(0.03)
    assert g.done
    assert g.offset() == 0.0


def test_a_zero_length_glide_cannot_divide_by_zero():
    # Asserted on the guard rather than by sleeping: a test that waits for a
    # millisecond to pass is a test that fails on a loaded machine.
    assert Glide(50, 0).duration > 0
    assert Glide(50, -10).duration > 0


def test_offset_carries_the_sign_of_the_distance():
    assert Glide(-40, 400).offset() < 0


def test_the_curve_used_is_the_scroll_curve():
    # Pinned deliberately: this is the value taken from a tuned implementation
    # rather than chosen, and a silent change would alter the feel of every move.
    assert motion.SCROLL_CURVE == (0.86, 0.0, 0.2, 1.0)
