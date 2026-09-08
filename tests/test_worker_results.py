"""Background answers cross onto the render thread once, by generation."""
import queue
from types import SimpleNamespace

from lyrica import artwork, sponsorblock
from lyrica.app import ArtworkResult, Overlay, Track, WorkerResult, transport_is_live
from lyrica.lyrics import Lyrics
from lyrica.sessions import Snapshot


def _panel() -> Overlay:
    panel = Overlay.__new__(Overlay)
    panel._worker_results = queue.SimpleQueue()
    panel._shape_gen = 4
    panel._pending_art = None
    panel._identified = artwork.Release()
    panel._cover_data = None
    panel._card_raw = ("cached",)
    panel._cuts = sponsorblock.Cuts()
    panel._cuts_checked = panel._cuts
    panel._cuts_discontinuous = False
    panel.fetch_gen = 7
    panel._shown = Track(gen=7, searched=True)
    panel._loading = panel._shown
    return panel


def test_late_lyrics_are_committed_to_an_already_promoted_track():
    panel = _panel()
    lyrics = Lyrics(lines=[(0.0, "late")], words=[[]], synced=True)
    panel._worker_results.put(WorkerResult(7, "lyrics", lyrics))

    panel._drain_worker_results()

    assert panel._shown.lyrics is lyrics
    assert panel._shown.lyrics_state == "present"


def test_late_artwork_refreshes_the_shown_track_and_pending_frame():
    panel = _panel()
    release = artwork.Release("artist", "title", "album")
    art = ("thumb", None, "colour")
    panel._worker_results.put(WorkerResult(
        7, "artwork", ArtworkResult(release, b"cover", art), shape_gen=4))

    panel._drain_worker_results()

    assert panel._shown.identified is release
    assert panel._shown.cover == b"cover"
    assert panel._identified is release
    assert panel._cover_data == b"cover"
    assert panel._pending_art is art
    assert panel._card_raw is None


def test_late_cuts_refresh_the_clock_and_mark_a_discontinuity():
    panel = _panel()
    cuts = sponsorblock.Cuts(((0.0, 20.0),))
    panel._worker_results.put(WorkerResult(7, "cuts", cuts))

    panel._drain_worker_results()

    assert panel._shown.cuts is cuts
    assert panel._cuts is cuts
    assert panel._cuts_checked is None
    assert panel._cuts_discontinuous is True


def test_an_abandoned_generation_cannot_mutate_either_track():
    panel = _panel()
    before = panel._shown.lyrics
    panel._worker_results.put(WorkerResult(
        99, "lyrics", Lyrics(lines=[(0.0, "stale")], words=[[]], synced=True)))

    panel._drain_worker_results()

    assert panel._shown.lyrics is before


def test_a_late_answer_does_not_repaint_the_frozen_outgoing_track():
    panel = _panel()
    panel._loading = Track(gen=8)
    panel.fetch_gen = 8
    cuts = sponsorblock.Cuts(((0.0, 20.0),))
    panel._worker_results.put(WorkerResult(7, "cuts", cuts))

    panel._drain_worker_results()

    assert panel._shown.cuts.spans == (), "the outgoing track is no longer mutable"
    assert panel._cuts.spans == (), "but the outgoing visual clock stays frozen"
    assert panel._cuts_discontinuous is False


def test_a_cut_correction_fades_the_landed_scene_out_of_the_wash(monkeypatch):
    from lyrica import app

    panel = Overlay.__new__(Overlay)
    shown = {}
    panel.canvas = SimpleNamespace(
        itemconfigure=lambda item, **values: shown.update({item: values["fill"]}))
    # A row carries the two tags a real `LineView` has, because a row whose
    # glyphs all land on the same presented colour is coloured by its text tag
    # in one call — the same shape `present_inactive` and `set_visible` use.
    # What is asserted is still the colour the row is presented in.
    panel._views = {
        0: SimpleNamespace(
            palette=SimpleNamespace(backdrop=(0, 0, 0)),
            _items=[[0, 0, "glyph", "#ffffff"]], _outline=[],
            _text_tag="row-text", _outline_tag="row-outline")
    }
    panel._echo = None
    panel._cut_fade_at = 10.0
    panel._presented_scene = None
    monkeypatch.setattr(app.time, "monotonic", lambda: 10.06)

    assert panel._advance_cut_fade() is True
    assert shown["row-text"] not in ("#000000", "#ffffff")

    monkeypatch.setattr(app.time, "monotonic", lambda: 10.20)
    assert panel._advance_cut_fade() is False
    assert shown["row-text"] == "#ffffff"
    assert panel._cut_fade_at is None


def test_player_artwork_is_requested_for_the_loading_snapshot(monkeypatch):
    from lyrica import app

    snapshot = Snapshot(
        app="browser", artist="Artist", title="Song", ok=True,
        session_id="bound-session")
    loading = Track(gen=3, snapshot=snapshot)
    panel = Overlay.__new__(Overlay)
    panel._thumb_size = 58
    panel._shape_gen = 4
    panel._worker_results = queue.SimpleQueue()
    panel._build_art = lambda data: ("built", data)
    received = []
    panel.reader = SimpleNamespace(
        read_artwork=lambda snap: received.append(snap) or b"player-art")
    monkeypatch.setattr(app.artwork, "available", lambda: True)
    monkeypatch.setattr(app.artwork, "best_cover_for_candidates", lambda *a, **k: None)
    monkeypatch.setattr(app.artwork, "identify", lambda *a, **k: None)

    panel._start_artwork(loading)
    result = panel._worker_results.get(timeout=2)

    assert received == [snapshot]
    assert result.value.cover == b"player-art"


def test_same_track_in_another_session_cannot_drive_visible_lyrics():
    shown = Snapshot(
        app="browser", artist="Artist", title="Song", playing=True, ok=True,
        session_id="session-a")
    other = Snapshot(
        app="browser", artist="Artist", title="Song", playing=True, ok=True,
        session_id="session-b")

    assert shown.track_key() == other.track_key()
    assert shown.playback_key() != other.playback_key()
    assert not transport_is_live(Track(snapshot=shown), other)
