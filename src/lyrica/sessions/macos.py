"""macOS session reader, backed by the `media-control` CLI.

**Unverified.** Written and tested against the tool's documented output, but
never run on a Mac. Treat a first run there as the real test.

macOS has no open equivalent of the Windows media session. The framework that
knows what is playing is private, and since macOS 15.4 it refuses callers
without an Apple entitlement — so an in-process binding cannot work at all.
`media-control` gets around that legitimately, by driving a system binary that
already holds the entitlement, and prints the result as JSON.

That puts it outside the app, which is why this is a subprocess rather than a
library, and why the reader has to survive the tool simply not being installed.
"""
import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime

from lyrica.sessions.base import SessionReader, Snapshot

CLI = "media-control"
CALL_TIMEOUT_S = 5

logger = logging.getLogger(__name__)


def parse_timestamp(value) -> datetime:
    """The moment the position was reported.

    Falls back to now, which is very nearly true: the value was read by a call
    that just returned. The tool's own documentation notes elapsed time can be
    off by up to a second when polled rather than streamed, so this is within
    the accuracy the source offers either way.
    """
    if isinstance(value, str):
        try:
            # Python 3.11 reads the trailing Z itself.
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        # A timestamp without a zone is read as UTC: the alternative is the
        # machine's local zone, which would silently shift the position by hours.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        # Seconds or milliseconds since the epoch — anything past the year 3000
        # in seconds is milliseconds.
        seconds = value / 1000 if value > 32_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return datetime.now(UTC)
    return datetime.now(UTC)


def snapshot_from_payload(payload: dict | None) -> Snapshot:
    """Map one `media-control get` result onto a Snapshot.

    Only `bundleIdentifier`, `playing` and `title` are guaranteed present, so
    everything else is read defensively: a track with no stated duration is
    normal, not a failure.
    """
    if not payload or not payload.get("title"):
        return Snapshot()
    return Snapshot(
        app=str(payload.get("bundleIdentifier") or ""),
        artist=str(payload.get("artist") or "").strip(),
        title=str(payload.get("title") or "").strip(),
        album=str(payload.get("album") or "").strip(),
        duration=float(payload.get("duration") or 0.0),
        position=float(payload.get("elapsedTime") or 0.0),
        updated_at=parse_timestamp(payload.get("timestamp")),
        playing=bool(payload.get("playing")),
        ok=True,
    )


class MacSessionReader(SessionReader):
    """Polls `media-control get` and publishes what it reports."""

    @staticmethod
    def available() -> bool:
        return shutil.which(CLI) is not None

    def _run(self):
        while not self._stop.is_set():
            try:
                self.snapshot = self._read()
            except Exception:
                # Same reasoning as the Windows reader: this thread is the
                # overlay's only source of truth, and a dead one freezes the
                # window on a stale line with nothing to show for it.
                logger.exception("media-control read failed; reporting no session")
                self.snapshot = Snapshot()
            self._stop.wait(self.interval)

    def seek(self, seconds: float) -> bool:
        """Ask the player to jump. **Unverified**, like the rest of this reader."""
        try:
            result = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [CLI, "seek", f"{max(0.0, seconds):.3f}"],
                capture_output=True, text=True, timeout=CALL_TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.debug("could not ask %s to seek", CLI, exc_info=True)
            return False
        return result.returncode == 0

    def _read(self) -> Snapshot:
        try:
            result = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [CLI, "get"], capture_output=True, text=True,
                timeout=CALL_TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.debug("could not run %s", CLI, exc_info=True)
            return Snapshot()

        out = (result.stdout or "").strip()
        # The tool answers `null` when nothing is playing, which is a valid
        # answer rather than a failure.
        if result.returncode != 0 or not out or out == "null":
            return Snapshot()
        try:
            payload = json.loads(out)
        except ValueError:
            logger.debug("unparseable %s output", CLI)
            return Snapshot()
        return snapshot_from_payload(payload)
