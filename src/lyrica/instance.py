"""One overlay at a time.

Nothing stopped a second Lyrica starting beside the first, and each one adds its
own notification-area icon and its own always-on-top window drawn over the same
lyrics. Two things made that easy to do by accident. The overlay only appears
once something is playing, so a launch can look as though it did nothing at all
and invite another. And the process used to end abruptly — the bloom cache
exhausted the GDI handles a process is given and Tk aborted from
`Tk_GetPixmap` — which leaves the icon behind and invites one more launch.

The guard is a named mutex rather than a lock file. Windows releases it when the
last handle to it closes, and it closes every handle a process holds when the
process dies however it dies, so there is no stale claim to reason about after a
crash — which is exactly what a lock file would have left.
"""
import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

# Session-scoped, because it is one overlay per logged-in user and not one per
# machine: a second user on the same box is entitled to their own.
NAME = "Lyrica-overlay-single-instance"

ERROR_ALREADY_EXISTS = 183

# The handle is held in a module global for the life of the process on purpose.
# The name lasts exactly as long as a handle to it is open, so letting this one
# be collected would free the name while the overlay it stands for is still up.
_held: int | None = None


def _kernel32():
    """kernel32 with `use_last_error`, which is what makes the code below work.

    `ctypes.windll` does not save the thread's last error, so
    `ctypes.get_last_error()` against it reads zero whatever happened — and the
    whole question here is whether `CreateMutexW` succeeded outright or
    succeeded by handing back the existing mutex.
    """
    return ctypes.WinDLL("kernel32", use_last_error=True)


def claim(name: str = NAME) -> bool:
    """True if this process may be the overlay; False if one is already running.

    A failure to ask is answered with yes. Refusing to start because the guard
    itself broke would trade a duplicate icon for no overlay at all, which is
    the worse of the two.
    """
    global _held
    if sys.platform != "win32":
        return True
    if _held is not None:
        return False
    try:
        k32 = _kernel32()
        handle = k32.CreateMutexW(None, False, name)
        if not handle:
            logger.debug("could not create the single-instance mutex",
                         exc_info=True)
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False
    except (AttributeError, OSError):
        logger.debug("could not ask whether Lyrica is already running",
                     exc_info=True)
        return True
    _held = handle
    return True


def release() -> None:
    """Give the name up. Safe having never claimed it."""
    global _held
    if _held is None:
        return
    handle, _held = _held, None
    try:
        _kernel32().CloseHandle(handle)
    except (AttributeError, OSError):
        logger.debug("could not release the single-instance mutex",
                     exc_info=True)
