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

Play something and the overlay appears at the bottom center of the screen.

| Action | Control |
|---|---|
| Move the overlay | Drag with the mouse |
| Quit | `Esc` or right click |
| Nudge sync offset | `+` / `-` (±0.25 s) |

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
