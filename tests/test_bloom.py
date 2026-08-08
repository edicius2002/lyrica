"""The light a struck character leaves behind, and that it drains in time."""
import pytest

from lyrica.lineview import LineView
from lyrica.palette import DEFAULT, KEYED

LINE = "porque todavia me acuerdo"


@pytest.fixture
def view(canvas):
    words = [(i * 0.2, i * 0.2 + 0.2, w) for i, w in enumerate(LINE.split())]
    v = LineView(canvas, 300, 40.0, LINE, words, font=("Segoe UI", 20, "bold"),
                 wrap=800, palette=DEFAULT, feather=12.0, bloom=1.0)
    yield v
    v.destroy()


def test_the_halo_is_built_once_when_the_line_becomes_active(view):
    # Measured: making and dropping copies as each character is reached costs
    # 4.8 ms of a 16 ms frame; recolouring ones that exist costs 1.9.
    assert not view._glow
    view.set_active(True)
    assert len(view._glow) == len([c for c in LINE if not c.isspace()])


def test_a_struck_character_stays_lit_after_the_front_has_gone(view):
    # The whole difference from what this replaces. A halo keyed to where the
    # front is travels with it like a carried lamp; one keyed to when the front
    # arrived stays behind, so each word is struck and the light drains away.
    view.set_active(True)
    view.show_sweep(0, 0.9)
    struck = dict(view._hit)
    assert struck, "the front passed characters and none was recorded"
    view.show_sweep(3, 0.9)          # the front is now far to the right
    assert set(struck) <= set(view._hit), "the light went out with the front"


def test_the_light_drains_and_then_stops(view):
    view.set_active(True)
    view.show_sweep(1, 0.5)
    when, span = next(iter(view._hit.values()))
    assert view.advance_bloom(when + span * 0.2) is True
    assert view.advance_bloom(when + span * 0.9) is True
    # A hair past, because `when` is a monotonic clock in the millions and
    # (when + span) - when does not come back as exactly span.
    assert view.advance_bloom(when + span + 1e-3) is False, "it has to end"
    assert not view._hit


def test_a_word_keeps_its_light_for_as_long_as_it_is_sung(view):
    # The sweep crossing a word is on the word's own clock. A constant put the
    # two in disagreement: spent before a long word was half lit, still burning
    # after a short one had gone.
    view.set_active(True)
    view.show_sweep(0, 0.6)
    _when, span = next(iter(view._hit.values()))
    sung_for = view._piece_time[0]
    assert span == pytest.approx(max(0.14, min(1.1, sung_for * view.bloom)))


def test_a_line_that_is_no_longer_active_carries_no_halo(view):
    view.set_active(True)
    view.show_sweep(1, 0.5)
    view.set_active(False)
    assert not view._glow
    assert not view._hit
    assert view.advance_bloom(0.0) is False


def test_turning_the_bloom_off_builds_nothing(canvas):
    words = [(0.0, 0.2, w) for w in LINE.split()]
    v = LineView(canvas, 300, 40.0, LINE, words, font=("Segoe UI", 20, "bold"),
                 wrap=800, palette=DEFAULT, feather=12.0, bloom=0.0)
    v.set_active(True)
    assert not v._glow
    v.show_sweep(1, 0.5)
    assert v.advance_bloom(1.0) is False
    v.destroy()


def test_a_palette_with_no_wash_behind_it_builds_nothing(canvas):
    # Keyed mode stands over arbitrary video; anything behind the text there
    # costs legibility rather than buying it.
    words = [(0.0, 0.2, w) for w in LINE.split()]
    v = LineView(canvas, 300, 40.0, LINE, words, font=("Segoe UI", 20, "bold"),
                 wrap=800, palette=KEYED, feather=12.0, bloom=0.25)
    v.set_active(True)
    assert not v._glow
    v.destroy()


def test_seeking_backwards_re_arms_a_character(view):
    # Otherwise a line jumped back to could never bloom again.
    view.set_active(True)
    view.show_sweep(2, 0.9)
    assert view._hit
    view.show_sweep(0, 0.0)
    assert len(view._hit) < len([c for c in LINE if not c.isspace()])


def test_a_character_is_struck_once_and_not_again(view):
    # The bloom ends while the front stays where it is. Reading "not blooming"
    # as "not yet struck" made every sung letter pulse for ever, over and over,
    # which is what this guards.
    view.set_active(True)
    view.show_sweep(1, 0.5)
    struck = dict(view._hit)
    assert struck

    when = min(w for w, _ in struck.values())
    assert view.advance_bloom(when + 2.0) is False, "the light should be spent"
    assert not view._hit

    # The front moves on, as it does on every following frame.
    for word in (2, 3):
        view.show_sweep(word, 0.5)
    assert not (set(struck) & set(view._hit)), \
        "a character that already had its bloom was struck a second time"


def test_the_strike_comes_back_after_seeking_behind_it(view):
    view.set_active(True)
    view.show_sweep(2, 0.9)
    first = dict(view._hit)
    assert first
    view.advance_bloom(min(w for w, _ in first.values()) + 2.0)
    view.show_sweep(0, 0.0)          # back to the start of the line
    view.show_sweep(2, 0.9)
    assert set(view._hit) & set(first), "seeking back has to re-arm the strike"


def test_a_whole_word_is_struck_at_once(view):
    # Lighting letters one at a time made the line ripple; what this imitates
    # strikes the word and lets it ring.
    view.set_active(True)
    view.show_sweep(0, 0.6)
    assert view._hit
    when = {w for w, _ in view._hit.values()}
    assert len(when) == 1, "the letters of a word were struck at different times"
    first_word = view._piece_chars[0]
    assert set(view._hit) == set(first_word), \
        "the strike did not cover exactly one word"


def test_the_next_word_gets_its_own_strike(view):
    view.set_active(True)
    view.show_sweep(0, 0.6)
    first = dict(view._hit)
    view.show_sweep(1, 0.6)
    added = set(view._hit) - set(first)
    assert added == set(view._piece_chars[1])
    assert len({view._hit[i][0] for i in added}) == 1


def test_the_words_partition_the_characters(view):
    covered = sorted(i for chars in view._piece_chars.values() for i in chars)
    assert covered == list(range(len(view._items)))


def test_the_letters_themselves_rise_and_settle(view):
    # A halo behind text that is not reacting reads as an effect laid over the
    # words. There is no room to brighten them instead — the sung colour is
    # already at 253 of 255 — so they move.
    view.set_active(True)
    view.show_sweep(0, 0.6)
    when = min(w for w, _ in view._hit.values())

    span = next(iter(view._hit.values()))[1]
    from lyrica.lineview import LIFT_ATTACK

    view.advance_bloom(when)
    assert not any(view._lift.values()), "the rise has to take time, not a frame"

    view.advance_bloom(when + span * LIFT_ATTACK)
    raised = dict(view._lift)
    assert max(raised.values()) > 0, "the word did not rise"

    view.advance_bloom(when + span * 0.6)
    assert 0 < max(view._lift.values()) < max(raised.values()), "it did not settle"

    view.advance_bloom(when + span + 1e-3)
    assert not any(view._lift.values()), "it never came back down"


def test_a_line_that_stops_being_active_puts_its_letters_back(view):
    view.set_active(True)
    view.show_sweep(0, 0.6)
    when, span = next(iter(view._hit.values()))
    from lyrica.lineview import LIFT_ATTACK
    view.advance_bloom(when + span * LIFT_ATTACK)
    assert any(view._lift.values())
    view.set_active(False)
    assert not any(view._lift.values())


def test_the_halo_is_the_same_size_and_sits_on_the_same_baseline():
    # It did neither. Tk reports `actual()["size"]` in points where the font was
    # asked for in pixels, so the halo was drawn at 22 where the glyph was 30
    # and from a different vertical origin — measured, the light sat 13 px above
    # the letter it belonged to, which is exactly what it looked like.
    from tkinter import font as tkfont

    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", -30, "bold")
    pil = bloom_mod._pil_font(spec)
    if pil is None:
        import pytest as _pytest
        _pytest.skip("no TrueType file for this font on this machine")
    tk_font = tkfont.Font(font=spec)
    assert pil.getmetrics()[0] == tk_font.metrics("ascent"), "baselines disagree"
    width_tk = tk_font.measure("acuerdo")
    assert abs(pil.getlength("acuerdo") - width_tk) / width_tk < 0.05


def test_the_rise_is_shaped_rather_than_linear():
    # Three designed pixels came to 3.4 real ones on the machine this was tuned
    # on, so a linear fall over eighteen frames visited four positions and read
    # as a staircase.
    from lyrica.lineview import LIFT_ATTACK, _lift_shape

    assert _lift_shape(0.0) == 0.0
    assert _lift_shape(LIFT_ATTACK) == pytest.approx(1.0, abs=0.02)
    assert _lift_shape(1.0) == pytest.approx(0.0, abs=0.02)
    # Rising fast and leaving slowly, which being struck and relaxing are.
    assert _lift_shape(LIFT_ATTACK * 0.5) > 0.5
    middle = (1.0 + LIFT_ATTACK) / 2
    assert 0.3 < _lift_shape(middle) < 0.7
