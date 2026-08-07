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

    def _run(self):
        asyncio.run(self._loop())

    async def _loop(self):
        while not self._stop.is_set():
            try:
                self.snapshot = await self._read()
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
