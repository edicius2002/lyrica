"""Stable, reviewable numbers behind the committed visual reference frames."""

import json
from pathlib import Path

import pytest

CONTRACTS = json.loads(
    (Path(__file__).with_name("visual_contracts.json")).read_text(encoding="utf-8"))


def test_word_strike_matches_the_visual_contract():
    from lyrica import bloom, config
    from lyrica.lineview import GROW_ATTACK_S, GROW_SETTLE_S

    expected = CONTRACTS["word_strike"]
    assert config.GROWTH_DEFAULT == expected["growth"]
    assert GROW_ATTACK_S * 1000 == pytest.approx(expected["attack_ms"])
    assert GROW_SETTLE_S * 1000 == pytest.approx(expected["settle_ms"])
    assert bloom.INNER_RADIUS == expected["inner_glow_radius"]
    assert bloom.OUTER_RADIUS == expected["outer_glow_radius"]


def test_duet_lanes_match_the_visual_contract():
    from lyrica import app, config

    expected = CONTRACTS["duet"]
    separation = config.VOICE_STEP_DEFAULT * 2
    assert app.HEIGHT == expected["panel_height"]
    assert app.WRAP == expected["wrap_width"]
    assert app.ROW_GAP == expected["row_gap"]
    assert config.VOICE_STEP_DEFAULT == expected["lane_step"]
    assert app.VOICE_SAFE_MARGIN == expected["safe_margin"]
    assert separation / expected["panel_width"] >= expected["minimum_separation_ratio"]


def test_backing_lane_matches_the_visual_contract():
    from lyrica import app

    expected = CONTRACTS["backing_vocal"]
    assert app.ECHO_CORNER_INSET == expected["corner_inset"]
    assert app.ECHO_SAFE_MARGIN == expected["safe_margin"]
    assert app.ECHO_ENTRY_LANE == expected["entry_lane_fraction"]
    assert app.ECHO_EXIT_LANE == expected["exit_lane_fraction"]


def test_beam_layers_match_the_visual_contract():
    # This contract has been rewritten twice and each rewrite is the record of
    # a design that stopped being true. `halo_width` went when the border
    # stopped being a stroke; `core_soft` and `halo_reach` go now, because they
    # described light laid *on* the panel and there is none. What is recorded
    # instead is where the light is relative to the panel's own silhouette:
    # `edge_inset` puts the field's rectangle on it, `crest` says how far
    # outside it the brightest pixel is, and the three reaches say how far the
    # light carries back onto the face, out as a rim, and out as a bloom.
    from lyrica import beam

    expected = CONTRACTS["beam"]
    assert beam.EDGE_INSET == expected["edge_inset"]
    assert beam.CREST == expected["crest"]
    assert beam.CORE_WIDTH == expected["core_width"]
    assert beam.BLEED_REACH == expected["bleed_reach"]
    assert beam.RIM_REACH == expected["rim_reach"]
    assert beam.SPILL_REACH == expected["spill_reach"]
    assert beam.MIN_BEAM_DE == expected["minimum_delta_e"]
    # The crest is outside the panel and the bleed cannot reach the words. Both
    # are properties of the design rather than of these five numbers, so they
    # are asserted about the numbers rather than left to the pictures.
    assert beam.CREST > 0.0, "the brightest pixel is on the panel"
    assert beam.BLEED_REACH < beam.RIM_REACH


def test_committed_reference_frames_are_real_images():
    from PIL import Image

    root = Path(__file__).parents[1] / "docs" / "visual-baselines"
    names = ("word-strike", "duet-lanes", "backing-vocal",
             "beam-quiet", "beam-loud")
    frames = []
    for name in names:
        with Image.open(root / f"{name}.png") as image:
            assert image.size == (900, 320)
            assert image.convert("RGB").getbbox() is not None
            frames.append(image.convert("RGB").tobytes())
    assert frames[-2] != frames[-1], "quiet and loud beam references are identical"
