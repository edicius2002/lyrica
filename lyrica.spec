# PyInstaller build. Run from the repository root:
#
#     pyinstaller lyrica.spec
#
# The result is dist/Lyrica.exe, one file, no console, no Python needed on the
# machine it is copied to.
#
# One file rather than one directory. It starts a fraction slower because it
# unpacks to a temporary folder first, and that is the wrong thing to optimise
# for something launched once and left running for hours — being a single file
# you can copy to another machine is worth more than a second at startup.
#
# What is deliberately *not* in here is `.env`. It holds a Discogs token, and
# bundling it would put a credential inside a file meant to be handed around.
# The executable reads one from beside itself or from the environment, and works
# without one — Apple is the primary cover source and needs no key.

from PyInstaller.utils.hooks import collect_submodules

# winsdk resolves its projections at import time rather than through statements
# PyInstaller can see, so the analysis finds nothing and the packaged app falls
# back to "no media session support" — it starts, and then never notices a
# track. Collected wholesale because the pieces are picked by name at runtime.
hidden = collect_submodules("winsdk")

analysis = Analysis(
    ["src/lyrica/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # Trimmed because they arrive through Pillow and are large. Nothing here
    # draws a plot or a window toolkit other than tkinter.
    excludes=["matplotlib", "numpy", "scipy", "pandas", "PyQt5", "PyQt6",
              "PySide2", "PySide6", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="Lyrica",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console. The overlay writes to a log file precisely because there is
    # nowhere else for it to write; a console window would sit behind the
    # lyrics for the whole session.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
