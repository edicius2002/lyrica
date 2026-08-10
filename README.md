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
| Nudge sync offset | `+` / `-` (±0.25 s, remembered per track) |
| Nudge by a whole second | `Ctrl` + `+` / `-` |
| Say the first line starts now | `Enter` (for videos with an intro) |
| Hide / show | `Ctrl`+`Alt`+`K` |
| Quit | `Ctrl`+`Alt`+`Q` |
| Make it bigger / smaller | `Ctrl`+`Alt`+`+` / `Ctrl`+`Alt`+`-` |
| Back to the designed size | `Ctrl`+`Alt`+`0` |

The size shortcuts are global on Windows: they work while Spotify or a browser
has the keyboard, which is the only way they are any use — focusing the overlay
means clicking it, and clicking it seeks to a line. `Esc` and `+` / `-` still
need it focused. On other platforms the size keys need the focus too.

A size chosen this way is remembered, and takes over from `LYRICA_SIZE`. So are
the place you drag the window to and the sync offset of every track you nudge — all of it in
`settings.json` beside the cache.

## How a word lights up

Three things happen as the sweep reaches a word, and each is tunable because
each was arrived at by measurement rather than by taste.

The **front** is the band where a letter is part-way between sung and unsung.
`LYRICA_SWEEP_FEATHER` sets its width in pixels, default 12. A letter is about
18 wide and lasts about 29 ms in fast delivery against a 16 ms frame, so the
useful span is narrow at both ends: at 40 there are two and a half letters in
transition and no boundary at all, only a cloud, and under about 10 the
transition is shorter than one frame and narrowing it further changes nothing
that can be drawn.

The **bloom** is the light a struck word leaves behind. `LYRICA_BLOOM` sets how
long it lasts as a multiple of how long that word is sung for, default 1.0 — the
light drains exactly as the word finishes. Not a number of seconds: the sweep
crossing a word is already on the word's own clock, and a constant put the two
in disagreement, spent before a long word was half lit and still burning after a
short one had gone. `off` removes it.

The **growth** is the word swelling and settling back. `LYRICA_GROWTH` sets it
as a fraction, default 0.14, `off` to remove it. It has its own short motion
clock, independent of the bloom, so a quick word and a held word make the same
readable gesture. Turning the bloom off leaves the growth in place. It has a floor that is not a
matter of taste: below about a tenth, several of its steps render to the same
whole-pixel size and a third of the frames show an identical picture. There is
no room to brighten a word instead — the sung colour is already at 253 of 255 —
so it grows.

Tk cannot scale a text item: its font sizes are integers and a subtle growth
lands on two or three of them. A growing letter is swapped for a resampled
image and swapped back after, which is also why, for as long as a word is
growing, its letters are either sung or unsung with nothing between — baking
the ramp's sixty-four steps into images would have been 21,504 of them.

The bloom is a two-stage gaussian blur: a tight core and a wider, quieter field,
drawn with PIL and handed to the canvas as an image rather than faked with
offset copies of the glyph. That is not only
truer — four copies two pixels apart double every curve — but twenty times
cheaper per frame, since showing a different image costs 0.005 ms where
recolouring four items costs 0.21.

## Who is singing

One dialect of the sources — Apple Music's TTML, which two of the community
providers serve — records more than the words. It says which voice sings each
line, and its head says whether that voice is a person or a group:

    <ttm:agent type="person" xml:id="v1"/>
    <p begin="27.395" ttm:agent="v1">…

In a duet the first two voices the song introduces step off the column, one to
each side, and whatever they sing together stays in the middle. Whoever opens
takes the left, because that is an order a listener can predict; by frequency it
would depend on the whole song, and the singer with the second verse would take
the left half of a duet purely by having more lines. A third voice keeps the
middle too — a duet has two sides, and inventing a third position would be
claiming a third singer.

It is a pair of responsive lanes rather than a hard edge alignment. At the
default `LYRICA_VOICE_STEP=160`, their centres sit near 32% and 68% of the
designed panel: plainly opposite, without sending a short phrase all the way to
a margin. `off` removes the lanes. A line too long to take the whole step takes
what room there is and no more, so nothing ever clips.

A backing vocal answers from the open lane and runs on its own word timings. A
lead in the left lane therefore gets its response on the right, and vice versa;
an unidentified or centred lead keeps the right-hand default.

Nothing moves for a song with one singer, or for a source that does not say —
which is most of them. The names in that head metadata are never read: they are
the one part of this that could be wrong about a real person, and nothing here
would show them.

## The border reacts to the music

The panel's edge carries a slow light that brightens with what is playing. It
reads the output level from Windows' own endpoint meter — no audio is captured,
no samples reach this process, and there is no extra dependency. A reading costs
0.21 ms and the loop it keeps awake runs at 30 Hz, measured at 1.1 % of a
twelve-core machine.

Three styles. `LYRICA_BEAM=shine` (the default) lights the whole border and rotates
a gradient through it; `LYRICA_BEAM=comet` sends a bright head round a dark
ring; and `LYRICA_BEAM=aurora` rotates neighbouring hues derived from the cover.
Every style has a crisp core over a wider, quieter halo, with a perceptual
contrast floor against the artwork wash. `LYRICA_BEAM_INTENSITY` adjusts their
visual weight from 0.5 to 2.0. With the shine, every edge stays lit and what moves is the colour — measured, the comet
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

### Videos that open with an intro (optional)

A lyric timeline is anchored to the release. A music video is not — it opens
with a film, a spoken intro, a countdown — so the words arrive whole seconds
late. `Enter` corrects that in one press and the correction is remembered for
that track, which is all you need if it is occasional.

With a YouTube Data API key it stops being your problem. SponsorBlock has a
category, `music_offtopic`, that exists only for music videos and marks the
parts of one that are not the song; a segment beginning at zero says exactly
where the song starts. Checked against twelve videos by removing every marked
stretch and comparing what was left against the release's own length, eleven
agreed within seven seconds.

```powershell
setx LYRICA_YOUTUBE_KEY "your-key-here"
```

The key is only used to work out *which* video is playing, which Windows does
not publish: Lyrica searches for the title the player reports and keeps the
result whose length matches, then asks SponsorBlock about it. That question goes
through SponsorBlock's hashed-prefix endpoint, so the service is told four
characters of a digest and answers with every video sharing them — it never
learns what you are listening to.

Two limits worth knowing. About four in ten tracks outside the mainstream have
nobody's annotation, and there `Enter` still applies. And a video whose music is
a longer edit than the record — Thriller's extended dance break is the clear
case — cannot be held in sync by any single correction, because its song is not
the one the lyrics were written against.

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

Everything this once listed has shipped — NetEase, the word-by-word layer, the
outline, the packaging — so what is left is what is actually left:

- **A configurable provider order.** Fixed in code today. The current order is
  measured rather than assumed, so this is a preference rather than a fix.
- **A release workflow.** CI tests on both platforms but does not build or
  publish the executable, so `pyinstaller lyrica.spec` is a local step.
- **macOS on real hardware.** There is a session reader and CI compiles it, but
  nobody has watched it read a song. Treat that path as unverified.
