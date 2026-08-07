"""Settings read from a local `.env`, for values that must not be committed.

Deliberately tiny and dependency-free: this reads a handful of keys once at
startup, which is not worth a library.

Nothing here overwrites a variable already set in the environment. A value
exported in a shell is a deliberate act for that session, and a file on disk
should not quietly override it.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

FILENAME = ".env"


def cache_root() -> Path:
    """Where everything Lyrica stores locally lives.

    `LYRICA_CACHE_DIR` moves the whole store, which is how it follows you
    between machines: every file under here is written once and never modified,
    so plain folder sync has nothing to reconcile.
    """
    override = os.environ.get("LYRICA_CACHE_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Lyrica"


# How far the overlay may be scaled from its designed size. Bounded rather than
# free: below the lower limit the lyric font rounds to a size where the sweep
# lands on whole characters and stops reading as a sweep, and above the upper
# one three lines and a card no longer fit on a laptop screen.
SIZE_MIN, SIZE_MAX = 0.6, 2.0


def clamp_size(value: float) -> float:
    return max(SIZE_MIN, min(SIZE_MAX, value))


def _parse_size(raw: str, source: str) -> float | None:
    """A size from text, or None if there was nothing usable in it.

    Never raises. These are read at startup on a machine with no console, so an
    exception here would be a window that never appears and no way to find out
    why — a warning in the log and the designed size is the honest failure.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using the designed size", source, raw)
        return None
    if not SIZE_MIN <= value <= SIZE_MAX:
        logger.warning("%s=%s is outside %s-%s; using %s",
                       source, value, SIZE_MIN, SIZE_MAX, clamp_size(value))
    return clamp_size(value)


# How solid the panel is. Below the lower bound the desktop reads through the
# text; at 1.0 it is an opaque slab and stops being glass at all.
OPACITY_MIN, OPACITY_MAX = 0.60, 1.0
OPACITY_DEFAULT = 0.90


def opacity() -> float:
    """How solid the panel should be, 0.6 to 1.0.

    A taste setting with a floor rather than a free one: the panel's whole job
    is to be legible over whatever is behind it, and past a point the desktop
    starts competing with the words. Bad values are ignored with a warning for
    the same reason sizes are — this is read at startup on a machine with no
    console.
    """
    raw = os.environ.get("LYRICA_OPACITY", "").strip()
    if not raw:
        return OPACITY_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        logger.warning("LYRICA_OPACITY=%r is not a number; using %s",
                       raw, OPACITY_DEFAULT)
        return OPACITY_DEFAULT
    if not OPACITY_MIN <= value <= OPACITY_MAX:
        logger.warning("LYRICA_OPACITY=%s is outside %s-%s; clamping",
                       value, OPACITY_MIN, OPACITY_MAX)
    return max(OPACITY_MIN, min(OPACITY_MAX, value))


def size_path() -> Path:
    """Where a size chosen with the keyboard is remembered."""
    return cache_root() / "size"


def saved_size() -> float | None:
    try:
        return _parse_size(size_path().read_text(encoding="utf-8"), "the saved size")
    except OSError:
        return None


def save_size(value: float) -> None:
    """Remember a size chosen with the keyboard. Failing to is not fatal."""
    try:
        path = size_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{clamp_size(value):.2f}", encoding="utf-8")
    except OSError:
        logger.debug("could not save the overlay size", exc_info=True)


def size_scale() -> float:
    """How much bigger or smaller than designed the overlay should be.

    Multiplies into the display scale, so it reaches every measurement the same
    way DPI does — window, fonts, cover, gaps and fade bands together. That is
    what keeps the proportions: nothing is resized against anything else.

    A size chosen with the keyboard wins over `LYRICA_SIZE`, which is the only
    order that is not surprising: adjusting the window and watching it change,
    then finding it back where it was on the next run, would read as the
    keyboard being broken rather than as a setting taking precedence. So
    `LYRICA_SIZE` is where a machine starts, and the keyboard is what overrides
    it from then on. Which source won is logged, since the loser is invisible.
    """
    saved = saved_size()
    if saved is not None:
        return saved
    return _parse_size(os.environ.get("LYRICA_SIZE", ""), "LYRICA_SIZE") or 1.0


def find_env(start: Path | None = None) -> Path | None:
    """The nearest `.env` at or above `start`, so it works from any directory."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / FILENAME
        if candidate.is_file():
            return candidate
    return None


def parse(text: str) -> dict[str, str]:
    """KEY=value pairs. Blank lines and `#` comments ignored.

    Surrounding quotes are stripped, since a token pasted from a web page often
    arrives wearing them and the quotes are not part of the value.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load(start: Path | None = None) -> dict[str, str]:
    """Read the nearest `.env` into the environment. Returns what it set."""
    path = find_env(start)
    if path is None:
        return {}
    try:
        values = parse(path.read_text(encoding="utf-8"))
    except OSError:
        logger.debug("could not read %s", path, exc_info=True)
        return {}

    applied = {}
    for key, value in values.items():
        if key in os.environ:
            continue        # an exported value wins; the file is the fallback
        os.environ[key] = value
        applied[key] = value
    if applied:
        # Names only. The values are exactly what must not reach a log file.
        logger.info("loaded %s from %s", ", ".join(sorted(applied)), path.name)
    return applied
