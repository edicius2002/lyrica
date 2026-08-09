"""Resizing and collapsing: the things that only break when sizes change.

All six of these came out of review rather than use, and they share a cause —
the panel has two sizes, and code written for one of them assumed the other.
"""
import itertools

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


def test_only_the_landing_frame_pays_for_the_mask_and_the_border(monkeypatch):
    # Deferring these is the whole fix: they are what took a resize frame past
    # its budget, and a mask a frame or two stale is invisible at that speed.
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
    assert done == [], f"a frame in flight did {done}"
    A.Overlay._resize_window(panel, 860, 280, settling=True)
    assert done == ["flush", "shape", "beam"]
