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
def test_no_role_but_the_sung_word_exceeds_the_ladder_ceiling(chrome):
    # Two separate caps that were the same number under acrylic: the
    # composition's clamp ceiling, and the ladder's own. The sung word is the
    # one role whose job is light rather than colour.
    for song in COLOURED:
        p = pal_mod.for_song(chrome, song)
        for role in ROLES:
            if role == "sung":
                continue
            assert max(rgb_of(getattr(p, role))) <= UNSUNG_CEILING + 1, role


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
def test_a_coloured_cover_tints_the_words_the_title_and_the_artist(chrome):
    p = pal_mod.for_song(chrome, TEAL)
    for role in ("unsung", "side", "title", "artist"):
        assert chroma(getattr(p, role)) >= 10, f"{role} came out grey"


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
