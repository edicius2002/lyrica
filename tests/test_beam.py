"""The path the border light travels, and the meter that drives it (offline)."""
import math
import sys

import pytest

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

def _lit(style, level, canvas):
    """What every segment of a ring would be lit to, as brightness 0..255."""
    from lyrica import palette as pal_mod
    from lyrica.beam import Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL, rgb_of
    from lyrica.songcolour import SongColour

    palette = pal_mod.for_song(
        Chrome(ChromeMode.PANEL, "#000", PANEL),
        SongColour(38.0, 0.8, 0.45, 38.0, False, (0, 0, 0)), (29, 24, 14))
    ring = Beam(canvas, 600, 200, 18, 1.0, style)
    ring.advance(0.0, level, palette)
    levels = [max(rgb_of(s)) for s in ring._shades]
    ring.destroy()
    return levels


def test_the_comet_leaves_most_of_the_ring_dark(canvas):
    # A travelling light needs somewhere dark to travel through; that is the
    # whole difference between a comet and a lit border.
    from lyrica.beam import COMET

    levels = _lit(COMET, 1.0, canvas)
    dark = sum(1 for v in levels if v < 40)
    assert dark > len(levels) * 0.7, "too much of the ring is lit to read as a comet"
    assert max(levels) > 200, "the head is not bright"


def test_the_shine_lights_every_edge_at_once(canvas):
    # Asked for as the quieter alternative: constant across all the borders,
    # with the colour moving rather than a bright spot.
    from lyrica.beam import SHINE

    levels = _lit(SHINE, 1.0, canvas)
    assert min(levels) > 50, "part of the border went dark"
    assert max(levels) - min(levels) > 30, "nothing moves through it"


def test_the_shine_stays_lit_with_no_audio_at_all(canvas):
    # There often is none — playback can be rendered on another device
    # entirely — so silence must not put the border out.
    from lyrica.beam import SHINE

    assert min(_lit(SHINE, 0.0, canvas)) > 20


def test_the_ring_has_a_crisp_core_and_a_wider_field(canvas):
    from lyrica.beam import SHINE, Beam

    ring = Beam(canvas, 600, 200, 18, 1.0, SHINE)
    assert len(ring._items) == len(ring._halo_items) > 0
    core = float(canvas.itemcget(ring._items[0], "width"))
    halo = float(canvas.itemcget(ring._halo_items[0], "width"))
    assert halo >= core * 2
    ring.destroy()


def test_music_energy_changes_the_beams_spatial_weight(canvas):
    from lyrica import palette as pal_mod
    from lyrica.beam import SHINE, Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.meter import Character
    from lyrica.songcolour import NEUTRAL

    palette = pal_mod.for_song(Chrome(ChromeMode.PANEL, "#000", PANEL), NEUTRAL)
    ring = Beam(canvas, 600, 200, 18, 1.0, SHINE)
    ring.advance(0.0, Character(level=0.0, dynamics=0.0), palette)
    quiet = float(canvas.itemcget(ring._halo_items[0], "width"))
    # Over frames rather than in one, because the width follows the level
    # through an envelope now: a level that jumps from silence to a peak used
    # to cross three quantised steps in a single frame, which is a snap where
    # a swell was wanted. `dt=0` therefore no longer means "at once" — it means
    # no time has passed, and nothing that answers over time can have answered.
    loud = quiet
    for _ in range(12):
        ring.advance(1 / 30, Character(level=1.0, dynamics=1.0), palette)
        loud = float(canvas.itemcget(ring._halo_items[0], "width"))
    assert loud > quiet
    ring.destroy()


def test_the_beam_colour_has_a_contrast_floor():
    from types import SimpleNamespace

    from lyrica.beam import COLOUR_STOP, MIN_BEAM_DE, _gradient
    from lyrica.glass import delta_e, rgb_of

    back = (40, 40, 40)
    palette = SimpleNamespace(backdrop=back, beam="#282828", sung="#ffffff")
    gradient = _gradient(palette)
    middle = rgb_of(gradient[round((len(gradient) - 1) * COLOUR_STOP)])
    assert delta_e(back, middle) >= MIN_BEAM_DE - 1


def test_aurora_uses_several_neighbouring_hues(canvas):
    from lyrica import palette as pal_mod
    from lyrica.beam import AURORA, Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.meter import Character
    from lyrica.songcolour import NEUTRAL

    palette = pal_mod.for_song(Chrome(ChromeMode.PANEL, "#000", PANEL), NEUTRAL)
    ring = Beam(canvas, 600, 200, 18, 1.0, AURORA)
    ring.advance(0.5, Character(level=0.7, dynamics=0.7, rate=0.5), palette)
    assert len(set(ring._shades)) > 8
    ring.destroy()


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
    from lyrica import palette as pal_mod
    from lyrica.beam import SHINE, Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL, rgb_of
    from lyrica.songcolour import SongColour

    palette = pal_mod.for_song(
        Chrome(ChromeMode.PANEL, "#000", PANEL),
        SongColour(38.0, 0.8, 0.45, 38.0, False, (0, 0, 0)), (29, 24, 14))
    ring = Beam(canvas, 1125, 375, 18, 1.25, SHINE)
    ring.advance(0.0, character, palette)
    levels = [max(rgb_of(s)) for s in ring._shades]
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


def test_busier_music_turns_it_faster(tk_root):
    # Driven by the onset rate rather than a tempo. Which multiple of the beat
    # that rate counts is not recoverable from loudness, so a ring spinning once
    # per beat would spin at half or double speed about half the time.
    import tkinter as tk

    from lyrica import palette as pal_mod
    from lyrica.beam import SHINE, Beam
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
                    1.0, SHINE)
        ring.advance(1.0, Character(level=0.5, dynamics=0.5, rate=rate), palette)
        moved.append(ring._phase)
        ring.destroy()
    assert moved[1] > moved[0], "the rate did not reach the rotation"


def test_the_comet_ignores_the_character(tk_root):
    # Only the shine reads it. The comet's whole shape is a travelling head, and
    # varying its speed with the music would fight the thing you follow.
    import tkinter as tk

    from lyrica import palette as pal_mod
    from lyrica.beam import COMET, Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.meter import Character
    from lyrica.songcolour import NEUTRAL

    palette = pal_mod.for_song(Chrome(ChromeMode.PANEL, "#000", PANEL), NEUTRAL)
    phases = []
    for rate in (0.0, 1.0):
        ring = Beam(tk.Canvas(tk_root, width=600, height=200), 600, 200, 18,
                    1.0, COMET)
        ring.advance(1.0, Character(level=0.5, dynamics=0.5, rate=rate), palette)
        phases.append(ring._phase)
        ring.destroy()
    assert phases[0] == phases[1]


# --- relaying the ring ------------------------------------------------------

def _ring(canvas, style, width=900, height=320, scale=1.0, radius=18):
    from lyrica import palette as pal_mod
    from lyrica.beam import Beam
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.songcolour import NEUTRAL

    palette = pal_mod.for_song(Chrome(ChromeMode.PANEL, "#000", PANEL), NEUTRAL)
    return Beam(canvas, width, height, radius, scale, style), palette


def test_reshaping_moves_the_segments_it_already_has(canvas):
    # `reshape` runs once per frame of the collapse animation. Rebuilding the
    # ring there spent 352 deletes and 352 creates a frame to put items back
    # where they already were; the segment count barely moves between
    # consecutive frames, so the items are reused and only the difference is
    # created or deleted.
    from lyrica.beam import SHINE

    ring, _palette = _ring(canvas, SHINE)
    before = list(ring._items), list(ring._halo_items)
    ring.reshape(760, 217, 18)
    kept = len(ring._items)
    assert kept < len(before[0]), "the smaller ring should need fewer segments"
    assert ring._items == before[0][:kept], "the cores were rebuilt, not moved"
    assert ring._halo_items == before[1][:kept], "the halos were rebuilt"
    # And the ones that went are really gone from the canvas, not merely
    # dropped from the list.
    for item in before[0][kept:] + before[1][kept:]:
        assert not canvas.type(item)
    ring.destroy()


def test_a_relaid_ring_keeps_every_halo_under_every_core(canvas):
    # The reason `reshape` creates all the halos before any of the cores: a
    # halo is three times the width of a core, so one sitting above its
    # neighbour's core covers the end of it and a continuous gradient reads as
    # a dashed line at every join. Items created to grow the ring land on top
    # of the display list, which would put the new halos over the old cores.
    from lyrica.beam import SHINE

    ring, _palette = _ring(canvas, SHINE, 620, 114)
    for width, height in ((900, 320), (620, 114), (1100, 380)):
        ring.reshape(width, height, 18)
        order = {item: index for index, item in enumerate(canvas.find_all())}
        halos = [order[item] for item in ring._halo_items]
        cores = [order[item] for item in ring._items]
        assert max(halos) < min(cores), "a halo climbed above a core"
        assert halos == sorted(halos) and cores == sorted(cores)
    ring.destroy()


def test_a_relaid_ring_never_lies_about_what_it_painted(canvas):
    # `_shades` and `_halo_shades` are `_paint`'s only evidence of what the
    # canvas is carrying: an unchanged core shade is taken as proof the halo is
    # unchanged too, and the halo write is skipped. A reused segment keeps its
    # fill, so its entry stays true — but a segment whose fill was reset
    # without its entry being reset would keep that stale halo for good, which
    # is the defect the sentinel seeding was introduced to close.
    from lyrica.beam import HALO_KEEP, SHINE, _mix
    from lyrica.glass import rgb_of
    from lyrica.meter import Character

    ring, palette = _ring(canvas, SHINE)
    for step, (width, height) in enumerate(
            ((900, 320), (760, 217), (620, 114), (900, 320), (1100, 380))):
        ring.reshape(width, height, 18)
        ring.advance(1 / 60, Character(level=0.2 + 0.2 * step, dynamics=0.5,
                                       rate=0.3), palette)
        for index, core in enumerate(ring._items):
            assert canvas.itemcget(core, "fill") == ring._shades[index]
            want = _mix(tuple(palette.backdrop),
                        rgb_of(ring._shades[index]), HALO_KEEP)
            assert canvas.itemcget(ring._halo_items[index], "fill") == want, (
                "the halo was left behind by its core")
    ring.destroy()


def test_a_grown_ring_wears_the_pulse_the_rest_of_it_wears(canvas):
    # Segments are created at the base width, so a ring that grew mid-song
    # would carry a few unpulsed segments among pulsed ones until the level
    # next happened to cross a quartile.
    from lyrica.beam import SHINE
    from lyrica.meter import Character

    ring, palette = _ring(canvas, SHINE, 620, 114)
    loud = Character(level=1.0, dynamics=1.0, rate=0.3)
    ring.advance(1 / 60, loud, palette)
    ring.reshape(900, 320, 18)
    ring.advance(1 / 60, loud, palette)
    assert len({canvas.itemcget(i, "width") for i in ring._items}) == 1
    assert len({canvas.itemcget(i, "width") for i in ring._halo_items}) == 1
    ring.destroy()


def test_a_bigger_window_does_not_buy_segments_it_pays_for_every_frame(canvas):
    # `STRAIGHT_SPACING` is in physical pixels, so before it was scaled a
    # Ctrl+Alt+plus took the default panel from 176 segments to 316 and then
    # charged for them on every frame of `advance` for the rest of the
    # session — measured 2.7 ms a frame against 1.7 for the shine, and 5.3
    # against 3.3 for the comet, at 60 Hz and a 16 ms budget. The density is
    # constant in design units instead; the corners never depended on the
    # spacing, since they have a fixed point budget of their own.
    from lyrica.beam import CORNER_POINTS, SHINE

    counts = {}
    for scale in (0.6, 1.0, 2.0):
        # The same three things the window scales together: the panel, the
        # corner radius and the ring's own thickness.
        ring, _palette = _ring(canvas, SHINE, round(900 * scale),
                               round(320 * scale), scale, round(18 * scale))
        counts[scale] = len(ring._items)
        ring.destroy()
    assert counts[0.6] == counts[1.0] == counts[2.0], counts
    assert counts[1.0] > 4 * CORNER_POINTS, "the straights vanished"


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
