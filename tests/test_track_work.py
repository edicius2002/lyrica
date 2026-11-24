"""Background work follows the most recent track even when providers block."""
import threading
import time

import pytest

from lyrica import app
from lyrica.app import Track
from lyrica.lyrics import Lyrics
from lyrica.sessions import Snapshot
from lyrica.workers import TrackWorkers, WorkerResult


def _eventually(check):
    deadline = time.monotonic() + 3
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def test_shutdown_releases_pending_jobs_and_ignores_a_late_answer():
    workers = TrackWorkers()
    entered, release = threading.Event(), threading.Event()
    calls, threads = [], []

    def slow(cancelled):
        threads.append(threading.current_thread())
        entered.set()
        release.wait(5)
        calls.append(("slow", cancelled.is_set()))
        return WorkerResult(1, "lyrics")

    workers.submit("lyrics", slow)
    try:
        assert entered.wait(3)
        workers.submit("lyrics", lambda _: calls.append("queued"))
        workers.close()
        workers.submit("artwork", lambda _: calls.append("closed"))
        assert threads[0].is_alive(), "shutdown must return while a provider is blocked"
    finally:
        release.set()
    threads[0].join(3)
    assert not threads[0].is_alive()
    assert calls == [("slow", True)]
    assert workers.drain() == []


@pytest.mark.parametrize("kind", ["lyrics", "artwork", "cuts"])
def test_rapid_skips_run_only_first_and_last_provider_calls(overlay, monkeypatch, kind):
    entered, release = threading.Event(), threading.Event()
    calls, decoded, cuts = [], [], []

    def block(title):
        calls.append(title)
        if title == "First":
            entered.set()
            release.wait(5)

    def lyrics(candidates, *_):
        title = candidates[0].title
        if kind == "lyrics":
            block(title)
        return Lyrics(lines=[(0.0, title)], words=[[]], synced=True)

    def cover(candidates, *_, **kwargs):
        if kind == "artwork":
            block(candidates[0].title)
        return candidates[0].title.encode()

    def video(title, _duration):
        if kind == "cuts":
            block(title)
        return title

    def find_cuts(video_id):
        cuts.append(video_id)
        return app.sponsorblock.Cuts(((0.0, 10.0 if video_id == "Last" else 5.0),))

    monkeypatch.setattr(app, "fetch_for_candidates", lyrics)
    monkeypatch.setattr(app.artwork, "available", lambda: True)
    monkeypatch.setattr(app.artwork, "best_cover_for_candidates", cover)
    monkeypatch.setattr(app.artwork, "identify", lambda *_: None)
    monkeypatch.setattr(app.artwork, "decode", lambda data: decoded.append(data))
    monkeypatch.setattr(app.artwork, "make_thumbnail", lambda *a, **kw: None)
    monkeypatch.setattr(app.artwork, "make_backdrop", lambda *a, **kw: None)
    monkeypatch.setattr(app.songcolour, "extract", lambda *_: None)
    monkeypatch.setattr(app.youtube, "api_key", lambda: "key")
    monkeypatch.setattr(app.youtube, "video_id_for", video)
    monkeypatch.setattr(app.sponsorblock, "cuts_for", find_cuts)

    def start(title):
        overlay._start_fetch(Snapshot(app="chrome", artist="Singer", title=title))

    try:
        start("First")
        assert entered.wait(3)
        for title in ["Second", "Third", "Fourth", "Last"]:
            start(title)
    finally:
        release.set()
    def completed():
        overlay._drain_worker_results()
        return overlay._loading.whole and overlay._loading.cuts.spans

    _eventually(completed)
    assert overlay._loading.lyrics.lines == [(0.0, "Last")]
    assert overlay._loading.cover == b"Last"
    assert overlay._loading.cuts.spans == ((0.0, 10.0),)
    assert calls == ["First", "Last"]
    if kind == "artwork":
        assert decoded == [b"Last"]
    if kind == "cuts":
        assert cuts == ["Last"]


def test_resizes_rebuild_only_the_latest_shape_off_the_render_thread(overlay, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    sizes = []
    overlay._loading = overlay._shown = Track(gen=overlay.fetch_gen, cover=b"picture")
    render_thread = threading.current_thread()

    def thumbnail(_data, size, **kw):
        sizes.append(size)
        if size == 41:
            entered.set()
            if threading.current_thread() is not render_thread:
                release.wait(5)
        return size

    monkeypatch.setattr(app.artwork, "decode", lambda *_: None)
    monkeypatch.setattr(app.artwork, "make_thumbnail", thumbnail)
    monkeypatch.setattr(app.artwork, "make_backdrop", lambda *a, **kw: None)
    monkeypatch.setattr(app.songcolour, "extract", lambda *_: None)
    try:
        monkeypatch.setattr(overlay, "_thumb_size", 41)
        overlay._reshape_art()
        assert entered.wait(3)
        for size in (42, 43, 44):
            monkeypatch.setattr(overlay, "_thumb_size", size)
            overlay._reshape_art()
    finally:
        release.set()
    def completed():
        overlay._drain_worker_results()
        return overlay._pending_art is not None

    _eventually(completed)
    assert sizes == [41, 44]
    assert overlay._pending_art[0] == 44
    assert overlay._shown.art is None
