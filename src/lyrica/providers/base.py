"""Provider interface for lyrics sources."""
from abc import ABC, abstractmethod

from lyrica.lyrics import Lyrics


class LyricsProvider(ABC):
    """A lyrics source. Implementations must be safe to call from any thread."""

    name: str = "base"

    @abstractmethod
    def fetch(self, artist: str, title: str, duration: float = 0.0,
              album: str = "") -> Lyrics | None:
        """Return lyrics for the track, or None if this source has nothing."""
