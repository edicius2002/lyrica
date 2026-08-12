"""One song at a time: what is on screen is always one song's worth."""
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from lyrica.lyrics import Lyrics
from lyrica.sessions import Snapshot


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


def _rendered_frame(panel):
    """The visible lyric state that a stopped transport must preserve exactly."""
    return (
        panel.line_index,
        tuple((index, view.y,
               tuple(tuple(panel.canvas.coords(entry[2])) for entry in view._items),
               tuple(panel.canvas.itemcget(entry[2], "fill") for entry in view._items))
              for index, view in sorted(panel._views.items())),
    )


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


def test_a_new_song_loads_its_own_remembered_offset(panel, monkeypatch):
    """A's correction must not travel with the outgoing panel into B."""
    from lyrica import app as A

    requested = []
    offsets = {"k|A": -8.0, "k|B": 0.75}
    monkeypatch.setattr(A.config, "saved_offset",
                        lambda key: requested.append(key) or offsets[key])
    monkeypatch.setattr(A, "fetch_for_candidates", lambda *_args: None)
    monkeypatch.setattr(panel, "_start_artwork", lambda _track: None)
    monkeypatch.setattr(panel, "_start_cuts", lambda _track: None)

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(A.threading, "Thread", ImmediateThread)
    started_at = time.monotonic()
    panel._start_fetch(_snap("B", "b", "k|B"))
    finished_at = time.monotonic()

    assert requested == ["k|B"]
    assert panel._loading.offset == 0.75
    assert panel._shown.scene == A.SCENE_OUTGOING
    assert panel._loading.scene == A.SCENE_PREPARING
    assert started_at + 1.0 <= panel._loading.deadline <= finished_at + 1.0


def test_a_rapid_second_skip_cancels_the_first_transition_deadline(panel, monkeypatch):
    from lyrica import app as A

    panel._outgoing_fade_at = time.monotonic() - A.OUTGOING_FADE_S
    def thread(**_kwargs):
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(A.threading, "Thread", thread)
    monkeypatch.setattr(panel, "_start_artwork", lambda _track: None)
    monkeypatch.setattr(panel, "_start_cuts", lambda _track: None)

    panel._start_fetch(_snap("C", "c", "k|C"))

    assert panel._outgoing_fade_at is None
    assert panel._loading.snapshot.track_key() == "k|C"
    assert panel._loading.deadline > time.monotonic()
    assert panel._loading.scene == A.SCENE_PREPARING


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


def test_a_worker_that_answers_after_its_track_was_abandoned_is_ignored(panel):
    # Background threads publish generation-stamped answers. Once neither the
    # loading nor shown track owns that generation, the UI thread drops it.
    from lyrica import app as A

    stale = panel._loading
    before = stale.lyrics
    panel._loading = A.Track(gen=3, snapshot=_snap("C", "c", "k|C"),
                             deadline=time.monotonic() + 100)
    panel._shown = A.Track(gen=2, snapshot=_snap("B", "b", "k|B"),
                           lyrics_state=A.LYRICS_PRESENT, searched=True)
    panel._worker_results.put(A.WorkerResult(
        stale.gen, "lyrics",
        Lyrics(lines=[(0.0, "from a song nobody is playing")],
               words=[[]], synced=True)))
    panel._drain_worker_results()
    assert not panel._ready_to_show(), "the abandoned track must not qualify"
    assert stale.lyrics is before


def test_an_answer_that_lands_after_the_song_went_up_is_still_heard(panel):
    # A song put up on the deadline goes up not knowing whether it has words,
    # and its answer arrives in the same Track a moment later. Copied at the
    # promotion, that answer was written where nobody looked — so a song with
    # no lyrics stayed expanded until the next track change. Intermittent,
    # because it needs the deadline to beat the provider.
    from lyrica import app as A

    late = A.Track(gen=2, snapshot=_snap("B", "b", "k|B"), searched=True,
                   deadline=time.monotonic() - 1)
    assert late.lyrics_state == A.LYRICS_UNKNOWN
    panel._loading = late
    assert panel._ready_to_show(), "the deadline has passed"
    panel._promote()
    assert panel._shown.scene == A.SCENE_CARD_ONLY
    panel._card_text = ("B", "b")
    panel._retarget_size()
    assert not panel._compact, "nothing is known yet, so nothing moves"

    panel._worker_results.put(A.WorkerResult(late.gen, "lyrics", None))
    panel._drain_worker_results()                 # the provider answers
    panel.lyrics = panel._shown.lyrics            # what the tick does
    panel._lyrics_state = panel._shown.lyrics_state
    panel._retarget_size()
    assert panel._compact, "it has to act on an answer it asked for"


def test_timeout_fades_the_outgoing_words_before_showing_card_only(panel, monkeypatch):
    from lyrica import app as A

    incoming_snap = _snap("CHIHIRO", "Billie Eilish", "k|B")
    incoming = A.Track(gen=2, snapshot=incoming_snap, searched=True,
                       deadline=time.monotonic() - 1, scene=A.SCENE_PREPARING)
    panel._shown.scene = A.SCENE_OUTGOING
    panel._loading = incoming
    panel.fetch_gen = incoming.gen
    panel.reader.snapshot = incoming_snap
    panel._fetching_key = incoming_snap.track_key()
    monkeypatch.setattr(panel.root, "after", lambda *_args: None)
    outgoing_line = _line(panel)

    panel._tick()

    assert panel._shown is not incoming
    assert panel._outgoing_fade_at is not None
    assert outgoing_line is not None
    assert _line(panel) == outgoing_line, "the outgoing items vanish only after the fade"

    panel._outgoing_fade_at = time.monotonic() - A.OUTGOING_FADE_S
    panel._tick()

    assert panel._shown is incoming
    assert panel._shown.scene == A.SCENE_CARD_ONLY
    assert not panel._views


def test_a_definite_no_lyrics_answer_fades_before_the_card_contracts(panel,
                                                                     monkeypatch):
    from lyrica import app as A

    incoming_snap = _snap("Instrumental", "B", "k|B")
    incoming = A.Track(
        gen=2, snapshot=incoming_snap, searched=True,
        lyrics_state=A.LYRICS_ABSENT, scene=A.SCENE_PREPARING)
    panel._shown.scene = A.SCENE_OUTGOING
    panel._loading = incoming
    panel.fetch_gen = incoming.gen
    panel.reader.snapshot = incoming_snap
    panel._fetching_key = incoming_snap.track_key()
    monkeypatch.setattr(panel.root, "after", lambda *_args: None)
    outgoing_line = _line(panel)

    panel._tick()

    assert panel._shown is not incoming, "promotion must wait for the text fade"
    assert panel._outgoing_fade_at is not None
    assert panel._collapse is None, "contraction must not overlap the fade-out"
    assert outgoing_line is not None
    assert _line(panel) == outgoing_line

    panel._outgoing_fade_at = time.monotonic() - A.OUTGOING_FADE_S
    panel._tick()

    assert panel._shown is incoming
    assert panel._shown.scene == A.SCENE_READY
    assert panel._outgoing_fade_at is None
    assert panel._compact
    assert panel._collapse is not None, "contraction begins after the fade settles"
    assert not panel._views


def test_late_lyrics_are_built_at_the_current_line_and_fade_in(panel, monkeypatch):
    from lyrica import app as A

    incoming_snap = _snap("CHIHIRO", "Billie Eilish", "k|B")
    incoming_snap.live_position = lambda: 6.0
    incoming = A.Track(gen=2, snapshot=incoming_snap, searched=True,
                       deadline=time.monotonic() - 1)
    panel._loading = incoming
    panel.fetch_gen = incoming.gen
    panel.reader.snapshot = incoming_snap
    panel._fetching_key = incoming_snap.track_key()
    panel._promote(A.SCENE_CARD_ONLY)
    lyrics = Lyrics(lines=[(0.0, "first of B"), (5.0, "second of B")],
                    words=[[], []], synced=True)
    panel._worker_results.put(A.WorkerResult(incoming.gen, "lyrics", lyrics))
    monkeypatch.setattr(panel.root, "after", lambda *_args: None)

    panel._tick()

    assert panel._shown.scene == A.SCENE_READY
    assert _line(panel) == "second of B"
    assert panel._lyrics_fade_at is not None
    assert not panel._glides
    active = panel._views[panel.line_index]
    assert panel.canvas.itemcget(active._items[0][2], "fill") != active._items[0][3]


def test_lyrics_wait_for_card_expansion_to_finish_before_fading_in(panel, monkeypatch):
    from lyrica import app as A

    # Begin where a lyric-less song leaves the overlay: hugging only its card.
    panel._clear_views()
    panel.line_index = -1
    panel._lyrics_state = A.LYRICS_ABSENT
    panel._compact = True
    panel._resize_window(*panel._target_size())

    incoming_snap = _snap("CHIHIRO", "Billie Eilish", "k|B")
    incoming_snap.live_position = lambda: 6.0
    incoming = A.Track(
        gen=2, snapshot=incoming_snap,
        lyrics=Lyrics(lines=[(0.0, "first of B"), (5.0, "second of B")],
                      words=[[], []], synced=True),
        lyrics_state=A.LYRICS_PRESENT, searched=True)
    panel._loading = incoming
    panel.reader.snapshot = incoming_snap
    panel._fetching_key = incoming_snap.track_key()
    monkeypatch.setattr(panel.root, "after", lambda *_args: None)

    panel._tick()

    assert panel._collapse is not None, "the card is still expanding"
    assert panel._lyrics_reveal_pending
    assert panel._lyrics_fade_at is None, "the fade clock must not overlap expansion"
    active = panel._views[panel.line_index]
    assert panel.canvas.itemcget(active._items[0][2], "fill") != active._items[0][3]

    collapse = panel._collapse
    panel._collapse = (*collapse[:4],
                       time.monotonic() - (A.COLLAPSE_MS + 10) / 1000,
                       *collapse[5:])
    panel._tick()

    assert panel._collapse is None
    assert not panel._lyrics_reveal_pending
    assert panel._lyrics_fade_at is not None, "fade begins only after final geometry"


def test_late_artwork_and_identity_are_committed_after_promotion(panel):
    from lyrica import app as A

    panel.fetch_gen = panel._shown.gen
    release = A.artwork.Release("Billie Eilish", "CHIHIRO", "HIT ME HARD AND SOFT")
    art = ("thumb", None, "song-colour")
    panel._worker_results.put(A.WorkerResult(
        panel._shown.gen, "artwork",
        A.ArtworkResult(release, b"late cover", art),
        shape_gen=panel._shape_gen))

    panel._drain_worker_results()

    assert panel._shown.identified is release
    assert panel._shown.cover == b"late cover"
    assert panel._cover_data == b"late cover"
    assert panel._identified is release
    assert panel._pending_art is art
    assert panel._card_raw is None, "the catalogue name must be derived again"


def test_late_cuts_correct_the_shown_track(panel):
    from lyrica import app as A

    panel.fetch_gen = panel._shown.gen
    found = A.sponsorblock.Cuts(((0.0, 21.8),))
    panel._worker_results.put(A.WorkerResult(panel._shown.gen, "cuts", found))

    panel._drain_worker_results()

    assert panel._shown.cuts is found
    assert panel._cuts is found
    assert panel._cuts_checked is None
    assert panel._cuts_discontinuous is True


def test_a_late_cut_lands_on_the_corrected_line_without_a_glide(panel, monkeypatch):
    from lyrica import app as A

    lyrics = Lyrics(lines=[(0.0, "zero"), (10.0, "ten"), (20.0, "twenty")],
                    words=[[], [], []], synced=True)
    panel._shown.lyrics = panel.lyrics = lyrics
    panel._shown.lyrics_state = A.LYRICS_PRESENT
    panel._lyrics_state = A.LYRICS_PRESENT
    panel.fetch_gen = panel._shown.gen
    snap = panel._shown.snapshot
    snap.live_position = lambda: 25.0
    panel.reader.snapshot = snap
    panel._fetching_key = snap.track_key()
    panel._go_to_line(2, lyrics)
    panel._worker_results.put(A.WorkerResult(
        panel._shown.gen, "cuts", A.sponsorblock.Cuts(((0.0, 20.0),))))
    monkeypatch.setattr(panel.root, "after", lambda *_args: None)
    calls = []
    original = panel._go_to_line

    def recording(index, lyr, *, animate=None):
        calls.append((index, animate))
        return original(index, lyr, animate=animate)

    monkeypatch.setattr(panel, "_go_to_line", recording)

    panel._tick()

    assert calls == [(0, False)]
    assert panel.line_index == 0
    assert not panel._glides
    assert panel._cut_fade_at is not None


def test_the_shown_track_is_the_state_rather_than_a_copy_of_it(panel):
    from lyrica import app as A

    assert panel._shown.lyrics is panel.lyrics
    assert panel._shown.lyrics_state == panel._lyrics_state
    assert panel._shown.gen == 1 and panel._loading is panel._shown
    assert isinstance(panel._shown, A.Track)


def _forbid_transport_animation(panel, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a frozen transport must not animate")

    for name in ("_apply_art", "_advance_beam", "_retarget_size",
                 "_advance_collapse", "_refit_views", "_settle_cuts",
                 "_go_to_line", "_show_backing"):
        monkeypatch.setattr(panel, name, forbidden)
    monkeypatch.setattr(panel.root, "after", lambda *_args: None)


def test_a_new_track_freezes_the_outgoing_frame_while_it_loads(panel, monkeypatch):
    incoming = _snap("CHIHIRO", "Billie Eilish", "k|B")
    panel.reader.snapshot = incoming
    panel._fetching_key = "k|A"
    started = []
    monkeypatch.setattr(panel, "_start_fetch", lambda snap: started.append(snap))
    _forbid_transport_animation(panel, monkeypatch)
    before = _rendered_frame(panel)

    panel._tick()

    assert started == [incoming]
    assert panel._shown.snapshot.track_key() == "k|A"
    assert _rendered_frame(panel) == before


@pytest.mark.parametrize("snapshot", [
    pytest.param(_snap("Traductor", "Tiago PZK", "k|A"), id="paused"),
    pytest.param(Snapshot(), id="no-session"),
])
def test_a_paused_or_missing_transport_freezes_every_animation(panel, monkeypatch, snapshot):
    if snapshot.ok:
        snapshot.playing = False
    panel.reader.snapshot = snapshot
    panel._fetching_key = "k|A"
    _forbid_transport_animation(panel, monkeypatch)
    before = _rendered_frame(panel)

    panel._tick()

    assert _rendered_frame(panel) == before


@pytest.mark.parametrize("playing", [True, False], ids=["playing", "paused"])
def test_an_abrupt_new_track_at_zero_never_resets_the_outgoing_lyrics(panel, monkeypatch,
                                                                       playing):
    incoming = _snap("CHIHIRO", "Billie Eilish", "k|B")
    incoming.position = 0.0
    incoming.playing = playing
    incoming.live_position = lambda: 0.0
    panel.reader.snapshot = incoming
    panel._fetching_key = "k|A"
    monkeypatch.setattr(panel, "_start_fetch", lambda _snap: None)
    _forbid_transport_animation(panel, monkeypatch)
    before = _rendered_frame(panel)

    panel._tick()

    assert _rendered_frame(panel) == before


def test_a_promoted_track_uses_its_own_clock_on_the_same_tick(panel, monkeypatch):
    from lyrica import app as A

    incoming_snap = _snap("CHIHIRO", "Billie Eilish", "k|B")
    incoming_snap.live_position = lambda: 6.0
    incoming = A.Track(
        gen=2, snapshot=incoming_snap,
        lyrics=Lyrics(lines=[(0.0, "first of B"), (5.0, "second of B")],
                      words=[[], []], synced=True),
        lyrics_state=A.LYRICS_PRESENT, searched=True)
    panel._loading = incoming
    panel.reader.snapshot = incoming_snap
    panel._fetching_key = "k|B"
    monkeypatch.setattr(panel.root, "after", lambda *_args: None)

    panel._tick()

    assert panel._shown is incoming
    assert panel.track_key == "k|B"
    assert _line(panel) == "second of B"
