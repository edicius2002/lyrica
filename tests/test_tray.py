"""The notification-area icon and starting with Windows (offline).

Nothing here creates a real icon or writes to a real registry: both would leave
something behind on the machine running the tests.
"""
import sys

import pytest

from lyrica import autostart, tray

# --- the menu ---------------------------------------------------------------

def test_every_menu_entry_names_an_action_the_overlay_handles():
    from lyrica.app import Overlay
    for entry in tray.MENU:
        if entry is not None:
            assert entry[2] in Overlay.ACTIONS, entry[1]


def test_the_menu_can_show_hide_resize_and_quit():
    actions = {e[2] for e in tray.MENU if e is not None}
    assert {"toggle", "quit", "bigger", "smaller", "reset"} <= actions


def test_menu_identifiers_are_unique_and_never_zero():
    # TrackPopupMenu returns 0 for "nothing was chosen", so an entry with that
    # identifier would fire every time the menu was dismissed.
    idents = [e[0] for e in tray.MENU if e is not None]
    assert 0 not in idents
    assert len(idents) == len(set(idents))


def test_the_icon_can_be_drawn():
    # Drawn from primitives rather than a glyph, so this does not depend on a
    # font that happens to carry a musical note.
    pytest.importorskip("PIL")
    path = tray._icon_file()
    assert path is not None and path.endswith(".ico")


# --- the listener contract --------------------------------------------------

def test_a_platform_without_a_notification_area_says_so():
    null = tray.NullTray()
    assert not null.available
    null.start()
    assert null.poll() == []
    null.set_autostart(True)
    null.stop()             # safe having never started


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only tray")
def test_choices_are_drained_oldest_first():
    icon = tray.WindowsTray()
    for action in ("toggle", "bigger", "quit"):
        icon._events.put(action)
    assert icon.poll() == ["toggle", "bigger", "quit"]
    assert icon.poll() == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only tray")
def test_stopping_one_that_never_started_is_not_an_error():
    tray.WindowsTray().stop()


def test_the_platform_picks_the_tray_it_can_use():
    icon = tray.create_tray()
    if sys.platform == "win32":
        assert isinstance(icon, tray.WindowsTray)
    else:
        assert isinstance(icon, tray.NullTray)


# --- starting with Windows --------------------------------------------------

def test_a_source_checkout_cannot_offer_to_start_with_windows(monkeypatch):
    # The command would have to name an interpreter, a working directory and a
    # module, all of which move the moment the checkout does.
    monkeypatch.setattr(autostart, "frozen", lambda: False)
    assert autostart.command() is None
    assert not autostart.available()
    assert not autostart.enabled()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only registry")
def test_a_packaged_build_names_its_own_executable(monkeypatch):
    monkeypatch.setattr(autostart, "frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", r"C:\somewhere\Lyrica.exe")
    assert autostart.command() == r'"C:\somewhere\Lyrica.exe"'
    assert autostart.available()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only registry")
def test_an_entry_pointing_somewhere_else_does_not_count_as_enabled(monkeypatch):
    # A stale entry left by an executable since moved or deleted would
    # otherwise show a tick beside something that does nothing.
    import winreg
    monkeypatch.setattr(autostart, "frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", r"C:\here\Lyrica.exe")

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda *a: (r'"C:\elsewhere\Lyrica.exe"', 1))
    assert not autostart.enabled()

    monkeypatch.setattr(winreg, "QueryValueEx",
                        lambda *a: (r'"C:\HERE\lyrica.exe"', 1))
    assert autostart.enabled(), "the comparison should ignore case"


def test_changing_it_from_a_source_checkout_is_a_no_op(monkeypatch):
    monkeypatch.setattr(autostart, "frozen", lambda: False)
    assert autostart.set_enabled(True) is False


def test_every_module_imports_on_any_platform():
    """The whole package, imported, wherever this runs.

    Windows-only type machinery — `ctypes.wintypes`, `ctypes.WINFUNCTYPE` —
    does not exist elsewhere, so declaring a struct at module level breaks the
    import on every other platform. That is how this file first failed CI: a
    collection error, from a module the rest of the app imports unconditionally.
    """
    import importlib
    import pkgutil

    import lyrica

    for module in pkgutil.walk_packages(lyrica.__path__, "lyrica."):
        importlib.import_module(module.name)
