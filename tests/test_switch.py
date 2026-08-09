"""One song at a time: what is on screen is always one song's worth."""
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from lyrica.lyrics import Lyrics


def _snap(title, artist, key):
    return SimpleNamespace(
        ok=True, playing=True, position=1.0, duration=200.0,
        updated_at=datetime.now(UTC), live_position=lambda: 1.0,
        track_key=lambda: key, is_browser=False, artist=artist, title=title,
        album="", norm_artist_title=lambda: (artist, title),
        lookup_candidates=lambda: [(artist, title)])


@pytest.fixture
def panel(overlay):
    from lyrica import app as A
    first = A.Track(
        gen=1, snapshot=_snap("Traductor", "Tiago PZK", "k|A"),
        lyrics=Lyrics(lines=[(0.0, "first of A"), (5.0, "last of A")],
                      words=[[], []], synced=True),
        lyrics_state=A.LYRICS_PRESENT, searched=True, cover=b"x")
    overlay._loading = first
    overlay._promote()
    overlay._go_to_line(1, overlay.lyrics)
    overlay.root.update()
    return overlay


def _line(panel):
    view = panel._views.get(panel.line_index)
    return view.text if view is not None else None


def test_the_outgoing_song_keeps_its_last_line_until_the_next_is_whole(panel):
    # `_start_fetch` used to destroy the column the moment a change was
    # detected, so the final state of a song was its thumbnail, its name and a
    # hole — for as long as the next song's lyrics took to arrive.
    from lyrica import app as A

    assert _line(panel) == "last of A"
    panel._loading = A.Track(gen=2, snapshot=_snap("CHIHIRO", "B", "k|B"),
                             deadline=time.monotonic() + 100)
    assert _line(panel) == "last of A", "the change alone must move nothing"

    panel._loading.lyrics = Lyrics(lines=[(0.0, "first of B")], words=[[]],
                                   synced=True)
    panel._loading.lyrics_state = A.LYRICS_PRESENT
    assert not panel._ready_to_show(), "the cover has not been looked for yet"
    assert _line(panel) == "last of A", "B's words must not sing under A's card"


def test_everything_changes_in_the_same_move(panel):
    from lyrica import app as A

    panel._loading = A.Track(
        gen=2, snapshot=_snap("CHIHIRO", "Billie Eilish", "k|B"),
        lyrics=Lyrics(lines=[(0.0, "first of B")], words=[[]], synced=True),
        lyrics_state=A.LYRICS_PRESENT, searched=True, cover=b"y")
    panel._promote()
    panel._go_to_line(0, panel.lyrics)
    assert _line(panel) == "first of B"
    assert panel._card_for(panel._shown.snapshot) == ("CHIHIRO", "Billie Eilish")
    assert panel.track_key == "k|B"


def test_a_seek_does_not_outlive_the_song_it_was_made_on(panel):
    # Its target drove the next song's clock, and did not converge until that
    # song had played as long as the position clicked in the last one.
    from lyrica import app as A

    panel._awaiting_seek = (180.0, 0.0)
    panel._loading = A.Track(gen=2, snapshot=_snap("B", "b", "k|B"),
                             lyrics_state=A.LYRICS_ABSENT, searched=True)
    panel._promote()
    assert panel._awaiting_seek is None


def test_a_song_with_no_cover_does_not_wear_the_last_one(panel):
    # "The search is over" and "the search found something" are different
    # facts. Treating them as one left the sleeve, the wash, the palette and
    # the border of the previous song up for the whole of this one.
    from lyrica import app as A

    panel._backdrop_item = panel.canvas.create_rectangle(0, 0, 10, 10)
    panel._thumb_photo = object()
    panel._loading = A.Track(gen=2, snapshot=_snap("B", "b", "k|B"),
                             lyrics_state=A.LYRICS_ABSENT, searched=True)
    panel._promote()
    assert panel._backdrop_item is None
    assert panel._thumb_photo is None
    assert panel._cover_data is None


def test_a_worker_that_answers_late_writes_where_nobody_is_looking(panel):
    # The guards this replaces were six check-then-store pairs on shared
    # attributes. A stale worker now fills a Track that is simply never put up.
    from lyrica import app as A

    stale = panel._loading
    panel._loading = A.Track(gen=3, snapshot=_snap("C", "c", "k|C"),
                             deadline=time.monotonic() + 100)
    stale.lyrics = Lyrics(lines=[(0.0, "from a song nobody is playing")],
                          words=[[]], synced=True)
    stale.lyrics_state = A.LYRICS_PRESENT
    stale.searched = True
    assert not panel._ready_to_show(), "the abandoned track must not qualify"
    assert panel.lyrics is not stale.lyrics
