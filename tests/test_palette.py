"""The colour ladder, the sweep rule, and the edge fade (offline)."""
import itertools

from lyrica import palette as pal_mod
from lyrica.chrome import Chrome, ChromeMode
from lyrica.glass import (
    HEADROOM,
    delta_e,
    luminance,
    plate,
    rgb_of,
    screen,
)
from lyrica.palette import GLASS, KEYED, SWEEP_DE, WORST_DESKTOP
from lyrica.songcolour import NEUTRAL, SongColour

GLASS_CHROME = Chrome(ChromeMode.GLASS, "#000000")
KEYED_CHROME = Chrome(ChromeMode.KEYED, "#010203")

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
    assert lum(GLASS.by_distance(2)) < lum(GLASS.by_distance(1)), \
        "an outer line must be fainter, or entry cannot fade"


def test_the_sung_ramp_runs_from_unsung_to_sung():
    assert GLASS.at(0.0) == GLASS.unsung
    assert GLASS.at(1.0) == GLASS.sung


def test_the_ramp_clamps_outside_its_range():
    assert GLASS.at(-5.0) == GLASS.unsung
    assert GLASS.at(5.0) == GLASS.sung


def test_the_ramp_never_darkens_as_it_advances():
    levels = [lum(GLASS.at(i / 32)) for i in range(33)]
    assert all(b >= a for a, b in itertools.pairwise(levels))


def test_the_active_line_outshines_its_neighbours():
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(GLASS_CHROME, song)
        assert lum(p.unsung) > lum(p.side) > lum(p.far)


# --- the sweep survives the worst desktop -----------------------------------

def test_the_sung_word_stays_distinguishable_over_a_white_desktop():
    # The failure a greyscale palette hides: over a bright desktop the plate
    # adds ~117 to everything, so 255 and 138 both clamp to white and the sweep
    # disappears. Measured at dE 3.4 before this rule existed.
    p_ = plate(WORST_DESKTOP)
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(GLASS_CHROME, song)
        got = delta_e(screen(p_, rgb_of(p.sung)), screen(p_, rgb_of(p.unsung)))
        assert got >= SWEEP_DE - 0.5, f"sweep vanishes at {got:.1f} for hue {p.hue:.0f}"


def test_no_role_but_the_sung_word_exceeds_the_clamp_ceiling():
    # At or below 138 a colour reaches the screen with its chroma intact on
    # every desktop; above it the chroma lost is level - 138, linearly. The
    # sung word is the one role whose job is light rather than colour.
    for song in COLOURED:
        p = pal_mod.for_song(GLASS_CHROME, song)
        for role in ROLES:
            if role == "sung":
                continue
            assert max(rgb_of(getattr(p, role))) <= HEADROOM + 1, role


def test_the_unsung_line_never_dims_past_legibility():
    for song in (NEUTRAL, *COLOURED):
        p = pal_mod.for_song(GLASS_CHROME, song)
        assert max(rgb_of(p.unsung)) >= pal_mod.MIN_UNSUNG - 1


# --- colour follows the cover -----------------------------------------------

def test_a_coloured_cover_tints_the_words_the_title_and_the_artist():
    p = pal_mod.for_song(GLASS_CHROME, TEAL)
    for role in ("unsung", "side", "title", "artist"):
        assert chroma(getattr(p, role)) >= 10, f"{role} came out grey"


def test_a_neutral_cover_leaves_the_palette_grey():
    # A black-and-white sleeve has no hue, and inventing one out of JPEG noise
    # is worse than staying grey.
    p = pal_mod.for_song(GLASS_CHROME, NEUTRAL)
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
    assert (pal_mod.for_song(GLASS_CHROME, TEAL).unsung
            != pal_mod.for_song(GLASS_CHROME, CRIMSON).unsung)


def test_a_missing_cover_falls_back_to_the_neutral_palette():
    assert pal_mod.for_song(GLASS_CHROME, None).strength == 0.0


# --- the edge fade ----------------------------------------------------------

def test_full_visibility_leaves_a_line_at_its_normal_level():
    assert GLASS.faded(1, 1.0) == GLASS.by_distance(1)


def test_a_line_at_the_edge_fades_into_the_backdrop_behind_it():
    # Not to black and not to a fixed floor. The cover wash covers the whole
    # window, so a glyph at #000000 there punches a dark hole in it and reads as
    # a silhouette — more visible than the line it was trying to hide.
    wash = (22, 14, 9)
    p = pal_mod.for_song(GLASS_CHROME, TEAL, wash)
    assert rgb_of(p.faded(1, 0.0)) == wash


def test_the_fade_is_gradual():
    wash = (20, 20, 24)
    p = pal_mod.for_song(GLASS_CHROME, TEAL, wash)
    full, half = lum(p.faded(1, 1.0)), lum(p.faded(1, 0.5))
    assert luminance(wash) <= half < full


def test_the_fade_never_undershoots_the_backdrop():
    wash = (26, 18, 12)
    p = pal_mod.for_song(GLASS_CHROME, CRIMSON, wash)
    for visibility in (0.0, 0.1, 0.5, 1.0):
        assert lum(p.faded(1, visibility)) >= luminance(wash) - 1e-6


def test_visibility_clamps_outside_its_range():
    wash = (18, 18, 18)
    p = pal_mod.for_song(GLASS_CHROME, TEAL, wash)
    assert p.faded(1, 5.0) == p.faded(1, 1.0)
    assert rgb_of(p.faded(1, -5.0)) == wash


def test_a_palette_can_be_moved_onto_a_new_backdrop():
    p = pal_mod.for_song(GLASS_CHROME, TEAL, (10, 10, 10))
    moved = pal_mod.rebacked(p, (40, 20, 20))
    assert moved.unsung == p.unsung
    assert rgb_of(moved.faded(1, 0.0)) == (40, 20, 20)


def test_moving_a_palette_onto_its_own_backdrop_changes_nothing():
    p = pal_mod.for_song(GLASS_CHROME, TEAL, (10, 10, 10))
    assert pal_mod.rebacked(p, (10, 10, 10)) is p


def test_replacing_composition_does_not_pretend_to_fade():
    # Dimming a colour that replaces what is behind it makes a darker colour,
    # which over a bright background is more visible rather than less.
    assert KEYED.faded(1, 0.0) == KEYED.by_distance(1)
    assert KEYED.faded(1, 1.0) == KEYED.by_distance(1)


# --- mode ------------------------------------------------------------------

def test_only_the_additive_palette_claims_to_be_additive():
    assert GLASS.additive
    assert not KEYED.additive


def test_only_the_additive_palette_glows():
    # An offset copy on a replacing surface smears rather than adding light.
    assert GLASS.glow
    assert not KEYED.glow


def test_only_the_replacing_palette_outlines():
    assert GLASS.outline == 0
    assert KEYED.outline > 0


def test_the_keyed_palette_ignores_the_cover():
    # Its colours have to survive whatever video is behind the window, which the
    # artwork says nothing about.
    assert pal_mod.for_song(KEYED_CHROME, TEAL) is KEYED
