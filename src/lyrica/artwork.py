"""Turn the playing track's artwork into a backdrop for the glass.

The point is colour, not the picture. Blurred past recognition and darkened
hard, the artwork reads as the song having a hue rather than as an image sitting
behind the words — which is what stops it competing with the lyrics for
attention.

Additive composition does most of the work: a heavily darkened image adds a
faint wash of its own colour through the frosted plate instead of painting over
it. That is why the brightness here can be so low and still be visible at all.
"""
import hashlib
import io
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from lyrica import config

logger = logging.getLogger(__name__)

# Apple's public catalogue search. No key, no registration, and the size is
# part of the image URL, so asking for a large one is a substitution.
SEARCH_URL = "https://itunes.apple.com/search"
HEADERS = {"User-Agent": "lyrica/0.1.0 (personal overlay)"}

# The open fallback. Also keyless, but slower and patchier — good exactly where
# a commercial catalogue is not.
DISCOGS_URL = "https://api.discogs.com/database/search"

# Downscaled brutally before blurring. A 30-pixel-wide image blurred and scaled
# back up is both far faster and softer than blurring at full size, and detail
# is exactly what is being thrown away.
SAMPLE_WIDTH = 32
BLUR_RADIUS = 8

# Where the wash is scaled to land: the 95th-percentile channel of every
# backdrop, whatever the cover started at. High enough to read as the song
# having a hue, low enough that unsung text at 138 keeps better than 3:1 over
# it — which a fixed brightness factor could not promise, because it has to be
# picked for the brightest sleeve and is then wrong for all the others.
BACKDROP_CAP = 30


class Unreachable(Exception):
    """A source could not be asked, as opposed to answering that it has nothing.

    The difference matters only here, but it matters a lot: a recorded miss is
    believed for a fortnight and is written under the album key, so treating one
    dropped connection as an answer leaves a whole record with no cover for two
    weeks. A source that could not be reached records nothing and is simply
    asked again next time.
    """


def fetch_cover(artist: str, title: str, album: str = "", size: int = 600) -> bytes | None:
    """A high-resolution cover from Apple's public catalogue search.

    The media session's own thumbnail is whatever the player felt like
    publishing — often 64 or 100 pixels, which looks soft the moment it is
    drawn at any size. This endpoint needs no key and returns a URL whose
    dimensions are part of the path, so asking for a larger one is a string
    substitution rather than an upscale.

    Returns None on anything unexpected: a missing cover is a cosmetic loss,
    and the session's own thumbnail is already on screen by the time this runs.
    """
    query = " ".join(part for part in (artist, title) if part).strip()
    if not query:
        return None
    try:
        r = requests.get(SEARCH_URL,
                         params={"term": query, "entity": "song", "limit": 3},
                         headers=HEADERS, timeout=8)
        r.raise_for_status()
        results = r.json().get("results") or []
    except (requests.RequestException, ValueError):
        logger.debug("cover search failed for %r", query, exc_info=True)
        raise Unreachable from None

    best = _closest(results, artist, title, album)
    if best is None:
        return None
    url = best.get("artworkUrl100") or best.get("artworkUrl60") or ""
    if not url:
        return None
    # The size lives in the filename, so a bigger one is a substitution.
    for token in ("100x100", "60x60"):
        url = url.replace(token, f"{size}x{size}")
    try:
        image = requests.get(url, headers=HEADERS, timeout=10)
        image.raise_for_status()
        return image.content
    except requests.RequestException:
        logger.debug("cover download failed", exc_info=True)
        raise Unreachable from None


def _closest(results: list, artist: str, title: str, album: str):
    """Pick the result that looks like the track, or nothing.

    A search always answers with something; showing a stranger's cover over
    someone's lyrics is worse than showing the small one the player gave us.
    """
    def norm(s: str) -> str:
        return "".join(c for c in (s or "").lower() if c.isalnum() or c.isspace()).strip()

    want_artist, want_title, want_album = norm(artist), norm(title), norm(album)
    best, best_score = None, 0.0
    for item in results:
        got_artist = norm(item.get("artistName", ""))
        got_title = norm(item.get("trackName", ""))
        score = 0.0
        if want_artist and (want_artist in got_artist or got_artist in want_artist):
            score += 2
        if want_title and (want_title == got_title):
            score += 2
        elif want_title and (want_title in got_title or got_title in want_title):
            score += 1
        if want_album and norm(item.get("collectionName", "")) == want_album:
            score += 1
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= 3 else None


def discogs_token() -> str:
    """The token, if the user set one. Read from the environment, never stored.

    A credential in a config file inside the repository is a credential waiting
    to be committed, so this one only ever lives in the environment of whoever
    chose to provide it.
    """
    return os.environ.get("LYRICA_DISCOGS_TOKEN", "").strip()


def fetch_cover_discogs(artist: str, title: str, album: str = "") -> bytes | None:
    """A cover from Discogs, if a token is configured.

    Worth having for what a commercial catalogue skips — obscure pressings,
    vinyl, special editions — and worth being honest about: the images are
    collector scans, so they range from excellent to a crooked photograph of a
    sleeve. That is why this runs after Apple rather than instead of it.

    Searches for the release rather than the track: Discogs is organised around
    physical objects, and a single is a release of its own.
    """
    token = discogs_token()
    if not token:
        return None
    query = " ".join(part for part in (artist, album or title) if part).strip()
    if not query:
        return None
    try:
        r = requests.get(DISCOGS_URL,
                         params={"q": query, "type": "release", "per_page": 5},
                         headers={**HEADERS, "Authorization": f"Discogs token={token}"},
                         timeout=10)
        r.raise_for_status()
        results = r.json().get("results") or []
    except (requests.RequestException, ValueError):
        # Deliberately quiet about the response: a failed auth reply can carry
        # the token back, and this goes to a log file.
        logger.debug("discogs search failed")
        raise Unreachable from None

    for item in results:
        url = item.get("cover_image") or ""
        # Discogs serves a generic record icon when a release has no scan.
        if not url or "spacer.gif" in url:
            continue
        try:
            image = requests.get(url, headers=HEADERS, timeout=10)
            if image.status_code == 200 and image.content:
                return image.content
        except requests.RequestException:
            continue
    return None


def cover_dir() -> Path:
    path = config.cache_root() / "covers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _digest(key: str) -> Path:
    return cover_dir() / (
        hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest() + ".img")


def _cover_path(artist: str, title: str, album: str) -> Path:
    """The per-track key. Every track gets one; it is the fallback."""
    return _digest(f"{artist.lower()}|{title.lower()}|{album.lower()}")


def _album_path(artist: str, album: str) -> Path | None:
    """The per-album key, where the player named an album.

    Cover art belongs to a release, not to a track, and keying it by track made
    every song on an album pay its own network round trip for the same picture.
    Measured on this machine's cache: 52 stored covers were 34 distinct images,
    one of them written six times — 18 fetches of roughly a second each that a
    release-shaped key would have answered from disk.
    """
    if not album or not (artist or album):
        return None
    return _digest(f"album|{artist.lower()}|{album.lower()}")


# How long a recorded miss is believed. Misses have to be recorded or a track
# no source has would re-ask every source on every replay — but they are also
# indistinguishable from a network that was down, and keying by album means one
# bad minute can silence a whole record. Believing them for a fortnight keeps
# the saving and puts a bound on the damage.
MISS_TTL_S = 14 * 24 * 3600


def _read(path: Path | None) -> bytes | None:
    """Bytes for a hit, b"" for a live recorded miss, None for nothing usable."""
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data:
        return data
    try:
        if time.time() - path.stat().st_mtime > MISS_TTL_S:
            return None         # stale miss: worth asking again
    except OSError:
        return None
    return b""


def cached_cover(artist: str, title: str, album: str = "") -> bytes | None:
    """A cover already on disk, or None.

    The album key is tried first and the track key second, so entries written
    before covers were keyed by release still answer instead of forcing one
    refetch each.
    """
    for path in (_album_path(artist, album), _cover_path(artist, title, album)):
        data = _read(path)
        if data:
            return data
    return None


def _recorded_miss(artist: str, title: str, album: str) -> bool:
    return any(_read(p) == b"" for p in
               (_album_path(artist, album), _cover_path(artist, title, album)))


def store_cover(artist: str, title: str, album: str, data: bytes | None) -> None:
    """Keep a cover for next time. Misses are kept too.

    Written under the album key when there is one, so the rest of the record is
    already answered before it is ever played.
    """
    path = _album_path(artist, album) or _cover_path(artist, title, album)
    try:
        path.write_bytes(data or b"")
    except OSError:
        logger.debug("could not cache the cover", exc_info=True)


def best_cover(artist: str, title: str, album: str = "", size: int = 600) -> bytes | None:
    """The best cover any configured source has, or None.

    Disk first, so a track played before appears instantly rather than after a
    network round trip. That is the path that matters — it costs 0.06 ms, and
    everything after it costs about a second.

    Then Apple, which is both quicker and usually better. Timed on four tracks:
    339-925 ms against Discogs' 1086-1198 ms, and where both answered, Apple
    returned the larger image twice of three — once by fifteen times, 166 KB
    against 11 KB. It also needs no token, so it is what works when none is
    configured.

    Discogs last, and it earns its place on exactly what a commercial catalogue
    skips. In the same four, the one track Apple had no cover for at all was a
    Latin release Discogs answered with 97 KB. The images are collector scans,
    so they range from excellent to a crooked photograph of a sleeve — good
    insurance, poor default.
    """
    if not (artist or title):
        return None
    cached = cached_cover(artist, title, album)
    if cached is not None:
        return cached
    if _recorded_miss(artist, title, album):
        return None     # asked before and nobody had it

    data, unreachable = None, False
    for ask in (lambda: fetch_cover(artist, title, album, size=size),
                lambda: fetch_cover_discogs(artist, title, album)):
        try:
            data = ask()
        except Unreachable:
            # Ask the next source anyway: Apple being down is no reason not to
            # try Discogs, and before this the `or` chain skipped it.
            unreachable = True
            continue
        if data:
            break
    if data or not unreachable:
        store_cover(artist, title, album, data)
    return data


def available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


# A cover this far from square is a photograph of a sleeve rather than the
# sleeve, and cropping it to a square would cut the artwork instead of framing
# it. Past this, the whole image is fitted and the gap filled instead.
SQUARE_ENOUGH = 1.08


def make_thumbnail(data: bytes, size: int):
    """A small square cover for the header, sharp and whole.

    Nearly-square art is cropped, which keeps its proportions and fills the
    box. Anything further off is fitted inside instead, on a background taken
    from its own edge — cropping a 500x453 scan to a square loses the sides of
    the sleeve, and losing part of the artwork is worse than a little padding
    nobody will notice behind a rounded corner.
    """
    if not data or size <= 0:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
        ratio = max(image.width, image.height) / max(1, min(image.width, image.height))
        if ratio <= SQUARE_ENOUGH:
            side = min(image.width, image.height)
            left = (image.width - side) // 2
            top = (image.height - side) // 2
            square = image.crop((left, top, left + side, top + side))
            return square.resize((size, size), Image.LANCZOS)

        fitted = image.copy()
        fitted.thumbnail((size, size), Image.LANCZOS)
        # The corner pixel, so the padding reads as part of the sleeve rather
        # than as a black bar around it.
        canvas = Image.new("RGB", (size, size), image.getpixel((0, 0)))
        canvas.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
        return canvas
    except Exception:
        logger.debug("could not build a thumbnail", exc_info=True)
        return None


def _peak(sample) -> int:
    """The 95th percentile of the largest channel across the wash.

    The 95th rather than the maximum, because one blown highlight in a corner
    should not darken the whole window, and rather than the mean because the
    mean is dragged down by the dark half of any cover and says nothing about
    where text will actually struggle.
    """
    raw = sample.tobytes()
    if not raw:
        return 0
    peaks = sorted(max(raw[i], raw[i + 1], raw[i + 2])
                   for i in range(0, len(raw) - 2, 3))
    return peaks[min(len(peaks) - 1, int(len(peaks) * 0.95))]


def _mean(sample) -> tuple:
    raw = sample.tobytes()
    count = len(raw) // 3
    if not count:
        return (0, 0, 0)
    return tuple(round(sum(raw[c::3]) / count) for c in range(3))


@dataclass(frozen=True)
class Backdrop:
    """The cover wash, plus what it measures out to."""
    image: object       # PIL, window-sized; converted to Tk on the render thread
    colour: tuple       # the mean wash — what a line fading out dissolves into
    peak: int           # B95 after capping, for the log


def make_backdrop(data: bytes, width: int, height: int) -> "Backdrop | None":
    """A blurred, darkened, window-sized wash, or None if it cannot be made.

    The darkening is measured per cover, not fixed. A single brightness factor
    has to be chosen for the brightest sleeve there is, which leaves every dark
    one needlessly dimmer than it could be, and still fails whenever a cover is
    brighter than the one the factor was picked against. Scaling each cover so
    its 95th-percentile channel lands on `BACKDROP_CAP` puts every backdrop at
    the same measured level instead, whatever it started at — which is what the
    text contrast is actually a function of.

    Only ever darkens. A cover already below the cap is left alone: lifting it
    would amplify JPEG noise in the shadows to buy contrast it already has.

    Returns PIL rather than Tk images because converting has to happen on the
    thread owning the widget, and this is called off it.
    """
    if not data:
        return None
    try:
        from PIL import Image, ImageEnhance, ImageFilter
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
    except Exception:
        logger.debug("could not decode artwork", exc_info=True)
        return None

    try:
        # Cover the window rather than fit it: letterboxing would put hard
        # edges into something whose whole job is to have none.
        ratio = max(width / image.width, height / image.height)
        sample = image.resize(
            (max(1, int(image.width * ratio / SAMPLE_WIDTH)),
             max(1, int(image.height * ratio / SAMPLE_WIDTH))),
            Image.BILINEAR)
        sample = sample.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))

        peak = _peak(sample)
        if peak > BACKDROP_CAP:
            sample = ImageEnhance.Brightness(sample).enhance(BACKDROP_CAP / peak)
            peak = _peak(sample)

        stretched = sample.resize((max(1, width), max(1, height)), Image.BICUBIC)
        return Backdrop(stretched, _mean(sample), peak)
    except Exception:
        logger.debug("could not build a backdrop", exc_info=True)
        return None
