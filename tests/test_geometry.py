"""Resizing and collapsing: the things that only break when sizes change.

All six of these came out of review rather than use, and they share a cause —
the panel has two sizes, and code written for one of them assumed the other.
"""
import itertools
import math
import sys

import pytest

from lyrica import motion


def test_the_panel_does_not_resize_on_the_curve_a_lyric_scrolls_on():
    # The scroll curve is steep on purpose: over the 50 px a row travels, the
    # sharp middle reads as weight. Over the 800 px the window changes width by,
    # the same curve put 89 % of the change into 40 % of the frames.
    def jumps(curve):
        steps = [motion.cubic_bezier(i / 10, curve) for i in range(11)]
        return [b - a for a, b in itertools.pairwise(steps)]

    assert max(jumps(motion.RESIZE_CURVE)) < max(jumps(motion.SCROLL_CURVE)) / 2


def test_the_resize_curve_still_settles_at_both_ends():
    # Gentler in the middle must not mean linear: it should still ease in and
    # ease out, or the panel starts and stops abruptly instead.
    early = motion.cubic_bezier(0.05, motion.RESIZE_CURVE)
    late = motion.cubic_bezier(0.95, motion.RESIZE_CURVE)
    assert early < 0.05 and late > 0.95
    assert motion.cubic_bezier(0.0, motion.RESIZE_CURVE) == 0.0
    assert motion.cubic_bezier(1.0, motion.RESIZE_CURVE) == 1.0


class FakeScreen:
    """Everything `desktop_bounds` asks a root for. No interpreter needed."""

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080


class FakeDesktop:
    """A second monitor to the left of the primary one, so the origin is
    negative — the case a naive (0, 0, w, h) rect gets wrong."""

    @staticmethod
    def desktop_bounds():
        return (-1920, 0, 3840, 1080)

    @staticmethod
    def monitor_bounds(x, y):
        return (-1920, 0, 1920, 1080) if x < 0 else (0, 0, 1920, 1080)


def test_the_platform_beats_what_tk_can_see(monkeypatch):
    # Tk only knows the primary screen, and clamping to it dragged the panel
    # back from a second monitor on every collapse.
    import lyrica.chrome.windows  # noqa: F401  (so the stub replaces it)
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod.sys, "platform", "win32")
    monkeypatch.setattr(chrome_mod, "windows", FakeDesktop, raising=False)
    assert chrome_mod.desktop_bounds(FakeScreen()) == (-1920, 0, 3840, 1080)


def test_the_desktop_falls_back_to_the_screen_without_the_platform(monkeypatch):
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod.sys, "platform", "linux")
    assert chrome_mod.desktop_bounds(FakeScreen()) == (0, 0, 1920, 1080)


def test_monitor_bounds_follow_the_point_on_a_secondary_screen(monkeypatch):
    import lyrica.chrome.windows  # noqa: F401
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod.sys, "platform", "win32")
    monkeypatch.setattr(chrome_mod, "windows", FakeDesktop, raising=False)
    assert chrome_mod.monitor_bounds(FakeScreen(), -800, 400) \
        == (-1920, 0, 1920, 1080)


def test_monitor_bounds_fall_back_to_primary_screen(monkeypatch):
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod.sys, "platform", "linux")
    assert chrome_mod.monitor_bounds(FakeScreen(), 2500, 400) \
        == (0, 0, 1920, 1080)


def test_resize_keeps_its_centre_on_the_left_monitor(monkeypatch):
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod, "monitor_bounds",
                        lambda _root, _x, _y: (-1920, 0, 1920, 1080))
    assert chrome_mod.position_in_monitor(
        FakeScreen(), (-800, 400), (1125, 400)) == (-1362, 200)


def test_resize_is_clamped_to_the_same_monitor_not_the_primary(monkeypatch):
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod, "monitor_bounds",
                        lambda _root, _x, _y: (1920, -200, 2560, 1440))
    assert chrome_mod.position_in_monitor(
        FakeScreen(), (2100, -100), (1125, 400)) == (1920, -200)


def test_keyboard_resize_grows_from_the_last_horizontal_centre_and_top(monkeypatch):
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod, "monitor_bounds",
                        lambda _root, _x, _y: (-1920, 0, 1920, 1080))
    place = (-800, 150)
    assert chrome_mod.place_in_monitor(
        FakeScreen(), place, (1125, 400), (-800, 310)) == (-1362, 150)
    assert chrome_mod.place_in_monitor(
        FakeScreen(), place, (900, 320), (-800, 350)) == (-1250, 150)


def test_last_place_survives_a_temporary_edge_clamp(monkeypatch):
    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod, "monitor_bounds",
                        lambda _root, _x, _y: (1920, 0, 1920, 1080))
    place = (2020, 900)
    assert chrome_mod.place_in_monitor(
        FakeScreen(), place, (1125, 400), (2100, 950)) == (1920, 680)
    # Shrinking derives from the original chosen place again, rather than from
    # the centre of the clamped large window.
    assert chrome_mod.place_in_monitor(
        FakeScreen(), place, (450, 160), (2100, 880)) == (1920, 900)


def test_tk_geometry_uses_valid_signs_for_negative_monitor_coordinates():
    from lyrica import chrome as chrome_mod

    assert chrome_mod.geometry(900, 320, -1362, 200) == "900x320-1362+200"
    assert chrome_mod.geometry(900, 320, 2200, -120) == "900x320+2200-120"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows monitor API")
def test_windows_can_resolve_a_real_monitor_from_a_desktop_point():
    from lyrica.chrome import windows

    left, top, width, height = windows.monitor_bounds(0, 0)
    assert width > 0 and height > 0
    assert left <= 0 <= left + width
    assert top <= 0 <= top + height


def test_the_remembered_place_is_the_middle_across_and_the_top_down(tmp_path,
                                                                    monkeypatch):
    # Two anchors because the window's two sizes preserve two different things:
    # a collapse holds the horizontal middle, and holds the *top* edge rather
    # than the vertical middle so the card does not move. A vertical centre
    # saved while compact belonged to a 114 px window.
    from lyrica import config

    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))
    config.save_place(900, 400)
    assert config.saved_place() == (900, 400)

    # Reopening at either height puts the top back where it was left.
    for height in (114, 375):
        assert config.saved_place()[1] == 400, f"moved at height {height}"


def test_an_older_saved_centre_is_still_honoured(tmp_path, monkeypatch):
    # Written before the vertical anchor was corrected. An upgrade must not
    # throw away the position the overlay already had.
    from lyrica import config

    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))
    config.save_setting("centre", [800, 500])
    assert config.saved_place() is None
    assert config.saved_centre() == (800, 500)


def test_drag_remembers_the_last_requested_position_not_stale_tk_geometry(
        tmp_path, monkeypatch):
    from lyrica import config
    from lyrica.app import Overlay

    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))

    class StaleRoot:
        def winfo_x(self): return 100
        def winfo_y(self): return 80

    panel = Overlay.__new__(Overlay)
    panel.root = StaleRoot()
    panel.width = 900
    # Native movement is asynchronous; this is the exact last position asked
    # for even if Tk still reports the preceding frame.
    panel._drag_at = (-1400, 220)

    panel._remember_where()

    assert panel._place_anchor == (-950, 220)
    assert config.saved_place() == (-950, 220)


class ArtStub:
    """Just the parts of the overlay that decide whether late art is kept."""

    def __init__(self):
        self.fetch_gen = 3
        self._shape_gen = 0
        self._pending_art = None
        self._cover_data = b"bytes"

    def _build_art(self, data):
        return ("thumb", "backdrop", data)


def test_art_built_for_the_old_size_is_dropped():
    # The worker reads the window's size while it builds. A resize in that gap
    # left it holding images for the size before, and it used to overwrite the
    # correctly-sized ones the resize had just built.
    from lyrica.app import Overlay

    panel = ArtStub()
    shape = panel._shape_gen
    Overlay._reshape_art(panel)                      # the resize rebuilds
    fresh = panel._pending_art
    Overlay._offer_art(panel, 3, shape, ("stale", None, b"old size"))
    assert panel._pending_art is fresh


def test_art_from_a_track_that_already_changed_is_dropped():
    from lyrica.app import Overlay

    panel = ArtStub()
    panel.fetch_gen = 4
    Overlay._offer_art(panel, 3, panel._shape_gen, ("late", None, b"old track"))
    assert panel._pending_art is None


def test_art_that_is_still_current_is_taken():
    from lyrica.app import Overlay

    panel = ArtStub()
    art = ("thumb", None, b"current")
    Overlay._offer_art(panel, 3, panel._shape_gen, art)
    assert panel._pending_art is art


def test_a_line_moves_to_the_new_centre_when_the_window_widens(tk_root):
    # Every character's x is computed once, from the centre the window had when
    # the line was built, and a line only ever moves vertically afterwards. A
    # panel coming back out of its compact size left the lyrics off to one side.
    import tkinter as tk

    from lyrica.lineview import LineView
    from lyrica.palette import DEFAULT

    def centre(view):
        return (min(s for s, _ in view._row_spans)
                + max(e for _, e in view._row_spans)) / 2

    view = LineView(tk.Canvas(tk_root, width=900, height=200), 394, 40.0,
                    "y no me digas que ya no me quieres", [],
                    font=("Segoe UI", 20), wrap=700, palette=DEFAULT)
    assert abs(centre(view) - 394) < 1
    view.recentre(504)
    assert abs(centre(view) - 504) < 1
    view.recentre(504)                      # idempotent
    assert abs(centre(view) - 504) < 1
    view.destroy()


@pytest.mark.parametrize("scale", [0.6, 1.0, 2.0])
def test_full_growth_stays_inside_the_horizontal_box_at_every_scale(tk_root,
                                                                     scale):
    import tkinter as tk

    from lyrica.lineview import LineView
    from lyrica.palette import DEFAULT

    width = round(900 * scale)
    text = "wide words keep every glowing letter inside the lyric window"
    words = [(0.0, 1.0, word) for word in text.split()]
    canvas = tk.Canvas(tk_root, width=width, height=round(320 * scale))
    view = LineView(
        canvas, width / 2, 40, text, words,
        font=("Segoe UI", round(-30 * scale), "bold"),
        wrap=round(760 * scale), palette=DEFAULT, scale=scale, growth=0.14)
    try:
        for row, (left, right) in enumerate(view._row_spans):
            guard = sum(view._piece_widths[p] for p in view._row_pieces[row]) \
                * view.growth / 2
            assert left - guard - view.effect_padding >= 0
            assert right + guard + view.effect_padding <= width
    finally:
        view.destroy()
        canvas.destroy()


@pytest.mark.parametrize("scale", [0.6, 1.0, 2.0])
def test_a_transition_is_clamped_by_complete_glyph_bounds(tk_root, scale):
    import tkinter as tk

    from lyrica import app as A
    from lyrica.lineview import LineView
    from lyrica.palette import DEFAULT

    height = round(A.HEIGHT * scale)
    canvas = tk.Canvas(tk_root, width=round(A.WIDTH * scale), height=height)
    view = LineView(
        canvas, round(A.WIDTH * scale) / 2, height, "whole letters", [],
        font=("Segoe UI", round(-30 * scale), "bold"),
        wrap=round(A.WRAP * scale), palette=DEFAULT, scale=scale, growth=0.14)

    class Panel:
        pass

    panel = Panel()
    panel.height = height
    try:
        for wanted in (-1000, height / 2, height + 1000):
            view.move_to(A.Overlay._safe_view_y(panel, view, wanted))
            top, bottom = view.visual_vertical_span()
            assert 0 <= top <= bottom <= height
    finally:
        view.destroy()
        canvas.destroy()


def test_active_lyrics_and_the_card_stay_above_inactive_lines(overlay):
    from lyrica.lyrics import Lyrics

    lyrics = Lyrics(
        lines=[(0.0, "line before"), (3.0, "line illuminated"),
               (6.0, "line entering")],
        words=[[], [], []], synced=True)
    overlay.lyrics = lyrics
    overlay._go_to_line(1, lyrics)

    stack = {item: position
             for position, item in enumerate(overlay.canvas.find_all())}
    active = list(overlay._views[1].item_ids())
    inactive = [item for index, view in overlay._views.items() if index != 1
                for item in view.item_ids()]
    assert min(stack[item] for item in active) > max(stack[item] for item in inactive)
    assert min(stack[item] for item in (overlay._title_item, overlay._artist_item)) \
        > max(stack[item] for view in overlay._views.values()
              for item in view.item_ids())


def test_a_real_wrapped_next_line_is_visible_below_the_current_one(overlay):
    from lyrica.lyrics import Lyrics

    wrapped = ("upcoming wrapped context remains visible below the current "
               "line throughout the preview before its natural rise")
    lyrics = Lyrics(
        lines=[(0.0, "short current row"), (3.0, wrapped),
               (6.0, "following row")],
        words=[[], [], []], synced=True)

    overlay.lyrics = lyrics
    overlay._go_to_line(0, lyrics, animate=False)

    incoming = overlay._views[1]
    assert incoming.height > incoming.line_height
    assert overlay._visibility(incoming) > 0.0
    assert incoming._visible is True


def test_a_wrapped_upcoming_row_remains_visible_at_the_lower_safe_edge():
    from lyrica.app import Overlay

    class Chrome:
        @staticmethod
        def px(value):
            return value

    class View:
        y = 236.0
        height = 70
        effect_padding = 14
        glyph_padding = 3

        def visual_vertical_span(self):
            return (self.y - self.effect_padding,
                    self.y + self.height + self.effect_padding)

        def glyph_vertical_span(self):
            return (self.y - self.glyph_padding,
                    self.y + self.height + self.glyph_padding)

    panel = Overlay.__new__(Overlay)
    panel.chrome = Chrome()
    panel.height = 320
    panel._content_top = 88

    # The halo touches y=320, but the grown letters end at y=309 and should be
    # shown as the next-line preview rather than hidden wholesale.
    assert View().visual_vertical_span()[1] == panel.height
    assert panel._visibility(View()) == pytest.approx(11 / 42)


def test_the_upper_card_boundary_still_counts_the_complete_halo():
    from lyrica.app import Overlay

    class Chrome:
        @staticmethod
        def px(value):
            return value

    class View:
        y = 102.0
        height = 70
        effect_padding = 14
        glyph_padding = 3

        def visual_vertical_span(self):
            return (self.y - self.effect_padding,
                    self.y + self.height + self.effect_padding)

        def glyph_vertical_span(self):
            return (self.y - self.glyph_padding,
                    self.y + self.height + self.glyph_padding)

    panel = Overlay.__new__(Overlay)
    panel.chrome = Chrome()
    panel.height = 320
    panel._content_top = 88

    assert View().visual_vertical_span()[0] == panel._content_top
    assert panel._visibility(View()) == 0.0


@pytest.mark.parametrize("line_height", [32, 35, 41])
def test_one_to_wrapped_layout_shows_the_incoming_row_below(line_height):
    from lyrica.app import Overlay

    class Chrome:
        @staticmethod
        def px(value):
            return value

    class View:
        def __init__(self, rows):
            self.line_height = line_height
            self.height = line_height * rows
            self.effect_padding = math.ceil(11 + line_height * 0.14 / 2)
            self.glyph_padding = math.ceil(line_height * 0.14 / 2)
            self.y = 0.0

        def visual_vertical_span(self):
            return (self.y - self.effect_padding,
                    self.y + self.height + self.effect_padding)

        def glyph_vertical_span(self):
            return (self.y - self.glyph_padding,
                    self.y + self.height + self.glyph_padding)

    panel = Overlay.__new__(Overlay)
    panel.chrome = Chrome()
    panel.height = 320
    panel.anchor_y = 176
    panel.row_gap = 50
    panel._content_top = 88
    panel.line_index = 0
    panel._views = {0: View(1), 1: View(2)}

    targets = panel._row_targets([0, 1])
    incoming = panel._views[1]
    incoming.y = targets[1]

    assert targets[0] == 176
    assert incoming.visual_vertical_span()[1] <= panel.height
    assert panel._visibility(incoming) > 0.0


def test_staggered_multiline_exit_never_crosses_the_new_active_line():
    from lyrica.app import Overlay

    class View:
        def __init__(self, y):
            self.y = float(y)
            self.height = 60       # two lyric rows
            self.effect_padding = 10
            self.glyph_padding = 3

        def move_to(self, y):
            self.y = float(y)

        def visual_vertical_span(self):
            return (self.y - self.effect_padding,
                    self.y + self.height + self.effect_padding)

        def glyph_vertical_span(self):
            return (self.y - self.glyph_padding,
                    self.y + self.height + self.glyph_padding)

    class Glide:
        done = False

        def __init__(self, offset):
            self._offset = offset

        def offset(self):
            return self._offset

    panel = Overlay.__new__(Overlay)
    panel.line_index = 1
    panel._views = {0: View(0), 1: View(0)}
    panel._targets = {0: 20.0, 1: 130.0}
    # The active row has advanced much further through its shorter glide. Raw,
    # the outgoing row would end at 170 while the active row starts at 140.
    panel._glides = {0: Glide(80.0), 1: Glide(20.0)}

    assert panel._advance_glides()

    outgoing_bottom = panel._views[0].glyph_vertical_span()[1]
    active_top = panel._views[1].glyph_vertical_span()[0]
    assert outgoing_bottom <= active_top


def test_multiline_glide_does_not_jump_when_only_soft_halos_meet():
    from lyrica.app import Overlay

    class View:
        height = 60
        effect_padding = 14
        glyph_padding = 3

        def __init__(self, y):
            self.y = float(y)

        def move_to(self, y):
            self.y = float(y)

        def glyph_vertical_span(self):
            return (self.y - self.glyph_padding,
                    self.y + self.height + self.glyph_padding)

    class Glide:
        done = False

        def __init__(self, offset):
            self._offset = offset

        def offset(self):
            return self._offset

    panel = Overlay.__new__(Overlay)
    panel.line_index = 1
    # The default-size two-row layout is clamped at the lower canvas edge.
    # Its halos overlap by 18 px, but its grown glyph boxes retain 4 px.
    panel._views = {0: View(0), 1: View(0)}
    panel._targets = {0: 66.0, 1: 176.0}
    panel._glides = {0: Glide(110.0), 1: Glide(70.0)}

    assert panel._advance_glides()
    assert panel._views[0].y == 176.0
    assert panel._views[1].y == 246.0


@pytest.mark.parametrize("line_height", [32, 35, 41])
def test_realistic_multiline_relay_keeps_rising_without_frame_corrections(
        monkeypatch, line_height):
    from lyrica import motion
    from lyrica.app import Overlay

    now = [10.0]
    monkeypatch.setattr(motion.time, "monotonic", lambda: now[0])

    class View:
        def __init__(self):
            self.line_height = line_height
            self.height = line_height * 2
            self.effect_padding = math.ceil(11 + line_height * 0.14 / 2)
            self.glyph_padding = math.ceil(line_height * 0.14 / 2)
            self.y = 0.0

        def move_to(self, y):
            self.y = float(y)

        def glyph_vertical_span(self):
            return (self.y - self.glyph_padding,
                    self.y + self.height + self.glyph_padding)

    panel = Overlay.__new__(Overlay)
    panel.line_index = 1
    panel.height = 320
    panel.anchor_y = 176
    panel.row_gap = 50
    panel._content_top = 88
    panel._relay_hold = {0, 1}
    panel._views = {0: View(), 1: View()}
    top, middle, _bottom = panel._multiline_relay_slots([0, 1])
    step = middle - top
    panel._targets = {0: top, 1: middle}
    base = motion.multiline_duration(step, line_height, panel.row_gap)
    clearance = (step - panel._views[0].height
                 - panel._views[0].glyph_padding
                 - panel._views[1].glyph_padding - 1)
    stagger = motion.safe_stagger(
        step, clearance, base, motion.MULTILINE_STAGGER_MS)
    panel._glides = {
        0: motion.Glide(step, base + stagger),
        1: motion.Glide(step, base),
    }

    for elapsed_ms in range(0, math.ceil(base + motion.STAGGER_MS) + 6, 5):
        now[0] = 10.0 + elapsed_ms / 1000
        outgoing_offset = panel._glides[0].offset() if 0 in panel._glides else 0.0
        active_offset = panel._glides[1].offset() if 1 in panel._glides else 0.0
        expected_outgoing = panel._targets[0] + outgoing_offset
        expected_active = panel._targets[1] + active_offset

        panel._advance_glides()

        # Equality proves the separation fallback did not introduce a jump.
        assert panel._views[0].y == pytest.approx(expected_outgoing)
        assert panel._views[1].y == pytest.approx(expected_active)
        assert (panel._views[0].glyph_vertical_span()[1]
                <= panel._views[1].glyph_vertical_span()[0] + 1e-6)
        visibility = panel._relay_outgoing_visibility(
            0, panel._views[0])
        if (panel._views[0].y - panel._views[0].effect_padding
                <= panel._content_top):
            assert visibility == 0.0
        else:
            assert 0.0 < visibility <= 1.0

    assert panel._views[0].y == pytest.approx(top)
    assert panel._views[1].y == pytest.approx(middle)
    assert not panel._relay_hold


def test_wrapped_outgoing_row_fades_before_entering_the_card_lane():
    from lyrica.app import Overlay

    class Palette:
        def faded(self, distance, visibility):
            return distance, visibility

    class View:
        def __init__(self):
            self.visible = None
            self.style = None
            self.y = 125.0
            self.effect_padding = 14

        def set_active(self, active):
            self.active = active

        def set_visible(self, visible):
            self.visible = visible

        def show_inactive(self, style):
            self.style = style

    panel = Overlay.__new__(Overlay)
    panel.line_index = 1
    panel._views = {0: View(), 1: View()}
    panel._targets = {0: 14.0, 1: 125.0}

    class Glide:
        distance = 111.0

    panel._glides = {0: Glide(), 1: Glide()}
    panel._relay_hold = {0, 1}
    panel.palette = Palette()
    panel._content_top = 88
    panel._order_text_layers = lambda: None

    panel._restyle()
    assert panel._views[0].visible is True
    assert panel._views[0].style == (1, 1.0)

    panel._views[0].y = 113.5
    panel._restyle()
    assert panel._views[0].visible is True
    assert panel._views[0].style == pytest.approx((1, 0.5))

    # Its complete visual box now touches the lyric/card boundary. It is
    # already invisible before the remaining glide can carry it under the card.
    panel._views[0].y = panel._content_top + panel._views[0].effect_padding
    panel._restyle()
    assert panel._views[0].visible is False


def test_wrapped_incoming_row_stays_visible_for_its_complete_rise():
    from lyrica.app import Overlay

    class View:
        def __init__(self):
            self.visible = None

        def set_active(self, active):
            self.active = active

        def set_visible(self, visible):
            self.visible = visible

    panel = Overlay.__new__(Overlay)
    panel.line_index = 1
    panel._views = {1: View()}
    panel._glides = {1: object()}
    panel._relay_hold = {1}
    panel._order_text_layers = lambda: None

    panel._restyle()
    assert panel._views[1].active is True
    assert panel._views[1].visible is True


def test_adjacent_multiline_rows_keep_a_bounded_fluid_stagger():
    from lyrica import motion
    from lyrica.app import Overlay

    class View:
        line_height = 30
        height = 60
        effect_padding = 10
        glyph_padding = 3

    panel = Overlay.__new__(Overlay)
    panel.line_index = 1
    panel.height = 320
    panel.row_gap = 50
    panel._views = {0: View(), 1: View(), 2: View()}

    top, middle, _bottom = panel._multiline_relay_slots([0, 1, 2])
    step = middle - top
    base = motion.multiline_duration(step, 30, panel.row_gap)
    active = base, motion.MULTILINE_CURVE
    clearance = step - 60 - 3 - 3 - 1
    stagger = motion.safe_stagger(
        110, clearance, base,
        motion.MULTILINE_STAGGER_MS, motion.MULTILINE_CURVE)
    neighbour = (base + stagger, motion.MULTILINE_CURVE)
    assert panel._row_glide_motion(0, panel._views[0], 110) == neighbour
    assert panel._row_glide_motion(1, panel._views[1], 110) == active
    assert panel._row_glide_motion(2, panel._views[2], 110) == neighbour


def test_single_row_neighbours_keep_the_original_stagger():
    from lyrica import motion
    from lyrica.app import Overlay

    class Single:
        line_height = 30
        height = 30

    class Double:
        line_height = 30
        height = 60

    panel = Overlay.__new__(Overlay)
    panel.line_index = 1
    panel._views = {0: Single(), 1: Double()}

    assert panel._row_glide_motion(0, panel._views[0], 80) == (
        motion.row_duration(0, 1), motion.SCROLL_CURVE)


def test_staggered_multiline_reverse_exit_never_crosses_the_active_line():
    from lyrica.app import Overlay

    class View:
        def __init__(self, y):
            self.y = float(y)
            self.height = 60
            self.effect_padding = 10
            self.glyph_padding = 3

        def move_to(self, y):
            self.y = float(y)

        def visual_vertical_span(self):
            return (self.y - self.effect_padding,
                    self.y + self.height + self.effect_padding)

        def glyph_vertical_span(self):
            return (self.y - self.glyph_padding,
                    self.y + self.height + self.glyph_padding)

    class Glide:
        done = False

        def __init__(self, offset):
            self._offset = offset

        def offset(self):
            return self._offset

    panel = Overlay.__new__(Overlay)
    panel.line_index = 0
    panel._views = {0: View(0), 1: View(0)}
    panel._targets = {0: 100.0, 1: 210.0}
    panel._glides = {0: Glide(-20.0), 1: Glide(-80.0)}

    assert panel._advance_glides()

    active_bottom = panel._views[0].glyph_vertical_span()[1]
    outgoing_top = panel._views[1].glyph_vertical_span()[0]
    assert active_bottom <= outgoing_top



def test_the_wash_is_built_for_the_panel_at_its_full_size(tmp_path, monkeypatch):
    # One built while the panel is compact leaves bare panel showing for the
    # whole of the next expansion. The window clips it, so oversized is free.
    from lyrica import app as A

    asked = []
    monkeypatch.setattr(A.artwork, "make_backdrop",
                        lambda data, w, h: asked.append((w, h)))
    monkeypatch.setattr(A.artwork, "make_thumbnail", lambda data, size: None)
    monkeypatch.setattr(A.songcolour, "extract", lambda data: None)

    class Panel:
        chrome = A.chrome_mod.Chrome(A.chrome_mod.ChromeMode.PANEL, "#000",
                                     A.glass.PANEL)
        width, height = 325, 114          # compact
        _thumb_size = 64

    A.Overlay._build_art(Panel(), b"x")
    full = (Panel.chrome.px(A.WIDTH), Panel.chrome.px(A.HEIGHT))
    assert asked == [full], f"built for {asked}, not the full {full}"


def test_only_the_landing_frame_pays_for_the_region_and_the_flush(monkeypatch):
    # They are what took a resize frame past its budget.
    # The clipping region is not deferrable with them: it decides what of the
    # window is painted at all, and holding it back showed a panel clipped to
    # the size it used to be while it had already moved and grown.
    from lyrica import app as A
    from lyrica import chrome as chrome_mod

    done = []
    monkeypatch.setattr(chrome_mod, "shape",
                        lambda *a: done.append("shape"))

    class Beam:
        def reshape(self, *a):
            done.append("beam")

    class Panel:
        chrome = chrome_mod.Chrome(chrome_mod.ChromeMode.PANEL, "#000",
                                   A.glass.PANEL)
        width, height = 900, 300
        anchor_y = 0.0
        beam = Beam()
        _card_text = ("t", "a")
        line_index = -1

        def __init__(self):
            self.root = self.canvas = self
            self._views = {}

        # everything `_resize_window` touches, answered flatly
        def winfo_x(self): return 0
        def winfo_y(self): return 0
        def geometry(self, _s): pass
        def configure(self, **_k): pass
        def update_idletasks(self): done.append("flush")
        def _lay_out_card(self, *_a): pass
        def _place_thumb(self): pass
        def _retarget(self, *_a, **_k): pass
        def _visible_indices(self, _n): return []

    monkeypatch.setattr(chrome_mod, "desktop_bounds",
                        lambda _root: (0, 0, 3000, 2000))
    panel = Panel()
    A.Overlay._resize_window(panel, 880, 290, settling=False)
    assert done == ["beam"], (
        "the border is geometry: left behind it stays drawn around the outline "
        f"the panel is leaving. This did {done}")
    done.clear()
    A.Overlay._resize_window(panel, 860, 280, settling=True)
    assert done == ["beam", "shape", "flush"]




def test_the_region_covers_both_ends_of_a_move_before_it_starts(monkeypatch):
    # It is what the window is clipped to. Left at the size the panel is
    # leaving, it showed a small box at the new left edge that snapped open on
    # landing; remade every frame, `SetWindowRgn` repaints the whole window
    # synchronously and costs 32 ms of a 16 ms budget. One region, big enough
    # for either end of the move, and the exact one when it lands.
    from lyrica import app as A
    from lyrica import chrome as chrome_mod

    asked = []
    monkeypatch.setattr(chrome_mod, "shape",
                        lambda _r, _c, w, h: asked.append((w, h)))

    class Panel:
        chrome = chrome_mod.Chrome(chrome_mod.ChromeMode.PANEL, "#000",
                                   A.glass.PANEL)
        width, height = 325, 114        # compact, about to expand
        _collapse = None
        _compact = False

        def __init__(self):
            self.root = self

        def winfo_x(self): return 800
        def winfo_y(self): return 100
        def _want_compact(self): return False
        def _target_size(self): return (1125, 375)

    A.Overlay._retarget_size(Panel())
    assert asked == [(1125, 375)], (
        f"one region covering the whole move, not {asked}")


def test_the_card_slides_to_the_middle_as_the_panel_opens():
    # And back to the margin as it shuts. It is the only thing moving: the
    # window holds the left edge it started from for the whole of a move, where
    # holding its centre meant it travelled left while the card travelled right
    # inside it by exactly as much — two movers, applied by two different
    # things, that had to cancel in the same repaint to look still. They
    # cancelled arithmetically and not visually.
    from lyrica import app as A

    class Panel:
        chrome = A.chrome_mod.Chrome(A.chrome_mod.ChromeMode.PANEL, "#000",
                                     A.glass.PANEL)
        _thumb_size = 78
        _card_y = 20
        _thumb_image = None

        def __init__(self, width):
            self.width = width
            self.placed = None
            self.canvas = self
            self._card_measured = None
            self._card_width = 0

        def coords(self, _item, *box):
            if self.placed is None:
                self.placed = box[0]

    def at(width):
        panel = Panel(width)
        panel._title_font = panel._artist_font = _Metrics(300)
        panel._thumb_item = panel._title_item = panel._artist_item = object()
        A.Overlay._lay_out_card(panel, "title", "artist")
        return panel.placed

    margin = Panel(0).chrome.px(12)
    block = 78 + Panel(0).chrome.px(10) + 300
    assert at(block + margin * 2) == margin, "compact sits against the margin"
    assert at(1125) == 1125 // 2 - block // 2, "and full sits in the middle"

    # Monotonic the whole way, so it slides rather than jumping about.
    places = [at(w) for w in range(block + margin * 2, 1200, 7)]
    assert places == sorted(places)


class _Metrics:
    """Just what `_lay_out_card` asks a font."""

    def __init__(self, width):
        self._width = width

    def measure(self, _text):
        return self._width

    def metrics(self, _what):
        return 20
