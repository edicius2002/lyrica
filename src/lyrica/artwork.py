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

logger = logging.getLogger(__name__)

# Downscaled brutally before blurring. A 30-pixel-wide image blurred and scaled
# back up is both far faster and softer than blurring at full size, and detail
# is exactly what is being thrown away.
SAMPLE_WIDTH = 32
BLUR_RADIUS = 8

# How much of the artwork's light reaches the plate. High enough to tint, low
# enough that no edge or face is ever recoverable.
BRIGHTNESS = 0.30


def available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


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
