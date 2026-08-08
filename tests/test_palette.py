"""The colour ladder, the sweep rule, and the edge fade (offline)."""
import itertools

import pytest

from lyrica import palette as pal_mod
from lyrica.chrome import KEYED_COMPOSITION, Chrome, ChromeMode
from lyrica.glass import ACRYLIC, PANEL, delta_e, luminance, rgb_of
from lyrica.palette import DEFAULT, KEYED, SWEEP_DE, UNSUNG_CEILING, WORST_DESKTOP
from lyrica.songcolour import NEUTRAL, SongColour

# The two translucent modes. Every rule below has to hold under both, because
# which one the window achieved is decided at startup by what the platform
# allows, not by anything the palette knows.
PANEL_CHROME = Chrome(ChromeMode.PANEL, "#000000", PANEL)
FROSTED_CHROME = Chrome(ChromeMode.FROSTED, "#000000", ACRYLIC)
KEYED_CHROME = Chrome(ChromeMode.KEYED, "#010203", KEYED_COMPOSITION)
LAWS = (PANEL_CHROME, FROSTED_CHROME)

# A cover with a hue worth using, and one without.
TEAL = SongColour(hue=182.0, sat=0.62, weight=0.44, accent_hue=30.0,
                  neutral=False, dominant=(20, 120, 130))
CRIMSON = SongColour(hue=352.0, sat=0.71, weight=0.38, accent_hue=210.0,
                     neutral=False, dominant=(160, 30, 50))
INDIGO = SongColour(hue=248.0, sat=0.55, weight=0.30, accent_hue=60.0,
                    neutral=False, dominant=(50, 40, 140))

COLOURED = (TEAL, CRIMSON, INDIGO)
ROLES = pal_mod.ROLES


def lum(colour: str) -> float:
    return luminance(rgb_of(colour))


def chroma(colour: str) -> int:
    c = rgb_of(colour)
    return max(c) - min(c)


# --- the ladder -------------------------------------------------------------

def test_lines_dim_the_further_they_sit_from_the_active_one():
    assert lum(DEFAULT.by_distance(2)) < lum(DEFAULT.by_distance(1)), \
        "an outer line must be fainter, or entry cannot fade"


def test_the_sung_ramp_runs_from_unsung_to_sung():
    assert DEFAULT.at(0.0) == DEFAULT.unsung
    assert DEFAULT.at(1.0) == DEFAULT.sung


def test_the_ramp_clamps_outside_its_range():
    assert DEFAULT.at(-5.0) == DEFAULT.unsung
    assert DEFAULT.at(5.0) == DEFAULT.sung


def test_the_ramp_never_darkens_as_it_advances():
    levels = [lum(DEFAULT.at(i / 32)) for i in range(33)]
    assert all(b >= a for a, b in itertools.pairwise(levels))


@pytest.mark.parametrize("chrome", LAWS)
def test_the_active_line_outshines_its_neighbours(chrome):
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(chrome, song)
        assert lum(p.unsung) > lum(p.side) > lum(p.far)


# --- the sweep survives the worst desktop -----------------------------------

@pytest.mark.parametrize("chrome", LAWS)
def test_the_sung_word_stays_distinguishable_over_a_white_desktop(chrome):
    # The failure a greyscale palette hides: over a bright desktop the acrylic
    # plate adds ~117 to everything, so 255 and 138 both clamp to white and the
    # sweep disappears. Measured at dE 3.4 before this rule existed.
    law = chrome.composition
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(chrome, song)
        got = delta_e(law.compose(WORST_DESKTOP, rgb_of(p.sung)),
                      law.compose(WORST_DESKTOP, rgb_of(p.unsung)))
        assert got >= SWEEP_DE - 0.5, f"sweep vanishes at {got:.1f} for hue {p.hue:.0f}"


def test_a_panel_that_does_not_clamp_pays_nothing_for_its_sweep():
    # The whole reason the rule is solved against the composition rather than
    # assumed: acrylic has to compress the ladder to keep the sung word
    # distinct, and a blended panel does not.
    panel = pal_mod.for_song(PANEL_CHROME, NEUTRAL)
    frosted = pal_mod.for_song(FROSTED_CHROME, NEUTRAL)
    # The panel takes the ladder's whole ceiling; acrylic has to give some back.
    assert max(rgb_of(panel.unsung)) == UNSUNG_CEILING
    assert max(rgb_of(frosted.unsung)) < UNSUNG_CEILING
    assert frosted.compression < panel.compression
    assert panel.sweep_de > frosted.sweep_de


@pytest.mark.parametrize("chrome", LAWS)
def test_no_lyric_role_but_the_sung_word_exceeds_the_ladder_ceiling(chrome):
    # Two separate caps that were the same number under acrylic: the
    # composition's clamp ceiling, and the ladder's own. The sung word is the
    # one role whose job is light rather than colour.
    for song in COLOURED:
        p = pal_mod.for_song(chrome, song)
        for role in ROLES:
            if role == "sung" or role in pal_mod.CARD_ROLES:
                continue
            assert max(rgb_of(getattr(p, role))) <= UNSUNG_CEILING + 1, role


def test_the_card_is_not_held_to_the_lyric_ladder():
    # It is not in that relationship with anything. Sharing the cap was why the
    # title read as dim beside the words it sits above.
    p = pal_mod.for_song(PANEL_CHROME, TEAL)
    assert max(rgb_of(p.title)) > UNSUNG_CEILING
    assert max(rgb_of(p.title)) > max(rgb_of(p.artist)) > max(rgb_of(p.side))


def test_the_card_never_asks_for_more_light_than_the_mode_can_give():
    # A target past the clamp ceiling makes the solver trade away every unit of
    # chroma and still fall short — measured under frosting as a flat grey card.
    p = pal_mod.for_song(FROSTED_CHROME, TEAL)
    assert chroma(p.title) > 0, "the card went grey rather than dimmer"
    assert chroma(p.artist) > 0


WASHES = ((30, 30, 30), (29, 24, 18), (12, 20, 30), (26, 12, 12))


def test_the_unsung_line_always_clears_the_wash_it_sits_on():
    # Measured as it lands, over every desktop. Without this rule three of 41
    # real covers came out under 3:1 — and it was the derived text colour that
    # was at fault, not the wash: darkening the backdrop from a cap of 30 to 18
    # moved the worst case only from 2.76 to 2.86.
    for wash in WASHES:
        for hue in range(0, 360, 20):
            song = SongColour(float(hue), 0.85, 0.45, float(hue), False, (0, 0, 0))
            p = pal_mod.for_song(PANEL_CHROME, song, wash)
            got = pal_mod.worst_contrast(rgb_of(p.unsung), wash, PANEL)
            assert got >= 3.0, f"hue {hue} on {wash} lands at {got:.2f}:1"


def test_frosting_cannot_hold_both_the_sweep_and_the_contrast_floor():
    # Not a defect in the derivation — a property of additive composition, and
    # the strongest argument for the blended panel. Under acrylic, paling out to
    # buy contrast forces the sweep rule to drop the level further than the
    # paling gained: measured at hue 0 over a 30 wash, contrast peaks at 2.50:1
    # around chroma 30 and falls back to 2.26:1 at grey.
    wash = (30, 30, 30)
    song = SongColour(40.0, 0.85, 0.45, 40.0, False, (0, 0, 0))
    frosted = pal_mod.for_song(FROSTED_CHROME, song, wash)
    panel = pal_mod.for_song(PANEL_CHROME, song, wash)
    assert pal_mod.worst_contrast(rgb_of(frosted.unsung), wash, ACRYLIC) < 3.0
    assert pal_mod.worst_contrast(rgb_of(panel.unsung), wash, PANEL) >= 3.0


def test_where_the_floor_cannot_be_met_the_best_attempt_is_kept():
    # Paling out is not monotone in contrast under acrylic, so walking to the
    # end and taking the last candidate would ship the worst one.
    wash = (30, 30, 30)
    song = SongColour(40.0, 0.85, 0.45, 40.0, False, (0, 0, 0))
    frosted = pal_mod.for_song(FROSTED_CHROME, song, wash)
    grey_end = pal_mod._sweep_limited(pal_mod._grey(255), pal_mod._grey,
                                      SWEEP_DE, ACRYLIC)
    assert (pal_mod.worst_contrast(rgb_of(frosted.unsung), wash, ACRYLIC)
            > pal_mod.worst_contrast(grey_end, wash, ACRYLIC))


def test_legibility_is_bought_with_colour_not_with_light():
    # The ordering the whole design uses: a role that cannot be both legible and
    # colourful comes out paler, never darker.
    for wash in WASHES:
        song = SongColour(248.0, 0.9, 0.5, 248.0, False, (0, 0, 0))
        p = pal_mod.for_song(PANEL_CHROME, song, wash)
        assert max(rgb_of(p.unsung)) >= pal_mod.MIN_UNSUNG


@pytest.mark.parametrize("chrome", LAWS)
def test_the_unsung_line_never_dims_past_legibility(chrome):
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(chrome, song)
        assert max(rgb_of(p.unsung)) >= pal_mod.MIN_UNSUNG - 1


# --- colour follows the cover -----------------------------------------------

@pytest.mark.parametrize("chrome", LAWS)
def test_a_coloured_cover_tints_the_words_and_the_card(chrome):
    p = pal_mod.for_song(chrome, TEAL)
    for role in ("unsung", "side"):
        assert chroma(getattr(p, role)) >= 10, f"{role} came out grey"
    # The card carries the cover's colour too. It is brighter than the lyrics
    # rather than paler than them — the brightness is what makes it stand off
    # the wash, so the tint does not have to be given up for legibility.
    for role in pal_mod.CARD_ROLES:
        assert chroma(getattr(p, role)) >= 10, f"{role} lost the song's colour"


def test_the_card_always_stands_off_the_wash_behind_it():
    # Panel only. Acrylic caps the card at its clamp ceiling of 138, and its
    # plate then adds ~117 to the text and the wash alike over a bright
    # desktop — measured worst 2.68:1, against the panel's 4.39:1. That is the
    # same limit of additive composition the sweep runs into, not something the
    # derivation can solve.
    for wash in WASHES:
        for hue in range(0, 360, 30):
            song = SongColour(float(hue), 0.85, 0.45, float(hue), False, (0, 0, 0))
            p = pal_mod.for_song(PANEL_CHROME, song, wash)
            for role in pal_mod.CARD_ROLES:
                got = pal_mod.worst_contrast(rgb_of(getattr(p, role)), wash, PANEL)
                assert got >= 3.0, f"{role} at hue {hue} lands at {got:.2f}:1"


@pytest.mark.parametrize("chrome", LAWS)
def test_a_neutral_cover_leaves_the_palette_grey(chrome):
    # A black-and-white sleeve has no hue, and inventing one out of JPEG noise
    # is worse than staying grey.
    p = pal_mod.for_song(chrome, NEUTRAL)
    for role in ROLES:
        assert chroma(getattr(p, role)) <= 8, role


def test_a_vivid_detail_on_a_grey_sleeve_is_not_the_songs_colour():
    barely = SongColour(hue=200.0, sat=0.9, weight=0.02, accent_hue=200.0,
                        neutral=False, dominant=(10, 10, 10))
    assert pal_mod.strength_of(barely) == 0.0


def test_strength_ramps_rather_than_flipping():
    # Gated, a borderline cover would swap palettes between plays.
    weights = [0.05, 0.12, 0.20, 0.30, 0.45]
    strengths = [pal_mod.strength_of(
        SongColour(180.0, 0.5, w, 180.0, False, (0, 0, 0))) for w in weights]
    assert all(b >= a for a, b in itertools.pairwise(strengths))
    assert strengths[0] < strengths[-1]


def test_different_covers_give_different_palettes():
    assert (pal_mod.for_song(PANEL_CHROME, TEAL).unsung
            != pal_mod.for_song(PANEL_CHROME, CRIMSON).unsung)


def test_a_missing_cover_falls_back_to_the_neutral_palette():
    assert pal_mod.for_song(PANEL_CHROME, None).strength == 0.0


# --- the edge fade ----------------------------------------------------------

def test_full_visibility_leaves_a_line_at_its_normal_level():
    assert DEFAULT.faded(1, 1.0) == DEFAULT.by_distance(1)


def test_a_line_at_the_edge_fades_into_the_backdrop_behind_it():
    # Not to black and not to a fixed floor. The cover wash covers the whole
    # window, so a glyph at #000000 there punches a dark hole in it and reads as
    # a silhouette — more visible than the line it was trying to hide.
    wash = (22, 14, 9)
    p = pal_mod.for_song(PANEL_CHROME, TEAL, wash)
    assert rgb_of(p.faded(1, 0.0)) == wash


def test_the_fade_is_gradual():
    wash = (20, 20, 24)
    p = pal_mod.for_song(PANEL_CHROME, TEAL, wash)
    full, half = lum(p.faded(1, 1.0)), lum(p.faded(1, 0.5))
    assert luminance(wash) <= half < full


def test_the_fade_never_undershoots_the_backdrop():
    wash = (26, 18, 12)
    p = pal_mod.for_song(PANEL_CHROME, CRIMSON, wash)
    for visibility in (0.0, 0.1, 0.5, 1.0):
        assert lum(p.faded(1, visibility)) >= luminance(wash) - 1e-6


def test_visibility_clamps_outside_its_range():
    wash = (18, 18, 18)
    p = pal_mod.for_song(PANEL_CHROME, TEAL, wash)
    assert p.faded(1, 5.0) == p.faded(1, 1.0)
    assert rgb_of(p.faded(1, -5.0)) == wash


def test_a_palette_can_be_moved_onto_a_new_backdrop():
    p = pal_mod.for_song(PANEL_CHROME, TEAL, (10, 10, 10))
    moved = pal_mod.rebacked(p, (40, 20, 20))
    assert moved.unsung == p.unsung
    assert rgb_of(moved.faded(1, 0.0)) == (40, 20, 20)


def test_moving_a_palette_onto_its_own_backdrop_changes_nothing():
    p = pal_mod.for_song(PANEL_CHROME, TEAL, (10, 10, 10))
    assert pal_mod.rebacked(p, (10, 10, 10)) is p


def test_a_mode_with_nothing_behind_the_text_does_not_pretend_to_fade():
    # Dimming a colour that replaces what is behind it makes a darker colour,
    # which over a bright background is more visible rather than less.
    assert KEYED.faded(1, 0.0) == KEYED.by_distance(1)
    assert KEYED.faded(1, 1.0) == KEYED.by_distance(1)


# --- mode ------------------------------------------------------------------

def test_only_the_washed_palettes_fade_into_a_backdrop():
    assert DEFAULT.washed
    assert not KEYED.washed


def test_only_an_additive_surface_glows():
    # An offset copy on a replacing surface smears rather than adding light.
    assert pal_mod.for_song(FROSTED_CHROME, NEUTRAL).glow
    assert not pal_mod.for_song(PANEL_CHROME, NEUTRAL).glow
    assert not KEYED.glow


def test_only_the_keyed_palette_outlines():
    assert DEFAULT.outline == 0
    assert KEYED.outline > 0


def test_the_keyed_palette_ignores_the_cover():
    # Its colours have to survive whatever video is behind the window, which the
    # artwork says nothing about.
    assert pal_mod.for_song(KEYED_CHROME, TEAL) is KEYED
    assert pal_mod.for_chrome(KEYED_CHROME) is KEYED


# --- the colour must not be what dims the words -----------------------------

def test_no_unlit_role_gives_up_much_of_its_light_for_colour():
    # The complaint this budget exists for: the colour lived exactly where the
    # light did not, so a coloured cover dimmed the whole body of unlit text
    # rather than one rung of the ladder.
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL

    ch = Chrome(ChromeMode.PANEL, "#101014", PANEL)
    grey = pal_mod.for_song(ch, NEUTRAL)
    for song in COLOURED:
        p = pal_mod.for_song(ch, song)
        for role in ("unsung", "side", "far"):
            kept = lum(getattr(p, role)) / lum(getattr(grey, role))
            assert kept > 0.90, (
                f"{role} at hue {song.hue:.0f} gave up "
                f"{(1 - kept) * 100:.0f}% of its light to buy colour")


def test_a_hard_hue_gives_up_colour_rather_than_light():
    # Luminance is 21% red, 72% green, 7% blue, so a violet tint costs several
    # times what a yellow one does. Paying in light made the same design look
    # fine on one cover and murky on the next; paying in colour does not.
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL, rgb_of
    from lyrica.glass import chroma as chroma_of
    from lyrica.songcolour import SongColour

    ch = Chrome(ChromeMode.PANEL, "#101014", PANEL)

    def unsung_for(hue):
        song = SongColour(hue=hue, sat=0.75, weight=0.6, accent_hue=hue,
                          neutral=False, dominant=(0, 0, 0))
        return pal_mod.for_song(ch, song).unsung

    easy, hard = unsung_for(55.0), unsung_for(275.0)      # yellow, violet
    assert abs(lum(easy) - lum(hard)) / lum(easy) < 0.06, "the light differs"
    assert chroma_of(rgb_of(hard)) < chroma_of(rgb_of(easy)), "the colour does not"


@pytest.mark.parametrize("chrome", LAWS)
def test_the_ladder_cannot_invert(chrome):
    # An ordering before it is a set of levels. The neighbours share only part
    # of the active line's compression, which under a composition that clamps
    # hard used to lift them past it.
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(chrome, song)
        assert lum(p.sung) > lum(p.unsung) > lum(p.side) > lum(p.far)


def test_the_beam_keeps_a_dark_ground_to_travel_through():
    # It used to borrow the unsung line's colour, and the two were the same
    # number only by accident: one wants to be legible, the other wants to be
    # dark enough that a travelling head reads as travelling.
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL

    ch = Chrome(ChromeMode.PANEL, "#101014", PANEL)
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(ch, song)
        assert lum(p.beam) < lum(p.unsung) * 0.85
