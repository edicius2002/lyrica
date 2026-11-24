"""Windows session reader, backed by the global media transport controls.

Every player that responds to the media keys publishes here, so Spotify and
four browser sites cost one implementation rather than five.

`winsdk` is imported inside the reader rather than at module scope: importing it
on any other platform fails outright, and the package has to remain importable
everywhere for the platform selection to run at all.
"""
import asyncio
import hashlib
import logging
import sys
import threading
import time
from collections import Counter
from concurrent.futures import CancelledError
from contextlib import suppress
from dataclasses import replace
from datetime import UTC

from lyrica.sessions.base import SessionReader, Snapshot
from lyrica.sessions.notifications import WindowsSessionNotifications
from lyrica.sessions.selection import SessionCandidate, StableSessionSelector

logger = logging.getLogger(__name__)

# A missed display deadline clears stale lyrics. The broker still owns the
# pending operation until it delivers a terminal result.
READ_TIMEOUT_S = 5.0
FALLBACK_POLL_S = 5.0
RETRY_INITIAL_S = 1.0
RETRY_MAX_S = 30.0


async def _winrt_call(method, *args, check=None):
    """Keep the native operation alive until completion or reader shutdown."""
    if check is not None:
        check()
    operation = method(*args)
    if not hasattr(operation, "get_results"):
        return await operation

    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    def completed(_sender, _status):
        # A completion after shutdown can race with the event loop closing.
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(done.set)

    operation.completed = completed
    try:
        await done.wait()
    except asyncio.CancelledError:
        with suppress(OSError, RuntimeError):
            operation.cancel()
        raise
    return operation.get_results()


def _session_manager_class():
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
    )
    return GlobalSystemMediaTransportControlsSessionManager


class WindowsSessionReader(SessionReader):
    """Owns the manager, subscriptions and native requests on one asyncio loop."""

    def __init__(self, interval: float = 0.5):
        super().__init__(interval)
        self._owner_loop = None
        self._main_task = None
        self._manager = None
        self._generation = 0
        self._notifications = None
        self._artwork_slot = threading.Lock()
        self._seek_slot = threading.Lock()
        self._selector = StableSessionSelector()
        self._previous_sessions: list[Snapshot] = []

    @staticmethod
    def available() -> bool:
        if sys.platform != "win32":
            return False
        try:
            _session_manager_class()
        except ImportError:
            return False
        return True

    def seek(self, seconds: float, snapshot: Snapshot | None = None) -> bool:
        return bool(self._action(
            self._seek_slot, self._seek, max(0.0, seconds), snapshot or self.snapshot))

    async def _seek(self, seconds: float, snapshot: Snapshot | None = None,
                    check=None) -> bool:
        expected = snapshot or self.snapshot
        session = await self._resolve_session(expected, check)
        if session is None:
            return False
        if await self._matching_media(session, expected, check) is None:
            return False
        controls = session.get_playback_info().controls
        if not getattr(controls, "is_playback_position_enabled", False):
            logger.info("this player does not accept position changes")
            return False
        # The API counts in 100-nanosecond ticks, not seconds.
        return bool(await _winrt_call(session.try_change_playback_position_async,
                                      int(seconds * 10_000_000), check=check))

    def read_artwork(self, snapshot: Snapshot | None = None) -> bytes | None:
        """The current track's artwork, if the player published any.

        Read on demand rather than with every poll: it is tens of kilobytes and
        changes once per track, where the position changes constantly.
        """
        return self._action(self._artwork_slot, self._artwork, snapshot or self.snapshot)

    def _action(self, slot, method, *args):
        """Keep one slot until native completion, even if the caller has left."""
        loop = self._owner_loop
        if loop is None or self._stop.is_set() or not slot.acquire(blocking=False):
            return None
        deadline = time.monotonic() + READ_TIMEOUT_S
        generation = self._generation

        def check():
            if (time.monotonic() >= deadline or self._stop.is_set()
                    or self._generation != generation):
                raise TimeoutError

        coroutine = method(*args, check=check)
        try:
            result = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            slot.release()
            return None
        result.add_done_callback(lambda _: slot.release())
        try:
            value = result.result(timeout=max(0.0, deadline - time.monotonic()))
            check()
            return value
        except (TimeoutError, CancelledError):
            return None
        except Exception:
            logger.debug("media session action failed", exc_info=True)
            return None

    async def _artwork(self, snapshot: Snapshot | None = None, check=None) -> bytes | None:
        expected = snapshot or self.snapshot
        session = await self._resolve_session(expected, check)
        if session is None:
            return None
        return await self._read_session_artwork(session, expected, check)

    async def _read_session_artwork(
            self, session, expected: Snapshot, check=None) -> bytes | None:
        media = await self._matching_media(session, expected, check)
        if media is None:
            return None
        reference = media.thumbnail
        if reference is None:
            return None

        from winsdk.windows.storage.streams import (
            Buffer,
            DataReader,
            InputStreamOptions,
        )
        with await _winrt_call(reference.open_read_async, check=check) as stream:
            if check is not None:
                check()
            size = stream.size
            if not size:
                return None
            buffer = Buffer(size)
            await _winrt_call(stream.read_async, buffer, size, InputStreamOptions.READ_AHEAD,
                              check=check)
            if check is not None:
                check()
            with DataReader.from_buffer(buffer) as reader:
                out = bytearray(buffer.length)
                reader.read_bytes(out)
                return bytes(out)

    def stop(self):
        super().stop()
        loop, task = self._owner_loop, self._main_task
        if loop is not None and task is not None:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)

    def _run(self):
        try:
            asyncio.run(self._loop())
        except asyncio.CancelledError:
            pass

    def _release_manager(self):
        if self._notifications is not None:
            self._notifications.close()
        self._notifications = None
        self._manager = None
        self._generation += 1

    async def _loop(self):
        self._main_task = asyncio.current_task()
        self._owner_loop = asyncio.get_running_loop()
        delay = RETRY_INITIAL_S
        try:
            while not self._stop.is_set():
                read = asyncio.create_task(self._read())
                try:
                    done, _ = await asyncio.wait({read}, timeout=READ_TIMEOUT_S)
                    if not done:
                        self.snapshot = Snapshot()
                        logger.warning("media session read pending after %.1fs", READ_TIMEOUT_S)
                    self.snapshot = await read
                except Exception:
                    self.snapshot = Snapshot()
                    self._release_manager()
                    logger.warning("media session read failed; retrying in %.1fs", delay,
                                   exc_info=True)
                    await asyncio.sleep(delay)
                    delay = min(RETRY_MAX_S, delay * 2)
                    continue
                finally:
                    if not read.done():
                        read.cancel()
                        with suppress(asyncio.CancelledError):
                            await read

                delay = RETRY_INITIAL_S
                await self._notifications.wait(max(FALLBACK_POLL_S, self.interval))
        finally:
            self._owner_loop = None
            self._main_task = None
            self._release_manager()

    async def _read(self) -> Snapshot:
        if self._manager is None:
            self._manager = await _winrt_call(_session_manager_class().request_async)
        if self._owner_loop is not None:
            if self._notifications is None:
                self._notifications = WindowsSessionNotifications(self._manager)
            sessions = self._notifications.sessions()
            cache = self._notifications.media_cache
        else:
            sessions = self._manager.get_sessions()
            cache = None
        records = await self._records(sessions, cache)
        self._retain_continuation([snapshot for _, snapshot, _ in records])
        if not records:
            self._selector.choose([])
            return Snapshot()
        selected = self._selector.choose([
            SessionCandidate(snapshot.session_id, snapshot.playing, status == "PAUSED")
            for _session, snapshot, status in records
        ])
        # Duplicate metadata is not a physical session identity. For display,
        # prefer the playing reading; never let a paused duplicate mask it.
        return max(
            (snapshot for _, snapshot, _ in records if snapshot.session_id == selected),
            key=lambda snapshot: (snapshot.playing, snapshot.position))

    def _retain_continuation(self, current: list[Snapshot]) -> None:
        """Follow one unambiguous metadata change without retaining COM objects.

        Match unchanged bindings first. Only one removed and one added record
        in the selected app can establish a continuation. Multiple simultaneous
        changes and duplicate metadata are not proof of tab identity.
        This affects display selection only; actions still require exact media.
        """
        previous, self._previous_sessions = self._previous_sessions, current
        selected = self._selector.selected
        old_ids = Counter(s.session_id for s in previous)
        new_ids = Counter(s.session_id for s in current)
        if not selected or new_ids[selected] or old_ids[selected] != 1:
            return
        old = next(s for s in previous if s.session_id == selected)
        removed = [s for s in previous if s.app == old.app and not new_ids[s.session_id]]
        added = [s for s in current if s.app == old.app and not old_ids[s.session_id]]
        if len(removed) == len(added) == 1 and not old.session_ambiguous:
            self._selector.selected = added[0].session_id

    @staticmethod
    def _session_id(app: str, artist: str, title: str,
                    album: str, duration: float) -> str:
        evidence = "\x1f".join((app, artist, title, album, f"{duration:.3f}"))
        return hashlib.sha1(
            evidence.encode("utf-8"), usedforsecurity=False).hexdigest()

    async def _records(
            self, sessions, cache=None, check=None) -> list[tuple[object, Snapshot, str]]:
        records = []
        for session in sessions:
            try:
                if check is not None:
                    check()
                playback = session.get_playback_info()
                status = (playback.playback_status.name
                          if playback and playback.playback_status else "")
                fields = cache.get(id(session)) if cache is not None else None
                if fields is None:
                    media = await _winrt_call(session.try_get_media_properties_async,
                                              check=check)
                    fields = ((media.artist or "").strip(), (media.title or "").strip(),
                              (media.album_title or "").strip())
                    if cache is not None:
                        cache[id(session)] = fields
                timeline = session.get_timeline_properties()
            except TimeoutError:
                raise
            except (OSError, RuntimeError):
                logger.debug("media session disappeared while being read", exc_info=True)
                continue
            app = session.source_app_user_model_id or ""
            artist, title, album = fields
            duration = timeline.end_time.total_seconds()
            updated = timeline.last_updated_time
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            snapshot = Snapshot(
                app=app,
                artist=artist,
                title=title,
                album=album,
                duration=duration,
                position=timeline.position.total_seconds(),
                updated_at=updated,
                playing=(status == "PLAYING"),
                ok=bool(title),
                session_id=self._session_id(app, artist, title, album, duration),
            )
            if snapshot.ok:
                records.append((session, snapshot, status))
        counts = Counter(snapshot.session_id for _, snapshot, _ in records)
        return [(session, replace(snapshot, session_ambiguous=counts[snapshot.session_id] > 1),
                 status) for session, snapshot, status in records]

    async def _resolve_session(self, snapshot: Snapshot, check=None):
        if not snapshot.ok or not snapshot.session_id or snapshot.session_ambiguous:
            return None
        if self._manager is None:
            return None
        if check is not None:
            check()
        records = await self._records(self._manager.get_sessions(), check=check)
        matches = [session for session, candidate, _status in records
                   if candidate.session_id == snapshot.session_id]
        return matches[0] if len(matches) == 1 else None

    async def _matching_media(self, session, snapshot: Snapshot, check=None):
        """Recheck track identity immediately before acting on a session."""
        try:
            media = await _winrt_call(session.try_get_media_properties_async, check=check)
            timeline = session.get_timeline_properties()
        except TimeoutError:
            raise
        except (OSError, RuntimeError):
            return None
        identity = self._session_id(
            session.source_app_user_model_id or "",
            (media.artist or "").strip(),
            (media.title or "").strip(),
            (media.album_title or "").strip(),
            timeline.end_time.total_seconds(),
        )
        return media if identity == snapshot.session_id else None
