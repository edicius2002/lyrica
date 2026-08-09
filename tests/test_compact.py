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


# --- one song starting is one event -----------------------------------------

class Reveal:
    """The readiness rule, without the rest of the overlay."""

    def __init__(self, now=0.0):
        from lyrica.app import Track
        self._loading = Track(deadline=100.0)
        self.now = now

    def ready(self):
        import time as _time

        from lyrica.app import Overlay
        real, _time.monotonic = _time.monotonic, lambda: self.now
        try:
            return Overlay._ready_to_show(self)
        finally:
            _time.monotonic = real


def test_a_song_goes_up_only_when_all_of_it_has_arrived():
    from lyrica.app import LYRICS_ABSENT, LYRICS_PRESENT

    r = Reveal()
    assert not r.ready(), "nothing has arrived"
    r._loading.searched = True
    assert not r.ready(), "the lyrics are still out"
    r._loading.lyrics_state = LYRICS_PRESENT
    assert r.ready()

    r = Reveal()
    r._loading.lyrics_state = LYRICS_ABSENT
    assert not r.ready(), "the cover search is still out"
    r._loading.searched = True
    assert r.ready()


def test_the_search_being_over_is_not_the_search_having_found_something():
    # Conflating the two is what let a song with no cover of its own wear the
    # previous song's for as long as it played.
    from lyrica.app import LYRICS_PRESENT, Track

    empty = Track(searched=True, lyrics_state=LYRICS_PRESENT)
    assert empty.cover is None and empty.art is None
    assert empty.whole, "a song without a cover is still a whole song"


def test_a_source_that_never_answers_does_not_strand_the_panel():
    r = Reveal()
    r._loading.deadline = 50.0
    r.now = 49.0
    assert not r.ready()
    r.now = 50.0
    assert r.ready(), "the deadline has to release it"
