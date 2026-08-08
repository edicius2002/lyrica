"""Keeping covers on disk so a replayed track needs no network (offline)."""
import os
import time

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
    # Two singles: no album to key on, so each gets its own entry.
    store_cover("A", "One", "", b"first")
    store_cover("A", "Two", "", b"second")
    assert cached_cover("A", "One", "") == b"first"
    assert cached_cover("A", "Two", "") == b"second"


def test_the_key_ignores_case():
    store_cover("Artist", "Title", "Album", b"x")
    assert cached_cover("ARTIST", "title", "AlBuM") == b"x"


# --- keyed by release -------------------------------------------------------

def test_the_rest_of_the_album_is_already_answered():
    # The saving this key exists for. Measured on the real cache before it: 52
    # stored covers were 34 distinct images, one of them written six times —
    # eighteen network round trips of about a second each, spent on pictures
    # that were already on disk.
    store_cover("Boards of Canada", "Roygbiv", "Music Has the Right", b"cover")
    assert cached_cover("Boards of Canada", "Olson",
                        "Music Has the Right") == b"cover"


def test_a_different_album_is_not_answered():
    store_cover("Boards of Canada", "Roygbiv", "Music Has the Right", b"cover")
    assert cached_cover("Boards of Canada", "Dayvan Cowboy",
                        "The Campfire Headphase") is None


def test_the_same_album_name_under_another_artist_is_not_answered():
    # "Greatest Hits" is not one record.
    store_cover("Boards of Canada", "Roygbiv", "Greatest Hits", b"cover")
    assert cached_cover("Someone Else", "Roygbiv", "Greatest Hits") is None


def test_an_entry_written_under_the_old_track_key_still_answers():
    # Written the way the cache used to be, so upgrading does not silently
    # refetch every cover already on disk.
    artwork._cover_path("Aphex Twin", "Xtal", "SAW 85-92").write_bytes(b"legacy")
    assert cached_cover("Aphex Twin", "Xtal", "SAW 85-92") == b"legacy"


def test_a_cover_fetched_for_one_track_answers_for_its_album(monkeypatch):
    monkeypatch.setattr(artwork, "fetch_cover", lambda *a, **k: b"fresh")
    best_cover("Daft Punk", "Aerodynamic", "Discovery")
    assert cached_cover("Daft Punk", "Veridis Quo", "Discovery") == b"fresh"


# --- misses expire ----------------------------------------------------------

def test_a_stale_miss_is_asked_again(monkeypatch):
    # A recorded miss is indistinguishable from a network that was down, and
    # keying by album means one bad minute would otherwise silence a whole
    # record for good.
    store_cover("Nobody", "Nothing", "Nowhere", None)
    path = artwork._album_path("Nobody", "Nowhere")
    stale = time.time() - artwork.MISS_TTL_S - 60
    os.utime(path, (stale, stale))
    monkeypatch.setattr(artwork, "fetch_cover", lambda *a, **k: b"found later")
    assert best_cover("Nobody", "Nothing", "Nowhere") == b"found later"


def test_a_fresh_miss_is_still_believed(monkeypatch):
    store_cover("Nobody", "Nothing", "Nowhere", None)
    monkeypatch.setattr(artwork, "fetch_cover",
                        lambda *a, **k: pytest.fail("should not have asked"))
    assert best_cover("Nobody", "Nothing", "Nowhere") is None


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


def down(*_a, **_k):
    raise artwork.Unreachable


def test_a_dropped_connection_does_not_silence_the_album(monkeypatch):
    # The miss is written under the album key and believed for a fortnight, so
    # one bad minute used to leave a whole record with no cover for two weeks.
    monkeypatch.setattr(artwork, "fetch_cover", down)
    monkeypatch.setattr(artwork, "fetch_cover_discogs", down)
    assert best_cover("Air", "La Femme d'Argent", "Moon Safari") is None

    monkeypatch.setattr(artwork, "fetch_cover", lambda *a, **k: b"back online")
    assert best_cover("Air", "Sexy Boy", "Moon Safari") == b"back online"


def test_one_source_being_down_still_asks_the_other(monkeypatch):
    # The sources used to be chained with `or`, which a raise would skip past.
    monkeypatch.setattr(artwork, "fetch_cover", down)
    monkeypatch.setattr(artwork, "fetch_cover_discogs", lambda *a, **k: b"scan")
    assert best_cover("Aventura", "Todavia", "God's Project") == b"scan"


def test_a_source_that_answers_nothing_is_still_a_miss(monkeypatch):
    # The saving this cache exists for: a track nobody has must not re-ask
    # every source on every replay.
    asked = []

    def none(*_a, **_k):
        asked.append(1)
        return None

    monkeypatch.setattr(artwork, "fetch_cover", none)
    monkeypatch.setattr(artwork, "fetch_cover_discogs", none)
    assert best_cover("Nobody", "Nothing", "Nowhere") is None
    assert best_cover("Nobody", "Nothing", "Nowhere") is None
    assert len(asked) == 2, "the second play asked again"
