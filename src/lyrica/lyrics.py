"""Lyrics data model and LRC parsing."""
import re
from dataclasses import dataclass, field
from enum import IntEnum

LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")

# (start_seconds, end_seconds, text). Kept as a plain tuple so it survives a
# JSON round trip through the cache without a custom encoder.
Word = tuple[float, float, str]

# A word whose end has to be inferred from the next word's start is capped at
# this. Musixmatch's richsync gives offsets but no durations, so a word before
# an instrumental break would otherwise stay lit for the length of the break.
MAX_INFERRED_WORD_S = 1.5


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
    # One list of Words per line, index-matched to `lines`. An empty inner list
    # means that line has no word timing, which happens on real sources: a track
    # can be word-timed for its verses and line-timed for a shouted chorus.
    words: list = field(default_factory=list)
    plain: str = ""
    synced: bool = False
    source: str = ""
    instrumental: bool = False
    exact: bool = False  # the provider identified the track, rather than guessing at it
    # The (artist, title) that produced this answer. A browser can state a track
    # several defensible ways and only one of them finds it, so the reading that
    # worked is worth keeping: it is the closest thing to a correct name for the
    # song that anything in the process knows.
    queried: tuple = ()

    @property
    def precision(self) -> Precision:
        if self.synced and self.lines:
            return Precision.WORD if any(self.words) else Precision.LINE
        if self.plain:
            return Precision.PLAIN
        return Precision.NONE

    def words_at(self, line_index: int) -> list:
        """Word timings for a line, or an empty list when it has none."""
        if 0 <= line_index < len(self.words):
            return self.words[line_index]
        return []

    def word_index_at(self, line_index: int, t: float) -> int:
        """Index of the word being sung at time t within a line.

        -1 before the first word starts, so a line can appear in full before
        anything is highlighted rather than lighting its first word early.
        """
        idx = -1
        for i, (start, _, _) in enumerate(self.words_at(line_index)):
            if start <= t:
                idx = i
            else:
                break
        return idx

    def word_progress_at(self, line_index: int, t: float) -> tuple[int, float]:
        """The active word and how far through it playback is, from 0 to 1.

        The fraction is what lets a renderer sweep across a word instead of
        switching it on, and it saturates at 1 once the word's end has passed —
        so a gap before the next word holds the sweep complete rather than
        letting it run on.
        """
        i = self.word_index_at(line_index, t)
        if i < 0:
            return -1, 0.0
        start, end, _ = self.words_at(line_index)[i]
        span = end - start
        if span <= 0:
            return i, 1.0
        return i, max(0.0, min(1.0, (t - start) / span))

    @property
    def is_definitive(self) -> bool:
        """True when no other source could improve on this.

        An instrumental counts only when the provider matched the track
        exactly. "This recording has no lyrics" is a complete answer, but a
        fuzzy search reaches for the nearest thing it can find, and karaoke and
        backing-track uploads sit right next to the songs they came from. One
        of those matched loosely would otherwise end the search and report a
        song as instrumental while another source had its lyrics all along —
        a silent wrong answer, which is the worst kind.
        """
        return (self.instrumental and self.exact) or self.precision >= Precision.LINE

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
