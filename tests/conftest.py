"""Shared fixtures.

The important one is `tk_root`. Tcl does not survive being initialised and torn
down many times in one process: on the Windows CI runner the fourteenth-or-so
interpreter fails with "Can't find a usable init.tcl ... No error", which reads
like a missing installation and is really exhaustion. Tests that need a canvas
share one root for the session instead of each building their own.
"""
import queue
import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:                     # pragma: no cover - headless CI
        pytest.skip("no display")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def canvas(tk_root):
    """A canvas of its own, on the shared root, cleaned up afterwards."""
    made = tk.Canvas(tk_root, width=1200, height=400)
    yield made
    made.destroy()


@pytest.fixture(scope="session")
def _overlay_once(tk_root):
    """One `Overlay`, and one Tcl interpreter, for the whole suite.

    Two reasons it cannot be per test. Tcl runs out of interpreters after a
    dozen or so and answers `Can't find a usable tk.tcl` — intermittently,
    which is worse than always. And `ImageTk.PhotoImage` binds to tkinter's
    *default* root, so images made for one overlay and drawn on another give
    `image "pyimage1" doesn't exist`.
    """

    import sys

    from lyrica import app as A
    from lyrica import config
    if sys.platform != "win32":
        # It builds a visible, borderless, always-on-top window and asks the
        # platform for a tray icon, a global hotkey and an output meter. On the
        # macOS runner that hangs rather than failing, which is worse: the job
        # sat in progress for seven minutes where it usually takes twenty
        # seconds. What these tests cover that is not Windows-first is covered
        # by the modules' own tests, which do run there.
        pytest.skip("the overlay is Windows-first")
    config.load()
    made = A.Overlay()
    # Handed straight back, because making a second root steals the default and
    # every test that is not an overlay test wants the shared one. Which root is
    # default decides which interpreter an `ImageTk.PhotoImage` belongs to, and
    # that was deciding it by creation order.
    tk._default_root = tk_root
    yield made
    made.root.destroy()


@pytest.fixture
def overlay(_overlay_once):
    """The shared overlay, wound back to the state a fresh one would be in."""
    import tkinter as tk

    from lyrica import app as A
    from lyrica import bloom
    o = _overlay_once
    # Only for as long as this test runs. Held for the session it would send
    # every other test's images into the overlay's interpreter instead of the
    # shared root's, which is the same fault the other way round.
    was, tk._default_root = tk._default_root, o.root
    bloom._cache.clear()
    bloom._fonts.clear()
    o._clear_views()
    o._compact = False
    o._collapse = None
    o._resize_window(*o._target_size())
    o.line_index = -1
    o.lyrics = None
    o.offset = 0.0
    o._awaiting_seek = None
    o._card_text = o._card_raw = o._card_measured = None
    o._shown = o._loading = A.Track(searched=True)
    o._worker_results = queue.SimpleQueue()
    o._fetching_key = ""
    o._cuts = A.sponsorblock.Cuts()
    o._cuts_checked = None
    o._cuts_discontinuous = False
    o._cut_fade_at = None
    o._outgoing_fade_at = None
    o._lyrics_fade_at = None
    o._lyrics_reveal_pending = False
    o._pending_art = None
    o.root.update()
    try:
        yield o
    finally:
        tk._default_root = was
        bloom._cache.clear()
