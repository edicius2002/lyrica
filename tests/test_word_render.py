"""Word layout and recolouring logic (offline, no display).

LineView needs a live canvas, so what is covered here is the geometry and the
state machine underneath it: wrapping, measurement memoisation and which colour
each word takes. Placeholder words only.
"""
from lyrica import overlay_text
from lyrica.overlay_text import measure, ring_offsets, wrap_words


class FakeFont:
    """Fixed-width stand-in, so widths in these tests are exact."""

    def __init__(self, char_width: int = 10):
        self.char_width = char_width
        self.calls = 0

    def measure(self, text: str) -> int:
        self.calls += 1
        return len(text) * self.char_width


def words(*texts):
    return [(float(i), float(i) + 0.5, t) for i, t in enumerate(texts)]


# --- measurement ------------------------------------------------------------

def test_measurements_are_memoised():
    overlay_text._measure_cache.clear()
    font = FakeFont()
    key = ("test-font",)
    assert measure(font, key, "alpha") == 50
    assert measure(font, key, "alpha") == 50
    assert font.calls == 1, "the same word is measured on every frame"


def test_different_fonts_do_not_share_measurements():
    overlay_text._measure_cache.clear()
    small, large = FakeFont(10), FakeFont(20)
    assert measure(small, ("small",), "ab") == 20
    assert measure(large, ("large",), "ab") == 40


# --- wrapping ---------------------------------------------------------------

def test_a_short_line_stays_on_one_row():
    rows = wrap_words(words("ab", "cd"), FakeFont(), ("f",), space=10, wrap=500)
    assert len(rows) == 1
    assert [w[1] for w in rows[0]] == ["ab", "cd"]


def test_a_long_line_wraps():
    # Each word is 40 wide plus a 10 space, so three do not fit in 100.
    rows = wrap_words(words("aaaa", "bbbb", "cccc"), FakeFont(), ("f",),
                      space=10, wrap=100)
    assert len(rows) == 2
    assert [w[1] for w in rows[0]] == ["aaaa", "bbbb"]
    assert [w[1] for w in rows[1]] == ["cccc"]


def test_word_indices_survive_wrapping():
    # The renderer colours by index, so a wrapped row must still know which
    # word each entry was.
    rows = wrap_words(words("aaaa", "bbbb", "cccc"), FakeFont(), ("f",),
                      space=10, wrap=100)
    assert [w[0] for row in rows for w in row] == [0, 1, 2]


def test_a_word_wider_than_the_limit_gets_its_own_row():
    rows = wrap_words(words("a", "bbbbbbbbbbbb"), FakeFont(), ("f",),
                      space=10, wrap=50)
    assert len(rows) == 2
    assert rows[1][0][1] == "bbbbbbbbbbbb"


def test_no_words_makes_no_rows():
    assert wrap_words([], FakeFont(), ("f",), space=10, wrap=100) == []


# --- outline ----------------------------------------------------------------

def test_the_outline_ring_is_symmetric():
    # Weighted to one side it would read as a drop shadow rather than an edge.
    offsets = set(ring_offsets(2))
    assert offsets
    assert all((-dx, -dy) in offsets for dx, dy in offsets)
    assert (0, 0) not in offsets, "the centre is where the fill goes"


# --- colour state -----------------------------------------------------------

def colour_for(index: int, active_index: int, fraction: float) -> str:
    """The word-state rule, isolated from the canvas."""
    if index < active_index:
        return "sung"
    if index > active_index:
        return "unsung"
    return "sung" if fraction >= 0.5 else "active"


def test_words_behind_the_cursor_read_as_sung():
    assert colour_for(0, 2, 0.0) == "sung"
    assert colour_for(1, 2, 0.0) == "sung"


def test_words_ahead_of_the_cursor_read_as_unsung():
    assert colour_for(3, 2, 0.9) == "unsung"


def test_the_active_word_flips_at_its_midpoint():
    assert colour_for(2, 2, 0.49) == "active"
    assert colour_for(2, 2, 0.50) == "sung"


def test_before_the_first_word_nothing_is_sung():
    assert [colour_for(i, -1, 0.0) for i in range(3)] == ["unsung"] * 3


# --- the wrapped-line sweep -------------------------------------------------

def sweep_t(char_row: int, active_row: int, char_centre: float,
            front_x: float, feather: float = 40.0) -> float:
    """The rule LineView.show_sweep applies, isolated from the canvas.

    Rows share the same horizontal range, so an x position alone cannot say
    whether a character has been sung.
    """
    if active_row < 0 or char_row > active_row:
        return 0.0
    if char_row < active_row:
        return 1.0
    return max(0.0, min(1.0, (front_x - char_centre) / feather + 0.5))


def test_a_wrapped_line_does_not_light_both_rows_at_once():
    # The reported bug. Two characters at the same x on different rows must not
    # share a state just because they share a column.
    on_active = sweep_t(char_row=0, active_row=0, char_centre=300, front_x=400)
    on_next = sweep_t(char_row=1, active_row=0, char_centre=300, front_x=400)
    assert on_active == 1.0
    assert on_next == 0.0


def test_a_finished_row_stays_lit_when_the_sweep_moves_on():
    # Otherwise the first row would appear to un-sing itself as the front
    # restarts at the left margin of the second.
    assert sweep_t(char_row=0, active_row=1, char_centre=900, front_x=100) == 1.0


def test_within_the_active_row_position_still_decides():
    behind = sweep_t(char_row=1, active_row=1, char_centre=100, front_x=400)
    ahead = sweep_t(char_row=1, active_row=1, char_centre=800, front_x=400)
    assert behind == 1.0
    assert ahead == 0.0


def test_the_feather_ramps_rather_than_stepping():
    # A hard step at the front edge is what makes a sweep read as clicking
    # between characters instead of flowing.
    at_front = sweep_t(char_row=0, active_row=0, char_centre=400, front_x=400)
    just_past = sweep_t(char_row=0, active_row=0, char_centre=390, front_x=400)
    assert 0.0 < at_front < 1.0
    assert just_past > at_front
