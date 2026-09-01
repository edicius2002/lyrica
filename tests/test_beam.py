"""The path the border light travels, and the meter that drives it (offline)."""
import math
import sys
from itertools import pairwise

import pytest
from conftest import Surface

from lyrica import meter as meter_mod
from lyrica.beam import CORNER_POINTS, STRAIGHT_SPACING, _rounded_path


def path(width=1125, height=375, radius=18, inset=2.0):
    return _rounded_path(width, height, radius, inset)


def test_the_ring_closes_on_itself():
    points = path()
    # Last point back to first must be a short hop, not a leap across the panel.
    gap = math.dist(points[-1], points[0])
    assert gap <= STRAIGHT_SPACING + 1


def test_every_point_lies_on_the_panel():
    for x, y in path():
        assert -1 <= x <= 1126 and -1 <= y <= 376


def test_the_corners_are_drawn_as_curves_not_cut_off():
    # The defect this spacing exists for. The default panel's perimeter is about
    # 2900 px and a corner arc is 28, so evenly spaced segments cheap enough to
    # recolour at 30 Hz gave a corner less than one segment — a chamfer.
    points = path()
    corner = [(x, y) for x, y in points if x < 30 and y < 30]
    assert len(corner) >= CORNER_POINTS - 1, "the corner reads as a cut"


def test_corner_points_sit_on_the_radius():
    radius, inset = 18, 2.0
    centre = (inset + radius, inset + radius)
    on_arc = [p for p in path(radius=radius, inset=inset)
              if p[0] < centre[0] and p[1] < centre[1]]
    for point in on_arc:
        assert math.dist(point, centre) == pytest.approx(radius, abs=1.0)


def test_a_longer_edge_gets_more_points_but_the_corner_does_not():
    short, long = path(width=400), path(width=1600)
    assert len(long) > len(short)
    for points in (short, long):
        corner = [(x, y) for x, y in points if x < 30 and y < 30]
        assert len(corner) >= CORNER_POINTS - 1


def test_a_panel_too_small_for_its_radius_still_produces_a_ring():
    # The compact panel is 114 px tall; the radius must not exceed half of it.
    points = path(width=120, height=60, radius=40)
    assert len(points) >= 4
    for x, y in points:
        assert -1 <= x <= 121 and -1 <= y <= 61


# --- the meter --------------------------------------------------------------

def test_a_platform_without_a_meter_reports_silence():
    null = meter_mod.NullMeter()
    assert not null.available
    assert null.level() == 0.0
    null.close()


def test_the_platform_picks_the_meter_it_can_use():
    made = meter_mod.create_meter()
    if sys.platform == "win32":
        assert isinstance(made, meter_mod.WindowsMeter)
    else:
        assert isinstance(made, meter_mod.NullMeter)
    made.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only meter")
def test_the_level_rises_at_once_and_falls_gently(monkeypatch):
    # A peak meter reads nothing between beats. Following it raw strobes; this
    # keeps the attack and turns the gaps into a decay.
    made = meter_mod.WindowsMeter.__new__(meter_mod.WindowsMeter)
    made._meter, made._value, made.available = None, 0.0, True
    monkeypatch.setattr(made, "raw", lambda: 0.8)
    assert made.level(1 / 60) == pytest.approx(0.8), "a rise must land at once"

    monkeypatch.setattr(made, "raw", lambda: 0.0)
    after_one = made.level(1 / 60)
    assert 0.0 < after_one < 0.8, "a fall must take time"
    assert made.level(1.0) == 0.0, "and must reach silence eventually"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only timer")
def test_holding_the_timer_resolution_is_idempotent():
    # Every begin has to be matched by exactly one end, so asking twice for a
    # state already held must not leave the resolution raised on the way out.
    from lyrica.chrome import windows as win

    assert win.hold_timer_resolution(True) is True
    assert win.hold_timer_resolution(True) is True
    assert win.hold_timer_resolution(False) is False
    assert win.hold_timer_resolution(False) is False


def test_asking_for_even_frames_is_safe_on_any_platform():
    from lyrica import chrome as chrome_mod

    chrome_mod.hold_timer_resolution(True)
    chrome_mod.hold_timer_resolution(False)


# --- the two styles ---------------------------------------------------------
#
# The ring is drawn as images now rather than as line items, so what the border
# is lit to is read off the pixels the compositor will show rather than off a
# list of fills. That is what these assertions were always about — the fills
# were only the evidence available while the ring was made of `create_line` —
# and reading the picture is the stronger evidence of the two: it covers the
# opacity as well as the colour, which the fills could not see.

def _panel(width=600, height=200):
    from lyrica import palette as pal_mod
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.songcolour import SongColour

    return pal_mod.for_song(
        Chrome(ChromeMode.PANEL, "#000", PANEL),
        SongColour(38.0, 0.8, 0.45, 38.0, False, (0, 0, 0)), (29, 24, 14))


def lit(canvas, width, height, character, radius=18, scale=1.0):
    """A ring of both halves, and the surface the outward one landed on."""
    from lyrica.beam import Beam

    palette = _panel()
    surface = Surface()
    ring = Beam(canvas, width, height, radius, scale, glow=surface)
    ring.advance(0.0, character, palette)
    # Twice over. One `advance` already paints every strip, so this is belt and
    # braces against a strip that needed a second look — and it costs nothing.
    for _ in range(len(ring.light.strips) * 2):
        ring.light.paint(ring._tables)
    return ring, palette, surface


def frame(ring, palette, surface, width, height):
    """The whole border as the screen will show it, panel and desktop alike.

    Padded by the light's own reach on every side, with the panel at
    `(pad, pad)`, because most of the border is *outside* the panel now: the
    peak sits past the silhouette and the canvas half carries only the frosted
    rim. A picture cropped to the panel is a picture of the two pixels this
    design deliberately left there.
    """
    from PIL import Image

    pad = ring.pad
    ground = (*(int(c) for c in palette.backdrop), 255)
    made = Image.new("RGBA", (width + 2 * pad, height + 2 * pad), ground)
    made.alpha_composite(surface.straight(width + 2 * pad, height + 2 * pad))
    plate = Image.new("RGBA", (width, height), ground)
    for strip in ring.light.strips:
        if strip.box is not None:
            plate.alpha_composite(ring.light.image(strip, ring._tables),
                                  (strip.box[0], strip.box[1]))
    made.alpha_composite(plate, (pad, pad))
    return made.convert("RGB"), pad


def ridge(ring, palette, surface, width, height, span=5):
    """The brightest the light gets at each point round the ring, 0..255.

    The brightest *near* each point rather than exactly on it, because the
    profile is a falloff sampled on a pixel grid: whether a crest lands on a
    pixel centre or between two of them moves the reading by a few levels, and
    that is the picture's own sampling rather than anything the music did.

    `span` is wider than it was, and that is the design and not slack. The path
    `ring._points` walks is the panel's own silhouette now, and the light peaks
    `CREST` pixels outside it, so a span that only looked at the path would be
    reading the rim rather than the light.
    """
    picture, pad = frame(ring, palette, surface, width, height)
    pixels = picture.load()
    levels = []
    for x, y in ring._points:
        near = [max(pixels[min(picture.width - 1, max(0, round(x) + pad + dx)),
                           min(picture.height - 1, max(0, round(y) + pad + dy))])
                for dx in range(-span, span + 1)
                for dy in range(-span, span + 1)]
        levels.append(max(near))
    return levels


def _lit(level, canvas, width=600, height=200):
    """What the border is lit to all the way round it, as brightness 0..255.

    Sampled on the ring's own path, which is where the light is brightest, and
    against the backdrop it is composed over — so an unlit stretch reads as the
    backdrop exactly as it does on screen.
    """
    from lyrica.meter import Character

    if not isinstance(level, Character):
        level = Character(level=level)
    ring, palette, surface = lit(canvas, width, height, level)
    levels = ridge(ring, palette, surface, width, height)
    ring.destroy()
    return levels


def test_the_shine_lights_every_edge_at_once(canvas):
    # Asked for as the quieter alternative: constant across all the borders,
    # with the light moving rather than a bright spot.
    #
    # Read at a dynamics the music might actually have. It used to be read at
    # `Character`'s default of zero, which is a master compressed until it
    # barely moves — and the border over one of those is *meant* to be even,
    # which is what `test_a_flat_master_gets_an_even_border` asserts two
    # screens down. The two only agreed while the ramp climbed to white, so a
    # flat master got its swing from the colour whatever `dynamics` said. With
    # the colour held still the premise had to be honest: at dynamics 0 the
    # range is 22 and belongs to the other test; at 0.3 it is 36 and at 0.9,
    # 65.
    from lyrica.meter import Character

    levels = _lit(Character(level=1.0, dynamics=0.5), canvas)
    assert min(levels) > 50, "part of the border went dark"
    assert max(levels) - min(levels) > 30, "nothing moves through it"


def test_the_shine_stays_lit_with_no_audio_at_all(canvas):
    # There often is none — playback can be rendered on another device
    # entirely — so silence must not put the border out.
    assert min(_lit(0.0, canvas)) > 20


def _section(ring, surface, width, height):
    """The light's cross-section outward from the middle of the left edge.

    Opacity rather than composited colour, because it is the *shape* of the
    falloff being measured and the colour varies along the ring while the shape
    does not. Read off the outward half and ordered from the silhouette
    outward, which since the light went behind the panel is the whole of it —
    the canvas half holds the frosted rim and nothing else.
    """
    pad = ring.pad
    alpha = surface.frame()[height // 2 + pad, :pad, 3]
    return [int(value) for value in alpha[::-1]]


def test_the_border_is_a_falloff_and_not_a_step(canvas):
    # What the two `create_line` strokes could not do, and the whole reason the
    # ring became an image. A five-pixel core at full colour under a fifteen-
    # pixel halo at a flat 42 % gives a cross-section that is bright, then a
    # plateau, then nothing — one step, and no width or percentage removes it,
    # because having ends is what a stroke is. This asserts the replacement has
    # the property the strokes could not: a single peak, and a descent that
    # never sits still and never jumps.
    #
    # Measured outward from the silhouette now rather than across the panel's
    # own edge, because that is where the light went.
    from lyrica.meter import Character

    ring, _palette, surface = lit(canvas, 600, 200,
                                  Character(level=0.8, dynamics=0.5))
    section = _section(ring, surface, 600, 200)
    peak = section.index(max(section))
    assert 0 <= peak < 4, "the brightest light is not next to the panel"
    tail = section[peak:]
    assert tail[-1] == 0, "the light does not end, it is cut"
    # Monotone down, over a distance, with no drop in one pixel big enough to
    # read as an end. The two strokes it replaced dropped 58 % of the peak at
    # the core's edge and the remaining 42 % at the halo's; a sixth is well
    # under either.
    assert len(tail) > 20, "the light ends too abruptly to be a falloff"
    assert all(b <= a + 1 for a, b in pairwise(tail))
    steps = [a - b for a, b in pairwise(tail)]
    assert max(steps) <= max(section) / 6, f"a step of {max(steps)} in the falloff"
    # And it keeps moving: a stroke of fixed opacity would show as a long run
    # of one value, which is exactly what the flat halo was.
    moving = [value for value in tail if value > 4]
    assert len(set(moving)) > len(moving) * 0.6, "too much of the falloff is flat"
    ring.destroy()


def test_music_energy_changes_the_beams_spatial_weight(canvas):
    # The line ring pulsed its stroke *widths*; an image cannot, without
    # rebuilding the blur. The same thing is said by how much light there is,
    # because the distance at which a falloff stops being visible moves with how
    # bright it started — so this measures both: the total light on the edge,
    # and how far from the edge it can still be seen.
    #
    # It matters more now than it did. A source hidden behind the panel has no
    # width to pulse even in principle — the panel decides where it stops — so
    # brightness is the only channel reactivity has left. If it did not carry,
    # this design would be a still life.
    from lyrica.meter import Character

    weights = []
    for character in (Character(level=0.0, dynamics=0.0),
                      Character(level=1.0, dynamics=1.0)):
        ring, _palette, surface = lit(canvas, 600, 200, character)
        section = _section(ring, surface, 600, 200)
        weights.append((sum(section), sum(1 for v in section if v > 8)))
        ring.destroy()
    quiet, loud = weights
    assert loud[0] > quiet[0] * 1.5, "the loud border carries no more light"
    assert loud[1] > quiet[1], "the loud border does not reach further"


def test_the_beam_colour_has_a_contrast_floor():
    # Asserted where the floor is actually applied. It used to be read off an
    # entry of the ramp, which meant reading it through whatever opacity that
    # entry carried; the ramp's dim end is *meant* to be transparent, so which
    # entry to ask was a judgement the test should not have been making. That
    # the lit border clears its backdrop is guarded separately, by
    # `test_the_shine_stays_lit_with_no_audio_at_all`.
    from types import SimpleNamespace

    from lyrica.beam import MIN_BEAM_DE, _beam_colour
    from lyrica.glass import delta_e

    back = (40, 40, 40)
    palette = SimpleNamespace(backdrop=back, beam="#282828", sung="#ffffff")
    assert delta_e(back, _beam_colour(palette)) >= MIN_BEAM_DE


def test_an_unknown_style_falls_back_rather_than_failing(monkeypatch):
    from lyrica import config

    monkeypatch.setenv("LYRICA_BEAM", "sparkles")
    assert config.beam_style() == "shine"
    monkeypatch.setenv("LYRICA_BEAM", "shine")
    assert config.beam_style() == "shine"
    monkeypatch.setenv("LYRICA_BEAM", "aurora")
    assert config.beam_style() == "aurora"
    monkeypatch.setenv("LYRICA_BEAM", "off")
    assert config.beam_style() == "off"


# --- the music's character drives the shine ---------------------------------

def _shine(character, canvas):
    """How far the border swings between its lightest and darkest part."""
    ring, palette, surface = lit(canvas, 1125, 375, character, scale=1.25)
    levels = ridge(ring, palette, surface, 1125, 375)
    ring.destroy()
    return max(levels) - min(levels)


def test_a_flat_master_gets_an_even_border(canvas):
    # Where the style of the music shows. A wall of sound has no air in it, and
    # neither should the border round it: measured 0.05 dynamics for a heavily
    # compressed master against 0.94 for an open one.
    from lyrica.meter import Character

    flat = _shine(Character(level=0.6, dynamics=0.05, rate=0.8), canvas)
    open_ = _shine(Character(level=0.6, dynamics=0.90, rate=0.5), canvas)
    assert open_ > flat * 2, f"flat {flat}, open {open_} — the styles look alike"


def test_silence_leaves_it_lit_but_still(canvas):
    from lyrica.meter import Character

    assert _shine(Character(), canvas) < 20


def _crest(canvas, level, width=900, height=320):
    """The pixel at the top edge's crest, as the screen will show it.

    A fixed point rather than the brightest one, so two readings are of the
    same pixel and not of two different ones that each happened to be a peak.
    """
    import colorsys

    from lyrica.meter import Character

    ring, palette, surface = lit(canvas, width, height,
                                 Character(level=level, dynamics=0.5))
    picture, pad = frame(ring, palette, surface, width, height)
    ring.destroy()
    red, green, blue = picture.load()[pad + width // 2, pad - 1]
    return colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)


def _light(canvas, level, width=900, height=320):
    """One strip's own pixels, before anything composes them over a backdrop.

    The composite is the wrong place to ask what colour the light is. Over a
    backdrop with a tint of its own — and the cover wash always has one — a
    faint part of the glow reads as mostly backdrop, so a reading taken there
    measures the wash rather than the border. These are the emitted pixels.
    """
    import numpy as np

    from lyrica.meter import Character

    ring, _palette, _surface = lit(canvas, width, height,
                                   Character(level=level, dynamics=0.5))
    picture = next(np.asarray(ring.light.image(strip, ring._tables))
                   for strip in ring.light.strips if strip.box is not None)
    ring.destroy()
    return picture


def _saturation(picture, mask):
    import colorsys

    red, green, blue = (picture[..., band][mask].mean() for band in range(3))
    return colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)[1]


def test_the_border_keeps_the_cover_s_colour_however_loud_it_gets(canvas):
    # The ramp climbed to `palette.sung`, which is white by design — chroma 8 —
    # so the louder the music the less of the cover was left in the border.
    # Measured over the whole ring: saturation 0.29 in silence against 0.07 at
    # the top of the level range, and since most music sits above 0.6 the
    # border was white nearly all the time. Loud is meant to mean more light,
    # not less colour.
    #
    # Read in the fringe, which is where the colour is kept. The core is whiter
    # on purpose and by a fixed amount — that is the cross-section's shape and
    # `test_the_light_is_whiter_at_its_core_than_at_its_fringe` guards it — so
    # a reading taken at the crest would be measuring that decision instead of
    # this one.
    import colorsys

    from lyrica.beam import _beam_colour

    red, green, blue = _beam_colour(_panel())
    _hue, cover, _value = colorsys.rgb_to_hsv(red / 255, green / 255,
                                              blue / 255)
    for level in (0.1, 1.0):
        picture = _light(canvas, level)
        alpha = picture[..., 3]
        peak = int(alpha.max())
        fringe = (alpha > peak * 0.05) & (alpha < peak * 0.25)
        assert fringe.any(), "the strip has no fringe to read"
        got = _saturation(picture, fringe)
        assert abs(got - cover) < 0.03, (
            f"at level {level} the fringe reads {got:.3f} against the cover's "
            f"{cover:.3f}")


def test_the_border_still_gets_brighter_when_the_music_does(canvas):
    # The guard on the test above: holding saturation still must not be done by
    # holding the whole border still.
    *_, value_quiet = _crest(canvas, 0.1)
    *_, value_loud = _crest(canvas, 1.0)
    assert value_loud > value_quiet * 1.15, (
        f"brightness went {value_quiet:.3f} -> {value_loud:.3f}")


def test_the_light_is_whiter_at_its_core_than_at_its_fringe(canvas):
    # A glow used to be one colour with the falloff applied to its opacity
    # alone, so the blazing crest and the last trace of spill twenty-six pixels
    # out carried the same hue and the same saturation to the digit. Nothing
    # real does that: a source bright enough to blaze at its centre is white
    # there, and its colour survives at the edges where there is less of it.
    # One chroma scaled only in alpha is tinted plastic over a lamp.
    picture = _light(canvas, 0.7)
    alpha = picture[..., 3]
    peak = int(alpha.max())
    core = alpha >= peak * 0.9
    fringe = (alpha > peak * 0.05) & (alpha < peak * 0.25)
    assert core.any() and fringe.any(), "the strip has no falloff to read"
    assert _saturation(picture, core) < _saturation(picture, fringe) - 0.02, (
        f"core {_saturation(picture, core):.3f} against fringe "
        f"{_saturation(picture, fringe):.3f} — the light is one flat chroma "
        "at every distance")


def test_no_frame_leaves_an_edge_showing_the_frame_before(canvas):
    # The one test in this file that paints the way the app does. Every other
    # one goes through `lit`, which paints the ring whole before measuring it —
    # so none of them can see the border the running overlay actually shows.
    #
    # The cap repainted one strip a frame in round-robin while the gradient
    # rotates every frame, so three of the four edges were always a frame or
    # more behind. On a level that jumped it showed: measured 139 of 255
    # between the brightest strip and the dimmest for three frames running,
    # against 57 for the gradient's own swing. A bright bar chasing its way
    # round the panel on every beat.
    from lyrica.beam import Beam
    from lyrica.meter import Character

    palette = _panel()
    ring = Beam(canvas, 900, 320, 18, 1.0, glow=Surface())
    kick = [0.10] * 4 + [0.95] + [0.95 - 0.08 * k for k in range(1, 8)]
    stale = []
    for level in kick:
        ring.advance(1 / 60, Character(level=level, dynamics=0.5, rate=0.4),
                     palette)
        # Nothing advanced in between, so anything this repaints is something
        # the frame itself should already have shown.
        stale.append(ring.light.paint(ring._tables))
    ring.destroy()
    assert stale == [0] * len(kick), (
        f"strips left carrying an older frame: {stale} — the border lights up "
        "a quadrant at a time")


def test_busier_music_turns_it_faster(tk_root):
    # Driven by the onset rate rather than a tempo. Which multiple of the beat
    # that rate counts is not recoverable from loudness, so a ring spinning once
    # per beat would spin at half or double speed about half the time.
    import tkinter as tk

    from lyrica import palette as pal_mod
    from lyrica.beam import Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.meter import Character
    from lyrica.songcolour import NEUTRAL

    palette = pal_mod.for_song(Chrome(ChromeMode.PANEL, "#000", PANEL), NEUTRAL)
    moved = []
    for rate in (0.0, 1.0):
        # A canvas of its own each time, but the session's one root. Building a
        # root per iteration was an earlier fix for tkinter's default-root
        # state; a root that is never torn down mid-session settles that too,
        # and without it Tcl runs out of interpreters on the CI runner.
        ring = Beam(tk.Canvas(tk_root, width=600, height=200), 600, 200, 18,
                    1.0)
        ring.advance(1.0, Character(level=0.5, dynamics=0.5, rate=rate), palette)
        moved.append(ring._phase)
        ring.destroy()
    assert moved[1] > moved[0], "the rate did not reach the rotation"


# --- relaying the ring ------------------------------------------------------

def _ring(canvas, width=900, height=320, scale=1.0, radius=18):
    from lyrica import palette as pal_mod
    from lyrica.beam import Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.songcolour import NEUTRAL

    palette = pal_mod.for_song(Chrome(ChromeMode.PANEL, "#000", PANEL), NEUTRAL)
    return Beam(canvas, width, height, radius, scale), palette


def test_reshaping_reuses_the_items_it_already_has(canvas):
    # What the segment pool was for, on what the ring is made of now. `reshape`
    # runs once per frame of the collapse animation, and the ring is drawn as
    # four images rather than 352 lines, so there is nothing left to pool by
    # count — but the stronger half of the old property still has to hold: the
    # canvas items are never torn down and laid again, whatever the size does.
    # An item created later lands on top of the display list, and the overlay
    # lays the beam *before* the card and the lyrics precisely so it can never
    # cover a word.
    ring, _palette = _ring(canvas)
    before = [strip.item for strip in ring.light.strips]
    seen = set(canvas.find_all())
    for width, height in ((760, 217), (620, 114), (1100, 380), (900, 320)):
        ring.reshape(width, height, 18)
        assert [s.item for s in ring.light.strips] == before, (
            "the ring's items were rebuilt, not reused")
        assert set(canvas.find_all()) == seen, "a reshape left items behind"
    ring.destroy()
    for item in before:
        assert not canvas.type(item)


def test_the_ring_tiles_the_edge_without_overlapping_itself(canvas):
    # The reason the ring is four images and not one per edge plus corners.
    # Every pixel of the light carries partial opacity, so two strips laid over
    # each other compose twice and the overlap reads as a bright band straight
    # across the glow — the same defect as the dashed joins the line ring got
    # when a halo climbed over its neighbour's core, and just as visible.
    ring, _palette = _ring(canvas, 620, 114)
    for width, height in ((900, 320), (620, 114), (1100, 380), (240, 90)):
        ring.reshape(width, height, 18)
        boxes = [strip.box for strip in ring.light.strips if strip.box]
        assert boxes, "the ring drew nothing"
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert (a[2] <= b[0] or b[2] <= a[0]
                        or a[3] <= b[1] or b[3] <= a[1]), f"{a} overlaps {b}"
        # And between them they cover the whole edge, which is the other half
        # of tiling: a gap is a stretch of border that is simply not drawn.
        covered = sum((a[2] - a[0]) * (a[3] - a[1]) for a in boxes)
        band = min(min(width, height) // 2,
                   round(ring.shape.inset + ring.shape.bleed + ring.shape.core + 4))
        assert covered >= width * height - max(0, width - 2 * band) * max(
            0, height - 2 * band)
    ring.destroy()


def test_a_relaid_ring_never_lies_about_what_it_painted(canvas):
    # `_shades` was `_paint`'s only evidence of what the canvas was carrying;
    # `strip.shown` is `paint`'s. Same trap: it is what lets an unchanged table
    # skip the repaint, so a strip whose *fields* were rebuilt under it while
    # its entry was left alone would go on showing the picture drawn for the
    # previous panel size for as long as the music happened not to move.
    #
    # So a reshape must reset it, and the ring must then paint its way back to
    # the truth.
    from lyrica.meter import Character

    ring, palette = _ring(canvas)
    for step, (width, height) in enumerate(
            ((900, 320), (760, 217), (620, 114), (900, 320), (1100, 380))):
        ring.reshape(width, height, 18)
        assert all(strip.shown is None for strip in ring.light.strips), (
            "a reshape left a strip claiming to show the old panel")
        music = Character(level=0.2 + 0.15 * step, dynamics=0.5, rate=0.3)
        for _ in range(len(ring.light.strips)):
            ring.advance(0.0, music, palette)
        for strip in ring.light.strips:
            if strip.box is None:
                continue
            want = bytes(table[key] for key in strip.keys
                         for table in ring._tables)
            assert strip.shown == want, "a strip was left behind by the table"
            assert canvas.itemcget(strip.item, "state") == "normal"
    ring.destroy()


def test_a_relaid_ring_wears_the_presence_the_rest_of_it_wears(canvas):
    # The line ring created fresh segments at the base stroke width, so a ring
    # that grew mid-song carried a few unpulsed segments among pulsed ones
    # until the level next crossed a quartile. The image ring cannot have that
    # defect per *segment*, because every strip reads the same table — but it
    # can have it per *strip*, since only one is repainted a call. The property
    # is the same: once the ring has settled, no part of it is still wearing
    # the weight the music had before the reshape.
    from lyrica.meter import Character

    ring, palette = _ring(canvas, 620, 114)
    quiet = Character(level=0.0, dynamics=0.0, rate=0.0)
    loud = Character(level=1.0, dynamics=1.0, rate=0.3)
    for _ in range(8):
        ring.advance(1 / 60, quiet, palette)
    ring.reshape(900, 320, 18)
    for _ in range(8):
        ring.advance(1 / 60, loud, palette)
    # Then held still, so the ring has a fixed thing to settle on rather than a
    # phase that has moved under it between one reading and the next.
    #
    # One pass over the strips is now all settling takes. It used to be that
    # plus `halo.FINE_AFTER`, because the fields were blurred at a third of the
    # resolution while the panel moved and built again at full resolution once
    # it stopped — so a still panel replaced its own fields a fifth of a second
    # in and invalidated all four strips at once. Nothing is blurred any more
    # and the closed form is evaluated at full resolution the first time, so
    # there is no second build to wait for and no constant to name.
    for _ in range(2 * len(ring.light.strips)):
        ring.advance(0.0, loud, palette)
    for strip in ring.light.strips:
        if strip.box is None:
            continue
        want = bytes(table[key] for key in strip.keys for table in ring._tables)
        assert strip.shown == want
    ring.destroy()


def test_a_bigger_window_does_not_buy_points_it_pays_for_every_resize(canvas):
    # `STRAIGHT_SPACING` is in physical pixels, so before it was scaled a
    # Ctrl+Alt+plus took the default panel from 176 points to 316. That used to
    # be charged on every frame of `advance`; it is charged on every frame of a
    # *resize* now, since the points are drawn into the light's fields and then
    # only looked up. Either way the density has to be constant in the units
    # the design is written in. The corners never depended on the spacing, since
    # they have a fixed point budget of their own.
    from lyrica.beam import CORNER_POINTS

    counts, items = {}, {}
    for scale in (0.6, 1.0, 2.0):
        # The same three things the window scales together: the panel, the
        # corner radius and the light's own shape.
        ring, _palette = _ring(canvas, round(900 * scale),
                               round(320 * scale), scale, round(18 * scale))
        counts[scale] = len(ring._points)
        items[scale] = len(ring.light.strips)
        ring.destroy()
    assert counts[0.6] == counts[1.0] == counts[2.0], counts
    assert counts[1.0] > 4 * CORNER_POINTS, "the straights vanished"
    # And whatever the scale, a frame has the same handful of canvas items to
    # think about, where it used to have two per point.
    assert items[0.6] == items[1.0] == items[2.0] == 4


# --- reading the character off a level ---------------------------------------

def test_a_flat_level_is_not_a_beat():
    # The relative threshold needs an absolute floor under it: a fraction of a
    # tiny range is a tiny number, and a flat tone's own ripple clears it over
    # and over. Measured a rate of 0.56 out of pure noise before that floor.
    import math

    from lyrica.meter import Envelope

    envelope = Envelope()
    for i in range(300):
        envelope.push(0.5 + 0.01 * math.sin(i / 7))
    got = envelope.character(0.5)
    assert got.rate == 0.0
    assert got.dynamics == 0.0


def test_a_compressed_beat_still_reports_its_rate():
    # The point of the relative threshold: a master squashed to a twentieth of
    # a clean track's range still has its beat found.
    import math

    from lyrica.meter import Envelope

    envelope = Envelope()
    period = 60 / 128
    for i in range(300):
        t = i / 60
        envelope.push(0.45 + 0.12 * math.exp(-((t % period) / period) * 9))
    assert envelope.character(0.5).rate > 0.2


def test_an_empty_envelope_reports_nothing():
    from lyrica.meter import Envelope

    got = Envelope().character(0.4)
    assert got.level == 0.4
    assert got.dynamics == 0.0 and got.rate == 0.0
