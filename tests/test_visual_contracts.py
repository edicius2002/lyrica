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
    from lyrica import config

    expected = CONTRACTS["duet"]
    separation = config.VOICE_STEP_DEFAULT * 2
    assert config.VOICE_STEP_DEFAULT == expected["lane_step"]
    assert separation / expected["panel_width"] >= expected["minimum_separation_ratio"]


def test_beam_layers_match_the_visual_contract():
    from lyrica import beam

    expected = CONTRACTS["beam"]
    assert beam.CORE_WIDTH == expected["core_width"]
    assert beam.HALO_WIDTH == expected["halo_width"]
    assert beam.MIN_BEAM_DE == expected["minimum_delta_e"]


def test_committed_reference_frames_are_real_images():
    from PIL import Image

    root = Path(__file__).parents[1] / "docs" / "visual-baselines"
    names = ("word-strike", "duet-lanes", "backing-vocal",
             "beam-quiet", "beam-loud")
    frames = []
    for name in names:
        with Image.open(root / f"{name}.png") as image:
            assert image.size == (900, 300)
            assert image.convert("RGB").getbbox() is not None
            frames.append(image.convert("RGB").tobytes())
    assert frames[-2] != frames[-1], "quiet and loud beam references are identical"
