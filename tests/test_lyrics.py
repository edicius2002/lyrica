# -*- coding: utf-8 -*-
"""Unit tests for LRC parsing and the Lyrics model (offline)."""
from lyrica.lyrics import Lyrics, parse_lrc

SAMPLE_LRC = """\
[00:12.34]First line
[00:15.00]Second line
[01:02.5]Third line

[not a timestamp]ignored
"""


def test_parse_lrc_extracts_timestamps_and_text():
    lines = parse_lrc(SAMPLE_LRC)
    assert lines == [(12.34, "First line"), (15.0, "Second line"), (62.5, "Third line")]


def test_parse_lrc_sorts_out_of_order_lines():
    lines = parse_lrc("[00:20.00]B\n[00:10.00]A")
    assert [t for t, _ in lines] == [10.0, 20.0]


def test_line_index_at_boundaries():
    lyr = Lyrics(lines=parse_lrc(SAMPLE_LRC), synced=True)
    assert lyr.line_index_at(0.0) == -1       # before first line
    assert lyr.line_index_at(12.34) == 0      # exactly on a timestamp
    assert lyr.line_index_at(14.99) == 0
    assert lyr.line_index_at(15.0) == 1
    assert lyr.line_index_at(999.0) == 2      # past the end stays on last line


def test_empty_lyrics_never_crash():
    lyr = Lyrics()
    assert lyr.line_index_at(10.0) == -1
