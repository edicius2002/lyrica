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
    # `halo_width` is gone from this contract because the thing it measured is:
    # the border was a flat nine-pixel stroke under a crisp core, and it is a
    # falloff now with no stroke anywhere in it. The numbers that decide how
    # wide it reads are the ones recorded instead — the ink, how far it is
    # smeared, how far the field carries, and how far inside the panel's own
    # edge the whole thing sits.
    from lyrica import beam

    expected = CONTRACTS["beam"]
    assert beam.CORE_WIDTH == expected["core_width"]
    assert beam.CORE_SOFT == expected["core_soft"]
    assert beam.HALO_REACH == expected["halo_reach"]
    assert beam.EDGE_INSET == expected["edge_inset"]
    assert beam.MIN_BEAM_DE == expected["minimum_delta_e"]


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
