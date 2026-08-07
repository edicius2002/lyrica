# Lyrica

Real-time synced lyrics overlay for Windows. Shows line-by-line lyrics for
whatever is playing — Spotify desktop app, or Chrome/Edge with YouTube,
YouTube Music or SoundCloud — in a floating, always-on-top transparent window.

Lyrics come from [LRCLIB](https://lrclib.net): free, keyless and without rate
limits. Track detection uses the Windows global media session (SMTC), so any
player that responds to media keys works out of the box.

## Install & run

```powershell
pip install -e .
lyrica          # or: python -m lyrica
```

### On a machine without Python

Build a standalone executable and copy that instead:

```powershell
pip install -e .[dev]
pyinstaller lyrica.spec
```

`dist/Lyrica.exe` is one file, about 30 MB, and needs nothing installed on the
machine it is copied to — no Python, no packages. It reads and writes the same
cache under `%LOCALAPPDATA%\Lyrica`.

The Discogs token is deliberately **not** bundled: it would put a credential
inside a file meant to be handed around. The executable reads one from a `.env`
beside itself or from the environment, and works without one — Apple is the
primary cover source and needs no key.

Play something and the overlay appears at the bottom center of the screen.

| Action | Control |
|---|---|
| Move the overlay | Drag with the mouse |
| Quit | `Esc` or right click |
| Nudge sync offset | `+` / `-` (±0.25 s) |
| Hide / show | `Ctrl`+`Alt`+`K` |
| Quit | `Ctrl`+`Alt`+`Q` |
| Make it bigger / smaller | `Ctrl`+`Alt`+`+` / `Ctrl`+`Alt`+`-` |
| Back to the designed size | `Ctrl`+`Alt`+`0` |

The size shortcuts are global on Windows: they work while Spotify or a browser
has the keyboard, which is the only way they are any use — focusing the overlay
means clicking it, and clicking it seeks to a line. `Esc` and `+` / `-` still
need it focused. On other platforms the size keys need the focus too.

A size chosen this way is remembered, and takes over from `LYRICA_SIZE`.

## Notification area

There is an icon beside the clock. Left click hides and shows the overlay; right
click opens a menu with the same actions plus **Start with Windows**, which
writes a per-user registry entry and needs no administrator. That item only does
anything for the packaged executable — from a source checkout the command would
have to name an interpreter, a working directory and a module, all of which move
the moment the checkout does, so it is greyed out instead.

Windows files new tray icons under the notification-area chevron rather than
showing them, so look behind the `^` and drag it out if you want it visible. The
log says when it was added.

Hiding puts the overlay away rather than closing it — `Esc` and right click
destroy the window, which is the right answer for "I am done" and the wrong one
for "not right now". While hidden it stops drawing entirely and only listens for
the shortcut that brings it back.

**If a shortcut does nothing**, another application is almost certainly eating
it. Anything with a low-level keyboard hook — remote desktop tools especially —
can swallow a combination before Windows dispatches it, and `RegisterHotKey`
still reports success, so the overlay cannot tell. Measured here: of twelve
`Ctrl`+`Alt`+letter combinations, ten arrive and `L` and `M` never do. The log
records which shortcuts were claimed at startup.

The overlay is drawn at a size that follows the display's own DPI scale. The
keyboard is the quickest way to change that, and `LYRICA_SIZE` sets where a
machine starts — a multiple of the designed size, between `0.6` and `2.0`:

```powershell
$env:LYRICA_SIZE = "1.4"; lyrica     # or put LYRICA_SIZE=1.4 in .env
```

It multiplies into the display scale rather than resizing the window on its own,
so everything moves together and the layout keeps its proportions. Measured
across 0.6-2.0: the window and the wrap width scale exactly, the rendered text
within 1.8 % and the cover within 1.2 %. Line height tracks within 8 % because
Windows quantises font metrics, and the lines stack from their measured heights
rather than assumed ones, so that absorbs itself.

## Album covers

The player's own thumbnail appears immediately, then a higher-resolution one
replaces it. Sources are tried in order:

| Source | Needs | Measured on this library |
| --- | --- | --- |
| Discogs | a token, optional | 5 of 5 tracks, 600 px, ~1.4 s |
| Apple catalogue search | nothing | 3 of 5 tracks, 600 px, ~0.9 s |

Discogs leads because it measured better, not because it is fancier: same
resolution, wider coverage, and the two tracks Apple missed were both Latin
releases. It is slower, but a cover is fetched once per track and kept, where a
missing cover is permanent.

Apple is the fallback and earns it — no token, so it is what works when none is
configured.

```powershell
setx LYRICA_DISCOGS_TOKEN "your-token-here"
```

The token is read from the environment and never written to disk by Lyrica, so
it cannot end up in the repository. It is sent as a request header rather than
in the URL, since a URL ends up in logs and proxies and a header does not.

## Sharing the cache between machines

Lookups are cached under `%LOCALAPPDATA%\Lyrica\cache`. Set `LYRICA_CACHE_DIR` to move that
somewhere synchronised and the cache follows you:

```powershell
setx LYRICA_CACHE_DIR "$env:OneDrive\Lyrica\cache"
```

Plain folder sync is enough. Every entry is written once, named by a hash of the track, and never
modified — two machines can add different files but can never disagree about one. A shared
database would be worse here, not better, since concurrent writers are what corrupt one.

## Architecture

```
src/lyrica/
├─ app.py            tkinter overlay; interpolates position every 100 ms
├─ smtc.py           Windows media session reader (background thread)
├─ lyrics.py         Lyrics model + LRC parser
└─ providers/
   ├─ base.py        LyricsProvider interface
   ├─ lrclib.py      LRCLIB: exact /get → scored fuzzy /search
   └─ __init__.py    provider cascade + on-disk cache (%LOCALAPPDATA%/Lyrica)
```

Browser metadata is normalized before lookup: "Artist - Title" splitting and
removal of video-title noise like "(Official Video)".

## Development

```powershell
pip install -e .[dev]
pytest
```

Research notes and source-viability scripts live in `research/`.

## Roadmap

- NetEase as a second synced provider
- Word-by-word (karaoke) layer: amll-ttml-db / Musixmatch richsync when available
- Text outline/shadow for readability on light backgrounds
- PyInstaller packaging
