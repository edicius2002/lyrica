"""Brightness levels and the edge fade (offline)."""
import itertools

from lyrica.palette import GLASS, KEYED


def level_of(colour: str) -> int:
    return int(colour[1:3], 16)


# --- the ladder -------------------------------------------------------------

def test_lines_dim_the_further_they_sit_from_the_active_one():
    near = level_of(GLASS.by_distance(1))
    far = level_of(GLASS.by_distance(2))
    assert far < near, "an outer line must be fainter, or entry cannot fade"


def test_the_sung_ramp_runs_from_unsung_to_sung():
    assert GLASS.at(0.0) == GLASS.unsung
    assert GLASS.at(1.0) == GLASS.sung


def test_the_ramp_clamps_outside_its_range():
    assert GLASS.at(-5.0) == GLASS.unsung
    assert GLASS.at(5.0) == GLASS.sung


def test_the_ramp_never_darkens_as_it_advances():
    levels = [level_of(GLASS.at(i / 32)) for i in range(33)]
    assert all(b >= a for a, b in itertools.pairwise(levels))


# --- the edge fade ----------------------------------------------------------

def test_full_visibility_leaves_a_line_at_its_normal_level():
    assert GLASS.faded(1, 1.0) == GLASS.by_distance(1)


def test_a_line_at_the_edge_fades_to_nothing():
    # The whole point: by the time the frame would clip a line, there is
    # nothing left of it to clip.
    assert level_of(GLASS.faded(1, 0.0)) == 0


def test_the_fade_is_gradual():
    full = level_of(GLASS.faded(1, 1.0))
    half = level_of(GLASS.faded(1, 0.5))
    assert 0 < half < full


def test_visibility_clamps_outside_its_range():
    assert GLASS.faded(1, 5.0) == GLASS.faded(1, 1.0)
    assert level_of(GLASS.faded(1, -5.0)) == 0


def test_replacing_composition_does_not_pretend_to_fade():
    # Dimming a colour that replaces what is behind it makes a darker colour,
    # which over a bright background is more visible rather than less.
    assert KEYED.faded(1, 0.0) == KEYED.by_distance(1)
    assert KEYED.faded(1, 1.0) == KEYED.by_distance(1)


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
