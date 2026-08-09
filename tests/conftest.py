"""Shared fixtures.

The important one is `tk_root`. Tcl does not survive being initialised and torn
down many times in one process: on the Windows CI runner the fourteenth-or-so
interpreter fails with "Can't find a usable init.tcl ... No error", which reads
like a missing installation and is really exhaustion. Tests that need a canvas
share one root for the session instead of each building their own.
"""
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


@pytest.fixture
def overlay():
    """An `Overlay` on an interpreter of its own, wired up so images land in it.

    `ImageTk.PhotoImage` binds to tkinter's *default* root, which in a suite is
    the session one from `conftest`, while an Overlay makes its own. Images then
    belong to one interpreter and are drawn on another, and Tk answers `image
    "pyimage1" doesn't exist` — intermittently, because it depends which test
    ran first. The bloom's cache outlives any single interpreter too, so it goes
    with them.
    """
    import tkinter as tk

    from lyrica import app as A
    from lyrica import bloom, config
    bloom._cache.clear()
    bloom._fonts.clear()
    config.load()
    o = A.Overlay()
    was, tk._default_root = tk._default_root, o.root
    try:
        yield o
    finally:
        tk._default_root = was
        bloom._cache.clear()
        o.root.destroy()
