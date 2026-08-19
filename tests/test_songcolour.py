"""Reading a song's colour off its cover (offline, synthetic images)."""
import io

import pytest

from lyrica import songcolour

pytest.importorskip("PIL")
from PIL import Image, ImageDraw


def encoded(image, fmt="JPEG") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=95)
    return buffer.getvalue()


def solid(colour, size=300) -> bytes:
    return encoded(Image.new("RGB", (size, size), colour))


def patched(background, patch, fraction: float, size=300) -> bytes:
    """A cover that is mostly `background` with a `patch` covering a fraction."""
    image = Image.new("RGB", (size, size), background)
    side = int((size * size * fraction) ** 0.5)
    ImageDraw.Draw(image).rectangle((0, 0, side, side), fill=patch)
    return encoded(image)


def near(hue: float, expected: float, tolerance: float = 15.0) -> bool:
    gap = abs((hue - expected) % 360.0)
    return min(gap, 360.0 - gap) <= tolerance


# --- reading a hue ----------------------------------------------------------

@pytest.mark.parametrize("colour,expected", [
    ((200, 40, 40), 0.0),      # red
    ((40, 180, 60), 120.0),    # green
    ((40, 70, 200), 225.0),    # blue
    ((30, 150, 160), 185.0),   # teal
])
def test_a_solid_cover_reports_its_own_hue(colour, expected):
    got = songcolour.extract(solid(colour))
    assert not got.neutral
    assert near(got.hue, expected)


def test_a_coloured_cover_reports_most_of_itself_as_coloured():
    assert songcolour.extract(solid((200, 40, 40))).weight > 0.9


# --- refusing to invent one -------------------------------------------------

@pytest.mark.parametrize("colour", [(0, 0, 0), (128, 128, 128), (250, 250, 250)])
def test_a_greyscale_cover_reports_no_colour(colour):
    # Treating a JPEG cast as the song's colour is how a black sleeve ends up
    # faintly green.
    assert songcolour.extract(solid(colour)).neutral


def test_a_small_vivid_patch_is_not_the_songs_colour():
    # Under the coloured fraction: a detail, not what the cover reads as.
    assert songcolour.extract(patched((40, 40, 40), (255, 0, 0), 0.02)).neutral


def test_a_large_vivid_region_is():
    got = songcolour.extract(patched((40, 40, 40), (255, 0, 0), 0.30))
    assert not got.neutral
    assert near(got.hue, 0.0)


def test_colour_beats_population():
    # The whole reason the swatches are scored rather than counted: a near-black
    # background covers more of this cover than the colour that names it.
    got = songcolour.extract(patched((12, 12, 14), (30, 150, 170), 0.22))
    assert not got.neutral
    assert near(got.hue, 187.0, 20.0)
    assert max(got.dominant) < 40, "the most populous swatch really is the dark one"


# --- the accent -------------------------------------------------------------

def test_a_two_colour_cover_reports_the_second_hue_as_its_accent():
    image = Image.new("RGB", (300, 300), (200, 40, 40))
    ImageDraw.Draw(image).rectangle((0, 0, 300, 140), fill=(40, 80, 200))
    got = songcolour.extract(encoded(image))
    assert not got.neutral
    hues = sorted((got.hue, got.accent_hue))
    assert near(hues[0], 0.0, 20.0) or near(hues[0], 360.0, 20.0)
    assert near(hues[1], 225.0, 20.0)


def test_a_single_colour_cover_reports_no_separate_accent():
    got = songcolour.extract(solid((200, 40, 40)))
    assert got.accent_hue == got.hue


# --- robustness -------------------------------------------------------------

def test_undecodable_data_is_neutral_rather_than_an_exception():
    assert songcolour.extract(b"this is not an image").neutral


def test_empty_data_is_neutral():
    assert songcolour.extract(b"").neutral


def test_png_is_accepted_as_well_as_jpeg():
    data = encoded(Image.new("RGB", (300, 300), (200, 40, 40)), fmt="PNG")
    assert not songcolour.extract(data).neutral


def test_a_one_pixel_cover_does_not_crash():
    assert songcolour.extract(solid((200, 40, 40), size=1)) is not None


def test_extraction_is_quick_enough_to_run_inline():
    # It runs when a cover arrives; a slow one would drop frames.
    data = solid((30, 150, 160), size=600)
    songcolour.extract(data)
    best = min(_timed(data) for _ in range(5))
    assert best < 0.050, f"{best * 1000:.1f} ms is too slow to run on arrival"


def _timed(data: bytes) -> float:
    import time
    # Emptied first, or this would time a dict lookup and pass whatever the
    # real cost had grown to. The cache is there to spare a resize the work,
    # not to spare this test the measurement.
    songcolour._CACHE.clear()
    start = time.perf_counter()
    songcolour.extract(data)
    return time.perf_counter() - start


# --- remembering an answer that cannot have changed -------------------------

def test_the_same_cover_gives_the_same_answer_from_the_cache():
    # A resize asks again about the cover already on screen. The cached answer
    # has to be the one the work would have produced, not merely a plausible
    # one.
    data = patched((12, 12, 14), (30, 150, 170), 0.22)
    songcolour._CACHE.clear()
    fresh = songcolour._measure(data)
    songcolour._CACHE.clear()
    first = songcolour.extract(data)
    assert first == fresh
    assert songcolour.extract(data) == fresh


def test_a_cached_answer_costs_no_decode(monkeypatch):
    data = solid((200, 40, 40))
    songcolour._CACHE.clear()
    songcolour.extract(data)

    calls = []
    monkeypatch.setattr(songcolour, "_measure",
                        lambda d: calls.append(d) or songcolour.NEUTRAL)
    assert not songcolour.extract(data).neutral, "answered from the cache"
    assert calls == [], "the cover was read again"


def test_the_cache_does_not_grow_without_bound():
    # It holds covers, which are hundreds of kilobytes each.
    songcolour._CACHE.clear()
    for shade in range(songcolour._CACHE_MAX + 3):
        songcolour.extract(solid((200, 40, 40 + shade)))
    assert len(songcolour._CACHE) <= songcolour._CACHE_MAX


def test_a_different_cover_is_not_answered_with_the_previous_one():
    songcolour._CACHE.clear()
    red = songcolour.extract(solid((200, 40, 40)))
    green = songcolour.extract(solid((40, 180, 60)))
    assert near(red.hue, 0.0)
    assert near(green.hue, 120.0)
