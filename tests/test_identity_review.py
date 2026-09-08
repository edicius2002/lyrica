"""Regressions for session continuity, ambiguous actions and provider recovery."""
import asyncio
import threading

from test_sessions import FakeWindowsManager, FakeWindowsSession, wire_windows_manager

from lyrica import providers
from lyrica.lyrics import Precision
from lyrica.providers.identity import validate_identity
from lyrica.sessions.windows import WindowsSessionReader


def test_track_changes_retain_selected_player(monkeypatch):
    a = FakeWindowsSession("A", app="player-a")
    b = FakeWindowsSession("B", app="player-b")
    manager = FakeWindowsManager([a, b])
    wire_windows_manager(monkeypatch, manager)
    reader = WindowsSessionReader()
    first = asyncio.run(reader._read())
    selected = next(s for s in manager.sessions
                    if s.source_app_user_model_id == first.app)
    for title in ("Track 0", "Track 1", "Track 2"):
        selected.media.title = title
        assert asyncio.run(reader._read()).app == first.app


def test_same_browser_track_change_retains_unique_continuation(monkeypatch):
    a, b = FakeWindowsSession("A"), FakeWindowsSession("B")
    manager = FakeWindowsManager([a, b])
    wire_windows_manager(monkeypatch, manager)
    reader = WindowsSessionReader()
    first = asyncio.run(reader._read())
    selected = a if first.title == "A" else b
    for title in ("Track 0", "Track 1", "Track 2"):
        selected.media.title = title
        assert asyncio.run(reader._read()).title == title


def test_duplicate_metadata_rejects_ambiguous_actions(monkeypatch):
    a, b = FakeWindowsSession("Same"), FakeWindowsSession("Same")
    manager = FakeWindowsManager([a, b])
    wire_windows_manager(monkeypatch, manager)
    reader = WindowsSessionReader()
    snapshot = asyncio.run(reader._read())
    manager.sessions.reverse()
    assert not reader.seek(12.5, snapshot)
    assert asyncio.run(reader._resolve_session(snapshot)) is None
    assert a.seeks == b.seeks == []


def test_duplicate_metadata_cannot_mask_playing_session(monkeypatch):
    manager = FakeWindowsManager([
        FakeWindowsSession("Same", status="PAUSED"), FakeWindowsSession("Same")])
    wire_windows_manager(monkeypatch, manager)
    assert asyncio.run(WindowsSessionReader()._read()).playing


def test_ambiguous_snapshot_stays_unsafe_after_one_tab_disappears(monkeypatch):
    a, b = FakeWindowsSession("Same"), FakeWindowsSession("Same")
    manager = FakeWindowsManager([a, b])
    wire_windows_manager(monkeypatch, manager)
    reader = WindowsSessionReader()
    snapshot = asyncio.run(reader._read())
    manager.sessions = [b]
    assert not reader.seek(12.5, snapshot)
    assert asyncio.run(reader._artwork(snapshot)) is None
    assert b.seeks == []


def test_new_duplicate_invalidates_a_previously_unique_action(monkeypatch):
    a = FakeWindowsSession("Same")
    manager = FakeWindowsManager([a])
    wire_windows_manager(monkeypatch, manager)
    reader = WindowsSessionReader()
    snapshot = asyncio.run(reader._read())
    b = FakeWindowsSession("Same")
    manager.sessions = [b, a]
    assert not reader.seek(12.5, snapshot)
    assert asyncio.run(reader._artwork(snapshot)) is None
    assert a.seeks == b.seeks == []


def test_multiple_changed_tabs_do_not_invent_a_continuation(monkeypatch):
    a, b = FakeWindowsSession("A"), FakeWindowsSession("B")
    manager = FakeWindowsManager([a, b])
    wire_windows_manager(monkeypatch, manager)
    reader = WindowsSessionReader()
    asyncio.run(reader._read())
    a.media.title, b.media.title = "New A", "New B"
    expected = asyncio.run(WindowsSessionReader()._read())
    assert asyncio.run(reader._read()).session_id == expected.session_id


def test_equivalent_version_suffix_formats():
    for requested, returned in (("Song - 2011 Remaster", "Song (Remastered 2011)"),
                                ("Song (Live)", "Song - Live")):
        assert validate_identity(
            requested_artist="Artist", requested_title=requested,
            returned_artist="Artist", returned_title=returned).accepted


def test_version_normalization_preserves_main_title():
    for requested, returned in (("Song - Live", "Other Song (Live)"),
                                ("Live Forever", "Forever"),
                                ("Song - Live Forever", "Song"),
                                ("Song (Live)", "Song (Remix)")):
        assert not validate_identity(
            requested_artist="Artist", requested_title=requested,
            returned_artist="Artist", returned_title=returned).accepted


def test_global_timeout_uses_bounded_retries(tmp_path, monkeypatch):
    released = threading.Event()

    class Slow:
        name = "slow"
        max_precision = Precision.WORD
        calls = 0

        def fetch(self, *args):
            self.calls += 1
            released.wait(5)
            return None

    source = Slow()
    monkeypatch.setattr(providers, "PROVIDERS", [source])
    monkeypatch.setattr(providers, "_cache_path", lambda *args: tmp_path / "entry.json")
    monkeypatch.setattr(providers, "OVERALL_TIMEOUT_S", 0.001)
    monkeypatch.setattr(providers, "_wall_time", lambda: 1000.0)
    try:
        for _ in range(3):
            assert providers.fetch_lyrics("Artist", "Song", 200) is None
        assert source.calls == 2
        monkeypatch.setattr(providers, "_wall_time", lambda: 1031.0)
        assert providers.fetch_lyrics("Artist", "Song", 200) is None
        assert source.calls == 3
    finally:
        released.set()
