"""Shortcuts that work while something else has the keyboard.

An overlay is watched, not used. Whoever is reading it has Spotify or a browser
in front, so a binding that needs the overlay focused is a binding nobody can
reach — and focusing it means clicking it, which seeks to whatever line was
under the pointer. A shortcut here has to be global or it is decoration.

On Windows that is `RegisterHotKey`, with one detail that decides the whole
shape of this module: registering with a null window sends `WM_HOTKEY` to the
*thread's* message queue rather than to a window, and Tk's own loop pulls those
messages and drops the ones it cannot dispatch. Measured — registration
succeeds, the key fires, and nothing ever arrives. So the registration lives on
a thread of its own with its own `GetMessage` loop, and the presses reach the
renderer through a queue it drains on a tick it was already running.

Elsewhere there is no equivalent that does not want accessibility permissions,
so this reports itself unavailable and the caller falls back to bindings that
need the focus.
"""
import logging
import queue
import sys
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
# Without this, holding the combination repeats it at the keyboard's own rate,
# which for a size step means crossing the whole range on one long press.
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_OEM_PLUS = 0xBB
VK_OEM_MINUS = 0xBD
VK_ADD = 0x6B
VK_SUBTRACT = 0x6D
VK_0 = 0x30
VK_NUMPAD0 = 0x60
VK_K = 0x4B
VK_Q = 0x51


@dataclass(frozen=True)
class Hotkey:
    action: str
    modifiers: int
    key: int
    label: str


# Control+Alt rather than Control alone. These are claimed system-wide, so a
# plain Control+= would take zoom away from every application on the machine
# for as long as the overlay is running — a shortcut that works everywhere has
# to be one nothing else wants.
BINDINGS = (
    # K rather than the obvious L. Measured on the development machine: of
    # twelve Ctrl+Alt+letter combinations, ten reach the dispatcher and L and M
    # do not — something installs a low-level keyboard hook and eats them before
    # Windows gets that far. `RegisterHotKey` still *succeeds* for those, which
    # is why the warning below cannot catch it and why the claimed shortcuts are
    # logged: a key that does nothing is otherwise indistinguishable from a bug.
    Hotkey("toggle", MOD_CONTROL | MOD_ALT, VK_K, "Ctrl+Alt+K"),
    Hotkey("quit", MOD_CONTROL | MOD_ALT, VK_Q, "Ctrl+Alt+Q"),
    Hotkey("bigger", MOD_CONTROL | MOD_ALT, VK_OEM_PLUS, "Ctrl+Alt++"),
    Hotkey("bigger", MOD_CONTROL | MOD_ALT, VK_ADD, "Ctrl+Alt+Num+"),
    Hotkey("smaller", MOD_CONTROL | MOD_ALT, VK_OEM_MINUS, "Ctrl+Alt+-"),
    Hotkey("smaller", MOD_CONTROL | MOD_ALT, VK_SUBTRACT, "Ctrl+Alt+Num-"),
    Hotkey("reset", MOD_CONTROL | MOD_ALT, VK_0, "Ctrl+Alt+0"),
    Hotkey("reset", MOD_CONTROL | MOD_ALT, VK_NUMPAD0, "Ctrl+Alt+Num0"),
)

# `quit` exists because `toggle` does. A hidden overlay has no window to right
# click and no `Esc` to reach, so without a shortcut of its own the only way to
# close one would be the task manager. When a tray icon lands this becomes a
# convenience rather than the only exit.



class NullListener:
    """What every platform without a global shortcut API gets."""

    available = False

    def start(self) -> None:
        pass

    def poll(self) -> list[str]:
        return []

    def stop(self) -> None:
        pass


class WindowsListener:
    """Global shortcuts, delivered through a queue the renderer drains."""

    available = True

    def __init__(self, bindings=BINDINGS):
        self.bindings = tuple(bindings)
        self._events: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="hotkeys",
                                        daemon=True)
        self._thread.start()
        # Waited on so a registration failure is reported at startup rather than
        # the first time someone presses the key and nothing happens.
        self._ready.wait(timeout=2.0)

    def poll(self) -> list[str]:
        """Every action fired since the last call, oldest first."""
        actions = []
        while True:
            try:
                actions.append(self._events.get_nowait())
            except queue.Empty:
                return actions

    def stop(self) -> None:
        import ctypes
        if self._thread_id is None:
            return
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread_id = None

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        registered = {}
        for index, binding in enumerate(self.bindings, start=1):
            if user32.RegisterHotKey(None, index,
                                     binding.modifiers | MOD_NOREPEAT,
                                     binding.key):
                registered[index] = binding.action
            else:
                # Almost always another application holding the combination.
                # Worth a line: the alternative is a key that silently does
                # nothing and no way to tell that from a bug in the resize.
                logger.warning("could not claim %s; another application has it",
                               binding.label)
        self._ready.set()
        if not registered:
            return
        # Logged because succeeding here is not the same as the key working:
        # anything with a low-level keyboard hook can swallow a combination
        # before Windows dispatches it, and registration still reports success.
        # This at least says what the overlay believes it holds.
        logger.info("shortcuts: %s", ", ".join(
            sorted({b.label for b in self.bindings
                    if b.action in registered.values()})))

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                action = registered.get(int(message.wParam))
                if action:
                    self._events.put(action)
        for index in registered:
            user32.UnregisterHotKey(None, index)


def create_listener() -> NullListener | WindowsListener:
    return WindowsListener() if sys.platform == "win32" else NullListener()
