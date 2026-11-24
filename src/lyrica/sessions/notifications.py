"""Bridge Windows media notifications on native threads to one asyncio reader."""
import asyncio
import logging
from contextlib import ExitStack
from functools import partial
from threading import Lock

logger = logging.getLogger(__name__)


def _subscribe(stack, source, event, handler):
    """Keep polling available when a source disappears or lacks an event."""
    try:
        remove = getattr(source, f"remove_{event}")
        token = getattr(source, f"add_{event}")(handler)
    except (AttributeError, OSError, RuntimeError):
        logger.debug("could not subscribe to %s", event, exc_info=True)
        return

    def detach():
        try:
            remove(token)
        except (OSError, RuntimeError):
            logger.debug("could not remove %s handler", event, exc_info=True)

    stack.callback(detach)


class WindowsSessionNotifications:
    """Own the live projections, event tokens and metadata for one reader loop."""

    def __init__(self, manager):
        self._manager = manager
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._lock = Lock()
        self._closed = False
        self._posted = False
        self._membership_changed = True
        self._dirty_media: set[int] = set()
        self._enumerated_at = self._loop.time()
        self._sessions = []
        self.media_cache: dict[int, tuple[str, str, str]] = {}
        self._subscriptions: dict[int, ExitStack] = {}
        self._manager_events = ExitStack()
        _subscribe(self._manager_events, manager, "sessions_changed",
                   partial(self._notify, membership=True))

    def _notify(self, _sender, _args, *, membership=False, media_id=None):
        # WinRT can invoke these concurrently, including while the reader is
        # awaiting metadata. Queue at most one loop callback until wait consumes it.
        with self._lock:
            if self._closed:
                return
            if membership:
                self._membership_changed = True
            if media_id is not None:
                self._dirty_media.add(media_id)
            if not self._posted:
                self._posted = True
                self._loop.call_soon_threadsafe(self._wake.set)

    def sessions(self):
        """Return subscribed projections and apply invalidations on the reader thread."""
        with self._lock:
            refresh, self._membership_changed = self._membership_changed, False
            dirty, self._dirty_media = self._dirty_media, set()
        for key in dirty:
            self.media_cache.pop(key, None)
        if refresh:
            self._enumerated_at = self._loop.time()
            current = list(self._manager.get_sessions())
            self.media_cache.clear()
            active = {id(session) for session in current}
            for key in self._subscriptions.keys() - active:
                self._subscriptions.pop(key).close()
            for session in current:
                key = id(session)
                if key not in self._subscriptions:
                    stack = self._subscriptions[key] = ExitStack()
                    _subscribe(stack, session, "media_properties_changed",
                               partial(self._notify, media_id=key))
                    _subscribe(stack, session, "playback_info_changed", self._notify)
                    _subscribe(stack, session, "timeline_properties_changed", self._notify)
            self._sessions = current
        return self._sessions

    async def wait(self, interval):
        """Wait for a native event or a periodic full read to recover missed changes."""
        deadline = self._enumerated_at + interval
        try:
            async with asyncio.timeout_at(deadline):
                await self._wake.wait()
        except TimeoutError:
            pass
        with self._lock:
            if self._loop.time() >= deadline:
                self._membership_changed = True
            if self._wake.is_set():
                self._wake.clear()
                self._posted = False

    def close(self):
        with self._lock:
            self._closed = True
            self._dirty_media.clear()
        self._wake.set()
        self._manager_events.close()
        for stack in self._subscriptions.values():
            stack.close()
        self._subscriptions.clear()
        self._sessions.clear()
        self.media_cache.clear()
