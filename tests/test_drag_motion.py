"""What the lyrics do while the window is being dragged (offline, no display)."""
from lyrica.app import CONTEXT, should_animate


def test_a_neighbouring_line_glides_normally():
    # The trailing feel of a good lyrics view comes from rows arriving at
    # slightly different times, and that only exists if they animate.
    assert should_animate(1, dragging=False) is True
    assert should_animate(CONTEXT, dragging=False) is True


def test_a_long_jump_lands_rather_than_travels():
    # A seek is a discontinuity. Animating one is how a view ends up chasing
    # itself across a song.
    assert should_animate(CONTEXT + 1, dragging=False) is False
    assert should_animate(None, dragging=False) is False
    assert should_animate(0, dragging=False) is False


def test_nothing_glides_while_the_window_is_being_dragged():
    # The whole lyric column is dirty for every frame of a glide, and on top of
    # a moving window that measured 5.41 ms a frame against 1.39 for the sweep
    # alone, worst case 45 ms — nearly three frames, which is what a hand feels
    # as a stutter.
    for step in (None, 0, 1, CONTEXT, CONTEXT + 1, 99):
        assert should_animate(step, dragging=True) is False


def test_the_drag_only_suppresses_the_glide():
    # It must not also suppress the move. The sweep and the line positions keep
    # up; what stops is the travelling between them — otherwise releasing the
    # window snaps everything to a state it never showed arriving at, which is
    # the behaviour this replaced.
    assert should_animate(1, dragging=True) is False
    assert should_animate(1, dragging=False) is True
