"""Provider interface for lyrics sources."""
from abc import ABC, abstractmethod

from lyrica.lyrics import Lyrics, Precision


class LyricsProvider(ABC):
    """A lyrics source. Implementations must be safe to call from any thread."""

    name: str = "base"

    # The best this source can ever return. It lets the cascade stop as soon as
    # nothing left to ask could improve on what it already holds — without it,
    # a line-level answer would either end the search while a word-level source
    # went unasked, or query every source on every track to find out.
    max_precision: Precision = Precision.LINE

    @abstractmethod
    def fetch(self, artist: str, title: str, duration: float = 0.0,
              album: str = "") -> Lyrics | None:
        """Return lyrics for the track, or None if this source has nothing."""
