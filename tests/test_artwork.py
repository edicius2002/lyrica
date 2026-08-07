"""Turning artwork into a backdrop (offline, no display).

Synthetic images only — nothing here loads real cover art.
"""
import io
import os
import time

import pytest

from lyrica import artwork

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def encoded(width: int, height: int, colour=(200, 60, 40), fmt="JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format=fmt)
    return buffer.getvalue()


def test_pillow_is_reported_present():
    assert artwork.available()


def test_a_backdrop_fills_the_window_exactly():
    # It has to cover, not fit: letterboxing would put hard edges into the one
    # thing on screen whose job is to have none.
    assert artwork.make_backdrop(encoded(600, 600), 400, 200).image.size == (400, 200)


def test_a_non_square_cover_still_fills_the_window():
    assert artwork.make_backdrop(encoded(1000, 300), 400, 200).image.size == (400, 200)


def test_even_a_white_cover_is_pulled_down_to_the_cap():
    # The case a fixed brightness factor cannot serve: whatever it is set to,
    # some sleeve is brighter. Scaling to a measured level has no such sleeve.
    back = artwork.make_backdrop(encoded(300, 300, (255, 255, 255)), 200, 100)
    assert back.peak <= artwork.BACKDROP_CAP


def test_a_dark_cover_is_left_where_it_is():
    # Lifting it would amplify shadow noise to buy contrast it already has.
    dark = (10, 10, 12)
    back = artwork.make_backdrop(encoded(300, 300, dark), 200, 100)
    assert back.peak <= max(dark) + 2


def test_the_backdrop_reports_the_wash_a_line_fades_into():
    back = artwork.make_backdrop(encoded(300, 300, (255, 255, 255)), 200, 100)
    assert max(back.colour) <= artwork.BACKDROP_CAP


def test_the_backdrop_keeps_the_hue_it_came_from():
    # The point is colour, not the picture: a red cover should still read red.
    back = artwork.make_backdrop(encoded(300, 300, (220, 30, 30)), 200, 100)
    r, g, b = back.image.getpixel((100, 50))
    assert r > g and r > b


def test_png_is_accepted_as_well_as_jpeg():
    assert artwork.make_backdrop(encoded(300, 300, fmt="PNG"), 200, 100) is not None


def test_no_data_makes_no_backdrop():
    assert artwork.make_backdrop(b"", 400, 200) is None


def test_undecodable_data_is_none_rather_than_an_exception():
    # Players publish whatever they like in that field.
    assert artwork.make_backdrop(b"this is not an image", 400, 200) is None


def test_a_tiny_cover_does_not_collapse_to_nothing():
    # Downscaling before blurring must never round a dimension to zero.
    assert artwork.make_backdrop(encoded(4, 4), 400, 200).image.size == (400, 200)


def test_a_zero_sized_window_does_not_crash():
    assert artwork.make_backdrop(encoded(300, 300), 0, 0).image.size == (1, 1)


# --- the cache --------------------------------------------------------------

@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A cache directory of its own, so these never touch the real one."""
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_a_stored_cover_comes_back(cache):
    artwork.store_cover("Boards of Canada", "Roygbiv", "Music Has the Right",
                        b"cover-bytes")
    assert artwork.cached_cover("Boards of Canada", "Roygbiv",
                                "Music Has the Right") == b"cover-bytes"


def test_the_rest_of_the_album_is_already_answered(cache):
    # The saving this key exists for. Measured on the real cache before it: 52
    # stored covers were 34 distinct images, one written six times — eighteen
    # network round trips of about a second each, for pictures already on disk.
    artwork.store_cover("Boards of Canada", "Roygbiv", "Music Has the Right",
                        b"cover-bytes")
    assert artwork.cached_cover("Boards of Canada", "Olson",
                                "Music Has the Right") == b"cover-bytes"


def test_a_different_album_is_not_answered(cache):
    artwork.store_cover("Boards of Canada", "Roygbiv", "Music Has the Right",
                        b"cover-bytes")
    assert artwork.cached_cover("Boards of Canada", "Dayvan Cowboy",
                                "The Campfire Headphase") is None


def test_a_different_artist_on_the_same_album_name_is_not_answered(cache):
    artwork.store_cover("Boards of Canada", "Roygbiv", "Greatest Hits",
                        b"cover-bytes")
    assert artwork.cached_cover("Someone Else", "Roygbiv", "Greatest Hits") is None


def test_a_track_with_no_album_still_caches(cache):
    # Plenty of players publish no album at all; the per-track key is what
    # answers then, and it has to keep working.
    artwork.store_cover("Zaxx", "Signal", "", b"single-bytes")
    assert artwork.cached_cover("Zaxx", "Signal", "") == b"single-bytes"
    assert artwork.cached_cover("Zaxx", "Other", "") is None


def test_an_entry_written_under_the_old_track_key_still_answers(cache):
    # Written the way the cache used to, so upgrading does not silently refetch
    # every cover on disk.
    artwork._cover_path("Aphex Twin", "Xtal", "SAW 85-92").write_bytes(b"legacy")
    assert artwork.cached_cover("Aphex Twin", "Xtal", "SAW 85-92") == b"legacy"


def test_nothing_stored_is_a_miss_not_an_error(cache):
    assert artwork.cached_cover("Nobody", "Nothing", "") is None


def test_a_recorded_miss_stops_the_sources_being_asked_again(cache, monkeypatch):
    # The expensive case: a track no source has would otherwise wait on every
    # source, every replay.
    asked = []
    monkeypatch.setattr(artwork, "fetch_cover",
                        lambda *a, **k: asked.append("apple"))
    monkeypatch.setattr(artwork, "fetch_cover_discogs",
                        lambda *a, **k: asked.append("discogs"))
    assert artwork.best_cover("Nobody", "Nothing", "Nowhere") is None
    assert asked == ["apple", "discogs"]
    assert artwork.best_cover("Nobody", "Nothing", "Nowhere") is None
    assert asked == ["apple", "discogs"], "the miss was not recorded"


def test_a_stale_miss_is_asked_again(cache, monkeypatch):
    # A miss is indistinguishable from a network that was down, and keying by
    # album means one bad minute can silence a whole record.
    artwork.store_cover("Nobody", "Nothing", "Nowhere", None)
    path = artwork._album_path("Nobody", "Nowhere")
    old = time.time() - artwork.MISS_TTL_S - 60
    os.utime(path, (old, old))
    monkeypatch.setattr(artwork, "fetch_cover", lambda *a, **k: b"found-later")
    assert artwork.best_cover("Nobody", "Nothing", "Nowhere") == b"found-later"


def test_the_quicker_source_is_asked_first(cache, monkeypatch):
    # Timed on four tracks: Apple 339-925 ms against Discogs' 1086-1198 ms, and
    # where both answered Apple returned the larger image twice of three — once
    # by fifteen times.
    order = []
    monkeypatch.setattr(artwork, "fetch_cover",
                        lambda *a, **k: order.append("apple") or b"apple-bytes")
    monkeypatch.setattr(artwork, "fetch_cover_discogs",
                        lambda *a, **k: order.append("discogs") or b"discogs-bytes")
    assert artwork.best_cover("Daft Punk", "Aerodynamic", "Discovery") == b"apple-bytes"
    assert order == ["apple"], "Discogs should not be asked when Apple answers"


def test_the_slower_source_covers_what_the_quicker_one_misses(cache, monkeypatch):
    # The one track of four Apple had nothing for was a Latin release Discogs
    # answered. That is the whole reason it is still in the cascade.
    monkeypatch.setattr(artwork, "fetch_cover", lambda *a, **k: None)
    monkeypatch.setattr(artwork, "fetch_cover_discogs", lambda *a, **k: b"discogs-bytes")
    assert artwork.best_cover("Aventura", "Todavia", "God's Project") == b"discogs-bytes"


def test_a_fetched_cover_is_kept(cache, monkeypatch):
    monkeypatch.setattr(artwork, "fetch_cover", lambda *a, **k: b"apple-bytes")
    artwork.best_cover("Daft Punk", "Aerodynamic", "Discovery")
    assert artwork.cached_cover("Daft Punk", "Veridis Quo", "Discovery") == b"apple-bytes"


def test_a_track_with_no_name_at_all_asks_nobody(cache, monkeypatch):
    monkeypatch.setattr(artwork, "fetch_cover",
                        lambda *a, **k: pytest.fail("should not have asked"))
    assert artwork.best_cover("", "", "") is None


# --- thumbnails -------------------------------------------------------------

def test_a_square_cover_fills_the_box():
    assert artwork.make_thumbnail(encoded(300, 300), 58).size == (58, 58)


def test_a_nearly_square_cover_is_cropped_to_fill():
    # Trimming a few pixels off the sides frames it; it does not lose artwork.
    thumb = artwork.make_thumbnail(encoded(300, 292), 58)
    assert thumb.size == (58, 58)


def test_a_clearly_non_square_cover_keeps_all_of_itself():
    # A 500x453 scan cropped square loses the sides of the sleeve. Padding is
    # the lesser loss, and it hides behind a rounded corner.
    wide = artwork.make_thumbnail(encoded(500, 300, (10, 200, 40)), 60)
    assert wide.size == (60, 60)
    # The full width survived, so the artwork was fitted rather than cut.
    assert wide.getpixel((1, 30))[1] > 100 or wide.getpixel((58, 30))[1] > 100


def test_the_padding_is_taken_from_the_artwork_not_left_black():
    tall = artwork.make_thumbnail(encoded(300, 600, (200, 30, 30)), 60)
    r, g, b = tall.getpixel((2, 2))
    assert r > g and r > b, "a black bar would read as a border round the cover"


def test_no_data_makes_no_thumbnail():
    assert artwork.make_thumbnail(b"", 58) is None


def test_a_zero_sized_thumbnail_is_refused():
    assert artwork.make_thumbnail(encoded(300, 300), 0) is None


def test_undecodable_data_makes_no_thumbnail():
    assert artwork.make_thumbnail(b"not an image", 58) is None
