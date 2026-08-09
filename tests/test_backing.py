"""How a backing vocal comes, is sung, and goes."""
import pytest

from lyrica.lyrics import Lyrics


@pytest.fixture
def panel():
    from lyrica import app as A
    from lyrica import bloom, config
    # An `ImageTk.PhotoImage` belongs to the interpreter that made it, and the
    # bloom's cache outlives any one of them. The overlay has a single root for
    # its whole life; a test suite does not.
    bloom._cache.clear()
    bloom._fonts.clear()
    config.load()
    o = A.Overlay()
    o.lyrics = Lyrics(
        lines=[(0.0, "I need you all night")],
        words=[[(0.0, 0.4, "I"), (0.4, 0.9, "need"), (0.9, 1.6, "you"),
                (1.6, 2.0, "all"), (2.0, 2.6, "night")]],
        synced=True, backing=["(You)"], backing_words=[[(1.0, 1.7, "(You)")]])
    o._lyrics_state = A.LYRICS_PRESENT
    o._go_to_line(0, o.lyrics)
    o.root.update()
    yield o
    o.root.destroy()


def _shown(panel):
    return None if panel._echo is None else {
        panel.canvas.itemcget(e[2], "fill") for e in panel._echo._items}


def test_it_is_absent_outside_its_own_window(panel):
    # The line it answers runs for seconds either side of it. Leaving the words
    # sitting there for all of that would make them furniture, not a voice.
    panel._show_backing(panel.lyrics, 0.1)
    assert _shown(panel) is None
    panel._show_backing(panel.lyrics, 2.5)
    assert _shown(panel) is None


def test_it_arrives_out_of_the_wash_and_leaves_into_it(panel):
    from lyrica.app import ECHO_FADE_S
    from lyrica.glass import rgb_of

    def light(colours):
        return max(sum(c * w for c, w in zip(rgb_of(x), (0.2126, 0.7152, 0.0722),
                                             strict=True)) for x in colours)

    panel._show_backing(panel.lyrics, 1.0 - ECHO_FADE_S * 0.8)
    early = light(_shown(panel))
    panel._show_backing(panel.lyrics, 1.0 - ECHO_FADE_S * 0.2)
    late = light(_shown(panel))
    assert 0 < early < late, "it has to come up rather than appear lit"

    panel._show_backing(panel.lyrics, 1.7 + ECHO_FADE_S * 0.2)
    going = light(_shown(panel))
    panel._show_backing(panel.lyrics, 1.7 + ECHO_FADE_S * 0.8)
    gone = light(_shown(panel))
    assert gone < going, "and go down rather than vanish"


def test_it_sweeps_on_its_own_timings_while_it_is_sung(panel):
    # It answers the line while the line is still being sung: the two overlap
    # rather than following one another, so it cannot share the line's clock.
    panel._show_backing(panel.lyrics, 1.3)
    assert len(_shown(panel)) > 1, "a single colour means it is not sweeping"


def test_it_sits_below_the_line_and_off_at_the_right_margin(panel):
    panel._show_backing(panel.lyrics, 1.3)
    line, echo = panel._views[0], panel._echo
    assert echo.y > line.y + line.line_height * 0.5, "it has to clear the line"
    left = min(s for s, _e in echo._row_spans)
    assert left > max(e for _s, e in line._row_spans), "and not touch it"


def test_a_line_with_nothing_behind_it_takes_the_last_one_down(panel):
    panel._show_backing(panel.lyrics, 1.3)
    assert panel._echo is not None
    panel.lyrics = Lyrics(lines=[(0.0, "solo")], words=[[(0.0, 1.0, "solo")]],
                          synced=True)
    panel._show_backing(panel.lyrics, 1.3)
    assert panel._echo is None, "it would otherwise hang over the wrong line"


def test_a_resize_takes_it_down_with_the_rest(panel):
    # It is built at one font and scale like every other line, and was the one
    # thing `_apply_scale` did not drop.
    panel._show_backing(panel.lyrics, 1.3)
    assert panel._echo is not None
    panel._apply_scale()
    assert panel._echo is None
