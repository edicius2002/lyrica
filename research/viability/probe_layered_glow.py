"""Can the panel's light fall on the desktop, in Tk, on Windows?

The border is clipped today by `SetWindowRgn`, which is a one-bit mask: the
glow stops dead at the panel's edge and the eye reads a sticker. Every design
that was rejected assumed the light spills forty pixels onto whatever is
behind. Tk on Windows offers `-alpha` (one number for the whole window) and
`-transparentcolor` (binary, by key colour) and nothing else, so the spill
cannot come from Tk.

`WS_EX_LAYERED` + `UpdateLayeredWindow` is per-pixel alpha in user32, no new
dependency. It replaces the window's contents with a bitmap you hand it, which
is why it does not simply drop into a Tk window. Two ways round that, and this
probe runs both:

    companion  the Tk panel stays exactly as it is — `-alpha`, `SetWindowRgn`,
               a canvas with words and the inward half of the light on it —
               and a larger click-through layered window behind it carries the
               outward half, which is the half that has nowhere to go today.
    full       one layered window carries panel and light together, composed
               in PIL. Tk draws nothing that is seen.

    compat     the questions that decide whether either is allowed: does
               `UpdateLayeredWindow` work on a window Tk has already made
               layered with `-alpha`; does the acrylic accent survive; does a
               region clip still apply; does it take clicks.
    glue       z-order against the panel, and whether the two windows stay
               together over a hundred moves.
    ab         both paths photographed over one identical backdrop and
               differenced, which is what says whether the choice is a visual
               one at all.
    bench      what a frame costs.

Nothing here imports from `lyrica` except `chrome.windows` in `compat`, and
nothing writes to `src`. Run with a second argument to save images.
"""
import ctypes
import sys
import time
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# ---------------------------------------------------------------- the look

PANEL = (900, 320)
RADIUS = 14
FACE = (38, 26, 18)
LIGHT_COLD = (127, 127, 121)
LIGHT_WARM = (255, 255, 249)

# How far the light is allowed to travel outside the panel. The whole point of
# the exercise: today this is zero, because the window ends at the panel.
SPILL = 40

# How far it carries inward, where there is panel to spread across.
REACH = 26

# The bitmap is the panel plus room for the spill on all four sides. Wider
# than `SPILL` on purpose: the wide field is a gaussian, so it is still worth
# something at two and three standard deviations, and the bitmap's own edge is
# a cliff exactly like the one this probe exists to remove. Cut the tail at
# 40 px and the spill acquires a straight edge in mid-air.
PAD = SPILL + 24
CANVAS = (PANEL[0] + 2 * PAD, PANEL[1] + 2 * PAD)

LUT_SIZE = 256
OFF_RING = 0


def _rounded(size, box, radius, fill=255, mode="L"):
    image = Image.new(mode, size, 0 if mode == "L" else (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(box, radius, fill=fill)
    return image


def _normalised(image, keep=1.0):
    peak = image.getextrema()[1]
    if not peak:
        return image
    gain = 255.0 * keep / peak
    return image.point([min(255, round(v * gain)) for v in range(256)])


def path(corner_points: int = 13, spacing: float = 7.0):
    """The rounded rectangle the light runs along, as a closed polyline.

    Points along the straights as well as round the corners, at `beam.py`'s
    own spacing. That is not decoration: the position round the ring is drawn
    as one thick line per segment carrying its own index, so a segment 870
    pixels long carries *one* index over its whole length and the gradient
    arrives in four blocks with straight seams between them. Measured that way
    first, and photographed — see the note in the report.
    """
    import math
    left, top = PAD, PAD
    right, bottom = PAD + PANEL[0] - 1, PAD + PANEL[1] - 1
    r = RADIUS
    points = []
    corners = ((right - r, top + r, -90), (right - r, bottom - r, 0),
               (left + r, bottom - r, 90), (left + r, top + r, 180))
    for index, (cx, cy, start) in enumerate(corners):
        for i in range(corner_points):
            a = math.radians(start + 90 * i / (corner_points - 1))
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        # ...then walk the straight to where the next corner begins.
        nx, ny, nstart = corners[(index + 1) % len(corners)]
        end = (nx + r * math.cos(math.radians(nstart)),
               ny + r * math.sin(math.radians(nstart)))
        here = points[-1]
        span = math.hypot(end[0] - here[0], end[1] - here[1])
        for i in range(1, max(1, int(span / spacing))):
            t = i / max(1, int(span / spacing))
            points.append((here[0] + t * (end[0] - here[0]),
                           here[1] + t * (end[1] - here[1])))
    return points


def _arc_lengths(points):
    """Cumulative distance round the closed path, normalised to 0..1.

    The index of a point is not its position round the ring — the corners hold
    nine points over twenty-two pixels and a straight holds one every sixteen —
    so the table has to be indexed by distance travelled, or the travelling
    head crawls round the corners and sprints down the sides.
    """
    import math
    run, total = [0.0], 0.0
    for i in range(len(points)):
        a, b = points[i], points[(i + 1) % len(points)]
        total += math.hypot(b[0] - a[0], b[1] - a[1])
        run.append(total)
    return [d / total for d in run[:-1]]


def fields():
    """`profile` (how much light reaches a pixel) and `where` (how far round).

    The same split `halo.py` uses, with one deliberate difference: nothing
    multiplies the field by an "inside the panel" mask. That mask exists in
    `halo.py` because the window boundary is a cliff and the outward tail has
    to be gone before it arrives — `Shape.edge` is the width over which it is
    taken away. Here there is no cliff, so the tail is kept, and that is the
    whole visual difference this probe exists to show.
    """
    points = path()
    ink = Image.new("L", CANVAS, 0)
    ImageDraw.Draw(ink).line([*points, points[0]], fill=255, width=3,
                             joint="curve")

    ridge = _normalised(ink.filter(ImageFilter.GaussianBlur(2.0)))
    near = _normalised(ink.filter(ImageFilter.GaussianBlur(SPILL / 4.0)), 0.58)
    far = _normalised(ink.filter(ImageFilter.GaussianBlur(SPILL / 2.2)), 0.36)
    profile = _normalised(ImageChops.screen(ImageChops.screen(ridge, near), far))

    # Position round the ring, as one byte, so a frame is a lookup table and a
    # rotation of the gradient is a rotation of that table. Drawn the way
    # `halo.py` draws it: a thick line per segment carrying its own index, and
    # a disc at each joint to fill the wedge two thick lines leave on the
    # outside of a turn.
    where = Image.new("L", CANVAS, OFF_RING)
    pen = ImageDraw.Draw(where)
    count = len(points)
    along = _arc_lengths(points)
    thick = 2 * PAD
    for index, start in enumerate(points):
        end = points[(index + 1) % count]
        fill = 1 + round((LUT_SIZE - 2) * along[index])
        pen.line([start, end], fill=fill, width=thick)
        pen.ellipse([start[0] - thick / 2, start[1] - thick / 2,
                     start[0] + thick / 2, start[1] + thick / 2], fill=fill)
    return profile, where


# How much of the ring the lit head covers, as a share of the perimeter, and
# how much light there is anywhere else. A head that covers everything is a
# ring that does not appear to travel at all, which is what the first pass of
# this probe photographed.
HEAD = 0.16
FLOOR = 0.20


def channels(lit: bool, turn: float = 0.0):
    """Four tables of 256 bytes: red, green, blue, opacity, by ring position.

    `lit` is the difference between the panel at rest and the panel answering
    the music: at rest the ring is the cold colour at a low, even opacity;
    lit, a warm head travels round it over a cold floor.
    """
    import math
    red, green, blue, opacity = [], [], [], []
    for key in range(LUT_SIZE):
        if key == OFF_RING:
            red.append(0), green.append(0), blue.append(0), opacity.append(0)
            continue
        phase = ((key - 1) / (LUT_SIZE - 2) - turn) % 1.0
        # Wrapped distance to the head, so the gradient joins itself where the
        # byte wraps from 255 back to 1 and there is no step there.
        away = min(phase, 1.0 - phase)
        heat = math.exp(-(away / HEAD) ** 2)
        if lit:
            heat = FLOOR + (1 - FLOOR) * heat
            level = 0.34 + 0.58 * heat
        else:
            heat = 0.12
            level = 0.30
        red.append(round(LIGHT_COLD[0] + heat * (LIGHT_WARM[0] - LIGHT_COLD[0])))
        green.append(round(LIGHT_COLD[1] + heat * (LIGHT_WARM[1] - LIGHT_COLD[1])))
        blue.append(round(LIGHT_COLD[2] + heat * (LIGHT_WARM[2] - LIGHT_COLD[2])))
        opacity.append(round(255 * level))
    return red, green, blue, opacity


def light(profile, where, table):
    """The glow alone, as straight RGBA. Nothing of the panel's face in it."""
    red, green, blue, opacity = table
    bands = [where.point(red), where.point(green), where.point(blue),
             ImageChops.multiply(profile, where.point(opacity))]
    return Image.merge("RGBA", bands)


def face():
    """The panel's own slab: rounded, opaque, in the canvas's coordinates."""
    box = (PAD, PAD, PAD + PANEL[0] - 1, PAD + PANEL[1] - 1)
    slab = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ImageDraw.Draw(slab).rounded_rectangle(box, RADIUS, fill=(*FACE, 255))
    return slab


def hollow(image):
    """The glow with the panel's own footprint punched out of it.

    What the companion window has to draw. Anything it paints under the Tk
    panel is invisible anyway — the panel is on top — but it is not free:
    `-alpha` makes the Tk panel translucent, so the companion *would* show
    through it, doubly lit and at the wrong brightness. So it is removed here
    rather than covered there.
    """
    box = (PAD, PAD, PAD + PANEL[0] - 1, PAD + PANEL[1] - 1)
    keep = Image.new("L", CANVAS, 255)
    ImageDraw.Draw(keep).rounded_rectangle(box, RADIUS, fill=0)
    out = image.copy()
    out.putalpha(ImageChops.multiply(image.getchannel("A"), keep))
    return out


def composed(profile, where, table):
    """Panel and light in one bitmap: what the `full` path would present."""
    glow = light(profile, where, table)
    plate = face()
    # The light is in front of the slab, so the ridge reads on the face too.
    return Image.alpha_composite(plate, glow)


# ------------------------------------------------------- the Win32 plumbing

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
GWL_EXSTYLE = -20
LWA_ALPHA = 0x00000002
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
BI_RGB = 0


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def _declare():
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClassW.restype = wintypes.ATOM
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
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
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
    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]


_declare()
_kept = []      # window procedures, which Windows calls after we stop looking


class Layered:
    """A top-level window whose pixels are a bitmap, with real per-pixel alpha.

    Click-through and never activated, so it cannot take the focus or a click
    from the panel it accompanies. It has no `WM_PAINT` worth answering:
    the system keeps the bitmap and composites it, so `DefWindowProc` is the
    whole window procedure.
    """

    _class = None

    def __init__(self, size, at=(0, 0), owner=None):
        if Layered._class is None:
            proc = WNDPROC(lambda h, m, w, p: user32.DefWindowProcW(h, m, w, p))
            _kept.append(proc)
            cls = WNDCLASS()
            cls.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
            cls.hInstance = kernel32.GetModuleHandleW(None)
            cls.lpszClassName = "LyricaGlowProbe"
            _kept.append(cls)
            if not user32.RegisterClassW(ctypes.byref(cls)):
                raise ctypes.WinError(ctypes.get_last_error())
            Layered._class = cls.lpszClassName

        self.size = size
        self.at = at
        style = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
                 | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
        self.hwnd = user32.CreateWindowExW(
            style, Layered._class, "glow", WS_POPUP,
            at[0], at[1], size[0], size[1],
            wintypes.HWND(owner) if owner else None, None,
            wintypes.HINSTANCE(kernel32.GetModuleHandleW(None)), None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        # One DIB section, reused. Allocating a bitmap a frame is how a
        # process runs out of GDI handles, which `bloom.py` already documents
        # the consequences of.
        self.dc = gdi32.CreateCompatibleDC(None)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = size[0]
        info.bmiHeader.biHeight = -size[1]      # top-down, like PIL's rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        self.bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(
            self.dc, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0)
        if not self.bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        self.old = gdi32.SelectObject(self.dc, self.bitmap)
        self.nbytes = size[0] * size[1] * 4
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)

    def push(self, image, at=None):
        """Hand Windows a new RGBA bitmap. This is the per-frame cost."""
        if at is not None:
            self.at = at
        # `UpdateLayeredWindow` wants premultiplied alpha, and BGRA byte
        # order. PIL will do the byte order; the premultiply is ours.
        alpha = image.getchannel("A")
        premultiplied = Image.merge("RGBA", [
            ImageChops.multiply(image.getchannel(band), alpha)
            for band in "RGB"] + [alpha])
        raw = premultiplied.tobytes("raw", "BGRA")
        ctypes.memmove(self.bits, raw, min(len(raw), self.nbytes))
        return self._present()

    def _present(self):
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        dst = wintypes.POINT(self.at[0], self.at[1])
        src = wintypes.POINT(0, 0)
        size = wintypes.SIZE(self.size[0], self.size[1])
        ok = user32.UpdateLayeredWindow(
            self.hwnd, None, ctypes.byref(dst), ctypes.byref(size),
            self.dc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return ok

    def behind(self, hwnd):
        """Sit directly under another window, without disturbing either."""
        user32.SetWindowPos(wintypes.HWND(self.hwnd), wintypes.HWND(hwnd),
                            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def destroy(self):
        gdi32.SelectObject(self.dc, self.old)
        gdi32.DeleteObject(self.bitmap)
        gdi32.DeleteDC(self.dc)
        user32.DestroyWindow(wintypes.HWND(self.hwnd))


def hwnd_of(root):
    """The real top-level handle for a Tk window (Tk wraps its toplevels)."""
    if not root.winfo_ismapped():
        root.update_idletasks()
    hwnd = int(root.winfo_id())
    parent = user32.GetParent(wintypes.HWND(hwnd))
    return int(parent) if parent else hwnd


# ------------------------------------------------------------- where it goes

def spot():
    """Somewhere with texture behind it, on the primary screen."""
    return (150, 180)


# ------------------------------------------------------------------ the runs

def run_full(seconds=4.0, at=None, shots=None):
    """One layered window carrying panel and light together."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    profile, where = fields()
    at = at or spot()
    window = Layered(CANVAS, at)
    window.push(composed(profile, where, channels(False)))
    if shots:
        _photograph(at, shots + "-inactivo.png")
    window.push(composed(profile, where, channels(True, 0.12)))
    if shots:
        _photograph(at, shots + "-activo.png")
        window.destroy()
        return
    end = time.perf_counter() + seconds
    turn = 0.0
    while time.perf_counter() < end:
        turn = (turn + 0.01) % 1.0
        window.push(composed(profile, where, channels(True, turn)))
        _pump()
    window.destroy()


def run_companion(seconds=4.0, at=None, shots=None):
    """The chosen path, whole: Tk keeps the panel, a layered window the spill.

    Arranged exactly as the app would have to arrange it. The Tk window is
    what it is today — `-alpha`, `SetWindowRgn`, a canvas with words on it —
    and it draws the *inward* half of the light on that canvas, which is what
    `halo.py` already does. The companion behind it is larger by the spill and
    carries the outward half, which is the half that has nowhere to go today.
    """
    import tkinter as tk

    from PIL import ImageTk

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass

    at = at or spot()
    panel_at = (at[0] + PAD, at[1] + PAD)
    faced = "#{:02x}{:02x}{:02x}".format(*FACE)

    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{PANEL[0]}x{PANEL[1]}+{panel_at[0]}+{panel_at[1]}")
    root.configure(bg=faced)
    root.attributes("-topmost", True)
    root.attributes("-alpha", PANEL_ALPHA)
    canvas = tk.Canvas(root, width=PANEL[0], height=PANEL[1], bg=faced,
                       highlightthickness=0)
    canvas.pack()
    # The ring goes on the canvas before the words, so it can never cover one
    # — the ordering `halo.py`'s docstring insists on. The companion window
    # cannot join that ordering, which is exactly why it carries only the
    # half that falls outside the panel, where there are no words.
    ring = canvas.create_image(0, 0, anchor="nw")
    canvas.create_text(PANEL[0] // 2, PANEL[1] // 2 - 26,
                       text="the light leaves the panel", fill="#f6efe4",
                       font=("Segoe UI Semilight", 40))
    canvas.create_text(PANEL[0] // 2, PANEL[1] // 2 + 34,
                       text="UpdateLayeredWindow  ·  per-pixel alpha  ·  Tk intact",
                       fill="#b9a58f", font=("Segoe UI", 17))
    root.update_idletasks()
    root.update()

    hwnd = hwnd_of(root)
    rgn = gdi32.CreateRoundRectRgn(0, 0, PANEL[0] + 1, PANEL[1] + 1,
                                   RADIUS * 2, RADIUS * 2)
    user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)

    profile, where = fields()
    glow = Layered(CANVAS, at, owner=hwnd)
    held = {}

    def lit(table):
        whole = light(profile, where, table)
        inward, outward = _halves(whole)
        held["photo"] = ImageTk.PhotoImage(
            inward.crop((PAD, PAD, PAD + PANEL[0], PAD + PANEL[1])),
            master=canvas)
        canvas.itemconfigure(ring, image=held["photo"])
        glow.push(outward)
        glow.behind(hwnd)
        root.update()

    if shots:
        lit(channels(False))
        _photograph(at, shots + "-inactivo.png", root)
        lit(channels(True, 0.30))
        _photograph(at, shots + "-activo.png", root)
        glow.destroy()
        root.destroy()
        return

    state = {"turn": 0.0}

    def frame():
        state["turn"] = (state["turn"] + 0.008) % 1.0
        lit(channels(True, state["turn"]))
        root.after(16, frame)

    root.after(16, frame)
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()
    glow.destroy()
    root.destroy()


PANEL_ALPHA = 0.92     # what the Tk window wears, as `config.opacity()` does


def _halves(image):
    """The light split at the panel's own boundary: (inward, outward).

    Which is the split path (a) is forced into. The Tk window ends at the
    rounded rectangle, so everything inside it is Tk's to draw and everything
    outside is the companion's, and neither can draw a pixel on the other's
    side.
    """
    box = (PAD, PAD, PAD + PANEL[0] - 1, PAD + PANEL[1] - 1)
    inside = Image.new("L", CANVAS, 0)
    ImageDraw.Draw(inside).rounded_rectangle(box, RADIUS, fill=255)
    alpha = image.getchannel("A")
    inward, outward = image.copy(), image.copy()
    inward.putalpha(ImageChops.multiply(alpha, inside))
    outward.putalpha(ImageChops.multiply(
        alpha, ImageChops.invert(inside)))
    return inward, outward


def run_glue():
    """The two things that decide whether path (a) can be held together.

    **Z-order.** Windows keeps an owned window above its owner, always. A
    companion that carries the spill has to be *below* the panel, so if the
    ownership rule bites, the glow covers the lyrics and the path is dead.
    Both ownerships are tried, and the answer is read off the screen rather
    than off the documentation.

    **Glue.** A drag issues moves as fast as the hand produces them and the
    overlay already moves itself with `SetWindowPos(SWP_ASYNCWINDOWPOS)`.
    Two windows moved by two calls can be photographed mid-flight one frame
    apart, which is a glow that visibly slides off its panel. This moves the
    pair a hundred times and measures how far apart they ever got.
    """
    import tkinter as tk
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    from PIL import ImageGrab

    at = (200, 200)
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{PANEL[0]}x{PANEL[1]}+{at[0] + PAD}+{at[1] + PAD}")
    root.attributes("-topmost", True)
    root.attributes("-alpha", PANEL_ALPHA)
    tk.Canvas(root, width=PANEL[0], height=PANEL[1], bg="#101010",
              highlightthickness=0).pack()
    root.update_idletasks()
    root.update()
    hwnd = hwnd_of(root)

    magenta = Image.new("RGBA", CANVAS, (255, 0, 255, 255))
    centre = (at[0] + CANVAS[0] // 2, at[1] + CANVAS[1] // 2)

    def who_wins(label, owner):
        glow = Layered(CANVAS, at, owner=owner)
        glow.push(magenta)
        glow.behind(hwnd)
        for _ in range(8):
            root.update()
            _pump()
            time.sleep(0.05)
        pixel = ImageGrab.grab(
            (centre[0], centre[1], centre[0] + 1, centre[1] + 1),
            all_screens=True).getpixel((0, 0))
        under = pixel[0] < 120 and pixel[2] < 120
        print(f"z-order, {label}: panel on top = {under}  (centre pixel {pixel})")
        glow.destroy()
        return under

    ok_owned = who_wins("companion owned by the panel", hwnd)
    ok_free = who_wins("companion unowned", None)

    # --- glue under a drag.
    glow = Layered(CANVAS, at, owner=None)
    profile, where = fields()
    _, outward = _halves(light(profile, where, channels(True, 0.0)))
    glow.push(outward)
    glow.behind(hwnd)
    root.update()

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    worst, lags = 0, []
    for i in range(100):
        x, y = at[0] + (i % 40) * 6, at[1] + (i % 17) * 4
        start = time.perf_counter()
        # The panel first, the glow second, both asynchronous — the order the
        # app's own `move_window` would use.
        user32.SetWindowPos(wintypes.HWND(hwnd), None, x + PAD, y + PAD, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE | 0x0004 | 0x4000)
        glow.at = (x, y)
        glow._present()
        lags.append((time.perf_counter() - start) * 1000)
        _pump()
        a, b = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(a))
        user32.GetWindowRect(wintypes.HWND(glow.hwnd), ctypes.byref(b))
        worst = max(worst, abs((a.left - PAD) - b.left),
                    abs((a.top - PAD) - b.top))
    lags.sort()
    print(f"glue: worst misalignment over 100 moves = {worst} px")
    print(f"      moving both costs {lags[len(lags)//2]:.2f} ms median,"
          f" {lags[-1]:.2f} ms worst")
    glow.destroy()
    root.destroy()
    return ok_owned, ok_free


def run_ab(out):
    """Photograph both paths over one identical backdrop, and difference them.

    The question is not whether either draws light — both do. It is whether
    the split path (a) is forced into leaves anything the single bitmap (b)
    does not, once both wear the same panel alpha. Two things could:

        * the light inside the panel is attenuated by the window's own alpha
          under (a), because Tk applies it to everything the window contains,
          light included. Under (b) the light's opacity is its own.
        * the panel's rounded corner is a one-bit region clip under (a) and an
          antialiased edge under (b).

    A known backdrop of five greys sits behind both, so the comparison is of
    two photographs of the same thing rather than of two intentions.
    """
    import tkinter as tk

    from PIL import ImageTk
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass

    at = (120, 120)
    steps = [0, 64, 128, 192, 255]
    plate = Image.new("RGBA", CANVAS, (0, 0, 0, 255))
    band = CANVAS[0] // len(steps)
    for i, level in enumerate(steps):
        ImageDraw.Draw(plate).rectangle(
            [i * band, 0, (i + 1) * band, CANVAS[1]],
            fill=(level, level, level, 255))
    backdrop = Layered(CANVAS, at)
    backdrop.push(plate)

    profile, where = fields()
    glow = light(profile, where, channels(True, 0.0))
    inward, outward = _halves(glow)

    # --- (a) Tk panel, region-clipped, with the inward half on its canvas;
    #         a layered companion behind carrying the outward half.
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{PANEL[0]}x{PANEL[1]}+{at[0] + PAD}+{at[1] + PAD}")
    root.attributes("-topmost", True)
    root.attributes("-alpha", PANEL_ALPHA)
    canvas = tk.Canvas(root, width=PANEL[0], height=PANEL[1],
                       bg="#{:02x}{:02x}{:02x}".format(*FACE), highlightthickness=0)
    canvas.pack()
    inner = inward.crop((PAD, PAD, PAD + PANEL[0], PAD + PANEL[1]))
    photo = ImageTk.PhotoImage(inner, master=canvas)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    root.update_idletasks()
    root.update()
    hwnd = hwnd_of(root)
    rgn = gdi32.CreateRoundRectRgn(0, 0, PANEL[0] + 1, PANEL[1] + 1,
                                   RADIUS * 2, RADIUS * 2)
    user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
    outer = Layered(CANVAS, at, owner=hwnd)
    outer.push(outward)
    outer.behind(hwnd)
    root.update()
    shot_a = _shoot(at, root)
    root.destroy()
    outer.destroy()

    # --- (b) one bitmap: the same panel alpha, the same light, composed here.
    slab = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ImageDraw.Draw(slab).rounded_rectangle(
        (PAD, PAD, PAD + PANEL[0] - 1, PAD + PANEL[1] - 1), RADIUS,
        fill=(*FACE, round(255 * PANEL_ALPHA)))
    whole = Layered(CANVAS, at)
    whole.push(Image.alpha_composite(slab, glow))
    shot_b = _shoot(at)
    whole.destroy()
    backdrop.destroy()

    diff = ImageChops.difference(shot_a.convert("RGB"), shot_b.convert("RGB"))
    grey = diff.convert("L")
    hist = grey.histogram()
    total = sum(hist)
    mean = sum(i * n for i, n in enumerate(hist)) / total
    print(f"(a) split vs (b) one bitmap: mean |difference| {mean:.2f} levels,"
          f" worst {grey.getextrema()[1]}")
    over = sum(n for i, n in enumerate(hist) if i >= 8) / total
    print(f"pixels differing by 8 levels or more: {over * 100:.1f}%")

    if out:
        shot_a.save(out + "-a-split.png")
        shot_b.save(out + "-b-onebitmap.png")
        grey.point(lambda v: min(255, v * 6)).save(out + "-diff-x6.png")
        # The corner, at four times, is where a one-bit clip is visible or is
        # not. Both paths, side by side, on the brightest band.
        crop = (PANEL[0] + PAD - 70, PAD - 30, PANEL[0] + PAD + 30, PAD + 70)
        pair = Image.new("RGB", (2 * 100 * 4 + 24, 100 * 4), (20, 20, 20))
        for i, shot in enumerate((shot_a, shot_b)):
            zoom = shot.crop(crop).resize((400, 400), Image.NEAREST)
            pair.paste(zoom, (i * (400 + 24), 0))
        pair.save(out + "-corner-x4.png")
        print("wrote", out + "-a-split.png,", out + "-b-onebitmap.png,",
              out + "-diff-x6.png,", out + "-corner-x4.png")


def _shoot(at, root=None):
    from PIL import ImageGrab
    for _ in range(10):
        if root is not None:
            root.update()
        _pump()
        time.sleep(0.05)
    return ImageGrab.grab((at[0], at[1], at[0] + CANVAS[0], at[1] + CANVAS[1]),
                          all_screens=True)


def _pump():
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _photograph(at, path, root=None):
    from PIL import ImageGrab
    for _ in range(14):
        if root is not None:
            root.update()
        _pump()
        time.sleep(0.05)
    box = (at[0] - 10, at[1] - 10, at[0] + CANVAS[0] + 10, at[1] + CANVAS[1] + 10)
    ImageGrab.grab(box, all_screens=True).save(path)
    print("photographed", path)


# ------------------------------------------------------------ compatibility

def run_compat():
    """The four questions that decide what any of this costs to adopt."""
    import tkinter as tk
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry("400x200+200+200")
    root.configure(bg="#261a12")
    root.attributes("-alpha", 0.9)
    root.update_idletasks()
    root.update()
    hwnd = hwnd_of(root)

    style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    print(f"1. Tk -alpha sets WS_EX_LAYERED: {bool(style & WS_EX_LAYERED)}"
          f"  (exstyle 0x{style & 0xffffffff:08x})")

    # Does UpdateLayeredWindow work on a window Tk already made layered?
    dc = gdi32.CreateCompatibleDC(None)
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth, info.bmiHeader.biHeight = 400, -200
    info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(info), 0,
                                    ctypes.byref(bits), None, 0)
    gdi32.SelectObject(dc, bitmap)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    dst, src = wintypes.POINT(200, 200), wintypes.POINT(0, 0)
    size = wintypes.SIZE(400, 200)
    ctypes.set_last_error(0)
    ok = user32.UpdateLayeredWindow(wintypes.HWND(hwnd), None,
                                    ctypes.byref(dst), ctypes.byref(size), dc,
                                    ctypes.byref(src), 0, ctypes.byref(blend),
                                    ULW_ALPHA)
    print(f"2. UpdateLayeredWindow on a Tk -alpha window: {bool(ok)}"
          f"  (error {ctypes.get_last_error()})")

    # And after clearing and resetting the layered bit, as MSDN prescribes?
    user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE,
                          style & ~WS_EX_LAYERED)
    user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE,
                          style | WS_EX_LAYERED)
    ctypes.set_last_error(0)
    ok = user32.UpdateLayeredWindow(wintypes.HWND(hwnd), None,
                                    ctypes.byref(dst), ctypes.byref(size), dc,
                                    ctypes.byref(src), 0, ctypes.byref(blend),
                                    ULW_ALPHA)
    print(f"3. ...after clearing and resetting WS_EX_LAYERED: {bool(ok)}"
          f"  (error {ctypes.get_last_error()})")
    root.update()

    # Does the acrylic accent apply to a window we present ourselves?
    sys.path.insert(0, __file__.rsplit("research", 1)[0] + "src")
    from lyrica.chrome import windows as win
    glow = Layered((300, 200), (700, 200))
    glow.push(Image.new("RGBA", (300, 200), (40, 30, 20, 120)))
    frosted = win._set_accent(glow.hwnd, win.ACCENT_ENABLE_ACRYLICBLURBEHIND,
                              win._abgr(*win.TINT_RGBA))
    print(f"4. SetWindowCompositionAttribute on a layered window: {frosted}")
    _pump()
    time.sleep(0.6)

    # Does SetWindowRgn still clip one?
    rgn = gdi32.CreateRoundRectRgn(0, 0, 301, 201, 60, 60)
    clipped = user32.SetWindowRgn(ctypes.c_void_p(glow.hwnd),
                                 ctypes.c_void_p(rgn), True)
    print(f"5. SetWindowRgn on a layered window: {bool(clipped)}")
    _pump()
    time.sleep(0.4)

    # Does it take clicks?
    point = wintypes.POINT(750, 250)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    hit = user32.WindowFromPoint(point)
    print(f"6. WindowFromPoint over the layered window is it: "
          f"{int(hit or 0) == int(glow.hwnd)}  (WS_EX_TRANSPARENT is set)")

    glow.destroy()
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(dc)
    root.destroy()


def run_bench(rounds=60):
    """What a frame costs: composing the bitmap, and handing it over."""
    profile, where = fields()
    window = Layered(CANVAS, (-CANVAS[0] - 50, 0))     # off-screen
    table = channels(True, 0.0)

    def timed(label, work):
        work()
        times = []
        for i in range(rounds):
            start = time.perf_counter()
            work(i)
            times.append((time.perf_counter() - start) * 1000)
        times.sort()
        print(f"{label:<46} {times[len(times)//2]:6.2f} ms median"
              f"   {times[-1]:6.2f} ms worst")

    timed("lookup: `where` -> RGBA glow (PIL)",
          lambda i=0: light(profile, where, channels(True, i / 100)))
    timed("compose: glow over the panel slab",
          lambda i=0: composed(profile, where, channels(True, i / 100)))
    ready = composed(profile, where, table)
    timed("premultiply + BGRA bytes",
          lambda i=0: Image.merge("RGBA", [
              ImageChops.multiply(ready.getchannel(b), ready.getchannel("A"))
              for b in "RGB"] + [ready.getchannel("A")]).tobytes("raw", "BGRA"))
    raw = Image.merge("RGBA", [
        ImageChops.multiply(ready.getchannel(b), ready.getchannel("A"))
        for b in "RGB"] + [ready.getchannel("A")]).tobytes("raw", "BGRA")
    timed("memmove into the DIB",
          lambda i=0: ctypes.memmove(window.bits, raw, window.nbytes))
    timed("UpdateLayeredWindow (the handover alone)",
          lambda i=0: window._present())
    timed("everything: compose, premultiply, push",
          lambda i=0: window.push(composed(profile, where, channels(True, i / 100))))
    print(f"bitmap: {CANVAS[0]}x{CANVAS[1]} = {window.nbytes / 1024:.0f} KiB a frame")

    # --- and now the way it would actually be done.
    #
    # Two things above are avoidable. The premultiply is a separate pass over
    # every pixel, and it need not be: the opacity a pixel gets is
    # `profile * opacity(where)`, so its premultiplied red is
    # `red(where) * opacity(where) * profile` — and `red * opacity` is a
    # function of `where` alone, which is to say it folds into the table. All
    # four bands then have the identical shape "look the byte up, multiply by
    # the profile", and premultiplication costs nothing at all.
    #
    # The other is the area. `halo.py` already repaints one strip of the edge
    # per frame rather than the whole ring, for damage reasons that apply here
    # too; the DIB persists between calls, so a frame only has to write the
    # strip that changed and then hand the whole bitmap over — and handing it
    # over was measured at 0.12 ms whatever is in it.
    print()

    def premultiplied_tables(table):
        red, green, blue, opacity = table
        return ([r * o // 255 for r, o in zip(red, opacity, strict=True)],
                [g * o // 255 for g, o in zip(green, opacity, strict=True)],
                [b * o // 255 for b, o in zip(blue, opacity, strict=True)],
                list(opacity))

    def strip_bytes(profile_cut, where_cut, ptable):
        bands = [ImageChops.multiply(where_cut.point(t), profile_cut)
                 for t in ptable]
        return Image.merge("RGBA", [bands[2], bands[1], bands[0], bands[3]])

    band = SPILL + 8
    top = (0, 0, CANVAS[0], band)
    p_cut, w_cut = profile.crop(top), where.crop(top)
    timed(f"one strip {top[2]}x{top[3]}: LUT premultiply -> RGBA",
          lambda i=0: strip_bytes(p_cut, w_cut,
                                  premultiplied_tables(channels(True, i / 100))))
    timed(f"one strip {top[2]}x{top[3]}: ...and its bytes",
          lambda i=0: strip_bytes(
              p_cut, w_cut,
              premultiplied_tables(channels(True, i / 100))).tobytes("raw", "RGBA"))
    ptable = premultiplied_tables(channels(True, 0.0))
    strip_raw = strip_bytes(p_cut, w_cut, ptable).tobytes("raw", "RGBA")

    def whole_frame(i=0):
        raw = strip_bytes(p_cut, w_cut, premultiplied_tables(
            channels(True, i / 100))).tobytes("raw", "RGBA")
        ctypes.memmove(window.bits, raw, len(raw))
        window._present()

    timed("A FRAME: one strip relit, written, handed over", whole_frame)
    print(f"one strip is {len(strip_raw) / 1024:.0f} KiB of the"
          f" {window.nbytes / 1024:.0f} KiB bitmap")
    window.destroy()


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = sys.argv[2] if len(sys.argv) > 2 else None
    if what in ("all", "compat"):
        run_compat()
    if what in ("all", "bench"):
        run_bench()
    if what == "full":
        run_full(shots=out)
    if what == "companion":
        run_companion(shots=out)
    if what == "glue":
        run_glue()
    if what == "ab":
        run_ab(out)
