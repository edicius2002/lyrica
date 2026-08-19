"""A second window, made of a bitmap, so the panel's light can leave the panel.

The border was rejected ten times for looking plastic, and none of the ten
attempts was working on the fault. The overlay is clipped by `SetWindowRgn` to
a one-bit rounded rectangle, so *every* pixel of light stops dead at the
window's edge — and real light has no edge. `halo.py` used to carry a whole
term, `Shape.edge`, whose only job was to extinguish the outward half of the
falloff before it reached that cliff, because a hard cut across a glow reads
worse than no glow at all. The light was being killed to hide the boundary.

Tk offers `-alpha`, which is one number for the whole window, and
`-transparentcolor`, which is one bit per pixel. Neither is per-pixel alpha, so
the spill cannot come from Tk at all. `WS_EX_LAYERED` with
`UpdateLayeredWindow` is per-pixel alpha in user32, with no new dependency: you
hand the system a 32-bit premultiplied bitmap and it composites it over
whatever is behind. It cannot be used *on* the Tk window — that window's
contents are Tk's, and `UpdateLayeredWindow` replaces them — so this is a
companion window, larger than the panel by the spill on every side, sitting
directly beneath it and carrying only the half of the light that falls outside.

Everything about it is arranged so it cannot interfere with the panel:

  * `WS_EX_TRANSPARENT | WS_EX_NOACTIVATE` — it takes no click and no focus.
    Measured in `research/viability/probe_layered_glow.py`: `WindowFromPoint`
    over it answers the window beneath, not this one.
  * `WS_EX_TOOLWINDOW` — no taskbar button for a window with no content of its
    own.
  * `WS_EX_TOPMOST`, then `SetWindowPos(glow, panel)` — the same band as the
    overlay, immediately under it. Measured with an owner and without: the
    panel stays on top either way. Even if it did not, the bitmap is punched
    through wherever the panel's own footprint is, so there is nothing there to
    cover a word with.
  * `DefWindowProc` is the whole window procedure. The system keeps the bitmap
    and composites it, so there is no `WM_PAINT` worth answering.

**Acrylic must never be applied to it.** `SetWindowCompositionAttribute`
returns success on a layered window — measured — and then DWM paints the accent
plate over the whole rectangle and ignores the per-pixel alpha entirely. It is
the same trap `chrome/windows.py` documents for the rounded corners, arriving
by a different road. The companion is simply never given the accent, and it has
nothing to sample anyway: the panel's footprint is a hole in it.

The bitmap is one `CreateDIBSection` that is written in place and re-presented,
never reallocated per frame. Allocating a GDI object a frame is how a process
runs out of handles, which `bloom.py` documents the consequences of — a
`Tcl_Panic` with nothing to catch it. `reserve` only ever *grows* the surface,
which is what makes a collapse animation free: the panel folds from its full
size down to the card and back, so the largest size is reached on the first
frame and nothing is allocated for the rest of the run. See decision 9.7.

Measured on this machine (probe `bench`): handing the bitmap over is 0.12 ms
median and 0.44 ms worst whatever is in it, and a whole frame — one strip of
the ring relit through a lookup table, written into the surface, presented —
is 1.66 ms median and 3.03 ms worst.
"""
import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)

# `WinDLL` and `WINFUNCTYPE` exist only on Windows, and reaching for either at
# import time is what stops the module being importable anywhere else. Nothing
# here is ever *called* off Windows — there is no companion window to drive —
# but `test_every_module_imports_on_any_platform` imports every module there
# is, on purpose: a package that cannot be imported cannot be inspected, and
# the failure lands on whoever next runs the suite on a Mac rather than on
# whoever wrote it. The structures below are plain `ctypes`, and `wintypes` is
# portable, so only these four names need the guard.
if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                 wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM)
else:
    user32 = gdi32 = kernel32 = None
    WNDPROC = ctypes.c_void_p

LRESULT = ctypes.c_ssize_t

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000

CLASS_NAME = "LyricaGlow"


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


def _declare() -> None:
    """Argument types for everything called here.

    Spelled out rather than left to ctypes' defaults, and not for tidiness: a
    handle is a pointer and `ctypes` passes a bare Python int as a C `int`, so
    on 64-bit Windows the top half of an `HWND` is silently dropped and the
    call fails against a handle that never existed.
    """
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
        ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


_declare()

# Windows calls the window procedure long after the call that installed it has
# returned, so the `WNDPROC` and the `WNDCLASS` naming it have to outlive this
# function. A local would be collected and the next message would land on freed
# memory.
_kept: list = []
_registered = False


def _register() -> str:
    global _registered
    if _registered:
        return CLASS_NAME
    proc = WNDPROC(lambda h, m, w, p: user32.DefWindowProcW(h, m, w, p))
    made = WNDCLASS()
    made.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    made.hInstance = kernel32.GetModuleHandleW(None)
    made.lpszClassName = CLASS_NAME
    _kept.extend((proc, made))
    if not user32.RegisterClassW(ctypes.byref(made)):
        raise ctypes.WinError(ctypes.get_last_error())
    _registered = True
    return CLASS_NAME


class Layered:
    """A click-through window whose pixels are a surface you write into.

    `reserve` sizes the surface, `frame()` hands out a view of it to draw on,
    and `present` gives it to the compositor at a position and a size. The
    surface is BGRA with **premultiplied** alpha, top-down, which is what
    `UpdateLayeredWindow` demands and what `halo.Spill` writes.

    The presented size may be smaller than the surface, and that is the point
    of separating the two: a collapse changes the panel's size on every frame,
    and a surface that had to be reallocated to match would be sixty
    `CreateDIBSection` calls a second. See `reserve`.
    """

    def __init__(self, width: int, height: int, at: tuple[int, int] = (0, 0),
                 owner: int | None = None):
        style = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
                 | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
        self.hwnd = user32.CreateWindowExW(
            style, _register(), "Lyrica glow", WS_POPUP,
            at[0], at[1], max(1, width), max(1, height),
            wintypes.HWND(owner) if owner else None, None,
            wintypes.HINSTANCE(kernel32.GetModuleHandleW(None)), None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.at = at
        self.size = (0, 0)
        self.capacity = (0, 0)
        self.dc = gdi32.CreateCompatibleDC(None)
        self.bitmap = None
        self.bits = ctypes.c_void_p()
        self._old = None
        self._view = None
        self._shown = False
        self.reserve(width, height)
        user32.ShowWindow(wintypes.HWND(self.hwnd), SW_SHOWNOACTIVATE)
        self._shown = True

    # --- the surface ---

    def reserve(self, width: int, height: int) -> bool:
        """Make sure the surface is at least this big. True if it was rebuilt.

        **It only ever grows.** A collapse folds the panel from its full size
        to the card and back over twenty-one frames, and every one of those
        frames asks for a different size; rebuilding the bitmap for each would
        be a GDI allocation and free per frame, on the path `bloom.py` already
        warns about. Growing only means the full size is reached once and never
        paid for again, and the cost of the slack is address space nobody
        touches — `present` is given the size that is actually wanted, and
        reads the top-left corner of the surface.
        """
        width, height = max(1, int(width)), max(1, int(height))
        have_w, have_h = self.capacity
        if width <= have_w and height <= have_h:
            return False
        width, height = max(width, have_w), max(height, have_h)

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        # Negative, so the rows run top-down like every image in this process.
        # A bottom-up DIB would mean flipping every strip before writing it.
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            self.dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        old = gdi32.SelectObject(self.dc, bitmap)
        if self.bitmap:
            gdi32.DeleteObject(self.bitmap)
        else:
            self._old = old
        self.bitmap, self.bits = bitmap, bits
        self.capacity = (width, height)
        self._view = None
        return True

    def frame(self):
        """The surface as a `(height, width, 4)` array of bytes, BGRA.

        Aliased onto the DIB's own memory rather than copied into it: the
        alternative is composing somewhere else and then `memmove`ing the whole
        bitmap, which was measured at 0.10 ms for a 1.8 MiB surface — small,
        but paid on a frame that only wanted to change one strip of the edge.
        """
        if self._view is None:
            import numpy as np

            width, height = self.capacity
            buffer = ctypes.cast(self.bits,
                                 ctypes.POINTER(ctypes.c_uint8 * (height * width * 4)))
            self._view = np.frombuffer(buffer.contents, dtype=np.uint8).reshape(
                height, width, 4)
        return self._view

    # --- presenting ---

    def present(self, width: int, height: int,
                at: tuple[int, int] | None = None) -> bool:
        """Show the top-left `width` x `height` of the surface, at `at`.

        One call moves the window, sizes it and replaces its contents, which is
        why a drag can afford to do it: there is no separate `SetWindowPos` to
        fall out of step with.
        """
        if at is not None:
            self.at = at
        self.size = (max(1, int(width)), max(1, int(height)))
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        dst = wintypes.POINT(int(self.at[0]), int(self.at[1]))
        src = wintypes.POINT(0, 0)
        size = wintypes.SIZE(self.size[0], self.size[1])
        ok = user32.UpdateLayeredWindow(
            wintypes.HWND(self.hwnd), None, ctypes.byref(dst),
            ctypes.byref(size), self.dc, ctypes.byref(src), 0,
            ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            logger.debug("UpdateLayeredWindow refused (error %d)",
                         ctypes.get_last_error())
        return bool(ok)

    def move(self, x: int, y: int) -> bool:
        """Put the window somewhere without touching its contents.

        What a drag uses. `present` would do the same thing and also hand over
        a bitmap that has not changed; this is the cheaper half of it, and
        asynchronous for the same reason `chrome.windows.move_window` is — a
        hand issues moves faster than the compositor finishes them, and waiting
        for each is what turns a stream into a queue.
        """
        self.at = (int(x), int(y))
        return bool(user32.SetWindowPos(
            wintypes.HWND(self.hwnd), None, int(x), int(y), 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS))

    def behind(self, hwnd: int) -> bool:
        """Sit directly under another window, disturbing neither.

        Re-asserted whenever the panel's own place in the z-order is, because
        mapping a window again puts it back as an ordinary one.
        """
        return bool(user32.SetWindowPos(
            wintypes.HWND(self.hwnd), wintypes.HWND(hwnd), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))

    def visible(self, shown: bool) -> None:
        """Follow the panel out of sight and back. Idempotent."""
        if shown == self._shown:
            return
        user32.ShowWindow(wintypes.HWND(self.hwnd),
                          SW_SHOWNOACTIVATE if shown else SW_HIDE)
        self._shown = shown

    def destroy(self) -> None:
        self._view = None
        if self.dc:
            if self._old:
                gdi32.SelectObject(self.dc, self._old)
            if self.bitmap:
                gdi32.DeleteObject(self.bitmap)
            gdi32.DeleteDC(self.dc)
        if self.hwnd:
            user32.DestroyWindow(wintypes.HWND(self.hwnd))
        self.dc = self.bitmap = self.hwnd = None
        self.capacity = (0, 0)


__all__ = ["Layered"]
