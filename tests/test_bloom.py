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
    from lyrica.lineview import GROW_SPAN_S

    assert view.advance_bloom(when + max(span, GROW_SPAN_S) + 1e-3) is False, \
        "both the light and the independent movement have to end"
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


def test_turning_the_bloom_off_keeps_the_growth(canvas):
    words = [(0.0, 0.2, w) for w in LINE.split()]
    v = LineView(canvas, 300, 40.0, LINE, words, font=("Segoe UI", 20, "bold"),
                 wrap=800, palette=DEFAULT, feather=12.0, bloom=0.0)
    v.set_active(True)
    assert not v._glow
    v.show_sweep(1, 0.5)
    when, _span = next(iter(v._hit.values()))
    assert v.advance_bloom(when + 0.1) is True
    v.destroy()


def test_turning_both_effects_off_builds_nothing(canvas):
    words = [(0.0, 0.2, w) for w in LINE.split()]
    v = LineView(canvas, 300, 40.0, LINE, words,
                 font=("Segoe UI", 20, "bold"), wrap=800, palette=DEFAULT,
                 feather=12.0, bloom=0.0, growth=0.0)
    v.set_active(True)
    assert not v._glow and not v._grown
    v.show_sweep(1, 0.5)
    assert not v._hit
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
    if v._blurred:
        assert v._grown, "keyed mode should remove the halo, not the movement"
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


def test_the_strike_is_shaped_rather_than_linear():
    # Three designed pixels came to 3.4 real ones on the machine this was tuned
    # on, so a linear fall over eighteen frames visited four positions and read
    # as a staircase.
    from lyrica.lineview import STRIKE_ATTACK, _strike_shape

    assert _strike_shape(0.0) == 0.0
    assert _strike_shape(STRIKE_ATTACK) == pytest.approx(1.0, abs=0.02)
    assert _strike_shape(1.0) == pytest.approx(0.0, abs=0.02)
    # Rising fast and leaving slowly, which being struck and relaxing are.
    assert _strike_shape(STRIKE_ATTACK * 0.5) > 0.5
    middle = (1.0 + STRIKE_ATTACK) / 2
    assert 0.3 < _strike_shape(middle) < 0.7


# --- the word grows ----------------------------------------------------------

def test_a_letter_at_rest_is_drawn_as_text_not_as_an_image():
    # Step zero is what an ordinary text item already draws, so nothing is made
    # for it and the ramp keeps its full sixty-four colours.
    from lyrica import bloom as bloom_mod

    assert bloom_mod.grown("a", ("Segoe UI", -30, "bold"), 0, (255, 255, 255)) is None


def test_a_growing_letter_swells_about_its_own_centre():
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", -30, "bold")
    if not bloom_mod.available(spec):
        pytest.skip("no TrueType file for this font on this machine")
    small, _, _ = bloom_mod.grown("a", spec, 1, (255, 255, 255))
    big, _, _ = bloom_mod.grown("a", spec, bloom_mod.SCALES, (255, 255, 255))
    assert big.width() > small.width() and big.height() > small.height()
    # And the letter's centre must not move at all, which is what growing about
    # its own centre means. The first attempt used the padded width where the
    # letter's was wanted and did not resample the padding, which put every
    # growing word twelve pixels down and to the right instead of swelling.
    font = bloom_mod._pil_font(spec)
    width = max(1, int(font.getlength("a")))
    height = sum(font.getmetrics())
    centres = {(round(width / 2, 3), round(height / 2, 3))}    # the letter at rest
    for step in range(1, bloom_mod.SCALES + 1):
        image, dx, dy = bloom_mod.grown("a", spec, step, (255, 255, 255))
        # Derived from the size the image actually came out, which is whole
        # pixels: asking the fraction instead leaves placement and picture
        # disagreeing by half a pixel and the letter trembles as it grows.
        got_x = image.width() / (width + bloom_mod.PAD * 2)
        got_y = image.height() / (height + bloom_mod.PAD * 2)
        centres.add((round(dx + bloom_mod.PAD * got_x + width * got_x / 2, 3),
                     round(dy + bloom_mod.PAD * got_y + height * got_y / 2, 3)))
    assert len(centres) == 1, f"the letter drifted: {sorted(centres)}"


def test_the_visual_padding_contains_every_growth_percentage(view):
    """No intermediate resampled size may put ink beyond the reserved box."""
    from lyrica import bloom as bloom_mod

    font = bloom_mod._pil_font(view._font)
    if font is None:
        pytest.skip("no resampled growth on this machine")
    char = "g"
    height = sum(font.getmetrics())
    pad = view.effect_padding
    for step in range(1, bloom_mod.SCALES + 1):
        image, _dx, dy = bloom_mod.grown(
            char, view._font, step, (255, 255, 255), view.growth)
        got_y = image.height() / (height + bloom_mod.PAD * 2)
        ink_top = dy + bloom_mod.PAD * got_y
        ink_bottom = ink_top + height * got_y
        assert ink_top >= -pad
        assert ink_bottom <= view.line_height + pad


def _distinct_steps(bloom_mod, spec, char, growth):
    """How many of the growth's steps render to a size of their own."""
    was, bloom_mod.GROWTH = bloom_mod.GROWTH, growth
    bloom_mod._cache.clear()
    try:
        sizes = set()
        for step in range(1, bloom_mod.SCALES + 1):
            image, _dx, _dy = bloom_mod.grown(char, spec, step, (255, 255, 255))
            sizes.add((image.width(), image.height()))
        return len(sizes)
    finally:
        bloom_mod.GROWTH, _ = was, bloom_mod._cache.clear()


def test_the_growth_is_above_the_floor_where_its_frames_repeat():
    # Not a matter of taste. At 6 % a narrow letter gained nine tenths of a
    # pixel across the whole growth and four of the nine steps rendered to the
    # same whole-pixel size as their neighbour, so a third of the frames showed
    # an identical picture — a stutter rather than a growth.
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", -30, "bold")
    if not bloom_mod.available(spec):
        pytest.skip("no TrueType file for this font on this machine")
    # Only the default is asserted. Where the floor lands depends on the font's
    # own pixel metrics, which differ between this machine and the runner — the
    # 6 % measurement is recorded in the history rather than checked here.
    steps = bloom_mod.SCALES
    for char in ("a", "m"):
        got = _distinct_steps(bloom_mod, spec, char, bloom_mod.GROWTH)
        assert got >= steps - 1, (
            f"{char!r} repeats {steps - got} of {steps} steps at the default")


def test_the_letter_is_swapped_for_its_stand_in_and_back(view):
    view.set_active(True)
    if not view._blurred:
        pytest.skip("no blurred glyphs on this machine")
    view.show_sweep(0, 0.6)
    when, span = next(iter(view._hit.values()))

    from lyrica.lineview import GROW_ATTACK_S, GROW_SPAN_S
    view.advance_bloom(when + GROW_ATTACK_S)
    assert view._showing, "nothing grew"
    index = next(iter(view._showing))
    assert view.canvas.itemcget(view._items[index][2], "state") == "hidden"

    view.advance_bloom(when + max(span, GROW_SPAN_S) + 1e-3)
    assert not view._showing, "the stand-ins never went away"
    assert view.canvas.itemcget(view._items[index][2], "state") == "normal"


def test_a_word_expands_as_one_group(view):
    """Outer letters travel apart instead of only thickening in place."""
    from lyrica import bloom as bloom_mod
    from lyrica.lineview import GROW_ATTACK_S

    view.set_active(True)
    if not view._blurred:
        pytest.skip("no resampled growth on this machine")
    view.show_sweep(0, 0.6)
    when, _span = next(iter(view._hit.values()))
    for _ in range(4):
        view.advance_bloom(when + GROW_ATTACK_S)

    chars = view._piece_chars[0]
    offsets = []
    for index in chars:
        step, colour = view._showing[index]
        char = view.canvas.itemcget(view._items[index][2], "text")
        _image, dx, _dy = bloom_mod.grown(
            char, view._font, step, colour, view.growth)
        grown_x = view.canvas.coords(view._grown[index])[0]
        text_x = view.canvas.coords(view._items[index][2])[0]
        offsets.append(grown_x - text_x - dx)
    assert offsets[0] < 0 < offsets[-1], offsets


def test_adjacent_words_breathe_with_the_expansion_and_return(view):
    """Growth uses surrounding space without consuming the original gap."""
    piece = 1
    before = {
        p: view.canvas.coords(view._items[chars[0]][2])[0]
        for p, chars in view._piece_chars.items()
    }

    view._set_piece_growth(piece, 1.0)

    assert view._piece_layout_shift[0] < 0
    assert view._piece_layout_shift[piece] == pytest.approx(0.0)
    assert view._piece_layout_shift[2] > 0
    assert (view._piece_layout_shift[2] - view._piece_layout_shift[piece]
            == pytest.approx(view._piece_widths[piece] * view.growth / 2))

    view._set_piece_growth(piece, 0.0)
    assert not view._piece_growth
    assert all(shift == pytest.approx(0.0)
               for shift in view._piece_layout_shift.values())
    after = {
        p: view.canvas.coords(view._items[chars[0]][2])[0]
        for p, chars in view._piece_chars.items()
    }
    assert after == pytest.approx(before), "the resting gaps accumulated drift"


def test_only_so_many_new_sizes_are_built_in_one_frame(view):
    # Each is about 0.7 ms against a 16 ms budget, and a word reaching for a new
    # size every frame overran on its first play: measured at 24 ms.
    from lyrica import bloom as bloom_mod
    from lyrica.lineview import GROW_ATTACK_S, NEW_SIZES_PER_FRAME

    view.set_active(True)
    if not view._blurred:
        pytest.skip("no blurred glyphs on this machine")
    bloom_mod._cache.clear()
    view.show_sweep(0, 0.6)
    when, _span = next(iter(view._hit.values()))
    view.advance_bloom(when + GROW_ATTACK_S)
    assert len(bloom_mod._cache) <= NEW_SIZES_PER_FRAME


def test_a_line_no_longer_active_leaves_no_stand_ins(view):
    view.set_active(True)
    if not view._blurred:
        pytest.skip("no blurred glyphs on this machine")
    view.show_sweep(0, 0.6)
    when, _span = next(iter(view._hit.values()))
    from lyrica.lineview import GROW_ATTACK_S
    view.advance_bloom(when + GROW_ATTACK_S)
    view.set_active(False)
    assert not view._showing
    # Tk answers "" for a state never set, which is what an untouched letter has.
    assert all(view.canvas.itemcget(e[2], "state") in ("", "normal")
               for e in view._items)
