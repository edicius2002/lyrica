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


def size_scale() -> float:
    """How much bigger or smaller than designed the overlay should be.

    Multiplies into the display scale, so it reaches every measurement the same
    way DPI does — window, fonts, cover, gaps and fade bands together. That is
    what keeps the proportions: nothing is resized against anything else.

    A bad value is ignored rather than fatal. This is read at startup on a
    machine with no console, so raising here would be a window that never
    appears and no way to find out why.
    """
    raw = os.environ.get("LYRICA_SIZE", "").strip()
    if not raw:
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning("LYRICA_SIZE=%r is not a number; using the designed size", raw)
        return 1.0
    if not SIZE_MIN <= value <= SIZE_MAX:
        clamped = max(SIZE_MIN, min(SIZE_MAX, value))
        logger.warning("LYRICA_SIZE=%s is outside %s-%s; using %s",
                       value, SIZE_MIN, SIZE_MAX, clamped)
        return clamped
    return value


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
