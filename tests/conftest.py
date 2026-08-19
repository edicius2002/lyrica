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


class Surface:
    """A stand-in for the layered window: the same five calls, in memory.

    Not a mock. It reproduces the two properties of the real one the code leans
    on — a surface that only ever grows, and content that survives between
    frames — so a test can read back what a strip actually wrote.

    Shared, because since the light moved behind the panel *most* of it lands
    here: a test that measures the border on the canvas half alone is now
    measuring the two pixels of frosted rim and calling it the border.
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

    # --- reading it back --------------------------------------------------

    def straight(self, width: int, height: int):
        """The premultiplied BGRA it holds, as the straight RGBA PIL wants.

        `UpdateLayeredWindow` demands premultiplied and `halo.Spill` folds the
        multiply into the colour table, where it is free. Everything that reads
        the surface back has to undo it, and doing that in one place is what
        stops two tests disagreeing about how bright the border is.
        """
        import numpy as np
        from PIL import Image

        got = self._frame[:height, :width].astype(np.float32)
        alpha = got[..., 3:4]
        colour = np.where(alpha > 0,
                          got[..., 2::-1] * 255.0 / np.maximum(alpha, 1e-6), 0.0)
        out = np.concatenate([np.clip(colour, 0, 255), alpha], axis=2)
        return Image.fromarray(out.astype(np.uint8), "RGBA")


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
    o._static_mount_pending = False
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
