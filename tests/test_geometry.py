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


def test_the_desktop_is_every_monitor(monkeypatch):
    # Tk only knows the primary screen, and clamping to it dragged the panel
    # back from a second monitor on every collapse.
    import tkinter as tk

    from lyrica import chrome as chrome_mod

    root = tk.Tk()
    root.withdraw()
    _left, _top, width, height = chrome_mod.desktop_bounds(root)
    assert width >= root.winfo_screenwidth()
    assert height >= root.winfo_screenheight()
    root.destroy()


def test_the_desktop_falls_back_to_the_screen_without_the_platform(monkeypatch):
    import tkinter as tk

    from lyrica import chrome as chrome_mod

    monkeypatch.setattr(chrome_mod.sys, "platform", "linux")
    root = tk.Tk()
    root.withdraw()
    assert chrome_mod.desktop_bounds(root) == (
        0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    root.destroy()


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
