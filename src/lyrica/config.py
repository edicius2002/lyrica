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
