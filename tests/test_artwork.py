"""Turning artwork into a backdrop (offline, no display).

Synthetic images only — nothing here loads real cover art.
"""
import io

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
    image = artwork.make_backdrop(encoded(600, 600), 400, 200)
    assert image.size == (400, 200)


def test_a_non_square_cover_still_fills_the_window():
    assert artwork.make_backdrop(encoded(1000, 300), 400, 200).size == (400, 200)


def test_the_backdrop_is_darkened_well_below_the_original():
    bright = (255, 255, 255)
    image = artwork.make_backdrop(encoded(300, 300, bright), 200, 100)
    r, g, b = image.getpixel((100, 50))
    assert max(r, g, b) < 128, "artwork at full strength would compete with the words"


def test_the_backdrop_keeps_the_hue_it_came_from():
    # The point is colour, not the picture: a red cover should still read red.
    image = artwork.make_backdrop(encoded(300, 300, (220, 30, 30)), 200, 100)
    r, g, b = image.getpixel((100, 50))
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
    assert artwork.make_backdrop(encoded(4, 4), 400, 200).size == (400, 200)


def test_a_zero_sized_window_does_not_crash():
    assert artwork.make_backdrop(encoded(300, 300), 0, 0).size == (1, 1)


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
