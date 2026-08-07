"""Lyrics data model and LRC parsing."""
import re
from dataclasses import dataclass, field
from enum import IntEnum

LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


class Precision(IntEnum):
    """How exactly a result can be followed, worst to best.

    Ordered so results compare directly. A source that answers is not the same
    as a source that answers well: plain text has no timing at all, and the
    overlay can only page it by playback progress, which looks synchronised
    while being a guess.
    """

    NONE = 0    # no lyrics
    PLAIN = 1   # text, no timing
    LINE = 2    # a timestamp per line
    WORD = 3    # a timestamp per word


@dataclass
class Lyrics:
    lines: list = field(default_factory=list)  # [(t_seconds, text)] sorted by time
    plain: str = ""
    synced: bool = False
    source: str = ""
    instrumental: bool = False

    @property
    def precision(self) -> Precision:
        if self.synced and self.lines:
            return Precision.LINE
        if self.plain:
            return Precision.PLAIN
        return Precision.NONE

    @property
    def is_definitive(self) -> bool:
        """True when no other source could improve on this.

        An instrumental is definitive despite scoring NONE: the track having no
        lyrics is a complete answer, and asking further sources would only
        invite one of them to invent some.
        """
        return self.instrumental or self.precision >= Precision.LINE

    def line_index_at(self, t: float) -> int:
        """Index of the active line at time t (-1 before the first line)."""
        idx = -1
        for i, (ts, _) in enumerate(self.lines):
            if ts <= t:
                idx = i
            else:
                break
        return idx


def parse_lrc(lrc: str) -> list:
    """Parse an LRC body into a sorted [(seconds, text)] list."""
    lines = []
    for raw in lrc.splitlines():
        m = LRC_LINE.match(raw.strip())
        if m:
            t = int(m.group(1)) * 60 + float(m.group(2))
            lines.append((t, m.group(3).strip()))
    lines.sort(key=lambda x: x[0])
    return lines
