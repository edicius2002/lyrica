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
