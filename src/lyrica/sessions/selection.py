"""Pure stable selection policy for competing playback sessions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionCandidate:
    """Only the facts the selection policy needs from a platform session."""

    identity: str
    playing: bool
    paused: bool


class StableSessionSelector:
    """Retain one session until playback state gives a reason to switch."""

    def __init__(self):
        self.selected: str | None = None

    def choose(self, candidates: list[SessionCandidate]) -> str | None:
        if not candidates:
            self.selected = None
            return None
        retained = next(
            (candidate for candidate in candidates
             if candidate.identity == self.selected),
            None,
        )
        if retained is not None and retained.playing:
            return retained.identity

        playing = [candidate for candidate in candidates if candidate.playing]
        if playing:
            chosen = min(playing, key=lambda candidate: candidate.identity)
        elif retained is not None:
            chosen = retained
        else:
            paused = [candidate for candidate in candidates if candidate.paused]
            chosen = min(paused or candidates, key=lambda candidate: candidate.identity)
        self.selected = chosen.identity
        return chosen.identity
