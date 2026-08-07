"""Word layout and recolouring logic (offline, no display).

`WordLine` needs a live canvas, so what is covered here is the geometry and the
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

def test_word_outline_is_symmetric_like_the_line_outline():
    offsets = set(ring_offsets(overlay_text.WORD_OUTLINE))
    assert offsets
    assert all((-dx, -dy) in offsets for dx, dy in offsets)
    assert (0, 0) not in offsets


# --- colour state -----------------------------------------------------------

def colour_for(index: int, active_index: int, fraction: float) -> str:
    """The rule WordLine.update applies, isolated from the canvas."""
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
