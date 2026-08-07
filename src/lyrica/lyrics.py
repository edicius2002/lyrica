# -*- coding: utf-8 -*-
"""Lyrics data model and LRC parsing."""
import re
from dataclasses import dataclass, field

LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


@dataclass
class Lyrics:
    lines: list = field(default_factory=list)  # [(t_seconds, text)] sorted by time
    plain: str = ""
    synced: bool = False
    source: str = ""
    instrumental: bool = False

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
