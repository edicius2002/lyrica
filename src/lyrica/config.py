"""Settings read from a local `.env`, for values that must not be committed.

Deliberately tiny and dependency-free: this reads a handful of keys once at
startup, which is not worth a library.

Nothing here overwrites a variable already set in the environment. A value
exported in a shell is a deliberate act for that session, and a file on disk
should not quietly override it.
"""
import json
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
# How far the sync nudge may be remembered. A correction, not a seek.
OFFSET_LIMIT_S = 10.0

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


BEAM_STYLES = ("comet", "shine")


def beam_style() -> str:
    """How the border reacts to what is playing: `comet`, `shine` or `off`.

    `comet` is a bright head travelling a dark ring. `shine` lights the whole
    border and rotates a gradient through it, so every edge stays lit and what
    moves is the colour — quieter to sit beside while reading.

    Either keeps the render loop awake for as long as the overlay is visible,
    which is the one running cost the overlay has that nothing else asks for.
    """
    raw = os.environ.get("LYRICA_BEAM", "").strip().lower()
    if raw in ("0", "off", "no", "false"):
        return "off"
    if raw in BEAM_STYLES:
        return raw
    if raw:
        logger.warning("LYRICA_BEAM=%r is not one of %s or off; using comet",
                       raw, ", ".join(BEAM_STYLES))
    return "comet"


def settings_path() -> Path:
    """Where choices made with the mouse and keyboard are remembered."""
    return cache_root() / "settings.json"


def size_path() -> Path:
    """The older single-value file, still read so an upgrade loses nothing."""
    return cache_root() / "size"


def settings() -> dict:
    """Everything remembered, or an empty mapping.

    Never raises and never returns anything but a mapping. This is read before
    the window exists, on a machine with no console, so a file someone edited
    into nonsense has to degrade into defaults rather than into no overlay.
    """
    try:
        loaded = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_setting(key: str, value) -> None:
    """Remember one thing. Failing to is not fatal.

    Read-modify-write of the whole file rather than an append: there are a
    handful of values, they are written when a hand stops moving rather than in
    a loop, and one small object is easier to reason about than a log to replay.
    """
    current = settings()
    if current.get(key) == value:
        return                      # nothing changed; do not touch the disk
    current[key] = value
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current), encoding="utf-8")
    except OSError:
        logger.debug("could not save %s", key, exc_info=True)


def saved_size() -> float | None:
    value = settings().get("size")
    if value is not None:
        return _parse_size(str(value), "the saved size")
    # The file this used to live in, before there were several things to keep.
    try:
        return _parse_size(size_path().read_text(encoding="utf-8"), "the saved size")
    except OSError:
        return None


def save_size(value: float) -> None:
    """Remember a size chosen with the keyboard."""
    save_setting("size", round(clamp_size(value), 2))


def saved_place() -> tuple[int, int] | None:
    """Where the window was left: (horizontal middle, top edge).

    Two different anchors, because the window's two sizes preserve two
    different things. Collapsing to the card holds the horizontal middle while
    the corner moves under it, so the middle is what survives across widths.
    But it holds the *top edge*, not the vertical middle — the card lives up
    there and must not move — so a vertical centre saved while compact belongs
    to a 114 px window and reopening at 375 px put the panel 130 px too high.

    Falls back to the older `centre` key, read the way it was written, so an
    upgrade does not lose the position it had.
    """
    value = settings().get("place")
    if _is_pair(value):
        return int(value[0]), int(value[1])
    return None


def saved_centre() -> tuple[int, int] | None:
    """Where the middle of the window was left, or None.

    The middle rather than the corner, because the window is not one width. It
    collapses to the card when a track has no lyrics and grows again for the
    next one that has some, and both hold the horizontal middle while the corner
    moves under it — so a corner saved while compact describes a place the
    full-size window was never at. Measured: left at 527, collapsed, saved as
    927, and reopened 400 px right of where it had been put.

    Vertically the two differ — a collapse holds the top edge so the card does
    not move, a keyboard resize holds the middle — which is why only a drag
    writes here. A drag is the one moment the position is a decision rather
    than something derived from a size change.

    Not trusted on the way back either. A position saved on a second monitor
    since unplugged is a window nobody can see and nobody can drag back, so the
    caller clamps it to whatever screen exists now.
    """
    value = settings().get("centre")
    if _is_pair(value):
        return int(value[0]), int(value[1])
    return None


def save_place(x: int, top: int) -> None:
    save_setting("place", [int(x), int(top)])


def _is_pair(value) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, (int, float)) for v in value))


def _bounded_offset(value) -> float:
    if not isinstance(value, (int, float)):
        return 0.0
    # Bounded, because a nudge is a correction and not a seek: anything past a
    # few seconds is a mis-saved file rather than a preference, and restoring it
    # would show the wrong line with no obvious cause.
    return max(-OFFSET_LIMIT_S, min(OFFSET_LIMIT_S, float(value)))


# How many tracks keep a remembered nudge. A nudge is worth a few bytes and
# almost never revisited, so the cap is only there to stop the settings file
# growing without bound over years of listening.
OFFSET_MEMORY = 200


def saved_offset(track: str = "") -> float:
    """The sync nudge for one track, in seconds.

    Per track because the thing it corrects is per track: a video with eight
    seconds of intro before the song needs eight seconds that no other track
    wants. One shared number meant fixing a video broke the next one.
    """
    offsets = settings().get("offsets")
    if track and isinstance(offsets, dict) and track in offsets:
        return _bounded_offset(offsets[track])
    # Whatever a single global nudge was set to before nudges were per track.
    # Still honoured as the starting point for a track that has none of its own.
    return _bounded_offset(settings().get("offset"))


def save_offset(seconds: float, track: str = "") -> None:
    if not track:
        save_setting("offset", round(seconds, 2))
        return
    stored = settings().get("offsets")
    offsets = dict(stored) if isinstance(stored, dict) else {}
    # Re-inserted rather than updated in place, so the dictionary's order is
    # least-recently-set first and the trim below drops the right end.
    offsets.pop(track, None)
    offsets[track] = round(seconds, 2)
    for stale in list(offsets)[:-OFFSET_MEMORY]:
        del offsets[stale]
    save_setting("offsets", offsets)


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
