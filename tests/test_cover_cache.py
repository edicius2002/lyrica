"""Keeping covers on disk so a replayed track needs no network (offline)."""
import pytest

from lyrica import artwork
from lyrica.artwork import best_cover, cached_cover, store_cover


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(artwork, "cover_dir", lambda: tmp_path)


def test_nothing_stored_is_a_miss():
    assert cached_cover("A", "B", "C") is None


def test_what_was_stored_comes_back():
    store_cover("A", "B", "C", b"image-bytes")
    assert cached_cover("A", "B", "C") == b"image-bytes"


def test_different_tracks_do_not_share_an_entry():
    store_cover("A", "One", "", b"first")
    store_cover("A", "Two", "", b"second")
    assert cached_cover("A", "One", "") == b"first"
    assert cached_cover("A", "Two", "") == b"second"


def test_the_key_ignores_case():
    store_cover("Artist", "Title", "Album", b"x")
    assert cached_cover("ARTIST", "title", "AlBuM") == b"x"


def test_a_miss_is_remembered_and_never_asked_again(monkeypatch):
    calls = []

    def never(*a, **k):
        calls.append(1)
        return None

    monkeypatch.setattr(artwork, "fetch_cover", never)
    monkeypatch.setattr(artwork, "fetch_cover_discogs", never)

    assert best_cover("A", "B") is None
    assert best_cover("A", "B") is None
    # A track no source has is the expensive case: without recording the miss,
    # every replay would wait for every source to fail again.
    assert len(calls) == 2, "one round of sources, then nothing"


def test_a_hit_is_served_from_disk_without_asking(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("a cached cover must not cost a request")

    store_cover("A", "B", "", b"stored")
    monkeypatch.setattr(artwork, "fetch_cover", explode)
    monkeypatch.setattr(artwork, "fetch_cover_discogs", explode)
    assert best_cover("A", "B") == b"stored"


def test_a_fetched_cover_is_kept_for_next_time(monkeypatch):
    monkeypatch.setattr(artwork, "fetch_cover", lambda *a, **k: b"fresh")
    monkeypatch.setattr(artwork, "fetch_cover_discogs", lambda *a, **k: None)
    assert best_cover("A", "B") == b"fresh"
    assert cached_cover("A", "B", "") == b"fresh"


def test_an_empty_query_asks_nothing(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("nothing to search for")

    monkeypatch.setattr(artwork, "fetch_cover", explode)
    assert best_cover("", "") is None


def test_an_unwritable_directory_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(artwork, "cover_dir", lambda: tmp_path / "nope" / "deeper")
    store_cover("A", "B", "", b"x")     # must not raise
    assert cached_cover("A", "B", "") is None
