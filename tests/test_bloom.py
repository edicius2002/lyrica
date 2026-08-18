"""The light a struck character leaves behind, and that it drains in time."""
import pytest

from lyrica.glass import rgb_of
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


def test_every_visible_halo_pixel_keeps_the_requested_colour():
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", -30, "bold")
    font = bloom_mod._pil_font(spec)
    if font is None:
        pytest.skip("no TrueType file for this font on this machine")

    colour = (255, 255, 255)
    image = bloom_mod._rendered_halo("W", font, bloom_mod.LEVELS, colour)
    colours = image.getcolors(maxcolors=image.width * image.height)
    pixels = [pixel for _count, pixel in colours]
    partial = [pixel for pixel in pixels if 0 < pixel[3] < 255]

    assert partial, "the test glyph produced no blurred edge"
    assert all(pixel[:3] == colour for pixel in pixels if pixel[3] > 0), \
        "the halo mixed its visible edge with transparent black"


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


def test_a_words_letters_never_show_two_sizes_in_one_frame(view):
    """The tremor itself, on the cache state that produced it.

    The per-frame image budget used to be spent a letter at a time, and a letter
    that found it spent kept the size it had. Measured on a cold cache, the eight
    letters of one word showed steps 1, 2, 4 and 7 in the same frame — a tenth of
    the whole growth in spread — and which letters led changed every frame, so
    the word deformed differently each time instead of swelling. A word is one
    gesture: it takes one size per frame or it keeps the one it has.
    """
    from lyrica import bloom as bloom_mod
    from lyrica.lineview import GROW_SPAN_S

    view.set_active(True)
    if not view._blurred:
        pytest.skip("no resampled growth on this machine")
    bloom_mod._cache.clear()            # the first play of this line
    view.show_sweep(0, 0.6)
    when, _span = next(iter(view._hit.values()))

    torn = []
    frames = int(GROW_SPAN_S * 60) + 4
    for frame in range(frames):
        view.advance_bloom(when + frame / 60.0)
        for piece, chars in view._piece_chars.items():
            shown = [i for i in chars if i in view._showing]
            steps = {view._showing[i][0] for i in shown}
            if len(steps) > 1 or (shown and len(shown) != len(chars)):
                torn.append({"frame": frame, "word": piece,
                             "steps": sorted(steps),
                             "grown": len(shown), "letters": len(chars)})
    assert not torn, f"a word wore more than one size in a frame: {torn}"


def test_one_frame_builds_one_word_whole_and_no_more(view):
    """A word is built entire or not at all, and only one word starts a frame.

    Measured at 0.329 ms an image — not the 0.7 ms an earlier estimate assumed —
    so one step of a twelve-letter word is 3.95 ms of a 16 ms frame and finishes
    inside one. What has to stay bounded is therefore how many *words* begin in a
    frame, because letting a word stop half way is the tear above.
    """
    import time

    from lyrica import bloom as bloom_mod
    from lyrica.lineview import GROW_ATTACK_S

    view.set_active(True)
    if not view._blurred:
        pytest.skip("no blurred glyphs on this machine")
    bloom_mod._cache.clear()
    now = time.monotonic()
    # Two neighbouring words struck together, which is what a fast phrase does.
    for piece in (0, 1):
        for index in view._piece_chars[piece]:
            view._hit[index] = (now, 0.6)
    view.advance_bloom(now + GROW_ATTACK_S)

    pieces = {view._char_piece[i] for i in view._showing}
    assert len(pieces) == 1, "two words built their sizes in the same frame"
    piece = next(iter(pieces))
    chars = view._piece_chars[piece]
    assert len(view._showing) == len(chars), "the word was left part grown"
    built = [k for k in bloom_mod._cache if k[0] == "grown"]
    assert len(built) == len(chars), (
        f"{len(built)} sizes built for a word of {len(chars)} letters")

    # And the word that waited gets its turn on the next frame.
    view.advance_bloom(now + GROW_ATTACK_S + 1 / 60.0)
    assert len({view._char_piece[i] for i in view._showing}) == 2


def test_the_halo_gets_on_with_it_whatever_the_growth_is_doing(canvas):
    """They shared one pool and the growth was served first.

    Measured over twelve cold frames, the halo received new images in six of
    them, in bursts of three and of one: a decay that is continuous in time was
    shown in irregular jumps, which is the pulsing that appeared behind the
    illumination.

    So the property is not that the halo gets *some* allowance — it sometimes did
    — but that what it gets does not depend on how hungry the growth is. Two
    identical frames are run, one with the sizes still to build and one with them
    already built, and the halo has to make the same progress in both.
    """
    from lyrica import bloom as bloom_mod
    from lyrica.lineview import GROW_ATTACK_S

    def halos_built_in_one_frame(warm_the_growth):
        bloom_mod._cache.clear()
        words = [(i * 0.2, i * 0.2 + 0.2, w) for i, w in enumerate(LINE.split())]
        v = LineView(canvas, 300, 40.0, LINE, words,
                     font=("Segoe UI", 20, "bold"), wrap=800, palette=DEFAULT,
                     feather=12.0, bloom=1.0)
        v.set_active(True)
        if not v._blurred or not v._glow:
            v.destroy()
            pytest.skip("no blurred glyphs on this machine")
        v.show_sweep(0, 0.6)
        when, _span = next(iter(v._hit.values()))
        if warm_the_growth:
            # Every size this frame will ask for, already in hand.
            for index in v._piece_chars[0]:
                char = canvas.itemcget(v._items[index][2], "text")
                for step in range(1, bloom_mod.SCALES + 1):
                    for shade in (DEFAULT.sung, DEFAULT.unsung):
                        bloom_mod.grown(char, v._font, step, rgb_of(shade),
                                        v.growth)
        before = sum(1 for k in bloom_mod._cache if k[0] != "grown")
        v.advance_bloom(when + GROW_ATTACK_S)
        after = sum(1 for k in bloom_mod._cache if k[0] != "grown")
        v.destroy()
        return after - before

    hungry = halos_built_in_one_frame(warm_the_growth=False)
    fed = halos_built_in_one_frame(warm_the_growth=True)
    assert hungry == fed, (
        f"the halo built {hungry} levels while the growth was building and "
        f"{fed} when it had nothing to build, so they share one allowance")
    assert fed > 0, "the halo built nothing at all"


def test_a_halo_travels_with_the_letter_it_belongs_to(view):
    """It used to hold still while its letter moved out from under it.

    A word swells about its own centre, so its outer letters travel as well as
    thicken — up to twelve pixels at the designed size. The halo kept its resting
    place, so the light visibly slid behind the glyph and back again.
    """
    from lyrica import bloom as bloom_mod
    from lyrica.lineview import GROW_ATTACK_S, GROW_SPAN_S

    view.set_active(True)
    if not view._blurred or not view._glow:
        pytest.skip("no blurred halo on this machine")
    view.show_sweep(0, 0.6)
    when, span = next(iter(view._hit.values()))
    chars = view._piece_chars[0]
    outer = chars[-1]

    def offset():
        glow = view.canvas.coords(view._glow[outer][0])[0]
        text = view.canvas.coords(view._items[outer][2])[0]
        return glow - text + bloom_mod.PAD

    assert offset() == pytest.approx(0.0), "the halo starts out of place"

    for _ in range(4):                  # warm, then read the grown frame
        view.advance_bloom(when + GROW_ATTACK_S)
    step, _colour = view._showing[outer]
    scale = 1.0 + view.growth * step / bloom_mod.SCALES
    want = view._group_dx(outer, scale)
    assert abs(want) > 1.0, "this letter does not travel; pick an outer one"
    assert offset() == pytest.approx(want), (
        "the halo stayed behind while its letter moved")

    view.advance_bloom(when + max(span, GROW_SPAN_S) + 1e-3)
    assert offset() == pytest.approx(0.0), "the halo did not come back"


def test_the_same_size_always_lands_on_the_same_pixels(view):
    """A word's centre may not wander between two showings of one step.

    The offsets are derived from the resting geometry and the scale every time
    rather than carried forward, so reaching step five on the way up and again on
    the way down has to put the ink in the same place. Anything accumulated shows
    as a word that walks while it breathes. Measured across a whole line, the
    wander at a fixed step is 0.0000 px and the width change 0.0000 px.
    """
    from lyrica import bloom as bloom_mod
    from lyrica.lineview import GROW_SPAN_S

    view.set_active(True)
    if not view._blurred:
        pytest.skip("no resampled growth on this machine")
    view.show_sweep(0, 0.6)
    when, _span = next(iter(view._hit.values()))
    chars = view._piece_chars[0]

    seen: dict = {}
    repeats = 0
    for frame in range(int(GROW_SPAN_S * 60) + 2):
        view.advance_bloom(when + frame / 60.0)
        shown = [i for i in chars if i in view._showing]
        if len(shown) != len(chars):
            continue
        steps = {view._showing[i][0] for i in shown}
        if len(steps) != 1:
            continue
        step = next(iter(steps))
        # Where the ink sits, held against the place the row has given the word.
        where = tuple(round(view.canvas.coords(view._grown[i])[0]
                            - view._piece_layout_shift[0], 6) for i in chars)
        if step in seen:
            repeats += 1
            assert seen[step] == where, (
                f"step {step} landed somewhere else the second time")
        seen[step] = where
    assert repeats, "no step was reached twice, so nothing was compared"
    assert len(seen) > 1, "the word never changed size"
    assert bloom_mod.SCALES in seen, "the word never reached full growth"


def test_a_line_dropped_mid_strike_returns_its_row_to_rest(view):
    """Letting go of the letters is only half of letting go of the growth.

    A word's neighbours stand aside while it expands. Retiring the stand-ins
    without releasing that expansion left them aside for an expansion that was
    no longer on screen, for as long as the line lived.
    """
    from lyrica.lineview import GROW_ATTACK_S

    view.set_active(True)
    if not view._blurred:
        pytest.skip("no resampled growth on this machine")
    rest = {piece: view.canvas.coords(view._items[chars[0]][2])[0]
            for piece, chars in view._piece_chars.items()}

    view.show_sweep(0, 0.6)
    when, _span = next(iter(view._hit.values()))
    for _ in range(3):
        view.advance_bloom(when + GROW_ATTACK_S)
    assert view._piece_growth, "nothing expanded, so nothing to let go of"
    assert any(abs(s) > 1e-6 for s in view._piece_layout_shift.values())

    view.set_active(False)

    assert not view._piece_growth
    assert all(shift == pytest.approx(0.0)
               for shift in view._piece_layout_shift.values())
    now = {piece: view.canvas.coords(view._items[chars[0]][2])[0]
           for piece, chars in view._piece_chars.items()}
    assert now == pytest.approx(rest), "the row kept standing aside"


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


def test_a_fully_faded_line_is_hidden_instead_of_painted_black(view):
    view.set_visible(False)
    view.show_inactive(view.palette.bloom(0.0))

    assert all(view.canvas.itemcget(item, "state") == "hidden"
               for item in view.item_ids())

    view.set_visible(True)
    assert all(view.canvas.itemcget(entry[2], "state") == "normal"
               for entry in view._items)


# --- what is kept, and what is let go ---------------------------------------

def _fill_one_song(bloom_mod, spec, colours, chars):
    """Every size a song of these characters and colours ever asks for."""
    for colour in colours:
        for char in chars:
            for step in range(1, bloom_mod.SCALES + 1):
                bloom_mod.grown(char, spec, step, colour, bloom_mod.GROWTH)


def test_the_kept_images_stay_within_the_handles_a_process_gets(canvas):
    """The cache is keyed by palette colour, and the palette follows the song.

    Every image in it is a Windows GDI bitmap once drawn — measured at one
    handle and 29.6 KiB each — and a process is given 10,000 handles. Unbounded,
    the cache reached the quota after about fifteen songs and Tk aborted the
    whole process from `Tk_GetPixmap` with "Fail to allocate bitmap". That is a
    `Tcl_Panic`, so there is no exception to catch and no chance to log: the only
    defence is never to hold that many.
    """
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", 20, "bold")
    if not bloom_mod.available(spec):
        pytest.skip("no TrueType file for this font on this machine")
    bloom_mod._cache.clear()
    chars = "abcdefghijklmnopqrstuvwxyz"
    per_song = len(chars) * bloom_mod.SCALES * 2
    for song in range(bloom_mod.LIMIT // per_song + 3):
        _fill_one_song(bloom_mod, spec, ((200, song * 7 % 256, 120),
                                         (90, 110, song * 11 % 256)), chars)
    assert len(bloom_mod._cache) <= bloom_mod.LIMIT
    assert bloom_mod._cache.total_bytes <= bloom_mod.BYTE_LIMIT


def test_a_song_keeps_every_size_it_built_while_it_is_playing(canvas):
    """The bound has to be above one song's whole appetite, not near it.

    Evicting inside a song would drop images a canvas item is still showing —
    Tk keeps only a weak claim on an image, so the letter would go blank — and
    would rebuild them at 0.33 ms each for the rest of the song. A Latin lyric
    has about fifty distinct characters, which is two colours by fifty by
    `SCALES`, so the whole of one is held with room to spare.
    """
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", 20, "bold")
    if not bloom_mod.available(spec):
        pytest.skip("no TrueType file for this font on this machine")
    bloom_mod._cache.clear()
    chars = "abcdefghijklmnopqrstuvwxyzáéíóúñABCDEFGHIJKLMNOPQ ,.'!¿?"
    colours = ((240, 236, 228), (150, 150, 160))
    _fill_one_song(bloom_mod, spec, colours, chars)
    missing = [(char, step, colour)
               for colour in colours for char in chars
               for step in range(1, bloom_mod.SCALES + 1)
               if not bloom_mod.ready(char, spec, step, colour,
                                      bloom_mod.GROWTH)]
    assert not missing, f"{len(missing)} sizes were evicted mid-song"


def test_the_oldest_song_goes_first_and_the_newest_stays(canvas):
    """Least recently used, because what is on screen is what was just used."""
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", 20, "bold")
    if not bloom_mod.available(spec):
        pytest.skip("no TrueType file for this font on this machine")
    bloom_mod._cache.clear()
    chars = "abcdefghijklmnopqrstuvwxyz"
    per_song = len(chars) * bloom_mod.SCALES
    songs = [((10 + song, 20, 30),) for song in
             range(bloom_mod.LIMIT // per_song + 2)]
    for colour in songs:
        _fill_one_song(bloom_mod, spec, colour, chars)
    newest = songs[-1][0]
    assert all(bloom_mod.ready(char, spec, step, newest, bloom_mod.GROWTH)
               for char in chars for step in range(1, bloom_mod.SCALES + 1)), \
        "the song that is playing lost its sizes"
    oldest = songs[0][0]
    assert not bloom_mod.ready("a", spec, 1, oldest, bloom_mod.GROWTH), \
        "the first song's sizes were still held after the cache overflowed"


def test_an_evicted_size_can_be_built_again(canvas):
    """Eviction drops the image, not the ability to make it."""
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", 20, "bold")
    if not bloom_mod.available(spec):
        pytest.skip("no TrueType file for this font on this machine")
    bloom_mod._cache.clear()
    chars = "abcdefghijklmnopqrstuvwxyz"
    first = (10, 20, 30)
    _fill_one_song(bloom_mod, spec, (first,), chars)
    per_song = len(chars) * bloom_mod.SCALES
    for song in range(bloom_mod.LIMIT // per_song + 2):
        _fill_one_song(bloom_mod, spec, ((100 + song, 40, 50),), chars)
    assert not bloom_mod.ready("a", spec, 1, first, bloom_mod.GROWTH)
    again = bloom_mod.grown("a", spec, 1, first, bloom_mod.GROWTH)
    assert again is not None and again[0].width() > 0


def test_clearing_the_cache_forgets_what_it_was_holding(canvas):
    """`total_bytes` and the keys are one fact; the fixtures clear the keys."""
    from lyrica import bloom as bloom_mod

    spec = ("Segoe UI", 20, "bold")
    if not bloom_mod.available(spec):
        pytest.skip("no TrueType file for this font on this machine")
    _fill_one_song(bloom_mod, spec, ((70, 80, 90),), "abc")
    assert bloom_mod._cache.total_bytes > 0
    bloom_mod._cache.clear()
    assert len(bloom_mod._cache) == 0
    assert bloom_mod._cache.total_bytes == 0
