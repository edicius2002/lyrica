import asyncio
from unittest.mock import AsyncMock

from test_sessions import FakeWindowsSession, eventually, wire_windows_manager

from lyrica.sessions.notifications import WindowsSessionNotifications
from lyrica.sessions.windows import WindowsSessionReader


class EventSource:
    def __init__(self):
        self.handlers = {}

    def __getattr__(self, name):
        action, _, event = name.partition("_")
        if action == "add":
            def add(handler):
                assert event not in self.handlers
                self.handlers[event] = handler
                return handler
            return add
        if action == "remove":
            def remove(token):
                assert self.handlers.pop(event) is token
            return remove
        raise AttributeError(name)

    def emit(self, event):
        self.handlers[event](self, None)


class Manager(EventSource):
    def __init__(self, *sessions):
        super().__init__()
        self.members = list(sessions)
        self.reads = 0

    def get_sessions(self):
        self.reads += 1
        return self.members


def test_reader_updates_transport_without_reloading_unchanged_metadata(monkeypatch):
    class Player(FakeWindowsSession, EventSource):
        def __init__(self, title):
            super().__init__(title)
            EventSource.__init__(self)

    session = Player("First song")
    media = AsyncMock(wraps=session.try_get_media_properties_async)
    monkeypatch.setattr(session, "try_get_media_properties_async", media)
    manager = Manager(session)
    wire_windows_manager(monkeypatch, manager)
    reader = WindowsSessionReader(interval=60)
    reader.start()
    try:
        eventually(lambda: reader.snapshot.title == "First song")
        session.status = "PAUSED"
        for _ in range(100):
            session.emit("timeline_properties_changed")
        session.emit("playback_info_changed")
        eventually(lambda: not reader.snapshot.playing)
        assert media.await_count == 1

        session.media.title = "Next song"
        session.emit("media_properties_changed")
        eventually(lambda: reader.snapshot.title == "Next song")
        assert media.await_count == 2
        assert manager.reads == 1
    finally:
        reader.stop()
        reader._thread.join(2)
    assert not session.handlers and not manager.handlers


def test_session_changes_wake_the_loop_and_a_burst_causes_one_refresh():
    async def run():
        session = EventSource()
        manager = Manager(session)
        watcher = WindowsSessionNotifications(manager)
        assert watcher.sessions() == [session]
        for event in ("media_properties_changed", "playback_info_changed",
                      "timeline_properties_changed"):
            await asyncio.to_thread(session.emit, event)
            await asyncio.wait_for(watcher.wait(60), 1)

        def burst():
            for _ in range(100):
                session.emit("timeline_properties_changed")

        await asyncio.to_thread(burst)
        await asyncio.wait_for(watcher.wait(60), 1)
        waiting = asyncio.create_task(watcher.wait(60))
        await asyncio.sleep(0)
        assert not waiting.done()
        manager.emit("sessions_changed")
        await asyncio.wait_for(waiting, 1)
        watcher.close()

    asyncio.run(run())


def test_membership_refresh_cleans_up_old_projections_and_close_detaches_all():
    async def run():
        a, b = EventSource(), EventSource()
        manager = Manager(a, b)
        watcher = WindowsSessionNotifications(manager)
        assert watcher.sessions() == [a, b]
        assert a.handlers.keys() == b.handlers.keys() and len(a.handlers) == 3
        old_callback = a.handlers["playback_info_changed"]
        fresh = EventSource()
        manager.members = [fresh, b]
        manager.emit("sessions_changed")
        await asyncio.wait_for(watcher.wait(60), 1)
        assert watcher.sessions() == [fresh, b]
        assert not a.handlers and len(fresh.handlers) == 3

        # A missed membership event is repaired even without a native wake.
        manager.members = []
        await watcher.wait(0)
        assert watcher.sessions() == []
        assert not fresh.handlers and not b.handlers
        watcher.close()
        watcher.close()
        assert not manager.handlers
        return old_callback

    late = asyncio.run(run())
    late(None, None)  # The callback was in flight when the native source detached.


def test_media_refresh_reuses_the_subscribed_windows_projections():
    async def run():
        manager = Manager(EventSource())
        watcher = WindowsSessionNotifications(manager)
        original, = watcher.sessions()
        manager.members = [EventSource()]  # WinRT may return a fresh wrapper each time.
        original.emit("media_properties_changed")
        await asyncio.wait_for(watcher.wait(60), 1)
        assert watcher.sessions() == [original]
        assert manager.reads == 1
        watcher.close()

    asyncio.run(run())


def test_only_changed_media_is_reread_between_full_reads():
    async def run():
        a, b = EventSource(), EventSource()
        manager = Manager(a, b)
        watcher = WindowsSessionNotifications(manager)
        watcher.sessions()
        metadata = ("Artist", "Title", "Album")
        watcher.media_cache.update({id(a): metadata, id(b): metadata})
        a.emit("playback_info_changed")
        b.emit("timeline_properties_changed")
        await watcher.wait(60)
        watcher.sessions()
        assert len(watcher.media_cache) == 2
        b.emit("media_properties_changed")
        await watcher.wait(60)
        watcher.sessions()
        assert watcher.media_cache == {id(a): metadata}
        # An event already pending at the fallback deadline must not postpone it.
        a.emit("timeline_properties_changed")
        await watcher.wait(0)
        watcher.sessions()
        assert not watcher.media_cache
        watcher.close()

    asyncio.run(run())
