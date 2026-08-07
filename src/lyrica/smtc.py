# -*- coding: utf-8 -*-
"""Windows global media session (SMTC) reader.

Runs on a background thread with its own asyncio loop and publishes
immutable state snapshots in `SmtcReader.snapshot`. Position is
interpolated by callers via `Snapshot.live_position()`.
"""
import asyncio
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)

# Browser apps may leave `artist` empty and encode "Artist - Title" in the title
BROWSER_HINTS = ("chrome", "msedge", "firefox", "opera", "brave", "vivaldi")

NOISE = re.compile(
    r"[\(\[][^\)\]]*(official|oficial|video|audio|lyric|letra|visualizer|remaster|hd|4k|mv|m/v)[^\)\]]*[\)\]]",
    re.I,
)


def clean_title(title: str) -> str:
    """Strip video-title noise like "(Official Video)" and stray separators."""
    t = NOISE.sub("", title)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" -–|·")


def split_browser_title(title: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title). Empty artist if no separator."""
    for sep in (" - ", " – ", " — ", " | "):
        if sep in title:
            artist, track = title.split(sep, 1)
            return artist.strip(), track.strip()
    return "", title.strip()


@dataclass(frozen=True)
class Snapshot:
    app: str = ""
    artist: str = ""
    title: str = ""
    album: str = ""
    duration: float = 0.0          # seconds; 0 if unknown
    position: float = 0.0          # seconds at the moment `updated_at`
    updated_at: Optional[datetime] = None  # when that position was reported (UTC)
    playing: bool = False
    ok: bool = False               # a valid session exists

    @property
    def is_browser(self) -> bool:
        return any(h in self.app.lower() for h in BROWSER_HINTS)

    def norm_artist_title(self) -> tuple[str, str]:
        """Artist/title normalized for lyrics lookup."""
        artist, title = self.artist, self.title
        if not artist and self.is_browser:
            artist, title = split_browser_title(title)
        return artist.strip(), clean_title(title)

    def track_key(self) -> str:
        return f"{self.app}|{self.artist}|{self.title}"

    def live_position(self) -> float:
        """Playback position interpolated to now."""
        if not self.ok or self.updated_at is None:
            return 0.0
        pos = self.position
        if self.playing:
            pos += (datetime.now(timezone.utc) - self.updated_at).total_seconds()
        if self.duration > 0:
            pos = min(pos, self.duration)
        return max(pos, 0.0)


class SmtcReader:
    """Background thread refreshing `self.snapshot` every `interval` seconds."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.snapshot = Snapshot()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="smtc-reader")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        asyncio.run(self._loop())

    async def _loop(self):
        while not self._stop.is_set():
            try:
                self.snapshot = await self._read()
            except Exception:
                self.snapshot = Snapshot()
            await asyncio.sleep(self.interval)

    async def _read(self) -> Snapshot:
        mgr = await SessionManager.request_async()
        sessions = list(mgr.get_sessions())
        if not sessions:
            return Snapshot()

        # Priority: playing session > paused > anything else
        def score(s):
            try:
                st = s.get_playback_info().playback_status.name
            except Exception:
                st = ""
            return 2 if st == "PLAYING" else (1 if st == "PAUSED" else 0)

        best = max(sessions, key=score)
        media = await best.try_get_media_properties_async()
        tl = best.get_timeline_properties()
        pb = best.get_playback_info()
        status = pb.playback_status.name if pb and pb.playback_status else ""
        updated = tl.last_updated_time
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)

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
