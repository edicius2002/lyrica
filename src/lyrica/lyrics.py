"""Lyrics data model and LRC parsing."""
import re
from dataclasses import dataclass, field
from enum import IntEnum

LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
PARENTHETICAL_SUFFIX = re.compile(r"\s(?:\([^()]*\)\s*)+$")

# (start_seconds, end_seconds, text). Kept as a plain tuple so it survives a
# JSON round trip through the cache without a custom encoder.
Word = tuple[float, float, str]

# A word whose end has to be inferred from the next word's start is capped at
# this. Musixmatch's richsync gives offsets but no durations, so a word before
# an instrumental break would otherwise stay lit for the length of the break.
MAX_INFERRED_WORD_S = 1.75

# Where a backing clock came from.  These are deliberately about timing, not
# about whether parentheses were semantically a backing vocal: direct TTML has
# measured starts and ends, a hybrid has measured TTML windows transformed onto
# another source's clock, and Richsync has source starts with inferred ends.
BACKING_SOURCE_EXACT = "source_exact"
BACKING_CROSS_SOURCE_ALIGNED = "cross_source_aligned"
BACKING_INFERRED = "inferred"


def split_parenthetical_adlib(text: str, words: list[Word]) -> tuple | None:
    """Separate a timed parenthetical backing phrase from one lyric line.

    Parentheses conventionally mark an echo or backing vocal, but punctuation
    alone cannot prove that. This accepts only complete word tokens inside
    balanced parentheses when they overlap the remaining lead words, or when
    they form a parenthesized suffix after the lead. A sequential parenthesis
    in the middle of a line, an inline fragment such as ``word(yeah)``, or an
    entire parenthetical line stays in the lead rather than being placed in the
    wrong visual channel.
    """
    main: list[Word] = []
    backing: list[Word] = []
    group: list[Word] = []
    depth = 0
    lead_after_backing = False

    for word in words:
        token = word[2].strip()
        if not token:
            return None
        opens, closes = token.count("("), token.count(")")
        if depth:
            group.append(word)
            depth += opens - closes
            if depth < 0:
                return None
            if depth == 0:
                backing.extend(group)
                group = []
            continue

        if opens or closes:
            # A parenthetical ad-lib has its own whole timed tokens. Splitting
            # a token would make its timing describe two different voices.
            if not token.startswith("(") or closes > opens:
                return None
            group = [word]
            depth = opens - closes
            if depth == 0:
                backing.extend(group)
                group = []
            continue
        if backing:
            lead_after_backing = True
        main.append(word)

    if depth or not main or not backing:
        return None
    overlaps_lead = any(start < lead_end and lead_start < end
                        for start, end, _text in backing
                        for lead_start, lead_end, _lead_text in main)
    # A closing parenthetical is a common editorial convention for an echoed
    # answer. Its own word timings say exactly when it is sung, even when it
    # follows the lead rather than overlapping it. Parentheses in the middle
    # still need overlap: otherwise an aside or alternate wording is too easy
    # to mistake for a backing vocal.
    is_parenthetical_suffix = (
        not lead_after_backing
        and PARENTHETICAL_SUFFIX.search(text) is not None
    )
    if not (overlaps_lead or is_parenthetical_suffix):
        return None

    kept, phrases = _separate_parenthetical_text(text)
    if not kept or not phrases:
        return None
    return kept, main, " ".join(phrases), backing


def _separate_parenthetical_text(text: str) -> tuple[str, list[str]]:
    """Return the lead text and complete parenthetical phrases it contains."""
    lead: list[str] = []
    phrases: list[str] = []
    phrase: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            if depth:
                phrase.append(char)
            else:
                phrase = [char]
            depth += 1
        elif char == ")" and depth:
            phrase.append(char)
            depth -= 1
            if not depth:
                phrases.append("".join(phrase))
        elif depth:
            phrase.append(char)
        else:
            lead.append(char)
    if depth:
        return text, []
    return " ".join("".join(lead).split()), phrases


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
    # What was sung behind each line, index-matched to `lines`, and empty where
    # there was nothing. Apart from `words` rather than folded into it because
    # the two overlap in time: a backing vocal answers a line while it is still
    # being sung, so one sequence would interleave them into nonsense.
    backing: list = field(default_factory=list)
    backing_words: list = field(default_factory=list)
    # Timing provenance for each backing line: direct measured source timing,
    # a locally validated cross-source transform, or inferred Richsync ends.
    # Kept per line because one hybrid can contain both transformed TTML and
    # untouched Richsync responses.
    backing_timing: list = field(default_factory=list)
    # Diagnostics for transformed backing clocks, index-matched to ``lines``.
    # Empty for direct-source and inferred timings. A hybrid entry records the
    # affine transform and the local anchor error which justified this graft.
    backing_alignment: list = field(default_factory=list)
    # Richsync suffixes can be sequential rather than sung behind the lead.
    # They remain an ad-lib lane visually, but hold their lead row until done.
    backing_modes: list = field(default_factory=list)
    # Who sings each line, as the source's own agent id, index-matched to
    # `lines`. Empty when the source does not say — which is most of them.
    voices: list = field(default_factory=list)
    # What each of those ids is: "person", "group" or "other", as declared.
    # Only the ones the source declared; an id absent here was used on a line
    # and never introduced, and that is a different thing from being a person.
    singers: dict = field(default_factory=dict)

    def voice_at(self, line_index: int) -> str:
        """Who sings a line, or "" when nothing said."""
        if 0 <= line_index < len(self.voices):
            return self.voices[line_index]
        return ""

    def voice_sides(self) -> dict:
        """Which way each voice leans: -1 towards the left, +1 the right, 0 not.

        Empty when there is nothing to tell apart, and that is the common case:
        one singer, or a source that never said. Nothing then moves, so a solo
        track looks exactly as it did.

        A side goes to the first two voices in the order the song introduces
        them, so whoever opens is on the left — which is both what the reference
        does and the only ordering a listener could predict. Frequency would
        have been the alternative and is worse: it can only be known after the
        whole song, so the singer with the second verse would take the left half
        of a duet purely by having more lines.

        Anything the document calls a group keeps the middle, and so does any
        third voice. A duet has two sides; what the two of them sing together
        belongs to neither, and inventing a third position for it would say
        there was a third singer.
        """
        order: list = []
        for who in self.voices:
            if who and who not in order:
                order.append(who)
        apart = [w for w in order if self.singers.get(w) != "group"]
        if len(apart) < 2:
            return {}
        return {w: (-1 if w == apart[0] else 1 if w == apart[1] else 0)
                for w in order}

    def backing_at(self, line_index: int) -> tuple:
        """(text, words) sung behind a line, or ("", []) where nothing was."""
        if 0 <= line_index < len(self.backing) and self.backing[line_index]:
            words = (self.backing_words[line_index]
                     if line_index < len(self.backing_words) else [])
            return self.backing[line_index], words
        return "", []

    def backing_timing_at(self, line_index: int) -> str:
        """The provenance/confidence class of this backing line's timing."""
        if 0 <= line_index < len(self.backing_timing):
            value = self.backing_timing[line_index]
            # Temporary source compatibility for callers constructing Lyrics
            # directly; disk entries are invalidated by the cache version.
            return BACKING_SOURCE_EXACT if value == "exact" else value
        return BACKING_SOURCE_EXACT

    def backing_alignment_at(self, line_index: int) -> dict:
        """Cross-source alignment diagnostics for one backing line."""
        if 0 <= line_index < len(self.backing_alignment):
            return self.backing_alignment[line_index]
        return {}

    def backing_mode_at(self, line_index: int) -> str:
        """Whether a backing vocal overlaps or follows its lead."""
        if 0 <= line_index < len(self.backing_modes):
            return self.backing_modes[line_index]
        return ""

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

    def backing_progress_at(self, line_index: int, t: float) -> tuple[int, float]:
        """The same as `word_progress_at`, for what was sung behind the line."""
        return progress_in(self.backing_at(line_index)[1], t)

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
        """The active word and how far through it playback is, from 0 to 1."""
        return progress_in(self.words_at(line_index), t)

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


def progress_in(words: list, t: float) -> tuple[int, float]:
    """The active word in a sequence and how far through it playback is.

    The fraction is what lets a renderer sweep across a word instead of
    switching it on, and it saturates at 1 once the word's end has passed — so
    a gap before the next word holds the sweep complete rather than letting it
    run on. -1 before the first word starts, so a line can appear in full before
    anything is highlighted.
    """
    i = -1
    for k, (start, _end, _text) in enumerate(words):
        if start <= t:
            i = k
        else:
            break
    if i < 0:
        return -1, 0.0
    start, end, _ = words[i]
    span = end - start
    if span <= 0:
        return i, 1.0
    return i, max(0.0, min(1.0, (t - start) / span))


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
