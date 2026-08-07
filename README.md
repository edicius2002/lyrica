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
lyrica
```

`lyrica` opens no console window. `python -m lyrica` does — use `pythonw -m
lyrica` if you start it that way. Everything it has to say goes to
`%LOCALAPPDATA%\Lyrica\lyrica.log` either way, which is also where to look if a
shortcut seems dead or the overlay never appears.

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

## The border reacts to the music

The panel's edge carries a slow light that brightens with what is playing. It
reads the output level from Windows' own endpoint meter — no audio is captured,
no samples reach this process, and there is no extra dependency. A reading costs
0.21 ms and the loop it keeps awake runs at 30 Hz, measured at 1.1 % of a
twelve-core machine.

Two styles. `LYRICA_BEAM=comet` (the default) sends a bright head round a dark
ring; `LYRICA_BEAM=shine` lights the whole border and rotates a gradient through
it, so every edge stays lit and what moves is the colour — measured, the comet
runs 19 to 132 in brightness and leaves the top edge dark once it has passed,
where the shine holds 36 to 83 everywhere, always. `LYRICA_BEAM=off` turns it
off.

The shine also reads what the music is *doing*, not only how loud it is. How
much the level moves sets how far the gradient swings — a compressed wall of
sound gets an almost even border, something with air between its hits gets a
border with the same air in it — and how often it rises turns the gradient a
little faster.

Neither is a tempo, deliberately. Which multiple of the beat an onset rate
counts cannot be recovered from loudness: a kick with an off-beat hat measured
265 for a track at 128, and folding that into a plausible range turned a 174 BPM
track into 91. A border that is merely busier when the music is cannot be wrong
that way, where one claiming a BPM would be wrong about half the time and
obviously so. See
[`research/viability/probe_envelope_tempo.py`](research/viability/probe_envelope_tempo.py).

It costs about a millisecond a frame at the default size — the loop it keeps
awake is the expense, not the drawing.

While the overlay is drawing it asks Windows for a finer timer than the 15.625 ms
one it hands out by default. Without that, a 33 ms request lands on 46 and the
lyric sweep's frames arrive 7 ms from where they should; with it, 33 ms lands on
34 and that spread falls to half a millisecond. It is released while hidden,
since the finer resolution costs a little power.

What it cannot do is follow a beat. The meter reports loudness and has no
spectrum, so there is nothing in it to find a downbeat with — the head travels
at a fixed rate and only its brightness answers the music. Looking the tempo up
instead was measured and rejected: see
[`research/viability/probe_bpm.py`](research/viability/probe_bpm.py).

## How solid the panel is

`LYRICA_OPACITY` sets it, between `0.6` and `1.0`, defaulting to `0.90`:

```powershell
$env:LYRICA_OPACITY = "0.95"   # or LYRICA_OPACITY=0.95 in .env
```

Where in that range to sit is taste rather than measurement. For reference,
0.75 is the most translucent value that clears WCAG's 3:1 for large text at
all, and 0.82 the most translucent that clears it with margin — both turned out
to be more see-through than anyone wanted to look at. The default puts 25 of a
white desktop through the panel where 0.82 put 46.

## Songs with no lyrics

When a search comes back empty the panel shrinks to just the cover, the title
and the artist, and grows again for the next track that has words. The width
follows the title, so a short name gets a small panel rather than a wide one
with space nobody is using.

It waits for a definite answer before moving. A search takes a second or two,
and "still asking" is deliberately not the same state as "nobody has any" —
collapsing on the first would shrink and grow the panel on every track change.
Lyrics that exist but carry no timings count as absent, because they are never
drawn.

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
