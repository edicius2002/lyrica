"""A notification-area icon, so a hidden overlay is still somewhere.

Hiding the overlay made it possible to have it running and invisible, and the
only ways back were shortcuts — which the hotkey module has already had to admit
can be swallowed by anything holding a low-level keyboard hook, silently and
per-machine. An icon is the answer that cannot be intercepted.

Shell_NotifyIcon needs a window to send its callbacks to, so this creates a
message-only one and runs its own loop on its own thread, the same shape the
hotkey listener uses. Presses arrive as the same action names, so the renderer
drains both through one path and does not care which produced them.

Written against the API directly rather than through a tray library: the rest of
the Windows integration here is already ctypes, and a dependency would also have
to be proved to survive PyInstaller, which the executable has just been
verified without.
"""
import ctypes
import logging
import queue
import sys
import threading

logger = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_QUIT = 0x0012
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
# Anything from WM_APP up is ours to define; this is the one the icon reports
# mouse activity on.
WM_TRAY = 0x0400 + 1

NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
MF_GRAYED = 0x0001
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

CW_USEDEFAULT = 0x80000000
HWND_MESSAGE = -3

# Everything below needs the Win32 type machinery, and `ctypes.wintypes` and
# `ctypes.WINFUNCTYPE` do not exist off Windows — importing them at module level
# breaks the import everywhere else, which is what CI caught. The constants and
# the menu above are plain values and stay unguarded so they can be tested
# anywhere.
if sys.platform == "win32":
    from ctypes import wintypes

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, ctypes.c_uint,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", ctypes.c_uint),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class NOTIFYICONDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", ctypes.c_uint),
            ("uFlags", ctypes.c_uint),
            ("uCallbackMessage", ctypes.c_uint),
            ("hIcon", wintypes.HICON),
            ("szTip", ctypes.c_wchar * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", ctypes.c_wchar * 256),
            ("uVersion", ctypes.c_uint),
            ("szInfoTitle", ctypes.c_wchar * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]


# The menu, in order. `None` is a separator. The identifiers start at 1 because
# TrackPopupMenu returns 0 for "nothing was chosen".
MENU = (
    (1, "Show / hide", "toggle"),
    None,
    (2, "Bigger", "bigger"),
    (3, "Smaller", "smaller"),
    (4, "Default size", "reset"),
    None,
    (5, "Start with Windows", "autostart"),
    None,
    (6, "Quit", "quit"),
)


def _icon_file() -> str | None:
    """A small icon drawn on the spot, written where Windows can load it.

    Drawn from primitives rather than a glyph: a font that happens to carry a
    musical note is not something to depend on, and the shape is two ellipses
    and two bars.
    """
    try:
        from PIL import Image, ImageDraw

        from lyrica import config
    except ImportError:
        return None
    try:
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=14,
                               fill=(24, 26, 34, 255))
        ink = (232, 236, 245, 255)
        # Two note heads, their stems, and the beam across the top.
        draw.ellipse((14, 38, 28, 50), fill=ink)
        draw.ellipse((36, 32, 50, 44), fill=ink)
        draw.rectangle((26, 16, 29, 44), fill=ink)
        draw.rectangle((48, 12, 51, 38), fill=ink)
        draw.polygon([(26, 16), (51, 12), (51, 20), (26, 24)], fill=ink)

        path = config.cache_root() / "tray.ico"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="ICO", sizes=[(16, 16), (32, 32), (64, 64)])
        return str(path)
    except Exception:
        logger.debug("could not draw a tray icon", exc_info=True)
        return None


class NullTray:
    """What a platform without a notification area gets."""

    available = False

    def start(self) -> None:
        pass

    def poll(self) -> list[str]:
        return []

    def set_autostart(self, enabled: bool) -> None:
        pass

    def stop(self) -> None:
        pass


class WindowsTray:
    def __init__(self, tooltip: str = "Lyrica", autostart: bool = False,
                 can_autostart: bool = True):
        self.tooltip = tooltip
        self._autostart = autostart
        self._can_autostart = can_autostart
        self._events: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hwnd: int | None = None
        self._ready = threading.Event()
        self._proc = None       # kept alive: a collected WNDPROC crashes Windows
        self.available = False

    # --- what the renderer talks to ---
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def poll(self) -> list[str]:
        actions = []
        while True:
            try:
                actions.append(self._events.get_nowait())
            except queue.Empty:
                return actions

    def set_autostart(self, enabled: bool) -> None:
        """Remembered so the menu can show a tick beside it."""
        self._autostart = enabled

    def stop(self) -> None:
        """Ask the icon to go, and wait long enough for it to have gone.

        Posting the quit and returning was a race the icon lost often enough to
        matter. The thread that owns it is a daemon, so the interpreter is free
        to exit the moment this returns, and what is left undone is the
        `NIM_DELETE` — leaving an icon Windows still draws until something makes
        it ask the owning window whether it is there. Which is why the leftovers
        vanish as soon as the notification area is opened, and why several of
        them look like several copies of the app.
        """
        if self._thread_id is None:
            return
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread_id = None
        if self._thread is not None:
            # Bounded, because a tray thread that will not come back is not
            # worth holding a quit open for. A removal measures well under a
            # millisecond; this is only for the case where it never happens.
            self._thread.join(timeout=1.0)

    # --- the thread that owns the icon ---
    def _run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            self._create(user32)
            self.available = True
            # Logged because the icon is easy to believe missing: Windows files
            # new ones under the overflow chevron rather than showing them, so
            # "I cannot see it" and "it was never added" look identical.
            logger.info("tray icon added (Windows hides new ones under the "
                        "notification-area chevron until you drag them out)")
        except Exception:
            logger.warning("could not create the tray icon", exc_info=True)
            self._ready.set()
            return
        self._ready.set()

        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            # In a `finally` because the icon outliving the process is the whole
            # failure: an ending that skips this leaves one in the notification
            # area that looks live until it is looked at.
            self._remove(user32)

    def _create(self, user32) -> None:
        instance = ctypes.windll.kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._on_message)

        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.hInstance = instance
        cls.lpszClassName = "LyricaTray"
        if not user32.RegisterClassW(ctypes.byref(cls)):
            # 1410 is "class already registered", which happens on a restart
            # inside one process and is not a failure.
            if ctypes.get_last_error() not in (0, 1410):
                raise ctypes.WinError(ctypes.get_last_error())

        # Message-only: it has no pixels and never appears anywhere. It exists
        # because Shell_NotifyIcon has to have somewhere to send clicks.
        self._hwnd = user32.CreateWindowExW(
            0, "LyricaTray", "Lyrica", 0, 0, 0, 0, 0,
            wintypes.HWND(HWND_MESSAGE), None, instance, None)
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        path = _icon_file()
        hicon = 0
        if path:
            hicon = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                      LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            hicon = user32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))

        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = hicon
        data.szTip = self.tooltip
        self._data = data
        if not ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _remove(self, user32) -> None:
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE,
                                                    ctypes.byref(self._data))
            if self._hwnd:
                user32.DestroyWindow(wintypes.HWND(self._hwnd))
        except Exception:
            logger.debug("could not remove the tray icon", exc_info=True)

    def _on_message(self, hwnd, message, wparam, lparam):
        user32 = ctypes.windll.user32
        if message == WM_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP:
                self._events.put("toggle")
            elif event == WM_RBUTTONUP:
                self._show_menu(user32, hwnd)
            return 0
        if message == WM_COMMAND:
            chosen = wparam & 0xFFFF
            for entry in MENU:
                if entry and entry[0] == chosen:
                    self._events.put(entry[2])
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(wintypes.HWND(hwnd), message,
                                     wintypes.WPARAM(wparam),
                                     wintypes.LPARAM(lparam))

    def _show_menu(self, user32, hwnd) -> None:
        menu = user32.CreatePopupMenu()
        for entry in MENU:
            if entry is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            ident, label, action = entry
            flags = MF_STRING
            if action == "autostart":
                if self._autostart:
                    flags |= MF_CHECKED
                if not self._can_autostart:
                    # Greyed rather than hidden: run from a source checkout this
                    # cannot work, and an item that quietly does nothing is
                    # worse than one that visibly will not.
                    flags |= MF_GRAYED
            user32.AppendMenuW(menu, flags, ident, label)

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Required, and the reason is unobvious: without it the menu does not
        # dismiss when you click elsewhere, because the owning window is not in
        # the foreground and never gets told the click happened.
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        chosen = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0,
            wintypes.HWND(hwnd), None)
        user32.DestroyMenu(menu)
        if chosen:
            user32.PostMessageW(wintypes.HWND(hwnd), WM_COMMAND, chosen, 0)


def create_tray(tooltip: str = "Lyrica", autostart: bool = False,
                can_autostart: bool = True):
    if sys.platform != "win32":
        return NullTray()
    return WindowsTray(tooltip, autostart, can_autostart)
