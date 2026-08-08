"""When the panel should shrink to just the card (offline, no display)."""
from lyrica.app import (
    LYRICS_ABSENT,
    LYRICS_PRESENT,
    LYRICS_UNKNOWN,
    lyrics_state,
)
from lyrica.lyrics import Lyrics


def synced(lines=(("0.0", "una linea"),)):
    return Lyrics(lines=[(float(t), text) for t, text in lines], synced=True)


def test_a_synced_lyric_keeps_the_panel_open():
    assert lyrics_state(synced()) == LYRICS_PRESENT


def test_nothing_found_collapses_it():
    assert lyrics_state(None) == LYRICS_ABSENT


def test_an_empty_result_collapses_it():
    assert lyrics_state(Lyrics(lines=[], synced=True)) == LYRICS_ABSENT


def test_unsynced_words_collapse_it_too():
    # Plain words with no timings are never drawn, so room for them is room for
    # nothing. This is the case that looks like a hit and is not one.
    assert lyrics_state(Lyrics(lines=[(0.0, "una linea")],
                               synced=False)) == LYRICS_ABSENT


def test_the_three_states_are_distinct():
    # The third exists because `None` lyrics means both "still asking" and
    # "nobody has any", and collapsing on the first would shrink and grow the
    # panel again on every track change.
    assert len({LYRICS_UNKNOWN, LYRICS_PRESENT, LYRICS_ABSENT}) == 3


def test_a_finished_search_never_reports_unknown():
    # `lyrics_state` is only called once a search has finished, so it must
    # always commit — leaving it unknown would freeze the panel at whatever
    # size it happened to have.
    for result in (None, Lyrics(lines=[], synced=True), synced(),
                   Lyrics(lines=[(0.0, "x")], synced=False)):
        assert lyrics_state(result) != LYRICS_UNKNOWN


# --- what a search still in flight must not do ------------------------------

from lyrica.app import compact_target  # noqa: E402


def test_an_unfinished_search_leaves_the_panel_where_it_is():
    # Both directions. Reading UNKNOWN as "not absent" made a track with no
    # lyrics followed by another with none expand for the second the search
    # takes and then collapse again.
    assert compact_target(LYRICS_UNKNOWN, currently_compact=True) is True
    assert compact_target(LYRICS_UNKNOWN, currently_compact=False) is False


def test_two_songs_with_no_lyrics_in_a_row_never_expand():
    compact = False
    for _track in range(3):
        compact = compact_target(LYRICS_UNKNOWN, compact)   # search goes out
        was = compact
        compact = compact_target(LYRICS_ABSENT, compact)    # nothing found
        assert compact is True
        if _track:
            assert was is True, "it expanded between two silent tracks"


def test_a_definite_answer_still_decides():
    assert compact_target(LYRICS_ABSENT, currently_compact=False) is True
    assert compact_target(LYRICS_PRESENT, currently_compact=True) is False


# --- the first line waits on screen -----------------------------------------

def test_the_first_line_shows_before_the_singing_starts():
    # `line_index_at` reports -1 until the first timestamp, and the panel used
    # to draw nothing at all until then. On a video with an intro that is the
    # whole intro spent looking at an empty box.
    from lyrica.lyrics import Lyrics

    lyr = Lyrics(lines=[(21.4, "primera"), (25.0, "segunda")], synced=True)
    assert lyr.line_index_at(0.0) == -1, "the source of the emptiness"

    # What the tick now does with that.
    index = lyr.line_index_at(0.0)
    waiting = index < 0
    if waiting:
        index = 0
    assert (index, waiting) == (0, True)
    assert lyr.line_index_at(22.0) == 0, "and it is the same line once it starts"
