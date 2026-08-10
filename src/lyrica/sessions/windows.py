"""Windows session reader, backed by the global media transport controls.

Every player that responds to the media keys publishes here, so Spotify and
four browser sites cost one implementation rather than five.

`winsdk` is imported inside the reader rather than at module scope: importing it
on any other platform fails outright, and the package has to remain importable
everywhere for the platform selection to run at all.
"""
import asyncio
import logging
import sys
from datetime import UTC

from lyrica.sessions.base import SessionReader, Snapshot

logger = logging.getLogger(__name__)

# `request_async()` normally finishes in a few milliseconds. When the media
# controls broker is restarting, though, WinRT can leave the await pending or
# reject the COM call with RPC_E_CALL_CANCELED. Do not leave the reader thread
# stranded on either outcome: cancelling the await lets asyncio dispose of the
# operation before a fresh loop is made.
READ_TIMEOUT_S = 5.0
RETRY_INITIAL_S = 1.0
RETRY_MAX_S = 30.0


class _RestartReadLoop(Exception):
    """A WinRT operation needs a fresh asyncio loop before it is retried."""


def _session_manager_class():
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
    )
    return GlobalSystemMediaTransportControlsSessionManager


class WindowsSessionReader(SessionReader):
    """Polls the Windows media session on its own asyncio loop."""

    @staticmethod
    def available() -> bool:
        if sys.platform != "win32":
            return False
        try:
            _session_manager_class()
        except ImportError:
            return False
        return True

    def seek(self, seconds: float) -> bool:
        """Ask the current session to jump. Verified working against Spotify.

        Runs on its own loop rather than the reader's: the reader is polling on
        its thread, and a jump is a one-off the caller is waiting on.
        """
        try:
            return asyncio.run(self._seek(max(0.0, seconds)))
        except Exception:
            logger.exception("seek to %.2fs failed", seconds)
            return False

    async def _seek(self, seconds: float) -> bool:
        manager_cls = _session_manager_class()
        mgr = await manager_cls.request_async()
        session = mgr.get_current_session()
        if session is None:
            return False
        controls = session.get_playback_info().controls
        if not getattr(controls, "is_playback_position_enabled", False):
            logger.info("this player does not accept position changes")
            return False
        # The API counts in 100-nanosecond ticks, not seconds.
        return bool(await session.try_change_playback_position_async(
            int(seconds * 10_000_000)))

    def read_artwork(self) -> bytes | None:
        """The current track's artwork, if the player published any.

        Read on demand rather than with every poll: it is tens of kilobytes and
        changes once per track, where the position changes constantly.
        """
        try:
            return asyncio.run(self._artwork())
        except Exception:
            logger.debug("could not read artwork", exc_info=True)
            return None

    async def _artwork(self) -> bytes | None:
        from winsdk.windows.storage.streams import (
            Buffer,
            DataReader,
            InputStreamOptions,
        )

        manager_cls = _session_manager_class()
        mgr = await manager_cls.request_async()
        session = mgr.get_current_session()
        if session is None:
            return None
        media = await session.try_get_media_properties_async()
        reference = media.thumbnail
        if reference is None:
            return None

        stream = await reference.open_read_async()
        size = stream.size
        if not size:
            return None
        buffer = Buffer(size)
        await stream.read_async(buffer, size, InputStreamOptions.READ_AHEAD)
        reader = DataReader.from_buffer(buffer)
        # read_bytes fills a buffer that is handed to it; it does not take a
        # length and return the data.
        out = bytearray(buffer.length)
        reader.read_bytes(out)
        return bytes(out)

    def _run(self):
        delay = RETRY_INITIAL_S
        while not self._stop.is_set():
            try:
                asyncio.run(self._loop())
                return
            except _RestartReadLoop:
                # `asyncio.run` closes this loop, cancelling anything left by
                # the rejected WinRT call. Starting it again is important:
                # retrying on the same loop can keep talking to the stale COM
                # operation that caused the failure.
                if self._stop.is_set():
                    return
                logger.info("restarting media-session reader in %.0fs", delay)
                self._stop.wait(delay)
                delay = min(RETRY_MAX_S, delay * 2)

    async def _loop(self):
        while not self._stop.is_set():
            try:
                self.snapshot = await asyncio.wait_for(
                    self._read(), timeout=READ_TIMEOUT_S,
                )
            except (OSError, TimeoutError) as error:
                # A cancelled COM call is not a statement that no player is
                # running. Publish the neutral value only until the fresh
                # loop gets a real answer, then back off so a Windows broker
                # restart is not hammered hundreds of times a minute.
                logger.warning("media session read interrupted; restarting reader: %s",
                               error)
                self.snapshot = Snapshot()
                raise _RestartReadLoop from error
            except Exception:
                # Deliberately broad: this thread is the overlay's only source
                # of truth, and if it dies the window freezes on a stale line
                # with nothing to indicate anything is wrong. The traceback is
                # logged, so breadth costs diagnosis nothing.
                logger.exception("media session read failed; reporting no session")
                self.snapshot = Snapshot()
            await asyncio.sleep(self.interval)

    async def _read(self) -> Snapshot:
        manager_cls = _session_manager_class()
        mgr = await manager_cls.request_async()
        sessions = list(mgr.get_sessions())
        if not sessions:
            return Snapshot()

        # Priority: playing session > paused > anything else
        def score(s):
            try:
                st = s.get_playback_info().playback_status.name
            except OSError:
                # A session can disappear between being listed and being read;
                # WinRT surfaces that as an OSError. Rank it last and move on.
                logger.debug("session %s did not report playback status",
                             s.source_app_user_model_id)
                st = ""
            return 2 if st == "PLAYING" else (1 if st == "PAUSED" else 0)

        best = max(sessions, key=score)
        media = await best.try_get_media_properties_async()
        tl = best.get_timeline_properties()
        pb = best.get_playback_info()
        status = pb.playback_status.name if pb and pb.playback_status else ""
        updated = tl.last_updated_time
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)

        return Snapshot(
            app=best.source_app_user_model_id or "",
            artist=(media.artist or "").strip(),
            title=(media.title or "").strip(),
            album=(media.album_title or "").strip(),
            duration=tl.end_time.total_seconds(),
            position=tl.position.total_seconds(),
            updated_at=updated,
            playing=(status == "PLAYING"),
            ok=bool(media.title),
        )
