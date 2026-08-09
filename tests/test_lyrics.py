"""Unit tests for LRC parsing and the Lyrics model (offline)."""
import pytest

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


# --- backing vocals ---------------------------------------------------------

def test_progress_runs_over_whichever_sequence_it_is_given():
    # The line's own words and what was sung behind it are two sequences, and
    # they overlap, so the same rule has to serve both.
    from lyrica.lyrics import Lyrics, progress_in

    words = [(1.0, 2.0, "a"), (3.0, 4.0, "b")]
    assert progress_in(words, 0.5) == (-1, 0.0)
    assert progress_in(words, 1.5) == (0, pytest.approx(0.5))
    assert progress_in(words, 2.5) == (0, 1.0), "a gap holds it complete"
    assert progress_in(words, 3.5) == (1, pytest.approx(0.5))

    lyr = Lyrics(lines=[(0.0, "a b")], words=[words], synced=True,
                 backing=["(oh)"], backing_words=[[(2.2, 2.8, "(oh)")]])
    assert lyr.backing_progress_at(0, 2.5) == (0, pytest.approx(0.5))
    assert lyr.word_progress_at(0, 2.5) == (0, 1.0), "the two run independently"


def test_backing_is_asked_for_by_line_and_answers_empty_where_there_is_none():
    from lyrica.lyrics import Lyrics

    lyr = Lyrics(lines=[(0.0, "a"), (5.0, "b")], words=[[], []], synced=True,
                 backing=["(oh)", ""], backing_words=[[(1.0, 2.0, "(oh)")], []])
    assert lyr.backing_at(0) == ("(oh)", [(1.0, 2.0, "(oh)")])
    assert lyr.backing_at(1) == ("", [])
    assert lyr.backing_at(9) == ("", []), "out of range is not a crash"


def test_a_source_without_backing_at_all_still_answers():
    from lyrica.lyrics import Lyrics

    assert Lyrics(lines=[(0.0, "a")], words=[[]], synced=True).backing_at(0) == ("", [])
