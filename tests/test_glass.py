"""The measured composition law, and the two metrics built on it (offline)."""
import pytest

from lyrica.glass import (
    ACRYLIC,
    HEADROOM,
    PANEL,
    alpha_panel,
    contrast,
    delta_e,
    hex_of,
    luminance,
    plate,
    rgb_of,
    screen,
    tint,
)

# What was actually sampled off the screen, desktop level -> plate colour.
MEASURED = {0: (18, 18, 19), 64: (42, 42, 44), 128: (67, 67, 69),
            200: (95, 95, 97), 255: (117, 117, 118)}


def test_the_plate_model_reproduces_what_was_measured():
    for level, expected in MEASURED.items():
        got = plate((level,) * 3)
        assert all(abs(a - b) <= 1 for a, b in zip(got, expected, strict=True)), \
            f"desktop {level}: modelled {got}, measured {expected}"


def test_the_plate_responds_per_channel():
    # A coloured desktop tints the plate rather than merely brightening it.
    r, g, b = plate((200, 40, 40))
    assert r > g and abs(g - b) <= 1


def test_composition_adds_and_clamps():
    assert screen((117, 117, 118), (255, 0, 0)) == (255, 117, 118)
    assert screen((18, 18, 19), (0, 0, 0)) == (18, 18, 19)


def test_the_desktop_cancels_out_while_nothing_clamps():
    # The reason dark text on a dark backdrop is legible at all: the plate is a
    # pedestal under both, so their difference is unchanged by it.
    text, back = (138, 130, 90), (26, 20, 14)
    for level in (0, 128, 255):
        p = plate((level,) * 3)
        gap = [a - b for a, b in zip(screen(p, text), screen(p, back), strict=True)]
        if max(screen(p, text)) < 255:
            assert gap == [a - b for a, b in zip(text, back, strict=True)]


def test_the_headroom_is_where_the_clamp_starts_biting():
    p = plate((255, 255, 255))
    # At the ceiling the sum lands exactly on 255, so the chroma still arrives.
    at_ceiling = screen(p, (HEADROOM, HEADROOM - 40, HEADROOM - 40))
    assert max(at_ceiling) - min(at_ceiling) == 40
    # Above it the colour is flattened toward white and the chroma is gone.
    over = screen(p, (HEADROOM + 40, HEADROOM, HEADROOM))
    assert max(over) - min(over) == 0


# --- the law, generalised ---------------------------------------------------

def test_the_acrylic_composition_agrees_with_what_was_measured():
    for level, expected in MEASURED.items():
        got = ACRYLIC.compose((level,) * 3, (0, 0, 0))
        assert all(abs(a - b) <= 1 for a, b in zip(got, expected, strict=True))


def test_a_surface_reaches_the_screen_whole_under_acrylic():
    # Gain 1: what is drawn is added, not scaled.
    assert ACRYLIC.compose((0, 0, 0), (100, 100, 100)) == (118, 118, 118)


def test_a_blended_panel_passes_only_its_share():
    panel = alpha_panel(0.80)
    # 80 % of the surface plus 20 % of the desktop, which is the whole law.
    assert panel.compose((0, 0, 0), (100, 100, 100)) == (80, 80, 80)
    assert panel.compose((255, 255, 255), (0, 0, 0)) == (51, 51, 51)
    assert panel.compose((255, 255, 255), (100, 100, 100)) == (131, 131, 131)


def test_acrylic_clamps_where_a_blended_panel_does_not():
    # The whole reason the ladder has to be solved per composition rather than
    # frozen: acrylic runs out of room at 138, the panel effectively never does.
    assert ACRYLIC.headroom == pytest.approx(HEADROOM, abs=1)
    assert alpha_panel(0.82).headroom == pytest.approx(255, abs=1)


def test_a_dimmer_panel_leaves_less_room_for_the_desktop_underneath():
    assert alpha_panel(0.60).pedestal((255,) * 3)[0] > \
        alpha_panel(0.90).pedestal((255,) * 3)[0]


def test_only_the_frosted_surface_claims_to_add_light():
    assert ACRYLIC.additive
    assert not PANEL.additive


def test_the_chosen_panel_alpha_keeps_unsung_text_clear_of_three_to_one():
    # Solved rather than picked: 0.75 is the most translucent that clears 3:1
    # and clears it by nothing, while both the text level and the backdrop cap
    # are themselves derived. The shipped value has to keep a margin.
    worst = min(contrast(PANEL.compose((d,) * 3, (138, 138, 132)),
                         PANEL.compose((d,) * 3, (30, 30, 30)))
                for d in range(0, 256, 5))
    assert worst >= 3.3, f"unsung text lands at {worst:.2f}:1 over its backdrop"


# --- the two metrics --------------------------------------------------------

def test_contrast_is_symmetric_and_bounded():
    assert contrast((255, 255, 255), (0, 0, 0)) == pytest.approx(21.0, abs=0.1)
    assert contrast((80, 80, 80), (80, 80, 80)) == pytest.approx(1.0)
    assert contrast((10, 10, 10), (200, 200, 200)) == \
        contrast((200, 200, 200), (10, 10, 10))


def test_unsung_text_clears_three_to_one_over_a_capped_backdrop():
    # 30 is the level every backdrop is scaled to; 138 is the clamp ceiling.
    assert contrast((138, 138, 132), (30, 30, 30)) >= 3.0


def test_contrast_goes_blind_exactly_where_the_clamp_bites():
    # Both colours clamp to white over a bright desktop, so the ratio says they
    # are identical. They are not, which is why delta_e exists.
    p = plate((255, 255, 255))
    white, amber = (255, 255, 249), (200, 150, 40)
    assert contrast(screen(p, white), screen(p, amber)) == pytest.approx(1.0, abs=0.1)
    assert delta_e(screen(p, white), screen(p, amber)) > 10


def test_delta_e_discounts_blue_yellow_separation():
    # Measured: a yellow tail scoring 20 unweighted still read as one flat bar.
    grey = (128, 128, 128)
    along_b = delta_e(grey, (128, 128, 60))     # blue-yellow axis
    along_a = delta_e(grey, (190, 128, 128))    # red-green axis
    assert along_b < along_a


def test_delta_e_is_zero_for_a_colour_against_itself():
    assert delta_e((70, 90, 110), (70, 90, 110)) == pytest.approx(0.0)


# --- tinting ----------------------------------------------------------------

def test_a_tint_lands_on_the_level_it_was_asked_for():
    for level in (48, 98, 138, 255):
        for hue in (0, 60, 120, 210, 300):
            assert max(tint(level, hue, 1.0)) == pytest.approx(level, abs=1)


def test_saturation_controls_chroma_not_level():
    vivid, pale = tint(138, 210, 1.0), tint(138, 210, 0.3)
    assert max(vivid) == pytest.approx(max(pale), abs=1)
    assert max(vivid) - min(vivid) > max(pale) - min(pale)


def test_a_tint_at_zero_saturation_is_grey():
    r, g, b = tint(120, 200, 0.0)
    assert r == g == b


def test_luminance_varies_by_hue_at_a_fixed_level():
    # The trap this whole design is built around: fully saturated red at 255 has
    # 21 % of white's luminance, so tinting every role at one level dims each
    # hue by a different amount.
    assert luminance(tint(255, 0, 1.0)) < luminance(tint(255, 60, 1.0))


# --- the tint worn while dragging -------------------------------------------

def tints():
    """The resting and dragging tints, or a skip where Windows chrome is absent.

    Guarded because CI runs this suite on macOS too, where `ctypes.wintypes` is
    not guaranteed to import.
    """
    try:
        from lyrica.chrome.windows import DRAG_TINT_RGBA, TINT_RGBA
    except (ImportError, ValueError):  # pragma: no cover - not Windows
        pytest.skip("the Windows chrome is not importable here")
    return TINT_RGBA, DRAG_TINT_RGBA


def test_the_drag_tint_reproduces_acrylics_own_line():
    # Dragging drops the blur, which is the expensive half, but must not change
    # the colour — switching the accent off entirely was the first version, and
    # it snapped the panel to flat black. The gradient state blends plainly,
    # plate = (1-a)*desktop + a*tint, so the tint is chosen to land on the same
    # line the acrylic plate was measured on.
    r, _g, _b, a = tints()[1]
    alpha = a / 255.0
    for level in (0, 64, 128, 200, 255):
        gradient = (1 - alpha) * level + alpha * r
        assert gradient == pytest.approx(plate((level,) * 3)[0], abs=1.5), \
            f"the panel would change colour at desktop {level}"


def test_the_drag_tint_is_not_the_resting_tint():
    # They cannot be the same value: one is composited over a blurred backdrop
    # and the other over a sharp one, at different alphas.
    resting, dragging = tints()
    assert resting != dragging


def test_hex_and_back_round_trips():
    assert rgb_of(hex_of((18, 200, 7))) == (18, 200, 7)


def test_hex_clamps_rather_than_overflowing():
    assert hex_of((300, -20, 128.6)) == "#ff0081"
