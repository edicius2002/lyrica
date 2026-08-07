"""Starting with Windows, as a per-user registry entry.

`HKEY_CURRENT_USER` rather than the machine-wide key on purpose: this needs no
administrator, affects nobody else who signs in, and can be undone by the same
menu item that set it. Nothing here writes outside the current user's own hive.

Only the packaged executable can really offer this. Run from a source checkout
the command would have to name an interpreter, a working directory and a
module, all of which move the moment the checkout does — so it is reported
unavailable instead, which is what greys the menu item out.
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Lyrica"


def frozen() -> bool:
    """True when running as the packaged executable rather than from source."""
    return bool(getattr(sys, "frozen", False))


def command() -> str | None:
    """What Windows should run at sign-in, or None if it cannot be named."""
    if sys.platform != "win32" or not frozen():
        return None
    return f'"{Path(sys.executable).resolve()}"'


def available() -> bool:
    return command() is not None


def enabled() -> bool:
    """Whether the entry is there *and* points at this copy.

    Compared rather than merely present: a stale entry left by an executable
    that has since been moved or deleted would otherwise report itself as
    working, and the menu would show a tick beside something that does nothing.
    """
    wanted = command()
    if wanted is None:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except (ImportError, OSError):
        return False
    return current.strip().lower() == wanted.strip().lower()


def set_enabled(enable: bool) -> bool:
    """Add or remove the entry. Returns what the state is afterwards.

    Never raises. This runs from a menu click on a machine with no console, so
    a registry that refuses has to come back as a tick that did not appear.
    """
    wanted = command()
    if wanted is None:
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
            if enable:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, wanted)
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass        # already gone is the state that was asked for
    except (ImportError, OSError):
        logger.warning("could not change the start-with-Windows setting",
                       exc_info=True)
        return enabled()
    logger.info("start with Windows: %s", "on" if enable else "off")
    return enable
