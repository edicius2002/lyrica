"""What a rounded corner actually looks like on this build, magnified.

The clip region is binary — a pixel is inside it or outside it, with no partial
coverage — so a curve made from it can only ever be a stair-step. The Windows 11
attribute that would smooth it is refused on this build. Everything past that is
opinion unless the pixels are looked at, so this photographs them.

Grabs the top-left corner under each variant, magnifies it, and writes one
side-by-side PNG to compare.

    python research/viability/probe_corners.py [out.png]
"""
import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from lyrica.chrome import windows as win

PANEL = (420, 220)
CORNER = 44          # device pixels of corner to photograph
ZOOM = 9
BACKDROP = 150       # a mid desktop, where the plate edge is most visible

# The rim experiment: a stroke drawn just inside the clip boundary. It cannot
# fix the silhouette — that is still the region's stair-step — but a bright,
# genuinely antialiased curve gives the eye something clean to follow, which is
# the trick tacky-borders and friends are actually performing.
RIM_INSET = 0.5
RIM_WIDTH = 1.6
RIM_LEVEL = 46


def rim_image(width: int, height: int, radius: int) -> Image.Image:
    """An antialiased rounded-rect stroke, supersampled then reduced."""
    s = 4
    big = Image.new("L", (width * s, height * s), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (RIM_INSET * s, RIM_INSET * s, (width - RIM_INSET) * s - 1,
         (height - RIM_INSET) * s - 1),
        radius=radius * s, outline=255, width=max(1, round(RIM_WIDTH * s)))
    mask = big.resize((width, height), Image.LANCZOS)
    out = Image.new("RGB", (width, height), (0, 0, 0))
    out.putdata([(round(v * RIM_LEVEL / 255),) * 3 for v in mask.getdata()])
    return out


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("corners.png")
    win.set_dpi_awareness()
    root = tk.Tk()
    root.withdraw()

    back = tk.Toplevel(root)
    back.overrideredirect(True)
    back.attributes("-topmost", True)
    sw, sh = back.winfo_screenwidth(), back.winfo_screenheight()
    back.geometry(f"{sw}x{sh}+0+0")
    back.configure(bg=f"#{BACKDROP:02x}{BACKDROP:02x}{BACKDROP:02x}")

    panel = tk.Toplevel(root)
    panel.overrideredirect(True)
    panel.attributes("-topmost", True)
    panel.geometry(f"{PANEL[0]}x{PANEL[1]}"
                   f"+{(sw - PANEL[0]) // 2}+{(sh - PANEL[1]) // 2}")
    panel.configure(bg="#000000")
    canvas = tk.Canvas(panel, width=PANEL[0], height=PANEL[1], bg="#000000",
                       highlightthickness=0, borderwidth=0)
    canvas.pack(fill="both", expand=True)
    root.update()

    hwnd = win._hwnd_of(panel)
    win._set_accent(hwnd, win.ACCENT_ENABLE_ACRYLICBLURBEHIND,
                    win._abgr(*win.TINT_RGBA))
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    box = (rect.left, rect.top, rect.left + CORNER, rect.top + CORNER)

    # Which accent state each variant wears, because the question is no longer
    # "how jagged is the region" but "does the region apply at all".
    ACRYLIC = win.ACCENT_ENABLE_ACRYLICBLURBEHIND
    GRADIENT = win.ACCENT_ENABLE_TRANSPARENTGRADIENT
    OFF = win.ACCENT_DISABLED
    variants = [("acrylic square", 0, False, ACRYLIC),
                ("acrylic r16", 16, False, ACRYLIC),
                ("gradient r16", 16, False, GRADIENT),
                ("no accent r16", 16, False, OFF),
                ("acrylic r16 + rim", 16, True, ACRYLIC)]

    # Order matters if DWM caches the region when the accent is turned on, so
    # the orderings are variants too rather than an assumption.
    variants += [("region then acrylic", 16, False, ACRYLIC),
                 ("region, re-accent", 16, False, ACRYLIC),
                 ("region + framechange", 16, False, ACRYLIC)]

    shots, held = [], []
    for name, radius, rim, state in variants:
        for item in canvas.find_all():
            canvas.delete(item)
        if rim:
            photo = ImageTk.PhotoImage(rim_image(*PANEL, radius))
            canvas.create_image(0, 0, image=photo, anchor="nw")
            held.append(photo)
        tint = {ACRYLIC: win.TINT_RGBA, GRADIENT: win.DRAG_TINT_RGBA}.get(state)
        packed = win._abgr(*tint) if tint else 0

        if name == "region then acrylic":
            win._set_accent(hwnd, OFF, 0)
            win.round_corners(panel, *PANEL, radius)
            win._set_accent(hwnd, state, packed)
        elif name == "region, re-accent":
            win.round_corners(panel, *PANEL, radius)
            win._set_accent(hwnd, state, packed)
            win._set_accent(hwnd, state, packed)
        elif name == "region + framechange":
            win._set_accent(hwnd, state, packed)
            win.round_corners(panel, *PANEL, radius)
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(hwnd), None, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)  # +FRAMECHANGED
        else:
            win._set_accent(hwnd, state, packed)
        if radius and not name.startswith("region"):
            ok = win.round_corners(panel, *PANEL, radius)
            got = wintypes.RECT()
            kind = ctypes.windll.user32.GetWindowRgnBox(
                wintypes.HWND(hwnd), ctypes.byref(got))
            print(f"  {name:20s} SetWindowRgn={ok} GetWindowRgnBox kind={kind} "
                  f"box=({got.left},{got.top},{got.right},{got.bottom})")
        elif not radius:
            ctypes.windll.user32.SetWindowRgn(
                ctypes.c_void_p(hwnd), None, True)
        panel.lift()
        root.update()
        time.sleep(0.35)
        root.update()
        shot = ImageGrab.grab(box).convert("RGB")
        shots.append((name, shot.resize((CORNER * ZOOM, CORNER * ZOOM),
                                        Image.NEAREST)))
        # Does the desktop show through at the very corner, or is it plate?
        px = shot.getpixel((1, 1))
        print(f"  {name:20s} corner pixel {px}  "
              f"({'desktop shows through' if max(px) > 110 else 'still plate'})")

    root.destroy()

    pad, label = 12, 22
    w = CORNER * ZOOM
    sheet = Image.new("RGB", (len(shots) * (w + pad) + pad, w + label + pad * 2),
                      (24, 24, 26))
    draw = ImageDraw.Draw(sheet)
    for i, (name, shot) in enumerate(shots):
        x = pad + i * (w + pad)
        sheet.paste(shot, (x, pad + label))
        draw.text((x, pad + 4), name, fill=(230, 230, 235))
    sheet.save(out_path)
    print(f"\nwrote {out_path.resolve()}  ({CORNER}px corner at {ZOOM}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
