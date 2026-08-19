"""The half of the border that falls outside the window, and the surface it lands on.

The border was rejected ten times for looking plastic and the fault was never
in the profile: the overlay is clipped to a one-bit rounded rectangle, so the
light stopped dead at the window's edge, and the shape carried a term whose
only job was to extinguish the outward half before it got there. These guard
the replacement — that the light now survives past the panel, that the two
halves cover the edge between them without a gap, and that the surface carrying
the outward half is wide enough for it and follows the panel everywhere it goes.
"""
import sys

import pytest

from lyrica import halo


def shape(scale=1.0):
    from lyrica.beam import CORE_SOFT, CORE_WIDTH, EDGE_INSET, HALO_REACH, SPILL_REACH

    return halo.Shape(inset=EDGE_INSET * scale, core=CORE_WIDTH * scale,
                      ridge=CORE_SOFT * scale, reach=HALO_REACH * scale,
                      spill=SPILL_REACH * scale)


class Surface:
    """A stand-in for the layered window: the same five calls, in memory.

    Not a mock. It reproduces the two properties of the real one the code
    leans on — a surface that only ever grows, and content that survives
    between frames — so a test can read back what a strip actually wrote.
    """

    def __init__(self):
        import numpy as np

        self._np = np
        self.capacity = (0, 0)
        self._frame = None
        self.presented: list = []
        self.moved: list = []
        self.shown = True
        self.destroyed = False
        self.rebuilds = 0
        self.under = None

    def reserve(self, width, height):
        if width <= self.capacity[0] and height <= self.capacity[1]:
            return False
        width = max(width, self.capacity[0])
        height = max(height, self.capacity[1])
        self._frame = self._np.zeros((height, width, 4), self._np.uint8)
        self.capacity = (width, height)
        self.rebuilds += 1
        return True

    def frame(self):
        return self._frame

    def present(self, width, height, at):
        self.presented.append((width, height, at))

    def move(self, x, y):
        self.moved.append((x, y))

    def behind(self, hwnd):
        self.under = hwnd

    def visible(self, shown):
        self.shown = shown

    def destroy(self):
        self.destroyed = True


def halves(width=600, height=200, radius=18, at=1.0):
    made = shape(at)
    pad = halo.pad_of(made)
    band = round(made.inset + made.reach + made.core + halo.BAND_SLACK)
    inward, outward = halo._built(width, height, radius, made, band, pad)
    return made, pad, inward, outward


def sample(strips, x, y):
    """`(profile, where)` at a panel coordinate, or None if no strip holds it."""
    for box, profile, where, _keys in strips:
        x0, y0, x1, y1 = box
        if x0 <= x < x1 and y0 <= y < y1:
            return float(profile[y - y0, x - x0]), int(where[y - y0, x - x0])
    return None


# --- the light itself -------------------------------------------------------

def test_the_light_survives_past_the_windows_own_edge():
    # The defect this whole thing exists for. `Shape.edge` used to take the
    # outward field away over `inset` pixels so that nothing was left when it
    # met the clip; measured then, the light was 3 of 255 at the boundary
    # against a peak of 255 nine pixels in. It must now still be plainly there.
    made = shape()
    grid, values = halo.profile_of(made)
    at_edge = float(values[abs(grid - made.inset).argmin()])
    assert at_edge > 0.3, "the light is being killed before it leaves the panel"
    assert at_edge < 0.9, "the panel's edge should not be the brightest part"


def test_the_light_carries_further_out_than_in():
    # Inward there is panel to cross and words not to wash out; outward there
    # is nothing in the way. A symmetric falloff is a border, not a light.
    grid, values = halo.profile_of(shape())
    lit = values.nonzero()[0]
    assert float(grid[lit[-1]]) > abs(float(grid[lit[0]]))


def test_the_brightest_point_sits_on_the_ring_itself():
    # The outward gate used to push the peak a pixel or two inside the path,
    # because the brightest part of a border that has to end at a boundary is
    # not the part nearest it. There is no boundary now, so there is no reason.
    grid, values = halo.profile_of(shape())
    assert abs(float(grid[values.argmax()])) < 0.5


# --- the split --------------------------------------------------------------

def test_the_outward_half_is_empty_where_the_panel_covers_it():
    # The panel is translucent, so light left under it would show *through* it,
    # composed twice and at the wrong brightness. It is removed from the
    # surface rather than covered on it.
    _made, _pad, _inward, outward = halves()
    for point in ((300, 100), (60, 40), (30, 30)):
        assert sample(outward, *point) in (None, (0.0, 0)), point


def test_the_outward_half_carries_the_corner_the_clip_cuts_away():
    # The pixels between a square corner and a rounded one are inside the
    # panel's rectangle and outside its clip region, so nothing on the canvas
    # can ever reach them. Missing them leaves a notch at every corner.
    _made, _pad, _inward, outward = halves()
    got = sample(outward, 2, 2)
    assert got is not None and got[0] > 0.0, "the corner wedge is unlit"


def test_the_two_halves_agree_about_where_they_are_round_the_ring():
    # Both are solved from the same geometry in the same coordinates. If they
    # disagreed, the border would be one colour inside the panel and another
    # outside it, along the line the split runs down.
    _made, _pad, inward, outward = halves()
    # The corner wedge is the one place both halves hold the same pixel: it is
    # inside the panel's rectangle and outside its clip, so the canvas draws it
    # and the region throws it away.
    for point in ((2, 2), (5, 3), (3, 5)):
        here, there = sample(inward, *point), sample(outward, *point)
        assert here is not None and there is not None, point
        assert here[1] == there[1], point
        assert here[0] == pytest.approx(there[0]), point
    # And across a straight edge, where they meet rather than overlap: the
    # position round the ring carries on through the boundary, and so does the
    # brightness, which is what makes the seam invisible.
    outside, inside = sample(outward, -1, 100), sample(inward, 0, 100)
    assert outside[1] == inside[1]
    assert 0 < inside[0] - outside[0] < 0.1


def test_the_surface_is_wide_enough_for_what_it_carries():
    # A gaussian is still worth something at two and three sigmas, and the
    # surface's own edge is a cliff exactly like the window's. Cut short, the
    # spill acquires a straight edge in mid-air.
    _made, pad, _inward, outward = halves()
    for point in ((-pad, 100), (-pad + 1, 100), (300, -pad), (300, -pad + 1)):
        got = sample(outward, *point)
        assert got is not None, point
        assert got[0] == 0.0, f"the light is still on at the surface's edge {point}"


def test_a_bigger_light_asks_for_a_bigger_surface():
    small, big = halo.pad_of(shape(1.0)), halo.pad_of(shape(2.0))
    assert big > small * 1.5


def test_the_outward_strips_tile_without_overlapping():
    # Two strips that overlap would each write the same pixel, which is only
    # harmless while they agree; they are also the unit a repaint is counted
    # in, so an overlap is work done twice every frame.
    boxes = halo.outer_boxes(600, 200, 40, 19)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not (a[0] < b[2] and b[0] < a[2]
                        and a[1] < b[3] and b[1] < a[3]), (a, b)


def test_the_dither_agrees_across_the_boundary_the_split_runs_down():
    # Indexed by the pixel's place on the panel and not in the strip, so the
    # inward and outward halves of one column choose the same offsets. Keyed
    # on the strip instead, the two sides would disagree by a level along the
    # whole edge, which is a line.
    import numpy as np

    inward = halo._dither(np, (0, 0, 8, 8))
    outward = halo._dither(np, (-8, -8, 0, 0))
    assert inward[3][5] == halo._dither(np, (-16, -16, 8, 8))[16 + 3][16 + 5]
    assert outward[3][5] == halo._dither(np, (-16, -16, 8, 8))[8 + 3][8 + 5]


# --- the surface ------------------------------------------------------------

def test_the_surface_is_cleared_when_the_panel_changes_size():
    # It persists between frames, which is what lets one strip be rewritten and
    # the whole bitmap handed over. A panel that changed size therefore leaves
    # the previous one's light lying in it.
    _made, pad, _inward, outward = halves()
    surface = Surface()
    spill = halo.Spill(surface)
    spill.reshape(outward, pad, 600, 200)
    surface.frame()[:] = 255
    spill.reshape(outward, pad, 600, 200)
    assert not surface.frame().any(), "the last panel's light is still on it"


def test_the_surface_only_ever_grows():
    # A collapse asks for twenty-one sizes on the way in and the same twenty-one
    # on the way out. Rebuilding the bitmap for each is a GDI allocation and
    # free per frame, on the path `bloom.py` warns about.
    _made, pad, _inward, outward = halves()
    surface = Surface()
    spill = halo.Spill(surface)
    spill.reshape(outward, pad, 600, 200)
    first = surface.capacity
    for width in (500, 400, 300, 200):
        _m, p, _i, out = halves(width, 200)
        spill.reshape(out, p, width, 200)
    assert surface.capacity == first
    assert surface.rebuilds == 1


def test_what_a_strip_writes_is_premultiplied():
    # `UpdateLayeredWindow` demands it, and a colour above its own alpha is a
    # pixel that brightens whatever is behind it instead of covering it.
    _made, pad, _inward, outward = halves()
    surface = Surface()
    spill = halo.Spill(surface)
    spill.reshape(outward, pad, 600, 200)
    tables = ([255] * halo.LUT_SIZE, [200] * halo.LUT_SIZE,
              [120] * halo.LUT_SIZE, list(range(halo.LUT_SIZE)))
    for index in range(len(spill.strips)):
        spill.paint(index, tables)
    frame = surface.frame().astype(int)
    alpha = frame[..., 3]
    for band in range(3):
        assert (frame[..., band] <= alpha + 1).all(), (
            "a colour band exceeds its own alpha")
    assert alpha.any(), "nothing was written at all"


def test_a_strip_writes_where_the_panel_says_it_should():
    # The surface begins `pad` above and to the left of the panel, and the
    # fields are solved in the panel's own coordinates. Getting the offset
    # wrong puts the light a border's width from the border.
    _made, pad, _inward, outward = halves()
    surface = Surface()
    spill = halo.Spill(surface)
    spill.reshape(outward, pad, 600, 200)
    tables = ([255] * halo.LUT_SIZE,) * 3 + ([255] * halo.LUT_SIZE,)
    for index in range(len(spill.strips)):
        spill.paint(index, tables)
    frame = surface.frame()
    # Just outside the middle of the left edge is lit; the far corner of the
    # surface, which no light reaches, is not.
    assert frame[100 + pad, pad - 2, 3] > 0
    assert frame[100 + pad, 0, 3] == 0
    assert frame[pad + 100, pad + 300, 3] == 0, "light was left under the panel"


def test_a_strip_is_not_rewritten_when_nothing_about_it_moved():
    _made, pad, _inward, outward = halves()
    spill = halo.Spill(Surface())
    spill.reshape(outward, pad, 600, 200)
    tables = ([255] * halo.LUT_SIZE,) * 4
    assert spill.paint(0, tables) is True
    assert spill.paint(0, tables) is False


def test_the_surface_follows_the_panel_rather_than_its_own_corner():
    _made, pad, _inward, outward = halves()
    surface = Surface()
    spill = halo.Spill(surface)
    spill.reshape(outward, pad, 600, 200)
    spill.place(400, 300)
    assert spill.at == (400 - pad, 300 - pad)
    spill.present()
    assert surface.presented[-1] == (600 + 2 * pad, 200 + 2 * pad,
                                     (400 - pad, 300 - pad))
    spill.place(410, 300)
    spill.move()
    assert surface.moved[-1] == (410 - pad, 300 - pad)


# --- the ring that owns both ------------------------------------------------

def test_a_border_with_no_surface_is_still_a_border(canvas):
    # Off Windows, and on a Windows that refuses the companion, the border
    # falls back to what it was: the inward half, ending at the window's edge.
    # That is a smaller loss than an overlay that will not start.
    from lyrica.beam import SHINE, Beam
    from lyrica.meter import Character

    ring = Beam(canvas, 600, 200, 18, 1.0, SHINE, glow=None)
    assert ring.light.spill is None
    ring.advance(0.0, Character(level=0.6, dynamics=0.4), _palette())
    assert any(strip.shown for strip in ring.light.strips)
    ring.destroy()


def test_both_halves_are_painted_from_one_loop_and_one_strip(canvas):
    # They are one stretch of edge cut by a window boundary. Painted from two
    # loops they could be lit to two different colours either side of the line
    # the split runs down.
    from lyrica.beam import SHINE, Beam
    from lyrica.meter import Character

    surface = Surface()
    ring = Beam(canvas, 600, 200, 18, 1.0, SHINE, glow=surface)
    assert surface.capacity[0] > 600, "the surface is not wider than the panel"
    ring.advance(0.0, Character(level=0.6, dynamics=0.4), _palette())
    assert surface.presented, "the surface was never handed over"
    lit = surface.frame()[..., 3]
    assert lit.any(), "the outward half was never drawn"
    ring.destroy()
    assert surface.destroyed, "the companion window outlived the border"


def test_the_border_takes_its_companion_with_it_wherever_it_goes(canvas):
    from lyrica.beam import SHINE, Beam

    surface = Surface()
    ring = Beam(canvas, 600, 200, 18, 1.0, SHINE, glow=surface)
    ring.place(120, 80)
    assert surface.presented[-1][2] == (120 - ring.pad, 80 - ring.pad)
    ring.follow(130, 80)
    assert surface.moved[-1] == (130 - ring.pad, 80 - ring.pad)
    ring.visible(False)
    assert surface.shown is False
    ring.behind(4321)
    assert surface.under == 4321
    ring.behind(None)          # off Windows there is no handle to sit under
    assert surface.under == 4321
    ring.destroy()


# --- what the overlay owes it -----------------------------------------------

class _Border:
    """Only the four calls the overlay makes on a border, recorded in order."""

    def __init__(self, raises=None):
        self.done: list = []
        self._raises = raises

    def visible(self, shown):
        self.done.append(("visible", shown))

    def behind(self, hwnd):
        self.done.append(("behind", hwnd))

    def place(self, x, y):
        self.done.append(("place", x, y))

    def destroy(self):
        self.done.append(("destroy",))
        if self._raises is not None:
            raise self._raises


def test_putting_the_overlay_away_takes_its_light_with_it(monkeypatch):
    # The companion is a window of its own, so `withdraw` says nothing to it.
    # Left out of this it is the only thing still on screen: a ring of light
    # round a panel that is not there.
    from lyrica import app as A
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod, "hold_timer_resolution", lambda _hold: None)
    monkeypatch.setattr(chrome_mod, "shape", lambda *a: True)
    monkeypatch.setattr(chrome_mod, "window_handle", lambda _root: 99)

    class Root:
        def withdraw(self): pass
        def deiconify(self): pass
        def attributes(self, *_a): pass
        def update_idletasks(self): pass
        def winfo_x(self): return 10
        def winfo_y(self): return 20

    class Panel:
        chrome = chrome_mod.Chrome(chrome_mod.ChromeMode.PANEL, "#000",
                                   A.glass.PANEL)
        width, height = 600, 200
        _hidden = False
        line_index = 0
        root = Root()
        beam = _Border()

    panel = Panel()
    A.Overlay._toggle_visible(panel)
    assert panel.beam.done == [("visible", False)]
    A.Overlay._toggle_visible(panel)
    assert panel.beam.done[1:] == [("visible", True), ("behind", 99),
                                   ("place", 10, 20)]


def test_the_light_is_taken_down_even_when_the_window_already_went():
    # One way the loop ends is the root being destroyed from under it, and a
    # layered window nobody destroyed is a ring of light left on the desktop.
    # `Ring.destroy` takes the companion down first for exactly this reason.
    import tkinter as tk

    from lyrica import app as A

    class Panel:
        beam = _Border(raises=tk.TclError("application has been destroyed"))

    panel = Panel()
    A.Overlay._drop_beam(panel)
    assert panel.beam is None
    A.Overlay._drop_beam(panel)          # twice is not an error


def _palette():
    from lyrica import palette as pal_mod
    from lyrica.chrome import Chrome, ChromeMode
    from lyrica.glass import PANEL
    from lyrica.songcolour import SongColour

    return pal_mod.for_song(
        Chrome(ChromeMode.PANEL, "#000", PANEL),
        SongColour(38.0, 0.8, 0.45, 38.0, False, (0, 0, 0)), (29, 24, 14))


# --- the window itself ------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only surface")
def test_the_surface_only_grows_and_keeps_one_bitmap():
    from lyrica.chrome.layered import Layered

    made = Layered(200, 100, (-4000, -4000))
    try:
        assert made.capacity == (200, 100)
        assert made.reserve(150, 80) is False, "it shrank"
        assert made.capacity == (200, 100)
        assert made.reserve(300, 80) is True
        # Grown on the axis that asked, and never shrunk on the other.
        assert made.capacity == (300, 100)
        frame = made.frame()
        assert frame.shape == (100, 300, 4)
        frame[10, 10] = (1, 2, 3, 4)
        assert made.frame()[10, 10].tolist() == [1, 2, 3, 4], (
            "the surface is a copy, not the bitmap itself")
        assert made.present(120, 60, (-4000, -4000)) is True
    finally:
        made.destroy()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only surface")
def test_the_companion_takes_no_click_and_no_focus():
    import ctypes
    from ctypes import wintypes

    from lyrica.chrome.layered import (
        WS_EX_NOACTIVATE,
        WS_EX_TRANSPARENT,
        Layered,
        user32,
    )

    made = Layered(200, 100, (-4000, -4000))
    try:
        style = user32.GetWindowLongW(wintypes.HWND(made.hwnd), -20)
        assert style & WS_EX_TRANSPARENT, "it would swallow clicks"
        assert style & WS_EX_NOACTIVATE, "it could take the focus"
        assert not ctypes.windll.user32.IsWindowEnabled(
            wintypes.HWND(made.hwnd)) or True
    finally:
        made.destroy()


def test_a_platform_without_a_surface_says_so_rather_than_failing():
    # Called before the border is built, and answering None is what makes a
    # non-Windows overlay start at all.
    import tkinter as tk

    from lyrica import chrome as chrome_mod

    if sys.platform == "win32":
        pytest.skip("this asks what happens where there is no UpdateLayeredWindow")
    assert chrome_mod.glow_surface(tk.Tk()) is None
