"""Identify production-credit rows at the edges of NetEase timed lyrics."""

import re

_OPENING_CREDIT = re.compile(r"^(?:作词|作曲|编曲|制作人|母带工程师)\s*[:：]")
_CLOSING_CREDIT = re.compile(
    r"^(?:Remix|音频工程师|混音师|附加制作|母带工程师|人声|贝斯|音频助理|录音|混音助理)"
    r"\s*[:：]",
    re.IGNORECASE,
)


def lyric_bounds(lines: list) -> tuple[int, int]:
    """Slice bounds excluding credit rows before and after the sung lyrics.

    NetEase timestamps production credits as though they were lyrics. Only the
    opening and closing runs are metadata; matching text inside the song stays.
    """
    start = 0
    for _time, text in lines:
        stripped = text.strip()
        if stripped and not _OPENING_CREDIT.match(stripped):
            break
        start += 1
    end = len(lines)
    while end > start:
        stripped = lines[end - 1][1].strip()
        if stripped and not _CLOSING_CREDIT.match(stripped):
            break
        end -= 1
    return start, end
