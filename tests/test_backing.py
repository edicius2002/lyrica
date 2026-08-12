"""How a backing vocal comes, is sung, and goes."""
import pytest

from lyrica.lyrics import Lyrics


@pytest.fixture
def panel(overlay):
    from lyrica import app as A
    overlay.lyrics = Lyrics(
        lines=[(0.0, "I need you all night"), (3.0, "Stay with me")],
        words=[[(0.0, 0.4, "I"), (0.4, 0.9, "need"), (0.9, 1.6, "you"),
                (1.6, 2.0, "all"), (2.0, 2.6, "night")],
               [(3.0, 3.4, "Stay"), (3.4, 3.8, "with"),
                (3.8, 4.2, "me")]],
        synced=True, backing=["(You)", "(Yeah)"],
        backing_words=[[(1.0, 1.7, "(You)")], [(3.1, 3.7, "(Yeah)")]],
        voices=["v1", "v2"], singers={"v1": "person", "v2": "person"})
    overlay._lyrics_state = A.LYRICS_PRESENT
    overlay._go_to_line(0, overlay.lyrics)
    overlay.root.update()
    return overlay


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


def test_a_sequential_parenthetical_suffix_is_visible_on_its_own_timing(panel):
    # The parser may identify this as a backing vocal even though it starts
    # after the lead has ended. Its clock, not overlap with the lead, is what
    # has to bring it on screen.
    panel.lyrics = Lyrics(
        lines=[(0.0, "Lead"), (2.0, "Next")],
        words=[[(0.0, 0.8, "Lead")], [(2.0, 2.5, "Next")]],
        synced=True, backing=["(yeah)", ""],
        backing_words=[[(0.8, 1.3, "(yeah)")], []])
    panel._go_to_line(0, panel.lyrics)

    panel._show_backing(panel.lyrics, 1.0)

    assert panel._echo is not None
    assert panel._echo.text == "(yeah)"


def test_an_inferred_short_richsync_adlib_does_not_preview_early_or_flash(panel):
    panel.lyrics = Lyrics(
        lines=[(0.0, "Lead"), (2.0, "Next")],
        words=[[(0.0, 0.8, "Lead")], [(2.0, 2.5, "Next")]],
        synced=True, backing=["(yeah)", ""],
        backing_words=[[(1.0, 1.03, "(yeah)")], []],
        backing_timing=["inferred", ""])
    panel._go_to_line(0, panel.lyrics)

    panel._show_backing(panel.lyrics, 0.85)
    assert panel._echo is None, "inferred timing has no 300 ms TTML preview"

    panel._show_backing(panel.lyrics, 1.02)
    assert panel._echo is not None
    panel._show_backing(panel.lyrics, 1.40)
    assert panel._echo is not None, "the visual dwell is readable despite a 30 ms source window"


def test_a_normal_length_richsync_adlib_keeps_the_original_animation(panel):
    panel.lyrics = Lyrics(
        lines=[(0.0, "Lead"), (2.0, "Next")],
        words=[[(0.0, 0.8, "Lead")], [(2.0, 2.5, "Next")]],
        synced=True, backing=["(yeah)", ""],
        backing_words=[[(1.0, 1.25, "(yeah)")], []],
        backing_timing=["inferred", ""])
    panel._go_to_line(0, panel.lyrics)

    panel._show_backing(panel.lyrics, 0.75)
    assert panel._echo is None, "a 250 ms N95-like suffix may not preview before its offset"
    panel._show_backing(panel.lyrics, 1.0)
    assert panel._echo is not None


def test_a_sequential_richsync_adlib_owns_the_display_until_its_tail_ends():
    from lyrica.app import _display_line_index

    lyrics = Lyrics(
        lines=[(0.0, "Lead"), (1.2, "Next")],
        words=[[(0.0, 0.8, "Lead")], [(1.2, 1.8, "Next")]],
        synced=True, backing=["(echo)", ""],
        backing_words=[[(1.0, 1.45, "(echo)")], []],
        backing_modes=["sequential", ""])

    assert _display_line_index(lyrics, 0.9) == 0
    assert _display_line_index(lyrics, 1.0) == 0
    assert _display_line_index(lyrics, 1.3) == 0
    assert _display_line_index(lyrics, 1.45) == 1




def test_a_long_adlib_stays_on_one_row_inside_the_panel(panel):
    tokens = ["(this"] + ["long" for _ in range(42)] + ["echo)"]
    text = " ".join(tokens)
    words = [(1.0 + i * 0.04, 1.04 + i * 0.04, token)
             for i, token in enumerate(tokens)]
    panel.lyrics = Lyrics(
        lines=[(0.0, "Lead"), (4.0, "Next")],
        words=[[(0.0, 0.8, "Lead")], [(4.0, 4.5, "Next")]],
        synced=True, backing=[text, text], backing_words=[words, words],
        voices=["v1", "v2"], singers={"v1": "person", "v2": "person"})

    panel._go_to_line(0, panel.lyrics)
    panel._show_backing(panel.lyrics, 1.4)
    right = panel._echo
    assert len(right._row_spans) == 1
    right_box = panel.canvas.bbox(right._tag)
    assert 0 <= right_box[0] <= right_box[2] <= panel.width
    assert sum(right._row_spans[0]) / 2 >= panel.width / 2

    panel._clear_backing()
    panel._go_to_line(1, panel.lyrics)
    panel._show_backing(panel.lyrics, 1.4)
    left = panel._echo
    assert len(left._row_spans) == 1
    left_box = panel.canvas.bbox(left._tag)
    assert 0 <= left_box[0] <= left_box[2] <= panel.width
    assert sum(left._row_spans[0]) / 2 <= panel.width / 2


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


def test_it_hangs_from_the_lines_lower_right_corner(panel):
    from lyrica.app import ECHO_CORNER_INSET, ECHO_DROP

    panel._show_backing(panel.lyrics, 1.3)
    line, echo = panel._views[0], panel._echo
    assert echo.y == pytest.approx(line.y + line.line_height * ECHO_DROP, abs=1.0)
    left = min(s for s, _e in echo._row_spans)
    corner = max(e for _s, e in line._row_spans)
    assert left == pytest.approx(
        corner - panel.chrome.px(ECHO_CORNER_INSET), abs=1.5)


def test_it_answers_from_the_lane_opposite_the_lead(panel):
    panel._go_to_line(1, panel.lyrics)
    panel._show_backing(panel.lyrics, 3.3)
    active = panel._views[1]
    echo = panel._echo
    active_centre = sum(active._row_spans[0]) / 2
    echo_centre = sum(echo._row_spans[0]) / 2
    assert active_centre > panel.width / 2
    assert echo_centre < active_centre


def test_left_and_right_adlibs_stay_on_safe_outer_corners(panel):
    from lyrica.app import ECHO_CORNER_INSET, ECHO_SAFE_MARGIN

    inset = panel.chrome.px(ECHO_CORNER_INSET)
    margin = panel.chrome.px(ECHO_SAFE_MARGIN)

    panel._show_backing(panel.lyrics, 1.3)
    right = panel._echo
    active = panel._views[0]
    assert min(a for a, _b in right._row_spans) == pytest.approx(
        max(b for _a, b in active._row_spans) - inset, abs=1.5)
    assert max(b for _a, b in right._row_spans) <= panel.width - margin

    panel._clear_backing()
    panel._go_to_line(1, panel.lyrics)
    panel._show_backing(panel.lyrics, 3.3)
    left = panel._echo
    active = panel._views[1]
    assert max(b for _a, b in left._row_spans) == pytest.approx(
        min(a for a, _b in active._row_spans) + inset, abs=1.5)
    assert min(a for a, _b in left._row_spans) >= margin


def test_an_adlib_eases_into_and_out_of_its_lane(panel):
    from lyrica.app import ECHO_EXIT_LANE, ECHO_FADE_S

    active = panel._views[0]
    origin = sum(active._row_spans[-1]) / 2
    opens = 1.0 - ECHO_FADE_S
    panel._show_backing(panel.lyrics, opens)
    opening = abs(sum(panel._echo._row_spans[0]) / 2 - origin)

    panel._show_backing(panel.lyrics, 1.0)
    settled = abs(sum(panel._echo._row_spans[0]) / 2 - origin)
    assert 0 < opening < settled, "it still jumped directly to the full lane"

    panel._show_backing(panel.lyrics, 1.7 + ECHO_FADE_S)
    leaving = abs(sum(panel._echo._row_spans[0]) / 2 - origin)
    assert settled * (ECHO_EXIT_LANE - 0.05) <= leaving < settled


def test_the_row_gap_contains_the_complete_adlib_and_next_line(panel):
    panel._show_backing(panel.lyrics, 1.3)
    echo_bottom = panel._echo.visual_vertical_span()[1]
    next_top = panel._views[1].visual_vertical_span()[0]
    assert echo_bottom <= next_top, (
        "the ad-lib still shares pixels with the line below")


def test_every_transition_endpoint_keeps_complete_glyphs_on_canvas(panel):
    panel._go_to_line(1, panel.lyrics)
    for index, view in panel._views.items():
        places = (view.y, panel._targets[index])
        original = view.y
        try:
            for y in places:
                view.move_to(y)
                top, bottom = view.visual_vertical_span()
                assert 0 <= top <= bottom <= panel.height
        finally:
            view.move_to(original)


def test_its_own_window_is_what_takes_it_down(panel):
    # Not the line. A line with nothing behind it used to kill it on the spot,
    # which is what cut its fade short; what has to end it is running out of
    # window, and that still has to happen with nothing else changing.
    panel._show_backing(panel.lyrics, 1.3)
    assert panel._echo is not None
    panel.lyrics = Lyrics(lines=[(0.0, "solo")], words=[[(0.0, 1.0, "solo")]],
                          synced=True)
    panel._show_backing(panel.lyrics, 1.3)
    assert panel._echo is not None, "it was still being sung"
    panel._show_backing(panel.lyrics, 9.0)
    assert panel._echo is None, "and it does have to end"


def test_a_resize_takes_it_down_with_the_rest(panel):
    # It is built at one font and scale like every other line, and was the one
    # thing `_apply_scale` did not drop.
    panel._show_backing(panel.lyrics, 1.3)
    assert panel._echo is not None
    panel._apply_scale()
    assert panel._echo is None


def test_it_finishes_leaving_after_the_line_has_moved_on(panel):
    # An ad-lib usually answers the end of a phrase, so the line advances
    # exactly while it is fading. Tying it to the line made it vanish mid-fade.
    from lyrica.app import ECHO_FADE_S
    from lyrica.glass import rgb_of

    panel._show_backing(panel.lyrics, 1.3)
    assert panel._echo is not None
    still = panel._echo

    # The column moves to a line that has nothing behind it.
    panel.lyrics = Lyrics(
        lines=[(0.0, "I need you all night"), (2.6, "on and on")],
        words=[[(0.0, 2.6, "x")], [(2.6, 3.4, "y")]], synced=True,
        backing=["(You)", ""], backing_words=[[(1.0, 1.7, "(You)")], []])
    panel.line_index = 1

    panel._show_backing(panel.lyrics, 1.7 + ECHO_FADE_S * 0.4)
    assert panel._echo is still, "it was killed by a line it does not belong to"
    fading = max(sum(c * w for c, w in zip(rgb_of(x), (0.2126, 0.7152, 0.0722),
                                           strict=True))
                 for x in {panel.canvas.itemcget(e[2], "fill")
                           for e in panel._echo._items})
    assert fading > 0

    panel._show_backing(panel.lyrics, 1.7 + ECHO_FADE_S * 1.5)
    assert panel._echo is None, "and it does have to end"


def test_it_holds_where_it_was_once_its_line_is_gone(panel):
    from lyrica.app import ECHO_FADE_S

    panel._show_backing(panel.lyrics, 1.3)
    where = panel._echo.y
    panel._views.clear()          # the line it answered has scrolled away
    panel._show_backing(panel.lyrics, 1.7 + ECHO_FADE_S * 0.4)
    assert panel._echo is not None and panel._echo.y == where
