"""Platform selection for reading what is playing.

The rest of the app only ever sees a `Snapshot` and a reader with start/stop,
so adding a platform means adding a module here rather than touching anything
that draws or looks up lyrics.
"""
import logging
import sys

from lyrica.sessions.base import (
    SessionReader,
    Snapshot,
    clean_title,
    split_browser_title,
    strip_artist_prefix,
)

logger = logging.getLogger(__name__)


class NullSessionReader(SessionReader):
    """Reports nothing, forever.

    Returned when no platform reader is usable — a Mac without the helper
    installed, or anything else. The overlay then sits waiting rather than
    failing to start, which keeps the reason visible on screen instead of in a
    traceback the user never sees.
    """

    def __init__(self, reason: str = "", interval: float = 0.5):
        super().__init__(interval)
        self.reason = reason

    @staticmethod
    def available() -> bool:
        return True

    def _run(self):
        self._stop.wait()


def reader_classes() -> list[type[SessionReader]]:
    """Candidate readers for this platform, imported lazily.

    Each one imports things that only exist on its own platform, so importing
    them all eagerly would break the very selection this performs.
    """
    if sys.platform == "win32":
        from lyrica.sessions.windows import WindowsSessionReader
        return [WindowsSessionReader]
    if sys.platform == "darwin":
        from lyrica.sessions.macos import MacSessionReader
        return [MacSessionReader]
    return []


def unavailable_reason() -> str:
    """Why nothing is being read, phrased for someone looking at the overlay."""
    if sys.platform == "darwin":
        return "Install media-control:  brew install ungive/media-control/media-control"
    if sys.platform == "win32":
        return "Windows media session unavailable"
    return f"No media session support on {sys.platform}"


def create_reader(interval: float = 0.5) -> SessionReader:
    """The best reader this machine can use, or one that reports why not."""
    for cls in reader_classes():
        try:
            if cls.available():
                logger.info("using %s", cls.__name__)
                return cls(interval)
        except Exception:
            logger.exception("%s failed its availability check", cls.__name__)
    reason = unavailable_reason()
    logger.warning("no session reader available: %s", reason)
    return NullSessionReader(reason, interval)


__all__ = [
    "NullSessionReader",
    "SessionReader",
    "Snapshot",
    "clean_title",
    "create_reader",
    "split_browser_title",
    "strip_artist_prefix",
]
