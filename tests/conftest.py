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
