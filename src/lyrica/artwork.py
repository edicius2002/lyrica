"""Turn the playing track's artwork into a backdrop for the glass.

The point is colour, not the picture. Blurred past recognition and darkened
hard, the artwork reads as the song having a hue rather than as an image sitting
behind the words — which is what stops it competing with the lyrics for
attention.

Additive composition does most of the work: a heavily darkened image adds a
faint wash of its own colour through the frosted plate instead of painting over
it. That is why the brightness here can be so low and still be visible at all.
"""
import io
import logging
import os

import requests

logger = logging.getLogger(__name__)

# Apple's public catalogue search. No key, no registration, and the size is
# part of the image URL, so asking for a large one is a substitution.
SEARCH_URL = "https://itunes.apple.com/search"
HEADERS = {"User-Agent": "lyrica/0.1.0 (personal overlay)"}

# The open fallback. Also keyless, but slower and patchier — good exactly where
# a commercial catalogue is not.
DISCOGS_URL = "https://api.discogs.com/database/search"
MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/recording"
COVER_ART_URL = "https://coverartarchive.org/release"
# MusicBrainz asks callers to identify themselves rather than arrive anonymous.
MB_HEADERS = {"User-Agent": "lyrica/0.1.0 (personal lyrics overlay; github.com/edicius2002/lyrica)"}

# Downscaled brutally before blurring. A 30-pixel-wide image blurred and scaled
# back up is both far faster and softer than blurring at full size, and detail
# is exactly what is being thrown away.
SAMPLE_WIDTH = 32
BLUR_RADIUS = 8

# How much of the artwork's light reaches the plate. High enough to tint, low
# enough that no edge or face is ever recoverable.
BRIGHTNESS = 0.30


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
        return None

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
        return None


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
        return None

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


def fetch_cover_openly(artist: str, title: str, size: int = 500) -> bytes | None:
    """A cover from MusicBrainz and the Cover Art Archive.

    The open fallback for what Apple's catalogue does not carry — obscure
    pressings, independent releases, anything outside a commercial store. Also
    keyless, but slower and patchier, which is why it runs second.

    MusicBrainz asks callers to identify themselves and to stay under a request
    a second. Both are honoured: this runs once per track, after another source
    has already failed, so the rate is a non-issue by construction.
    """
    query = " AND ".join(
        part for part in (f'artist:"{artist}"' if artist else "",
                          f'recording:"{title}"' if title else "") if part)
    if not query:
        return None
    try:
        r = requests.get(MUSICBRAINZ_URL,
                         params={"query": query, "fmt": "json", "limit": 3},
                         headers=MB_HEADERS, timeout=10)
        r.raise_for_status()
        recordings = r.json().get("recordings") or []
    except (requests.RequestException, ValueError):
        logger.debug("musicbrainz lookup failed for %r", query, exc_info=True)
        return None

    for recording in recordings:
        for release in recording.get("releases") or []:
            mbid = release.get("id")
            if not mbid:
                continue
            try:
                art = requests.get(f"{COVER_ART_URL}/{mbid}/front-{size}",
                                   headers=MB_HEADERS, timeout=10)
                if art.status_code == 200 and art.content:
                    return art.content
            except requests.RequestException:
                continue
    return None


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


def make_backdrop(data: bytes, width: int, height: int):
    """A blurred, darkened, window-sized image, or None if it cannot be made.

    Returns a PIL image rather than a Tk one: converting to a Tk image has to
    happen on the thread that owns the widget, and this is called off it.
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
            (max(1, int(image.width * ratio / SAMPLE_WIDTH)) or 1,
             max(1, int(image.height * ratio / SAMPLE_WIDTH)) or 1),
            Image.BILINEAR)
        sample = sample.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
        sample = ImageEnhance.Brightness(sample).enhance(BRIGHTNESS)
        stretched = sample.resize((max(1, width), max(1, height)), Image.BICUBIC)
        return stretched
    except Exception:
        logger.debug("could not build a backdrop", exc_info=True)
        return None
