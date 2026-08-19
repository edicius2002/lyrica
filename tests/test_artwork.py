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


# --- sharing one decode -----------------------------------------------------
#
# A resize rebuilds everything a cover feeds from the same bytes, on the UI
# thread, while somebody holds the keys down. Decoding the JPEG once and
# lending the image round is worth ~3.5 ms of that, but only if the borrowers
# behave: a lent image must come back unclosed, unmodified, and must produce
# exactly what decoding separately produced.


def busy(width=300, height=300) -> bytes:
    """A cover with detail in it. A flat colour hides resampling differences."""
    from PIL import ImageDraw
    image = Image.new("RGB", (width, height), (12, 12, 14))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width * 0.5, height * 0.5), fill=(30, 150, 170))
    draw.ellipse((width * 0.3, height * 0.4, width * 0.9, height * 0.95),
                 fill=(210, 60, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def test_a_lent_decode_gives_the_identical_thumbnail():
    data = busy()
    alone = artwork.make_thumbnail(data, 58)
    shared = artwork.make_thumbnail(data, 58, decoded=artwork.decode(data))
    assert shared.tobytes() == alone.tobytes(), "not the same pixels"


def test_a_lent_decode_gives_the_identical_thumbnail_when_fitted():
    # The other branch: art too far off square to crop, padded instead.
    data = busy(500, 300)
    alone = artwork.make_thumbnail(data, 60)
    shared = artwork.make_thumbnail(data, 60, decoded=artwork.decode(data))
    assert shared.tobytes() == alone.tobytes()


def test_a_lent_decode_gives_the_identical_backdrop():
    data = busy()
    alone = artwork.make_backdrop(data, 400, 200)
    shared = artwork.make_backdrop(data, 400, 200, decoded=artwork.decode(data))
    assert shared.image.tobytes() == alone.image.tobytes(), "not the same wash"
    assert shared.colour == alone.colour
    assert shared.peak == alone.peak


def test_everything_a_cover_feeds_survives_one_shared_decode():
    """The whole point, end to end: one decode, three identical answers.

    Including the colour, which decodes for itself on purpose — `draft` is a
    destructive re-decode and could not join in without changing its answer.
    This is the guard on that decision holding.
    """
    from lyrica import songcolour
    data = busy()
    songcolour._CACHE.clear()
    separately = (artwork.make_thumbnail(data, 58),
                  artwork.make_backdrop(data, 400, 200),
                  songcolour.extract(data))

    songcolour._CACHE.clear()
    lent = artwork.decode(data)
    together = (artwork.make_thumbnail(data, 58, decoded=lent),
                artwork.make_backdrop(data, 400, 200, decoded=lent),
                songcolour.extract(data))

    assert together[0].tobytes() == separately[0].tobytes()
    assert together[1].image.tobytes() == separately[1].image.tobytes()
    assert together[1].colour == separately[1].colour
    assert together[2] == separately[2]


def test_one_shared_decode_replaces_two(monkeypatch):
    import PIL.Image
    opens = []
    real = PIL.Image.open
    monkeypatch.setattr(PIL.Image, "open",
                        lambda *a, **k: (opens.append(1), real(*a, **k))[1])

    data = busy()
    artwork.make_thumbnail(data, 58)
    artwork.make_backdrop(data, 400, 200)
    apart = len(opens)

    opens.clear()
    lent = artwork.decode(data)
    artwork.make_thumbnail(data, 58, decoded=lent)
    artwork.make_backdrop(data, 400, 200, decoded=lent)
    assert apart == 2, "the two of them used to open the JPEG twice"
    assert len(opens) == 1, "sharing should leave exactly one decode"


def test_a_lent_image_is_not_closed_by_its_borrowers():
    # A `with` over a borrowed image would close it for whoever came next, and
    # the failure would surface in an unrelated function.
    data = busy()
    lent = artwork.decode(data)
    artwork.make_thumbnail(data, 58, decoded=lent)
    artwork.make_backdrop(data, 400, 200, decoded=lent)
    assert lent.tobytes(), "the lender's image no longer reads"
    assert artwork.make_thumbnail(data, 58, decoded=lent) is not None


def test_a_lent_image_is_not_modified_by_its_borrowers():
    # `draft`, `thumbnail` and `paste` all change an image in place. If one of
    # them ever runs on the lent image, the next borrower gets a degraded cover.
    data = busy()
    lent = artwork.decode(data)
    before, size, mode = lent.tobytes(), lent.size, lent.mode
    artwork.make_thumbnail(data, 58, decoded=lent)
    artwork.make_backdrop(data, 400, 200, decoded=lent)
    assert lent.size == size, "the lent image was resized underneath its owner"
    assert lent.mode == mode
    assert lent.tobytes() == before, "the lent image's pixels changed"


def test_a_lent_image_that_is_not_rgb_is_accepted_and_left_alone():
    data = busy()
    lent = Image.open(io.BytesIO(data)).convert("RGBA")
    before = lent.tobytes()
    assert artwork.make_thumbnail(data, 58, decoded=lent).mode == "RGB"
    assert artwork.make_backdrop(data, 400, 200, decoded=lent) is not None
    assert lent.mode == "RGBA", "converted in place instead of on a copy"
    assert lent.tobytes() == before


def test_decoding_nothing_gives_nothing():
    assert artwork.decode(b"") is None
    assert artwork.decode(b"this is not an image") is None


def test_a_failed_decode_still_makes_nothing_when_shared():
    # The caller decodes once and gets None; the builders must behave exactly
    # as they do today rather than blowing up on the None.
    assert artwork.make_thumbnail(b"not an image", 58, decoded=None) is None
    assert artwork.make_backdrop(b"not an image", 400, 200, decoded=None) is None
