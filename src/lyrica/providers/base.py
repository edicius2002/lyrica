"""Provider interface for lyrics sources."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from lyrica.lyrics import Lyrics, Precision

if TYPE_CHECKING:
    from lyrica.providers.identity import SongQuery


class OutcomeKind(StrEnum):
    """The cache-relevant meaning of one provider attempt."""

    HIT = "hit"
    NO_MATCH = "no_match"
    RETRYABLE = "retryable"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProviderOutcome:
    """A provider answer without collapsing inability into a valid miss."""

    kind: OutcomeKind
    lyrics: Lyrics | None = None
    reason: str = ""
    retry_after: float | None = None

    def __post_init__(self):
        if self.kind is OutcomeKind.HIT and self.lyrics is None:
            raise ValueError("a hit requires lyrics")
        if self.kind is not OutcomeKind.HIT and self.lyrics is not None:
            raise ValueError("a non-hit cannot carry lyrics")
        if self.retry_after is not None and self.retry_after < 0:
            raise ValueError("retry_after cannot be negative")

    @classmethod
    def hit(cls, lyrics: Lyrics, *, reason: str = "") -> "ProviderOutcome":
        return cls(OutcomeKind.HIT, lyrics=lyrics, reason=reason)

    @classmethod
    def no_match(cls, *, reason: str = "") -> "ProviderOutcome":
        return cls(OutcomeKind.NO_MATCH, reason=reason)

    @classmethod
    def retryable(cls, *, reason: str = "") -> "ProviderOutcome":
        return cls(OutcomeKind.RETRYABLE, reason=reason)

    @classmethod
    def unavailable(cls, *, reason: str = "",
                    retry_after: float | None = None) -> "ProviderOutcome":
        return cls(OutcomeKind.UNAVAILABLE, reason=reason, retry_after=retry_after)


class LyricsProvider(ABC):
    """A lyrics source. Implementations must be safe to call from any thread."""

    name: str = "base"

    # The best this source can ever return. It lets the cascade stop as soon as
    # nothing left to ask could improve on what it already holds — without it,
    # a line-level answer would either end the search while a word-level source
    # went unasked, or query every source on every track to find out.
    max_precision: Precision = Precision.LINE

    # Whether this source states what was sung *behind* a line. Only one does,
    # and it is the reason the cascade cannot simply keep the first word-timed
    # answer that arrives: two sources can agree on precision and disagree on
    # whether there is anything there at all.
    carries_backing: bool = False

    @abstractmethod
    def lookup(self, query: "SongQuery") -> ProviderOutcome:
        """Return the cache-relevant outcome of one complete provider attempt."""

    @abstractmethod
    def fetch(self, artist: str, title: str, duration: float = 0.0,
              album: str = "") -> Lyrics | None:
        """Compatibility facade returning only usable lyrics, if any."""
