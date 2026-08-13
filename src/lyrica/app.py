"""Lyrica — an always-on-top synced lyrics overlay.

Reads whatever is playing from the platform's media session and shows the
lyrics over everything else, swept word by word.

Run:      python -m lyrica   (or the `lyrica` console script)
Keys:     Esc = quit | right click = quit | drag with mouse = move
          +/- = nudge this track's sync offset by ±0.25 s, remembered
          Ctrl +/- = the same by ±1 s | Enter = the first line starts now
                (these need the overlay focused)
Global:   Ctrl+Alt+K = hide/show | Ctrl+Alt+Q = quit
          Ctrl+Alt+plus / Ctrl+Alt+minus = resize | Ctrl+Alt+0 = designed size
"""
import logging
import logging.handlers
import math
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field, replace
from pathlib import Path
from tkinter import font as tkfont
from typing import ClassVar

from lyrica import (
    artwork,
    autostart,
    config,
    glass,
    hotkeys,
    motion,
    songcolour,
    sponsorblock,
    tray,
    youtube,
)
from lyrica import (
    beam as beam_mod,
)
from lyrica import chrome as chrome_mod
from lyrica import (
    meter as meter_mod,
)
from lyrica import palette as palette_mod
from lyrica.lineview import LineView
from lyrica.lyrics import (
    BACKING_CROSS_SOURCE_ALIGNED,
    BACKING_INFERRED,
    BACKING_SOURCE_EXACT,
    Lyrics,
    progress_in,
)
from lyrica.overlay_text import OUTLINE_COLOUR, measure, split_for_wrapping
from lyrica.providers import fetch_for_candidates
from lyrica.sessions import Snapshot, create_reader

# Logical pixels at 96 dpi; scaled by the chrome's DPI factor at startup.
#
# Three lines: the one before, the one being sung, and the one coming. The
# height follows from that plus the card and a fade band at each edge — it is
# derived, not chosen, and shrinking the count is what shrinks the window.
WIDTH, HEIGHT = 900, 320
WRAP = 760
# A backing line occupies the lower corner between two main rows. The gap keeps
# the actual glyph boxes separate; their soft outer halos may meet, but the
# echo is layered behind both lead rows so that light can never cover letters.
ROW_GAP = 50

# The band at each edge where a line fades away. Shallower now that only one
# line sits either side: deep enough to read as a fade rather than a cut,
# shallow enough that the neighbours are still properly legible.
FADE_ZONE = 42
# The next lyric is a readable preview, not edge decoration. This is the
# medium presence the established 1 -> 1 layout has at its normal lower slot.
# Keep it as an explicit floor so wrapping and font metrics cannot turn 1 -> 2
# or 2 -> 2 into an effectively invisible incoming row.
INCOMING_VISIBILITY_FLOOR = 0.50

# Where the line being sung sits, as a fraction of the window height. Derived
# rather than chosen: low enough that the line above clears the card and its
# fade band, high enough that the line below clears the bottom one.
ANCHOR = 0.55

FONT_TITLE = ("Segoe UI Semibold", -20)
FONT_ARTIST = ("Segoe UI", -16)
THUMB_SIZE = 62

# One size for every lyric line. A role change that also changed size would
# force a relayout, which is a rebuild wearing a different name — and lines
# reading as louder or quieter rather than bigger or smaller is what the
# reference does anyway.
FONT_LINE = ("Segoe UI", -30, "bold")

# What a backing vocal is drawn in. Smaller and, through `Palette.quieter`, a
# rung further down the ladder — it is being sung by somebody standing behind
# the singer, and it should look it.
FONT_ECHO = ("Segoe UI", -20, "italic")

# Keep a backing vocal visibly separate from the lead and slightly dimmer. On
# the same baseline it reads as part of the main lyric; with the lead's exact
# colour it reads as its unsung half.
ECHO_KEEP = 0.72
ECHO_VERTICAL_GAP = 2

# How long a backing line takes to arrive and to leave, in seconds. It has its
# own window inside the line — it answers a phrase rather than lasting as long
# as one — so it comes and goes on that window rather than on the line's.
ECHO_FADE_S = 0.30
# A transformed TTML clock is locally verified but not source-native. Keep its
# presentation transition inside the same 150-200 ms error budget instead of
# giving it the full direct-TTML preview on top of alignment uncertainty.
ECHO_ALIGNED_FADE_S = 0.15
# Richsync has only word starts; its final token can therefore receive a
# near-zero inferred duration.  Only a genuinely microscopic *whole ad-lib*
# receives the conservative path: most serial Richsync timings, including
# N95's, are visually sound and retain the normal TTML-like animation.
ECHO_INFERRED_MAX_SOURCE_S = 0.15
ECHO_INFERRED_FADE_S = 0.10
# A Richsync suffix can infer to only a few frames.  Four hundred and fifty
# milliseconds is long enough to read without making the fallback feel like a
# separate lyric line; source timestamps remain untouched.
ECHO_INFERRED_MIN_VISIBLE_S = 0.45
# Lead lyrics are previewed because they must be read before the voice. A small
# backing response is perceived rather than read and already owns a fade-in;
# advancing its entire clock by the lead's 150 ms made aligned adlibs end early.
BACKING_SOURCE_LEAD_S = 0.05
BACKING_ALIGNED_LEAD_S = 0.0

# A late SponsorBlock answer can move the recording clock by whole seconds.
# Landing on the corrected row is honest; scrolling every intervening row is
# not.  The replacement scene rises out of the wash just long enough to make
# the discontinuity read as deliberate rather than as a flash.
CUT_CORRECTION_FADE_S = 0.12

# A timed-out lookup has two deliberate visual moves: the outgoing words sink
# into the wash before the incoming card replaces them, and lyrics that answer
# afterwards rise from that same wash already laid out at their current time.
OUTGOING_FADE_S = 0.25
LYRICS_FADE_S = 0.16


def _needs_inferred_backing_fallback(lyr: Lyrics, line_index: int) -> bool:
    """Whether Richsync inferred a microscopic final backing token."""
    if lyr.backing_timing_at(line_index) != BACKING_INFERRED:
        return False
    _text, words = lyr.backing_at(line_index)
    return bool(words and words[-1][1] - words[-1][0]
                <= ECHO_INFERRED_MAX_SOURCE_S)


def _backing_lead_s(lyr: Lyrics, line_index: int) -> float:
    """Small provenance-aware perceptual lead for a backing clock."""
    timing = lyr.backing_timing_at(line_index)
    if timing == BACKING_SOURCE_EXACT:
        return BACKING_SOURCE_LEAD_S
    if timing in (BACKING_CROSS_SOURCE_ALIGNED, BACKING_INFERRED):
        return BACKING_ALIGNED_LEAD_S
    return 0.0


def _backing_window(lyr: Lyrics, line_index: int) -> tuple[float, float, float]:
    """Effective open, visible-end and fade duration for one backing line."""
    _text, words = lyr.backing_at(line_index)
    if not words:
        return 0.0, 0.0, ECHO_FADE_S
    timing = lyr.backing_timing_at(line_index)
    inferred = timing == BACKING_INFERRED
    short_final = _needs_inferred_backing_fallback(lyr, line_index)
    if short_final:
        fade_s = ECHO_INFERRED_FADE_S
    elif timing == BACKING_CROSS_SOURCE_ALIGNED:
        fade_s = ECHO_ALIGNED_FADE_S
    else:
        fade_s = ECHO_FADE_S
    visible_end = words[-1][1]
    if inferred:
        # Richsync starts are useful but its final duration is inferred. Base
        # the safety floor on that final token, not on the whole multi-word
        # response, whose long prefix used to hide a near-zero tail.
        visible_end = max(visible_end,
                          words[-1][0] + ECHO_INFERRED_MIN_VISIBLE_S)
    opens = words[0][0] if inferred else words[0][0] - fade_s
    return opens, visible_end, fade_s


def _display_line_index(lyr: Lyrics, pos: float) -> int:
    """Lead row to display, holding it through a sequential response."""
    index = lyr.line_index_at(pos + LINE_LEAD_S)
    while index > 0:
        previous = index - 1
        _text, backing_words = lyr.backing_at(previous)
        _opens, visible_end, _fade = _backing_window(lyr, previous)
        if (lyr.backing_mode_at(previous) != "sequential" or not backing_words
                or pos + _backing_lead_s(lyr, previous) >= visible_end):
            break
        # The response remains small and below this preceding lead, but must
        # not be visually overlaid by the following lead line.
        index = previous
    return index

# Backing vocals hang from the lower outer corner of the line they answer. The
# inset lets the two boxes meet visually without laying one word over another.
ECHO_CORNER_INSET = 10
ECHO_SAFE_MARGIN = 44
ECHO_ENTRY_LANE = 0.55
ECHO_EXIT_LANE = 0.75

# A voice may lean towards an edge, but neither its outline nor its maximum
# growth should read as touching the glass. Eight pixels was only a clipping
# guard; this is an optical margin.
VOICE_SAFE_MARGIN = 36

SLOW_TICK_MS = 100
FAST_TICK_MS = 16   # 60 Hz; measured at ~1% of this machine with stable items

# Drop the blur while dragging. Everything measurable inside the process came
# back clean — the move lands in ~2.4 ms, the loop stalls twice in eight
# seconds — which leaves the compositor's own work, outside this process and
# not measurable from here. Switching the effect costs 0.006 ms, so this is
# cheap to try and cheap to turn off if it makes no difference.
SUSPEND_GLASS_WHILE_DRAGGING = True

# Lyrics feel late when they land exactly on the beat: you read a line as it
# begins, so it has to be there fractionally before the voice. better-lyrics
# settled on the same correction, and those numbers are adopted, not guessed.
# How long the card will wait for every part of itself before going up with
# whatever it has. One second keeps the outgoing song from lingering while a
# slow provider continues in the background; late results are revealed through
# CARD_ONLY instead of holding the whole transition open.
REVEAL_WAIT_S = 1.0

LINE_LEAD_S = 0.115
WORD_LEAD_S = 0.150

# How many lines either side of the current one are kept on screen. One, so the
# view is the line before, the line now, and the line next — and nothing else
# competing for the glance.
CONTEXT = 1

# How much one press of the resize keys moves the size. A tenth: coarse enough
# that a couple of presses is a visible change, fine enough that the range is
# not crossed by accident.
SIZE_STEP = 0.1

# The border runs at the same rate as the lyric sweep. It was 30 Hz while the
# loop could not keep an even 30 — measured at 22, with frames landing 2 ms
# apart from where they belonged. Now that the scheduler holds a millisecond,
# 60 Hz costs about half a millisecond a frame more and there is no reason to
# animate one thing on the panel more coarsely than another.
BEAM_TICK_MS = FAST_TICK_MS

# What a track's lyrics are known to be. Three states rather than two, and the
# third is the whole point: `None` lyrics means both "still asking" and "nobody
# has any", and a panel that collapsed on the first would shrink and grow again
# on every single track change.
LYRICS_UNKNOWN, LYRICS_PRESENT, LYRICS_ABSENT = "unknown", "present", "absent"

# The visual lifecycle of a track change. OUTGOING and PREPARING coexist on
# different Track objects while a lookup is in flight; CARD_ONLY is specifically
# an incoming track promoted by the deadline before its lyric answer; READY has
# a definite lyric answer, including a definite absence.
SCENE_OUTGOING = "outgoing"
SCENE_PREPARING = "preparing"
SCENE_CARD_ONLY = "card_only"
SCENE_READY = "ready"

# The panel with nothing to say: the card, and the same margin under it as
# above it. Everything else was the room the lyrics needed.
COMPACT_MIN_WIDTH = 260
COLLAPSE_MS = 320

# How far the pointer may travel and still count as a click rather than a drag.
CLICK_SLACK = 4

# How close the reported position must come before a seek is considered landed.
# Generous, because the player moves to roughly where it was asked rather than
# exactly, and because playback keeps running while the request travels.
SEEK_SETTLED_S = 2.5

logger = logging.getLogger(__name__)


def _between(low: tuple, high: str, k: float) -> str:
    """A colour `k` of the way from a backdrop to one of the palette's own."""
    k = max(0.0, min(1.0, k))
    return glass.hex_of(tuple(a + (b - a) * k for a, b in
                              zip(low, glass.rgb_of(high), strict=True)))


@dataclass
class Track:
    """Everything one song owns while it is being assembled.

    Workers return immutable, generation-stamped events. The render thread is
    the only writer here, so readiness is observed as a coherent UI state and a
    late result can still be committed after this object has been promoted.
    Results whose generation no longer names the track being assembled are
    simply discarded; the outgoing frame stays frozen once a successor exists.

    `searched` is not `cover is not None`. "The search is over" and "the search
    found something" are different facts, and treating them as one is what let a
    song with no cover of its own wear the previous song's for its whole
    duration.
    """

    gen: int = 0
    snapshot: Snapshot = field(default_factory=Snapshot)
    offset: float = 0.0
    lyrics: Lyrics | None = None
    lyrics_state: str = LYRICS_UNKNOWN
    scene: str = SCENE_READY
    identified: artwork.Release = field(default_factory=artwork.Release)
    cuts: sponsorblock.Cuts = field(default_factory=sponsorblock.Cuts)
    cover: bytes | None = None
    art: tuple | None = None
    searched: bool = False
    # How long this song may take before it goes up with whatever it has. Its
    # own, not the overlay's: a deadline left outside meant a track assembled
    # after another one was already overdue inherited an expired one and went
    # up half-made.
    deadline: float = 0.0

    @property
    def whole(self) -> bool:
        """Whether this song is ready to be put up as one thing."""
        return self.searched and self.lyrics_state != LYRICS_UNKNOWN


@dataclass(frozen=True)
class WorkerResult:
    """One background answer, handed back to the Tk thread.

    Workers never mutate the state being rendered.  A generation identifies
    the track the answer belongs to; artwork also carries the window-shape
    generation it was rasterised for, because an image built across a resize
    is valid content at the wrong dimensions.
    """

    gen: int
    kind: str
    value: object = None
    shape_gen: int | None = None


@dataclass(frozen=True)
class ArtworkResult:
    identified: artwork.Release
    cover: bytes | None
    art: tuple | None


def transport_is_live(track: Track, snap: Snapshot) -> bool:
    """Whether ``snap`` may advance the track currently on screen.

    The latest media-session report can already name the next song while the
    panel is deliberately holding the previous one during its background
    lookup. Letting that new clock drive the old lyrics makes them sing to a
    recording that is no longer playing. A paused or unavailable session has
    no moving clock either, so its last rendered frame remains the truth.
    """
    return (snap.ok and snap.playing and track.snapshot.ok
            and snap.track_key() == track.snapshot.track_key())


def should_animate(step: int | None, dragging: bool) -> bool:
    """Whether a move between lines should glide or simply land.

    A move within the visible column animates, in either direction — going back
    a line should travel just as the next one does. A longer jump is a
    discontinuity, and animating one is how a view ends up chasing itself across
    a song.

    Never while the window is being dragged. The whole lyric column is dirty for
    every frame of a glide, and on top of a moving window that measured 5.41 ms
    a frame against 1.39 for the sweep alone, with a worst case of 45 ms —
    nearly three frames, which is what a hand feels as a stutter. Nobody follows
    a 460 ms glide while moving the panel it is drawn on.
    """
    return step is not None and 0 < step <= CONTEXT and not dragging


def compact_target(state: str, currently_compact: bool) -> bool:
    """Whether the panel should be compact, given what is known about the words.

    Three states, and the third has to stay a third all the way here. Collapsing
    reads `UNKNOWN` as "not absent" the moment it is a bool, so a track with no
    lyrics followed by another track with no lyrics would expand for the second
    or so the search takes and then collapse again — the panel bouncing between
    two songs that both had nothing to show.
    """
    if state == LYRICS_UNKNOWN:
        return currently_compact        # no opinion; keep whatever it is
    return state == LYRICS_ABSENT


def lyrics_state(lyr: Lyrics | None) -> str:
    """What a finished search means for the panel.

    Unsynced counts as absent. Plain words with no timings are never drawn, so
    a panel holding room for them would be holding room for nothing — and this
    is only ever called once a search has finished, which is what keeps
    `UNKNOWN` meaning "still asking" rather than "nobody has any".
    """
    return (LYRICS_PRESENT if lyr and lyr.synced and lyr.lines
            else LYRICS_ABSENT)


def _scaled_font(spec: tuple, scale: float) -> tuple:
    family, size, *rest = spec
    return (family, round(size * scale), *rest)


def _font_that_fits_width(font: tuple, measured: float, available: float) -> tuple:
    """Scale a Tk font down just enough to keep one measured row in its box."""
    if measured <= available or not font or len(font) < 2:
        return font
    size = int(font[1])
    if not size:
        return font
    # Tk uses a negative size for pixels. Leave a one-pixel reduction even
    # when rounding says the old size would do, so the next measurement makes
    # progress instead of rebuilding the same overflowing row forever.
    reduced = min(abs(size) - 1, int(abs(size) * available / measured))
    reduced = max(1, reduced)
    return (font[0], -reduced if size < 0 else reduced, *font[2:])


def _single_row_width(text: str, font: tuple) -> int:
    """Measure the exact unwrapped row shape LineView will later construct."""
    font_obj = tkfont.Font(font=font)
    key = tuple(sorted(font_obj.actual().items()))
    space = measure(font_obj, key, " ")
    width = 0
    for index, (piece, glued) in enumerate(split_for_wrapping(text)):
        if index and not glued:
            width += space
        width += measure(font_obj, key, piece)
    return width


def _font_for_single_row(text: str, font: tuple, available: float) -> tuple:
    """Choose an unwrapped font before any canvas items are constructed."""
    fitted = font
    for _attempt in range(8):
        measured = _single_row_width(text, fitted)
        smaller = _font_that_fits_width(fitted, measured, available)
        if smaller == fitted:
            return fitted
        fitted = smaller
    return fitted


class Overlay:
    def __init__(self):
        self.reader = create_reader(interval=0.5)
        self.hotkeys = hotkeys.create_listener()
        self.meter = meter_mod.create_meter()
        self.beam = None
        self._beam_at = None
        self.tray = tray.create_tray(autostart=autostart.enabled(),
                                     can_autostart=autostart.available())
        self.lyrics: Lyrics | None = None
        self.track_key = ""
        self.fetch_gen = 0
        self._worker_results: queue.SimpleQueue[WorkerResult] = queue.SimpleQueue()
        self.offset = config.saved_offset()
        self.line_index = -1

        self._views: dict[int, LineView] = {}
        self._glides: dict[int, motion.Glide] = {}
        self._targets: dict[int, float] = {}
        self._relay_hold: set[int] = set()
        self._header_text = None
        self._dragging = False
        self._drag_at = (None, None)
        self._press_at = (0, 0)
        self._press_y = 0
        self._moved = False
        self._pending_art = None
        self._shape_gen = 0
        self._identified = artwork.Release()
        self._cover_data = None     # kept so a resize can re-derive both images
        self._backdrop_item = None
        self._backdrop_photo = None
        self._thumb_image = None
        self._thumb_photo = None
        self._card_text = None
        self._card_measured = None
        self._card_width = 0
        self._card_raw = None
        self._awaiting_seek = None
        self._hidden = False
        self._closing = False
        self._lyrics_state = LYRICS_UNKNOWN
        self._compact = False
        self._collapse = None
        self._views_width = 0
        self._echo = None
        self._echo_line = -1
        self._echo_side = 0
        self._echo_origin_lean = 0.0
        self._echo_target_lean = 0.0
        self._echo_words: list = []
        self._echo_fade_s = ECHO_FADE_S
        self._echo_opens = 0.0
        self._echo_ends = 0.0
        self._echo_blocked: tuple | None = None
        self._feather = config.sweep_feather()
        self._bloom = config.bloom_factor()
        self._growth = config.growth_factor()
        self._sides: dict = {}
        self._sides_of: Lyrics | None = None
        # A step, not an alignment: the reference pushes one voice to each edge,
        # which works in a full-height view and does not here, because this
        # panel's width comes from its own longest line — a short line aligned
        # outright ends up against the margin, and the eye crosses the whole
        # panel every time the singers trade.
        self._voice_step = config.voice_step()
        self._shown = Track(searched=True, lyrics_state=LYRICS_ABSENT)
        self._loading = self._shown
        self._fetching_key = ""
        self._cuts = sponsorblock.Cuts()
        self._cuts_checked = None
        self._cuts_discontinuous = False
        self._cut_fade_at: float | None = None
        self._outgoing_fade_at: float | None = None
        self._lyrics_fade_at: float | None = None
        self._lyrics_reveal_pending = False

        # Before Tk exists: Tk reads the display metrics when the root window is
        # created, so declaring DPI awareness afterwards leaves it holding
        # virtualised numbers and every geometry it reports is off by the scale.
        # The display's own scale, times whatever size the user asked for. They
        # multiply because they are the same kind of quantity: both say how many
        # device pixels a designed unit is worth, and folding them together here
        # means every measurement downstream is scaled once, by one number.
        self._dpi_scale = chrome_mod.prepare()
        self._size = config.size_scale()
        scale_hint = self._dpi_scale * self._size

        self.root = tk.Tk()
        self.root.title("Lyrica")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Chrome decides how the window composites, and everything visual
        # follows from that: glass adds light and needs no outline, keyed
        # replaces colour and cannot survive without one.
        self.chrome = chrome_mod.setup(self.root, scale_hint)
        self.palette = palette_mod.for_chrome(self.chrome)
        scale = self.chrome.scale

        self.width = self.chrome.px(WIDTH)
        self.height = self.chrome.px(HEIGHT)
        self.wrap = self.chrome.px(WRAP)
        self.row_gap = self.chrome.px(ROW_GAP)
        self.f_title = _scaled_font(FONT_TITLE, scale)
        self.f_artist = _scaled_font(FONT_ARTIST, scale)
        self.f_line = _scaled_font(FONT_LINE, scale)
        self.f_echo = _scaled_font(FONT_ECHO, scale)
        # Made once. Creating a Font is a round trip into Tk, and these were
        # being rebuilt on every tick just to measure a string.
        self._title_font = tkfont.Font(font=self.f_title)
        self._artist_font = tkfont.Font(font=self.f_artist)
        self.anchor_y = self.height * ANCHOR

        # Clamped rather than computed and trusted: screen metrics and window
        # size can disagree about whether they are logical or device pixels, and
        # the failure is the overlay sitting half off the bottom of the screen.
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self._start_x = max(0, (sw - self.width) // 2)
        self._start_y = max(0, min(sh - self.height - self.chrome.px(80),
                                   sh - self.height))
        # Where it was left, if it was left anywhere reachable. Clamped rather
        # than trusted: a position saved on a monitor since unplugged is a
        # window nobody can see and nobody can drag back. Across the whole
        # desktop, not the primary screen, or a second monitor is unreachable.
        dleft, dtop, dwidth, dheight = chrome_mod.desktop_bounds(self.root)
        place, centre = config.saved_place(), config.saved_centre()
        if place is not None:
            wanted = (place[0] - self.width // 2, place[1])
        elif centre is not None:
            # Written before the vertical anchor was corrected; read the way it
            # was written so an upgrade keeps the position it had.
            wanted = (centre[0] - self.width // 2, centre[1] - self.height // 2)
        else:
            wanted = None
        if wanted is not None:
            self._start_x = max(dleft, min(wanted[0], dleft + dwidth - self.width))
            self._start_y = max(dtop, min(wanted[1], dtop + dheight - self.height))
        # The user's stable position is a horizontal centre and a top edge.
        # Size changes derive geometry from this anchor; they do not overwrite
        # it with the centre of whichever size happened to be visible last.
        self._place_anchor = (
            self._start_x + self.width // 2, self._start_y)
        self.root.geometry(chrome_mod.geometry(
            self.width, self.height, self._start_x, self._start_y))

        self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                bg=self.chrome.background,
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)

        # Only now that the window is its final size: the region is in device
        # pixels and clips whatever it was applied to.
        self.root.update_idletasks()
        chrome_mod.shape(self.root, self.chrome, self.width, self.height)

        self._build_frame()
        self._bind()

    def _bind(self):
        for widget in (self.root, self.canvas):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Button-3>", lambda e: self._close())
        self.root.bind("<Escape>", lambda e: self._close())
        self.root.bind("<plus>", lambda e: self._nudge(+0.25))
        self.root.bind("<minus>", lambda e: self._nudge(-0.25))
        # A whole second, for the scale a video intro works at.
        self.root.bind("<Control-plus>", lambda e: self._nudge(+1.0))
        self.root.bind("<Control-minus>", lambda e: self._nudge(-1.0))
        self.root.bind("<Return>", lambda e: self._align())
        # Size. These are the fallback: they need the overlay focused, which
        # means clicking it, which seeks. The global shortcuts are what anyone
        # actually uses on Windows; this is what a platform without them gets.
        # Several spellings per direction because which one arrives depends on
        # the keyboard — `plus` needs Shift on most layouts, so `equal` is the
        # key under the finger, and the numeric pad sends its own names again.
        for sequence in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.root.bind(sequence, lambda e: self._resize(+SIZE_STEP))
        for sequence in ("<Control-minus>", "<Control-underscore>",
                         "<Control-KP_Subtract>"):
            self.root.bind(sequence, lambda e: self._resize(-SIZE_STEP))
        self.root.bind("<Control-0>", lambda e: self._resize_to(1.0))

    ACTIONS: ClassVar[dict] = {
        "toggle": lambda self: self._toggle_visible(),
        "quit": lambda self: self._close(),
        "bigger": lambda self: self._resize(+SIZE_STEP),
        "smaller": lambda self: self._resize(-SIZE_STEP),
        "reset": lambda self: self._resize_to(1.0),
        "autostart": lambda self: self._toggle_autostart(),
    }

    def _close(self) -> None:
        """Ask for the overlay to go away, without pulling it out from under us.

        Destroying the root where the request arrives is what a shortcut or a
        tray click used to do, and both are drained from inside the tick — so
        the rest of that tick carried on measuring text and recolouring a canvas
        that no longer existed, throwing TclError from an `after` callback. The
        request is recorded here and acted on at the top of the loop, where
        nothing follows it.
        """
        self._closing = True

    def _drain_actions(self) -> None:
        """Act on whatever the shortcuts and the tray icon asked for.

        Both name the same actions, so neither has to be told what the other
        can do. Drained on the tick rather than delivered from the threads that
        produced them: everything these touch is a Tk widget, and Tk is only
        safe on the thread that made it.
        """
        for source in (self.hotkeys, self.tray):
            for action in source.poll():
                handler = self.ACTIONS.get(action)
                if handler:
                    handler(self)

    def _toggle_autostart(self) -> None:
        state = autostart.set_enabled(not autostart.enabled())
        self.tray.set_autostart(state)

    def _build_frame(self):
        """The parts that never move: the card.

        There is no edge highlight. It was built on the belief that this
        surface only ever adds light, so a dim grey could not darken anything.
        Sampling the actual pixels disproved that: against a plate reading 58,
        the highlight's own rows measured 67, 50 and 35 — the lower two are
        darker than what they sit on, which is why the top edge looked like a
        badly drawn black line rather than a soft one.

        Correcting the levels would have kept a decoration that has now caused
        two visible faults. Removing it is the smaller thing on screen and the
        smaller thing to explain.
        """
        # The card: cover, title beside it, artists under the title. Laid out
        # left to right but centred as a group, so it stays put while its own
        # contents change width from song to song.
        self._card_y = self.chrome.px(14)
        self._thumb_size = self.chrome.px(THUMB_SIZE)
        self._thumb_item = self.canvas.create_rectangle(
            0, 0, 0, 0, outline="", fill="")
        self._title_item = self.canvas.create_text(
            0, 0, text="", anchor="w", font=self.f_title, fill=self.palette.title)
        self._artist_item = self.canvas.create_text(
            0, 0, text="", anchor="w", font=self.f_artist, fill=self.palette.artist)

        # Laid before the card and the lines so it can never sit on top of a
        # word; it lives at the very edge, where nothing else is drawn.
        style = config.beam_style()
        if style != "off" and self.chrome.washed:
            self.beam = beam_mod.Beam(self.canvas, self.width, self.height,
                                      self.chrome.px(chrome_mod.CORNER_RADIUS),
                                      self.chrome.scale, style,
                                      config.beam_intensity())

        # Lyrics must be gone before they reach the card. Without this the
        # outermost line arrives at the top still faintly visible and overlaps
        # it, and the card is the one thing on screen that never moves.
        self._content_top = self._card_y + self._thumb_size + self.chrome.px(12)

    def _lay_out_card(self, title: str, artists: str) -> None:
        """Place the card's parts and centre the group."""
        gap = self.chrome.px(10)
        title_font, artist_font = self._title_font, self._artist_font
        # Measured once per pair of strings. `measure` is a round trip into Tk
        # for an answer that cannot change while the words do not, and this runs
        # on every frame of a resize.
        key = (title, artists)
        if key != self._card_measured:
            self._card_measured = key
            self._card_width = max(title_font.measure(title),
                                   artist_font.measure(artists))
        # The cover's space is reserved whether or not it has arrived, so the
        # card does not shuffle sideways the moment it does.
        cover = self._thumb_size + gap

        # Centred in the panel, and free to be again. The trembling this went
        # through was never the centring: it was the *window* travelling left to
        # hold its own centre while the card travelled right inside it by
        # exactly as much, two movers applied by two different things that had
        # to cancel in the same repaint. The window holds its left edge through
        # a move now, so the card is the only thing moving and has nothing to
        # cancel against — it simply slides to the middle as the panel opens,
        # and back to the margin as it shuts.
        #
        # Halved the same way the window's is anyway, so the two agree at every
        # width instead of disagreeing by a pixel on the odd ones.
        block = self._thumb_size + gap + self._card_width
        left = max(self.chrome.px(12), self.width // 2 - block // 2)
        top = self._card_y

        self.canvas.coords(self._thumb_item, left, top,
                           left + self._thumb_size, top + self._thumb_size)
        # Placed from the number that was just computed, not read back out of
        # the canvas. Asking where the slot ended up is a round trip whose
        # answer can lag by a frame, which put the cover a step behind the box
        # it belongs in every time the panel was moving.
        if self._thumb_image is not None:
            self.canvas.coords(self._thumb_image, left, top)
        text_x = left + cover
        # Both text rows share the cover's vertical centre, so the pair reads as
        # one block rather than as two lines that happen to sit near a square.
        mid = top + self._thumb_size / 2
        self.canvas.coords(self._title_item, text_x,
                           mid - artist_font.metrics("linespace") / 2)
        self.canvas.coords(self._artist_item, text_x,
                           mid + title_font.metrics("linespace") / 2)

    # --- interaction ---
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._drag_at = (self.root.winfo_x(), self.root.winfo_y())
        self._press_at = (e.x_root, e.y_root)
        self._press_y = e.y
        self._moved = False
        self._dragging = True
        # The blur is *not* dropped here. Doing it on the press meant every
        # click flashed the panel, including clicks meant only to seek — the
        # window changed colour for the length of a tap. It waits until the
        # pointer has actually travelled.

    def _drag_move(self, e):
        x, y = e.x_root - self._dx, e.y_root - self._dy
        if (x, y) == self._drag_at:
            return          # motion events repeat; moving to where we already are stutters
        self._drag_at = (x, y)
        # A hand never holds perfectly still, so a few pixels of travel is still
        # a click. Without the slack, seeking would almost never fire.
        px, py = self._press_at
        if not self._moved and (abs(e.x_root - px) > CLICK_SLACK
                                or abs(e.y_root - py) > CLICK_SLACK):
            self._moved = True
            # Now it is a drag rather than a click, so the blur can go.
            if SUSPEND_GLASS_WHILE_DRAGGING:
                chrome_mod.suspend_effects(self.root, self.chrome, True)
        chrome_mod.move(self.root, x, y)

    def _remember_where(self) -> None:
        """Keep the horizontal middle and the top edge — the two that survive.

        Not the corner, because the window has two widths and a corner is only
        true for the one it was taken at. Not the vertical middle either, and
        that half was wrong until review caught it: a collapse holds the *top*
        so the card does not move, so a middle saved while the panel was
        compact belonged to a 114 px window and reopening at 375 put it 130 px
        too high.
        """
        x, top = self._drag_at
        if x is None or top is None:
            x, top = self.root.winfo_x(), self.root.winfo_y()
        self._place_anchor = (x + self.width // 2, top)
        config.save_place(*self._place_anchor)

    def _drag_end(self, _e):
        was_dragging = self._moved
        self._dragging = False
        if was_dragging and SUSPEND_GLASS_WHILE_DRAGGING:
            chrome_mod.suspend_effects(self.root, self.chrome, False)
        if not self._moved:
            self._seek_to_line_at(self._press_y)
            return
        # Written when the hand stops, not while it moves: a drag issues
        # hundreds of positions and only the last one is a decision.
        self._remember_where()

    def _seek_to_line_at(self, y: int) -> None:
        """Jump to whichever line was clicked, if the player will allow it."""
        lyr = self.lyrics
        if lyr is None or not lyr.synced:
            return
        for index, view in self._views.items():
            if view.y <= y <= view.y + view.height and index < len(lyr.lines):
                target = lyr.lines[index][0] - self.offset
                # The lead exists so a line appears before it is sung; seeking
                # to a line means starting where it starts, so it comes back off.
                # `target` stays in the recording's time because that is what the
                # tick compares against; only the player is told where the video
                # has to be for it.
                if self.reader.seek(max(0.0, self._cuts.to_video(target))):
                    logger.info("seeking to line %d at %.2fs", index, target)
                    # Move now rather than waiting for the next poll, and ignore
                    # the position until it catches up. Without the guard the
                    # view snapped straight back: the reader is still reporting
                    # where playback was, so the very next frame recomputed the
                    # old line and undid the jump, then jumped again half a
                    # second later. That bounce is the delay, not the seek.
                    self._awaiting_seek = (target, time.monotonic())
                    self._go_to_line(index, lyr)
                return

    def _go_to_line(self, index: int, lyr: Lyrics, *, animate: bool | None = None) -> None:
        """Make `index` the current line, animating when the move is small."""
        previous_index = self.line_index
        step = abs(index - previous_index) if previous_index >= 0 else None
        self.line_index = index
        indices = self._visible_indices(len(lyr.lines))
        self._ensure_views(indices, lyr)
        move = should_animate(step, self._dragging) if animate is None else animate
        previous = self._views.get(previous_index)
        active = self._views.get(index)
        self._relay_hold = (
            {previous_index, index}
            if (move and step == 1 and previous is not None and active is not None
                and previous.height > previous.line_height
                and active.height > active.line_height)
            else set())
        self._retarget(indices, animate=move)
        self._restyle(indices)

    def _nudge(self, dt: float):
        self._set_offset(self.offset + dt)

    def _align(self):
        """Take this moment as the song's first line.

        A video with an intro runs ahead of a lyric timeline written for the
        release, and the gap is whole seconds — one measured video was twenty.
        Nothing in the metadata says how many: every synced record for that song
        starts at 00:02.17 whatever duration it claims, so the timeline is the
        release's and the intro is invisible to it. Correcting twenty seconds a
        quarter at a time is eighty keypresses; the position the video is at when
        the first words are sung is the whole answer, in one.
        """
        lyr = self.lyrics
        snap = self.reader.snapshot
        if lyr is None or not lyr.lines or not snap.ok:
            return
        self._set_offset(lyr.lines[0][0] - self._cuts.to_song(snap.live_position()))

    def _set_offset(self, seconds: float):
        self.offset = max(-config.OFFSET_LIMIT_S,
                          min(config.OFFSET_LIMIT_S, seconds))
        config.save_offset(self.offset, self.track_key)

    # --- collapsing to the card ---
    def _card_span(self) -> int:
        """How wide the card's own contents are, cover and text together.

        Measured rather than assumed, because it is the song title that decides
        it and that changes with every track.
        """
        title, artists = self._card_text or ("", "")
        text = max(self._title_font.measure(title),
                   self._artist_font.measure(artists))
        return self._thumb_size + self.chrome.px(10) + text

    def _target_size(self) -> tuple[int, int]:
        """What the window should be, given whether there are lyrics to show.

        Compact hugs the card: its height is the cover plus the same margin
        under it as above, and its width follows the title rather than being
        fixed, so a short name gets a small panel instead of a wide one with
        space nobody is using.
        """
        full = (self.chrome.px(WIDTH), self.chrome.px(HEIGHT))
        if not self._compact:
            return full
        height = self._card_y * 2 + self._thumb_size
        width = self._card_span() + self.chrome.px(12) * 2
        return (max(self.chrome.px(COMPACT_MIN_WIDTH), min(width, full[0])),
                height)

    def _want_compact(self) -> bool:
        # Only on a definite answer. While a fetch is still out the panel keeps
        # whatever size it has, so a track change does not flicker through a
        # collapse on its way to lyrics that were coming all along — nor back
        # out of one on its way to another track that has none either.
        return compact_target(self._lyrics_state, self._compact)

    def _retarget_size(self, animate: bool = True) -> None:
        """Start moving toward the size the current state calls for.

        Driven by the size rather than by the state. Comparing states instead
        was correct about *when* to collapse and blind to the compact panel's
        width, which is measured from the card: two lyric-less tracks in a row
        left the second one wearing the first one's width, so a long title was
        truncated inside a panel sized for a short one.
        """
        self._compact = self._want_compact()
        target = self._target_size()
        if self._collapse is not None and self._collapse[2:4] == target:
            # Already on its way there. Re-arming would rebuild the animation
            # with a fresh start time on every tick, which pins it at its first
            # frame and leaves the window exactly where it began.
            return
        if (self.width, self.height) == target:
            self._collapse = None
            return
        if not animate:
            self._collapse = None
            self._resize_window(*target)
            return
        # The left edge, held for the whole move, and taken once. Holding the
        # centre meant the window travelled left while the card travelled right
        # inside it by exactly as much — two movers, applied by two different
        # things, that have to cancel in the same repaint to look still. The
        # arithmetic cancelled and the repaints did not, and the card shook.
        # Nothing cancels now: the window stays where it is and unfolds to the
        # right, and the only thing moving is the card finding its place.
        self._collapse = (self.width, self.height, *target, time.monotonic(),
                          self.root.winfo_x(), self.root.winfo_y())
        # One region for the whole move, big enough for either end of it. A
        # region wider than the window clips nothing, so the panel is never cut
        # short of itself; the corners are square while it travels and rounded
        # again the moment it lands.
        chrome_mod.shape(self.root, self.chrome,
                         max(self.width, target[0]), max(self.height, target[1]))

    def _advance_collapse(self) -> bool:
        """Move one frame along the collapse. True while it is still going."""
        if self._collapse is None:
            return False
        from_w, from_h, to_w, to_h, started, left, top = self._collapse
        elapsed = (time.monotonic() - started) * 1000
        done = elapsed >= COLLAPSE_MS
        t = motion.cubic_bezier(1.0 if done else elapsed / COLLAPSE_MS,
                                motion.RESIZE_CURVE)
        self._resize_window(round(from_w + (to_w - from_w) * t),
                            round(from_h + (to_h - from_h) * t), settling=done,
                            anchor=(left, top))
        if not done:
            return True
        self._collapse = None
        return False

    def _resize_window(self, width: int, height: int,
                       settling: bool = True,
                       anchor: tuple | None = None) -> None:
        """Put the window at a size, keeping the card exactly where it is.

        The top edge and the horizontal centre are held. The card lives at the
        top, so anchoring there means the one thing still on screen does not
        move while everything below it goes away.
        """
        if (width, height) == (self.width, self.height):
            return
        # A move in progress passes its own anchor: the left edge it started
        # from, held for the whole of it. Anything else is a one-off, and keeps
        # the horizontal centre — a panel that changes size once should stay
        # where it looked like it was.
        if anchor is None:
            keep = self.root.winfo_x() + self.width // 2 - width // 2
            top = self.root.winfo_y()
        else:
            keep, top = anchor
        self.width, self.height = width, height
        self.anchor_y = self.height * ANCHOR
        # The whole desktop, not the primary monitor. Clamping to
        # `winfo_screenwidth` teleported the panel back from a second screen
        # every time a track with no lyrics collapsed it — measured here, a
        # 4480 px desktop against a 1920 px primary.
        edge, dtop, dwidth, _dheight = chrome_mod.desktop_bounds(self.root)
        x = max(edge, min(keep, edge + dwidth - width))
        top = max(dtop, top)
        self.root.geometry(chrome_mod.geometry(width, height, x, top))
        self.canvas.configure(width=width, height=height)
        # The border is rebuilt every frame, because it is geometry and not
        # decoration: its segments are laid out for a particular width, so one
        # left behind stays drawn around the panel's old outline while the panel
        # grows away from it — which reads as the border sliding left. It costs
        # about a millisecond and a half, which is affordable; the two below
        # were what took a resize frame to 30.8 ms against a budget of 16.
        if self.beam is not None:
            self.beam.reshape(width, height,
                              self.chrome.px(chrome_mod.CORNER_RADIUS))
        if settling:
            # `SetWindowRgn` repaints the whole window synchronously and is most
            # of that cost on its own. It cannot simply be left stale either,
            # because it is what the window is *clipped to*: held at the shape
            # the panel was leaving, it showed a small box at the new left edge
            # that snapped open on landing. A region larger than the window
            # clips nothing, so `_retarget_size` sets one big enough for the
            # whole move before it starts, and this puts the exact one back.
            chrome_mod.shape(self.root, self.chrome, width, height)
            self.root.update_idletasks()
        title, artists = self._card_text or ("", "")
        self._lay_out_card(title, artists)
        self._place_thumb()
        # The anchor moved, so the lines have to. `_retarget` is otherwise only
        # reached from `_go_to_line`, so a column placed against the compact
        # anchor at the start of an expansion stayed there for the whole growth
        # — the active line drawn over the card, its neighbours above
        # `_content_top` and painted invisible, until the next lyric arrived.
        if self._views and self.line_index in self._views:
            self._retarget(sorted(self._views), animate=False)

    # --- showing and hiding ---
    def _toggle_visible(self) -> None:
        """Put the overlay away, or bring it back.

        Hidden rather than closed. `Esc` and right click destroy the window,
        which is the right answer for "I am done" and the wrong one for "not
        during this call" — there is no way back from it but relaunching.
        """
        self._hidden = not self._hidden
        if self._hidden:
            self.root.withdraw()
            chrome_mod.hold_timer_resolution(False)
            logger.info("overlay hidden")
            return

        chrome_mod.hold_timer_resolution(True)
        self.root.deiconify()
        # Re-asserted rather than assumed. Mapping a window again puts it back
        # in the z-order as an ordinary one, so without this it returns *behind*
        # whatever was in front — which looks exactly like the shortcut having
        # done nothing at all.
        self.root.attributes("-topmost", True)
        self.root.update_idletasks()
        chrome_mod.shape(self.root, self.chrome, self.width, self.height)
        # Nothing was drawn while it was away, so whatever line is playing now
        # is not the one on screen. Forgetting which it was makes the next tick
        # place them without animating, rather than gliding through however many
        # lines went past while nobody was looking.
        self.line_index = -1
        logger.info("overlay shown")

    # --- size ---
    def _resize(self, delta: float) -> None:
        self._resize_to(self._size + delta)

    def _resize_to(self, size: float) -> None:
        size = round(config.clamp_size(size), 2)
        if abs(size - self._size) < 1e-6:
            return          # already at the limit; rebuilding would only flicker
        self._size = size
        config.save_size(size)
        self._apply_scale()

    def _apply_scale(self) -> None:
        """Rebuild every measurement against the new scale.

        Everything the layout knows is derived from `chrome.px()` and the
        scaled fonts, so this recomputes exactly the same things `__init__`
        did — which is the reason it can be this short, and the reason to keep
        the two lists next to each other if either ever grows.
        """
        scale = self._dpi_scale * self._size
        self.chrome = replace(self.chrome, scale=scale)

        old_centre = (self.root.winfo_x() + self.width // 2,
                      self.root.winfo_y() + self.height // 2)
        place = getattr(
            self, "_place_anchor",
            (self.root.winfo_x() + self.width // 2, self.root.winfo_y()))
        self.wrap = self.chrome.px(WRAP)
        self.row_gap = self.chrome.px(ROW_GAP)
        self.f_title = _scaled_font(FONT_TITLE, scale)
        self.f_artist = _scaled_font(FONT_ARTIST, scale)
        self.f_line = _scaled_font(FONT_LINE, scale)
        self.f_echo = _scaled_font(FONT_ECHO, scale)
        self._title_font = tkfont.Font(font=self.f_title)
        self._artist_font = tkfont.Font(font=self.f_artist)
        self._card_y = self.chrome.px(14)
        self._thumb_size = self.chrome.px(THUMB_SIZE)
        self._content_top = self._card_y + self._thumb_size + self.chrome.px(12)
        # After the fonts and the cover, because a compact panel's width is
        # measured from the card and both have just changed. A collapse in
        # flight is abandoned: it was interpolating toward a size from the old
        # scale, and its destination no longer exists.
        self._collapse = None
        self.width, self.height = self._target_size()
        self.anchor_y = self.height * ANCHOR

        # Grow or shrink from the last place the user chose: horizontal centre
        # and top edge. The old on-screen centre selects the current monitor;
        # the saved place remains the anchor even when a larger size has to be
        # temporarily clamped at an edge.
        x, y = chrome_mod.place_in_monitor(
            self.root, place, (self.width, self.height), old_centre)
        self.root.geometry(chrome_mod.geometry(self.width, self.height, x, y))
        self.canvas.configure(width=self.width, height=self.height)
        # After the geometry, never before: the clip region is in device pixels
        # and does not track the window, so applying it early clips the window
        # to whatever size it used to be.
        self.root.update_idletasks()
        chrome_mod.shape(self.root, self.chrome, self.width, self.height)
        # The ring is laid out once for a size and only recoloured after that,
        # so a scale change leaves it tracing the previous window: inset well
        # inside the panel after growing, clipped off the edge after shrinking.
        if self.beam is not None:
            self.beam.reshape(self.width, self.height,
                              self.chrome.px(chrome_mod.CORNER_RADIUS))

        self.canvas.itemconfigure(self._title_item, font=self.f_title)
        self.canvas.itemconfigure(self._artist_item, font=self.f_artist)
        # Both card caches, and the second one is not optional. The card is
        # only laid out again when its *text* changes, so invalidating the
        # fitted text alone leaves a resize where the text happens to come out
        # identical — which is most of them — holding its old coordinates while
        # the cover is rebuilt at the new size and placed into them. Measured at
        # 1.4x: the title started 20 px inside the cover.
        self._card_raw = None
        self._card_text = None

        # The lines cannot be resized in place: a LineView lays its characters
        # out once, at the font and wrap width it was built with. Dropping them
        # and forgetting which line was current makes the next tick rebuild and
        # place them without animating, which is what a resize should look like.
        self._clear_backing()
        for view in self._views.values():
            view.destroy()
        self._views.clear()
        self._glides.clear()
        self._targets.clear()
        self._relay_hold.clear()
        self.line_index = -1

        # The cover has to be re-derived: both images were built for the old
        # window. Inline rather than on a thread — it measures ~18 ms, and this
        # is a keypress rather than a track change, so there is a hand waiting
        # for it and nothing else in flight.
        self._reshape_art()
        logger.info("overlay size %.2f (%dx%d)", self._size, self.width, self.height)

    # --- lyrics ---
    def _track_for_result(self, gen: int) -> Track | None:
        """The still-relevant track a worker generation belongs to."""
        if self._loading.gen == gen:
            return self._loading
        return None

    def _queue_art_build(self, track: Track, data: bytes) -> None:
        """Rasterise known cover bytes for the current window shape."""
        shape_gen = self._shape_gen

        def work():
            art = self._build_art(data)
            self._worker_results.put(WorkerResult(
                track.gen, "art", art, shape_gen=shape_gen))

        threading.Thread(target=work, daemon=True).start()

    def _drain_worker_results(self) -> None:
        """Commit background answers on the only thread that renders them."""
        while True:
            try:
                result = self._worker_results.get_nowait()
            except queue.Empty:
                return
            track = self._track_for_result(result.gen)
            if track is None:
                continue

            if result.kind == "lyrics":
                track.lyrics = result.value
                track.lyrics_state = lyrics_state(result.value)
                continue

            if result.kind == "cuts":
                found = result.value
                track.cuts = found
                # Do not disturb the deliberately frozen outgoing scene once a
                # newer track is assembling. If this is still the current song,
                # the corrected clock is consumed below without a row glide.
                if track is self._shown and result.gen == self.fetch_gen:
                    changed = found.spans != self._cuts.spans
                    self._cuts, self._cuts_checked = found, None
                    self._cuts_discontinuous = self._cuts_discontinuous or changed
                continue

            if result.kind == "artwork":
                answer = result.value
                track.identified = answer.identified
                track.cover = answer.cover
                track.searched = True
                if result.shape_gen == self._shape_gen:
                    track.art = answer.art
                elif answer.cover:
                    # The fetch is still useful; only its raster dimensions are
                    # stale. Rebuild from the bytes rather than throwing the
                    # whole late answer away after a resize.
                    track.art = None
                    self._queue_art_build(track, answer.cover)

                if track is self._shown and result.gen == self.fetch_gen:
                    self._identified = answer.identified
                    self._cover_data = answer.cover
                    self._card_raw = None
                    if result.shape_gen == self._shape_gen and answer.art is not None:
                        self._offer_art(result.gen, result.shape_gen, answer.art)
                    elif answer.cover is None:
                        self._pending_art = None
                        self._forget_cover()
                continue

            if result.kind == "art" and result.shape_gen == self._shape_gen:
                track.art = result.value
                if track is self._shown and result.gen == self.fetch_gen:
                    self._offer_art(result.gen, result.shape_gen, result.value)
            elif result.kind == "art" and track.cover:
                # It crossed another resize while being built. Keep chasing the
                # current shape off-thread; each stale result schedules at most
                # one successor, so there is never an unbounded fan-out.
                self._queue_art_build(track, track.cover)

    def _start_fetch(self, snap: Snapshot):
        """Begin assembling the track that is now playing.

        Nothing on screen changes here. Workers return generation-stamped
        events, the Tk thread commits them to this track, and the tick puts it
        up in one move once it is whole — which is what lets the outgoing song
        keep its last line and makes a half-changed panel impossible rather
        than unlikely.
        """
        # A rapid second skip may interrupt either presentation fade. Restore
        # the frame's true colours before freezing it, and never let the first
        # incoming track's expired deadline promote the second one early.
        if self._lyrics_reveal_pending:
            # These glyphs are already indistinguishable from the wash. Drop
            # them without first making them flash into view as outgoing text.
            self._clear_views()
            self.line_index = -1
        elif (self._outgoing_fade_at is not None or self._lyrics_fade_at is not None
              or self._cut_fade_at is not None):
            self._fade_text_scene(1.0)
        self._outgoing_fade_at = None
        self._lyrics_fade_at = None
        self._lyrics_reveal_pending = False
        self._cut_fade_at = None
        self.fetch_gen += 1
        self._shown.scene = SCENE_OUTGOING
        loading = Track(gen=self.fetch_gen, snapshot=snap,
                        offset=config.saved_offset(snap.track_key()),
                        scene=SCENE_PREPARING)
        loading.deadline = time.monotonic() + REVEAL_WAIT_S
        self._loading = loading

        def work():
            found = fetch_for_candidates(snap.lookup_candidates(), snap.duration,
                                         snap.album)
            self._worker_results.put(WorkerResult(loading.gen, "lyrics", found))

        threading.Thread(target=work, daemon=True).start()
        self._start_artwork(loading)
        self._start_cuts(loading)

    def _start_cuts(self, loading: "Track") -> None:
        """Find out which parts of this video are not the song.

        A lyric timeline is anchored to the release; a music video opens with a
        film or a spoken intro and the words arrive whole seconds late. This is
        the only thing in the process that knows how many, and it knows because
        somebody wrote it down.

        Not waited for. A track whose answer arrives late is a track whose
        lyrics jump once into place, which is better than a card held back.
        """
        snap = loading.snapshot
        if not snap.is_browser or not youtube.api_key():
            return
        title, duration = snap.title, snap.duration

        def work():
            video_id = youtube.video_id_for(title, duration)
            if not video_id:
                return
            found = sponsorblock.cuts_for(video_id)
            if found is None:
                return
            self._worker_results.put(WorkerResult(loading.gen, "cuts", found))
            if found.intro:
                logger.info("%s opens with %.1fs that is not the song",
                            video_id, found.intro)

        threading.Thread(target=work, daemon=True).start()

    def _start_artwork(self, loading: "Track") -> None:
        """Fetch and prepare the cover off the render thread.

        Decoding an image takes long enough to drop frames if it happened
        inline, and it only needs doing once a track.
        """
        if not artwork.available():
            loading.searched = True
            return

        snap = loading.snapshot
        candidates = snap.lookup_candidates()
        album = snap.album
        shape_gen = self._shape_gen

        def work():
            # One cover, shown once. Fetching the good one first and only
            # falling back to the player's own means nothing is ever replaced on
            # screen. Showing the local thumbnail immediately was faster, but
            # the swap to the sharp one was visible — and a cover that changes
            # under you reads worse than one that arrives a moment late.
            wanted = max(600, self._thumb_size * 4)
            data = (artwork.best_cover_for_candidates(candidates, album, size=wanted)
                    or self.reader.read_artwork())
            # After the cover, because the search both need has then run and its
            # answer is on disk. Kept even when no cover was found: a track can
            # be named by a catalogue that has no picture of it.
            named = artwork.identify(candidates, album)
            if named:
                logger.info("catalogue: %s - %s (%s)", named.artist, named.title,
                            named.album or "no album")
            identified = named or artwork.Release()
            art = self._build_art(data) if data else None
            # Last, and whatever the outcome. Saying the search is over is not
            # the same as saying it found something, and conflating the two is
            # what let a track with no cover of its own keep the last one's for
            # as long as it played.
            self._worker_results.put(WorkerResult(
                loading.gen, "artwork", ArtworkResult(identified, data, art),
                shape_gen=shape_gen))

        threading.Thread(target=work, daemon=True).start()

    def _offer_art(self, gen: int, shape: int, art: tuple) -> None:
        """Take images built off the render thread, if they are still current.

        Two ways they may not be. The track can have changed, which `gen`
        catches; and the window can have been resized while they were being
        built, which `shape` catches — those images are the wrong size and the
        resize has already built the right ones.
        """
        if gen == self.fetch_gen and shape == self._shape_gen:
            self._pending_art = art

    def _reshape_art(self) -> None:
        """Rebuild the cover for the size the window has now.

        Bumping the generation is the point as much as the rebuild: the artwork
        worker may be part-way through building images for the size the window
        had a moment ago, and without a mark to check it would land after this
        one and put them back.
        """
        self._shape_gen += 1
        if self._cover_data:
            self._pending_art = self._build_art(self._cover_data)

    def _build_art(self, data: bytes) -> tuple:
        """Everything derived from one cover, at the current size.

        Kept as a method rather than a closure because a resize needs it too:
        both images are built for a particular window, so growing the window
        means deriving them again from the same bytes.
        """
        return (
            artwork.make_thumbnail(data, self._thumb_size),
            # The backdrop only makes sense where the panel has a body to wash;
            # over a colour key it would be a dark rectangle.
            # Always at the panel's full size, never at whatever it is right
            # now. The wash is clipped by the window, so an oversized one costs
            # nothing — where one built while compact leaves bare panel showing
            # for the whole of the next expansion, which is the flicker.
            artwork.make_backdrop(data, self.chrome.px(WIDTH),
                                  self.chrome.px(HEIGHT))
            if self.chrome.washed else None,
            # Measured here rather than on the render thread: it costs a couple
            # of milliseconds, and whichever thread calls this has them.
            songcolour.extract(data),
        )

    def _apply_art(self) -> None:
        """Put prepared images on the canvas, on the render thread."""
        if self._pending_art is None or self._dragging:
            # Measured at 1.0 ms for the backdrop and 0.02 ms for the cover, so
            # this is not the guard against stutter it was once written as —
            # the old comment claimed 15 ms. It stays because it costs nothing:
            # a cover that waits for the hand to stop is never noticed, and the
            # decoding it waits on already happened off this thread.
            return
        thumb, backdrop, song = self._pending_art
        self._pending_art = None
        try:
            from PIL import ImageTk
        except ImportError:
            return

        # Held, not just drawn: Tk keeps only a weak claim on an image, so
        # dropping the reference blanks it.
        if backdrop is not None:
            photo = ImageTk.PhotoImage(backdrop.image)
            if self._backdrop_item is not None:
                self.canvas.delete(self._backdrop_item)
            self._backdrop_item = self.canvas.create_image(0, 0, image=photo, anchor="nw")
            self.canvas.tag_lower(self._backdrop_item)
            self._backdrop_photo = photo

        # The palette follows the cover, and the fade follows the wash the cover
        # actually left on the glass — a line dims into what is behind it rather
        # than toward a level guessed once and applied to every song.
        self._adopt_palette(palette_mod.for_song(
            self.chrome, song, backdrop.colour if backdrop else (0, 0, 0)))

        if thumb is not None:
            photo = ImageTk.PhotoImage(thumb)
            if self._thumb_image is not None:
                self.canvas.delete(self._thumb_image)
            self._thumb_image = self.canvas.create_image(0, 0, image=photo, anchor="nw")
            self._thumb_photo = photo
        else:
            if self._thumb_image is not None:
                self.canvas.delete(self._thumb_image)
            self._thumb_image = None
            self._thumb_photo = None
        # Put the cover where the layout already says it goes. Waiting for the
        # card text to change would leave it at the origin whenever a new cover
        # arrives for the same song — which is most of the time, since the
        # cover is fetched after the title is already on screen.
        self._place_thumb()

    def _adopt_palette(self, pal) -> None:
        """Switch every coloured thing over to a new palette.

        The glyphs snap rather than cross-fading. A fade would have to run for
        several hundred milliseconds over every character on screen, and it
        would land in the middle of a sweep — where a colour drifting under the
        front is far more noticeable than one that simply changed while the
        cover was appearing anyway.
        """
        if pal is self.palette:
            return
        self.palette = pal
        logger.debug("palette hue=%.0f strength=%.2f sweep dE=%.1f",
                     pal.hue, pal.strength, pal.sweep_de)
        # The backing line is coloured from the palette too, and was not being
        # switched over: it kept the outgoing song's tint after the new cover
        # had already repainted everything else.
        self._clear_backing()
        self.canvas.itemconfigure(self._title_item, fill=pal.title)
        self.canvas.itemconfigure(self._artist_item, fill=pal.artist)
        for view in self._views.values():
            view.set_palette(pal)
        self._restyle()

    def _place_thumb(self) -> None:
        """Move the cover image onto the slot the layout reserved for it."""
        if self._thumb_image is None:
            return
        box = self.canvas.coords(self._thumb_item)
        if len(box) >= 2:
            self.canvas.coords(self._thumb_image, box[0], box[1])

    def _promote(self, scene: str | None = None, *, fade_lyrics: bool = False) -> None:
        """Put the assembled track up — all of it, in one assignment each.

        The panel used to switch four times: the card on one clock, the lyric
        column on another, the panel's size on a third and the collapse on a
        fourth. Everything a song owns changes here and nowhere else, so there
        is no interval in which two songs are on screen at once.
        """
        track = self._loading
        track.scene = scene or (
            SCENE_CARD_ONLY if track.lyrics_state == LYRICS_UNKNOWN else SCENE_READY)
        self._shown = track
        self.track_key = track.snapshot.track_key()
        self.offset = track.offset
        # Also read afresh from `_shown` on every tick, because an answer can
        # land after the promotion. Set here as well so the size and the column
        # are right on the frame the song goes up, rather than on the next one.
        self.lyrics = track.lyrics
        self._lyrics_state = track.lyrics_state
        self._identified = track.identified
        self._cuts, self._cuts_checked = track.cuts, None
        self._cover_data = track.cover
        self._pending_art = track.art
        # A seek belongs to the song it was made on. Left behind, the target of
        # a click near the end of one track drove the next one's clock and did
        # not converge until that track had played as long.
        self._awaiting_seek = None
        self._cuts_discontinuous = False
        self._cut_fade_at = None
        self._outgoing_fade_at = None
        # From a card-only panel the window must make room first. Building the
        # rows while it expands is useful — they track the changing anchor —
        # but revealing them at the same time makes the text look attached to
        # the stretching edge. Keep it in the wash until expansion has landed.
        expanding_for_lyrics = (
            track.lyrics_state == LYRICS_PRESENT and self._compact)
        self._lyrics_reveal_pending = expanding_for_lyrics
        self._lyrics_fade_at = (
            time.monotonic() if fade_lyrics and not expanding_for_lyrics else None)
        self._clear_views()
        self.line_index = -1
        self._card_raw = None       # so the card re-derives against the new song
        if track.art is None:
            self._forget_cover()
        logger.info("now showing %s", self.track_key)

    def _forget_cover(self) -> None:
        """Drop the last song's cover, for a song that has none of its own.

        Its own case rather than a silent absence. Leaving the images up was
        indistinguishable from a slow answer, so a track no catalogue has wore
        the previous track's sleeve, wash, palette and border for as long as it
        played.
        """
        if self._backdrop_item is not None:
            self.canvas.delete(self._backdrop_item)
            self._backdrop_item = self._backdrop_photo = None
        self._thumb_image = self._thumb_photo = None
        self._place_thumb()
        self._adopt_palette(palette_mod.for_song(self.chrome, None))

    def _ready_to_show(self) -> bool:
        """Whether this track's card may go up yet.

        The three things that make up a card arrive at three different times.
        Measured cold, from a track change: the player's own words are there
        within a tick, the cover and the catalogue's name land together at about
        a second, and the lyrics at about one and a half. So the panel used to
        put up a name, change it to a different name a second later, and grow a
        thumbnail beside it — three separate events for one song starting.

        Held until they can go up together instead. The previous track stays on
        screen for that second rather than the panel blanking, because it was
        true a moment ago and a gap reads as a fault. Warm, all of this is about
        ten milliseconds and nobody sees it at all.

        The deadline is what keeps a source that never answers — no network, no
        cover anywhere — from holding the panel on the last song forever.
        """
        return Overlay._promotion_scene(self) is not None

    def _promotion_scene(self) -> str | None:
        """The incoming scene that may be shown now, or None while preparing."""
        if self._loading.whole:
            return SCENE_READY
        if time.monotonic() < self._loading.deadline:
            return None
        if self._loading.lyrics_state == LYRICS_UNKNOWN:
            return SCENE_CARD_ONLY
        return SCENE_READY

    def _settle_cuts(self, lyr: Lyrics, snap: Snapshot) -> None:
        """Refuse a set of cuts the recording cannot fit inside the video.

        Checked here rather than where they arrive because it takes the lyrics,
        and the two are fetched in parallel. Once per set: the answer cannot
        change while the track does not.
        """
        if self._cuts_checked is self._cuts or not self._cuts.spans or not lyr.lines:
            return
        end = lyr.lines[-1][0]
        for fewer in (self._cuts, self._cuts.leading(), sponsorblock.Cuts()):
            if fewer.fits(end, snap.duration):
                if fewer.spans != self._cuts.spans:
                    logger.info("cuts %s leave no room for a %.0fs recording in "
                                "a %.0fs video; keeping %s", self._cuts.spans,
                                end, snap.duration, fewer.spans)
                    self._cuts = fewer
                break
        self._cuts_checked = self._cuts

    def _resolved_name(self) -> tuple:
        """What the song turned out to be called, or () while nothing is known.

        Three readings, weakest last. A catalogue that recognised the track
        knows its name; failing that, the reading that found the lyrics is the
        closest thing to one, since naming the card any other way contradicts
        the lyrics on screen — the panel read "BillieEilishVEVO — Billie Eilish
        - CHIHIRO" over a word-timed match found as "Billie Eilish — CHIHIRO";
        failing both, the player's own words are all anyone knows.
        """
        # A catalogue entry first: it is a record of what the track is, where
        # the reading below is only the wording that happened to find it.
        named = self._identified
        if named:
            return (named.artist, named.title)
        lyr = self.lyrics
        queried = getattr(lyr, "queried", ()) if lyr is not None else ()
        return queried if len(queried) == 2 and all(queried) else ()

    def _card_for(self, snap: Snapshot) -> tuple[str, str]:
        # Short-circuited on the raw inputs. Measuring text costs a round trip
        # into Tk, and this runs on every tick where the song changes a few
        # times an hour — a millisecond spent re-deriving an unchanged string
        # is a millisecond mouse events spend queued.
        found = self._resolved_name()
        raw = (snap.ok, snap.artist, snap.title, found, round(self.offset, 2))
        if raw == self._card_raw:
            return self._card_text or ("", "")
        self._card_raw = raw

        if not snap.ok:
            return "", ""
        artist, title = found or snap.norm_artist_title()
        if self.offset:
            title += f"   [{self.offset:+.2f}s]"
        limit = self.wrap - self._thumb_size
        return (self._fit(title, self._title_font, limit),
                self._fit(artist, self._artist_font, limit))

    def _fit(self, text: str, font_obj: tkfont.Font, limit: int) -> str:
        """Shorten text that will not fit, rather than letting it overflow.

        Truncated, not wrapped: a second line would change the card's height
        from song to song, and the card is what everything else is measured
        from.
        """
        if not text:
            return text
        if font_obj.measure(text) <= limit:
            return text
        room = limit - font_obj.measure("…")
        cut = len(text)
        while cut > 0 and font_obj.measure(text[:cut]) > room:
            cut -= 1
        return text[:cut].rstrip() + "…"

    # --- the line pool ---
    def _clear_views(self):
        self._clear_backing()
        for view in self._views.values():
            view.destroy()
        self._views.clear()
        self._glides.clear()
        self._targets.clear()
        self._relay_hold.clear()

    def _visible_indices(self, count: int) -> list[int]:
        if self.line_index < 0:
            return [0] if count else []
        lo = max(0, self.line_index - CONTEXT)
        hi = min(count - 1, self.line_index + CONTEXT)
        return list(range(lo, hi + 1))

    def _ensure_views(self, indices: list[int], lyr: Lyrics) -> None:
        """Create what has come into view, drop what has left it.

        Everything already on screen is left alone — that is the whole point.
        A line that was next becomes the line that is current by being the same
        items in a new place, not by being replaced by a copy of itself.
        """
        for index in list(self._views):
            if index not in indices:
                self._views.pop(index).destroy()
                self._glides.pop(index, None)
                self._targets.pop(index, None)

        sides = self._voice_sides(lyr)
        for index in indices:
            if index in self._views:
                continue
            text = lyr.lines[index][1] if index < len(lyr.lines) else ""
            words = lyr.words_at(index)
            # Born off the bottom when it is arriving from below, so its first
            # movement is the same rise as the lines already on screen.
            start_y = self.height if index > self.line_index else -self.chrome.px(60)
            view = LineView(
                self.canvas, self.width // 2, start_y, text, words,
                font=self.f_line, wrap=self.wrap, palette=self.palette,
                scale=self.chrome.scale, feather=self._feather,
                bloom=self._bloom, growth=self._growth,
                lean=sides.get(lyr.voice_at(index), 0)
                * self.chrome.px(self._voice_step))
            # A row used to be born beyond the window and enter letter by
            # letter. Fading hid most of it, but at intermediate opacity the
            # edge still cut a glyph. Its first position now contains the full
            # maximum-growth box; every later glide lies between safe endpoints.
            view.move_to(self._safe_view_y(view, start_y))
            self._views[index] = view
            self._fit_view(view)
        self._views_width = self.width

    def _voice_sides(self, lyr: Lyrics) -> dict:
        """Which way each of this song's voices leans, worked out once.

        Kept against the object rather than the track, because the lyrics are
        what the answer is about: a song whose words arrive late replaces this
        `Lyrics` and gets asked again, and one that never changes is asked once
        however many thousand ticks it plays for.
        """
        if lyr is not self._sides_of:
            self._sides_of, self._sides = lyr, lyr.voice_sides()
        return self._sides

    def _fit_view(self, view: LineView) -> None:
        """Let a line take its voice's step, as far as the margins allow."""
        if not view.lean:
            return
        margin = self.chrome.px(VOICE_SAFE_MARGIN)
        view.fit(margin, self.width - margin)

    def _safe_view_y(self, view: LineView, wanted: float) -> float:
        """Nearest vertical position whose complete visual box is on-canvas."""
        pad = view.effect_padding
        low = float(pad)
        high = float(self.height - view.height - pad)
        if high < low:
            # A pathological multi-row line taller than the panel cannot be
            # made to fit by choosing an edge. Centre it, which loses equally
            # at both ends instead of silently preferring half of one glyph.
            return (self.height - view.height) / 2
        return max(low, min(float(wanted), high))

    def _show_backing(self, lyr: Lyrics, pos: float, *, effects: bool = True) -> None:
        """Draw what is sung behind the active line, on its lower corner.

        It sits below the line without joining the main column's row stack, so
        its arrival never pushes the lyrics up or down. Horizontally it occupies
        the outer lower corner opposite the lead, close to the line's final
        letters rather than to the panel edge.

        It sweeps on its own timings, which the source gives it, and it has to:
        a backing vocal answers the line while the line is still being sung, and
        the two overlap rather than following one another.
        """
        # Its own window before the current line's. A backing vocal that is
        # still leaving stopped belonging to whichever line is active by then —
        # it was answering the one before, and an ad-lib usually answers the end
        # of a phrase, so the line moves on exactly as it is fading. Tying it to
        # the line is what made it vanish mid-fade instead of sinking away.
        if self._echo is not None and self._advance_backing(pos, effects=effects):
            self._order_text_layers()
            return
        self._clear_backing(preserve_block=True)

        text, words = lyr.backing_at(self.line_index)
        active = self._views.get(self.line_index)
        if not text or not words or active is None or not self._views:
            return
        block_key = (id(lyr), self.line_index, self.width, self.height, self.row_gap)
        if self._echo_blocked == block_key:
            return
        opens, visible_end, fade_s = _backing_window(lyr, self.line_index)
        if not opens <= pos <= visible_end + fade_s:
            return
        active_side = self._voice_sides(lyr).get(lyr.voice_at(self.line_index), 0)
        # The supporting voice answers from the open lane. A centred or
        # unidentified lead keeps the established right-side placement.
        echo_side = -active_side if active_side else 1
        margin = self.chrome.px(ECHO_SAFE_MARGIN)
        available = max(1.0, self.width - 2 * margin)
        echo_font = _font_for_single_row(text, self.f_echo, available)
        # Backing vocals are a single responding voice, never a second lyric
        # block. Keep the designed echo size consistently; only a genuinely
        # long response is reduced enough to remain inside the safe width. Its
        # final size is known now, so canvas glyphs and effects are built once.
        self._echo = LineView(
            self.canvas, self.width // 2, active.y + active.height, text, words,
            font=echo_font, wrap=10**9,
            palette=self.palette.dimmed(ECHO_KEEP),
            scale=self.chrome.scale, bloom=self._bloom, growth=self._growth)
        self._echo_line, self._echo_words = self.line_index, words
        self._echo_fade_s, self._echo_opens, self._echo_ends = fade_s, opens, visible_end
        self._echo_side = echo_side
        active_left, active_right = active._row_spans[-1]
        echo_left = min(a for a, _b in self._echo._row_spans)
        echo_right = max(b for _a, b in self._echo._row_spans)
        echo_width = echo_right - echo_left
        inset = self.chrome.px(ECHO_CORNER_INSET)
        origin = (active_left + active_right) / 2
        target = (active_right - inset + echo_width / 2
                  if echo_side > 0
                  else active_left + inset - echo_width / 2)
        middle = self.width / 2
        self._echo_origin_lean = origin - middle
        self._echo_target_lean = target - middle
        if not self._advance_backing(pos, effects=effects):
            # A very tall lead at the lower edge can leave no honest lane for
            # the response. Missing it is preferable to drawing it through the
            # main lyric or through the following row.
            moving_anchor = self.line_index in self._glides
            self._clear_backing()
            # An incoming row starts near the lower edge and gains room as it
            # glides to the anchor. Retry on the next frame instead of turning
            # that temporary lack of space into a whole-line suppression.
            if not moving_anchor:
                self._echo_blocked = block_key
            return
        self._echo_blocked = None
        self._order_text_layers()

    def _place_backing_y(self, anchor: LineView) -> bool:
        """Centre the echo in the reserved row gap below the lead.

        Return false when canvas clipping or an unusually tall glyph leaves no
        non-overlapping position. The caller then suppresses this response
        instead of allowing the edge clamp to push it over the lead.
        """
        gap = self.chrome.px(ECHO_VERTICAL_GAP)
        lane_top = anchor.y + anchor.height + gap
        lane_bottom = anchor.y + anchor.height + self.row_gap - gap
        if self._echo.height > lane_bottom - lane_top + 0.5:
            return False
        wanted = lane_top
        safe = self._safe_view_y(self._echo, wanted)
        safe_top = safe
        safe_bottom = safe + self._echo.height
        if safe_top < lane_top - 0.5 or safe_bottom > lane_bottom + 0.5:
            return False
        # The lane itself is relative to the lead. A moving following row no
        # longer pulls the response upward through the line it belongs to.
        self._echo.move_to(safe)
        return True

    def _advance_backing(self, pos: float, *, effects: bool = True) -> bool:
        """Carry the backing through its own window. False once it is spent.

        It follows the line it answers for as long as that line is still on
        screen, and holds where it is once the line has gone — which is what
        lets it finish leaving after the column has moved on without it.
        """
        words = self._echo_words
        if not words:
            return False
        opens = self._echo_opens
        closes = self._echo_ends + self._echo_fade_s
        if not opens <= pos <= closes:
            return False
        anchor = self._views.get(self._echo_line)
        if anchor is not None and not self._place_backing_y(anchor):
            return False
        # Enter from the lead towards its corner and settle there with an
        # ordinary ease-in-out. On departure it yields only a quarter of that
        # short journey, so the response remains attached to the phrase.
        if pos < words[0][0]:
            phase = (pos - opens) / self._echo_fade_s
            lane = ECHO_ENTRY_LANE + (1.0 - ECHO_ENTRY_LANE) * motion.cubic_bezier(
                phase, motion.RESIZE_CURVE)
        elif pos > self._echo_ends:
            phase = (pos - self._echo_ends) / self._echo_fade_s
            lane = 1.0 - (1.0 - ECHO_EXIT_LANE) * motion.cubic_bezier(
                phase, motion.RESIZE_CURVE)
        else:
            lane = 1.0
        self._echo.lean = (self._echo_origin_lean
                           + (self._echo_target_lean - self._echo_origin_lean)
                           * lane)
        margin = self.chrome.px(ECHO_SAFE_MARGIN)
        self._echo.fit(margin, self.width - margin)
        pal = self._echo.palette
        if pos < words[0][0]:
            self._echo.set_active(False)
            self._echo.show_inactive(_between(pal.backdrop, pal.unsung,
                                              (pos - opens) / self._echo_fade_s))
        elif pos > self._echo_ends:
            # Struck first, so `set_active(False)` puts back any letter still
            # standing in for itself before the colour takes over.
            self._echo.set_active(False)
            self._echo.show_inactive(_between(pal.backdrop, pal.sung,
                                              (closes - pos) / self._echo_fade_s))
        else:
            self._echo.set_active(True)
            word, fraction = progress_in(words, pos)
            self._echo.show_sweep(word, fraction)
            if effects:
                self._echo.advance_bloom(time.monotonic())
        return True

    def _clear_backing(self, *, preserve_block: bool = False) -> None:
        if self._echo is not None:
            self._echo.destroy()
        self._echo, self._echo_line, self._echo_side = None, -1, 0
        self._echo_origin_lean = self._echo_target_lean = 0.0
        self._echo_words: list = []
        self._echo_fade_s = ECHO_FADE_S
        self._echo_opens = 0.0
        self._echo_ends = 0.0
        if not preserve_block:
            self._echo_blocked = None

    def _order_text_layers(self) -> None:
        """Keep opaque fading glyphs below the text they must never cover."""
        active = self._views.get(self.line_index)
        if self._echo is not None:
            # Its wide translucent halo may enter a neighbour's visual box,
            # but it remains behind every main lyric glyph. This preserves the
            # designed echo size without letting its light muddy either row.
            self._echo.raise_layer()
        for index, view in self._views.items():
            if index != self.line_index:
                view.raise_layer()
        if active is not None:
            active.raise_layer()

        # Lyric views are created after the permanent card, so without an
        # explicit order even a fully faded (background-coloured) glyph can
        # punch black letter shapes through the title or artist.
        for item in (self._thumb_item, self._thumb_image,
                     self._title_item, self._artist_item):
            if item is not None:
                self.canvas.tag_raise(item)

    def _refit_views(self) -> None:
        """Keep the lines centred on the window they are actually in.

        Two answers, because the width changes in two circumstances. While the
        panel is still moving the lines are only shifted, which is cheap and
        leaves the vertical glide alone. Once it settles they are rebuilt, since
        the wrap width moved as well and no amount of shifting answers that.
        """
        if self.width == self._views_width or not self._views:
            return
        if self._collapse is not None:
            for view in self._views.values():
                view.recentre(self.width // 2)
                # Re-clipped rather than carried: the box the step is bounded
                # by is narrower mid-collapse than the one it was granted in,
                # and a line holding its full step through that would be the
                # one thing on screen crossing the margin.
                self._fit_view(view)
            return
        self._views_width = self.width
        self._clear_backing()
        for view in self._views.values():
            view.destroy()
        self._views.clear()
        self._glides.clear()
        self._targets.clear()
        self._relay_hold.clear()
        self.line_index = -1        # the next tick builds them at the new width

    def _multiline_relay_slots(
            self, indices: list[int]) -> tuple[float, float, float] | None:
        """Fixed top, middle and bottom positions for a wrapped hand-off."""
        active = self._views.get(self.line_index)
        if active is None or active.height <= active.line_height:
            return None
        neighbours = [self._views[index] for index in indices
                      if abs(index - self.line_index) == 1
                      and index in self._views
                      and self._views[index].height > self._views[index].line_height]
        if not neighbours:
            return None
        pair = [active, *neighbours]
        top = float(max(view.effect_padding for view in pair))
        bottom = float(min(self.height - view.height - view.effect_padding
                           for view in pair))
        if bottom <= top:
            return None
        step = (bottom - top) / 2
        required = max(
            upper.height + upper.glyph_padding + lower.glyph_padding + 1
            for upper in pair for lower in pair)
        if step < required:
            return None
        return top, top + step, bottom

    def _row_targets(self, indices: list[int]) -> dict[int, float]:
        """Resting row positions, including fixed wrapped relay slots."""
        slots = self._multiline_relay_slots(indices)
        active_y = slots[1] if slots is not None else self.anchor_y
        targets = {self.line_index: self._safe_view_y(
            self._views[self.line_index], active_y)}

        cursor = active_y
        for index in sorted((i for i in indices if i < self.line_index),
                            reverse=True):
            view = self._views[index]
            if (slots is not None and index == self.line_index - 1
                    and view.height > view.line_height):
                cursor = slots[0]
            else:
                cursor -= view.height + self.row_gap
            targets[index] = self._safe_view_y(view, cursor)

        cursor = active_y
        upper = self._views[self.line_index]
        for index in sorted(i for i in indices if i > self.line_index):
            view = self._views[index]
            if (slots is not None and index == self.line_index + 1
                    and view.height > view.line_height):
                cursor = slots[2]
            else:
                cursor += upper.height + self.row_gap
            targets[index] = self._safe_view_y(view, cursor)
            upper = view
            cursor = targets[index]
        return targets

    def _retarget(self, indices: list[int], animate: bool) -> None:
        """Place the active line and stack the rest around its relay slots."""
        if self.line_index not in self._views:
            return
        targets = self._row_targets(indices)
        for index in sorted(indices):
            view = self._views[index]
            # A view seen for the first time starts from wherever it was born,
            # so it travels in with the rest. Snapping it into place was what
            # made arriving lines land on top of lines still moving.
            previous = self._targets.get(index, view.y)
            target = targets[index]
            self._targets[index] = target
            if not animate:
                view.move_to(target)
                self._glides.pop(index, None)
            elif abs(previous - target) >= 1:
                # Displacement decaying, not a position being driven: a change
                # landing mid-glide adds to the journey instead of restarting it.
                remaining = self._glides[index].offset() if index in self._glides else 0.0
                distance = previous - target + remaining
                duration, curve = self._row_glide_motion(index, view, distance)
                self._glides[index] = motion.Glide(
                    distance, duration, curve)

    def _row_glide_motion(self, index: int, view: LineView,
                          distance: float = 0.0) -> tuple[float, tuple]:
        """Clock and curve for one row, coupling multi-row neighbours.

        Ordinarily the stagger is the motion's character. Two wrapped rows
        amplify its relative displacement enough for the new active line to
        catch the outgoing one, though. Use the ordinary weighted curve and
        request its full 45 ms drag, reducing only the unsafe fraction against
        the actual visual clearance between these two views.
        """
        active = self._views.get(self.line_index)
        both_multiline = (
            active is not None
            and view.height > view.line_height
            and active.height > active.line_height)
        adjacent_pair = abs(index - self.line_index) == 1 and both_multiline
        active_has_pair = (
            index == self.line_index and both_multiline
            and any(abs(other - index) == 1
                    and other_view.height > other_view.line_height
                    for other, other_view in self._views.items()))
        slots = self._multiline_relay_slots(sorted(self._views))
        relay_step = slots[1] - slots[0] if slots is not None else None
        base_duration = (motion.multiline_duration(
            relay_step if relay_step is not None else abs(distance),
            view.line_height, self.row_gap)
            if active_has_pair or adjacent_pair else motion.DURATION_MS)
        if active_has_pair:
            return base_duration, motion.MULTILINE_CURVE
        if adjacent_pair:
            if relay_step is None:
                nominal_gap = self.row_gap
            elif index < self.line_index:
                nominal_gap = relay_step - view.height
            else:
                nominal_gap = relay_step - active.height
            clearance = max(0.0, nominal_gap - active.glyph_padding
                            - view.glyph_padding - 1)
            stagger = motion.safe_stagger(
                abs(distance), clearance, base_duration,
                motion.MULTILINE_STAGGER_MS, motion.MULTILINE_CURVE)
            return base_duration + stagger, motion.MULTILINE_CURVE
        return motion.row_duration(index, self.line_index), motion.SCROLL_CURVE

    def _advance_glides(self) -> bool:
        moving = False
        settled = False
        for index, glide in list(self._glides.items()):
            view = self._views.get(index)
            if view is None:
                del self._glides[index]
                continue
            offset = glide.offset()
            view.move_to(self._targets[index] + offset)
            if glide.done:
                view.move_to(self._targets[index])
                del self._glides[index]
                settled = True
            else:
                moving = True
        self._keep_gliding_rows_apart()
        relay_hold = getattr(self, "_relay_hold", set())
        relay_finished = (bool(relay_hold)
                          and not any(index in self._glides
                                      for index in relay_hold))
        if relay_finished:
            relay_hold.clear()
        # One final restyle is required after settling: held multiline rows
        # remain fully visible during the relay and disappear only now.
        return moving or settled or relay_finished

    def _keep_gliding_rows_apart(self) -> None:
        """Keep staggered row trajectories from crossing the active row.

        A regular multi-row relay pre-bounds its stagger against the available
        gap, so this is chiefly the fallback for a new line change interrupting
        an older glide. In that case anchor the active view and push rows above
        and below away only for the overlapping part of that frame; their
        individual glides still determine every other pixel of the journey.
        """
        ordered = sorted(self._views)
        if self.line_index not in self._views:
            return
        active_at = ordered.index(self.line_index)
        active = self._views[self.line_index]

        lower_top = active.glyph_vertical_span()[0]
        for index in reversed(ordered[:active_at]):
            view = self._views[index]
            _top, bottom = view.glyph_vertical_span()
            if bottom > lower_top:
                view.move_to(view.y - math.ceil(bottom - lower_top))
            lower_top = view.glyph_vertical_span()[0]

        upper_bottom = active.glyph_vertical_span()[1]
        for index in ordered[active_at + 1:]:
            view = self._views[index]
            top, _bottom = view.glyph_vertical_span()
            if top < upper_bottom:
                view.move_to(view.y + math.ceil(upper_bottom - top))
            upper_bottom = view.glyph_vertical_span()[1]

    def _fade_text_scene(self, progress: float) -> None:
        """Present every lyric glyph between the wash and its true colour."""
        # `entry[3]` remains the renderer's target colour. Only the colour sent
        # to Tk is blended, so the ordinary sweep can keep calculating its true
        # state while this short presentation effect catches up with it.
        views = list(self._views.values())
        if self._echo is not None:
            views.append(self._echo)
        for view in views:
            for entry in view._items:
                self.canvas.itemconfigure(
                    entry[2], fill=_between(view.palette.backdrop, entry[3], progress))
            for item in view._outline:
                self.canvas.itemconfigure(
                    item, fill=_between(view.palette.backdrop, OUTLINE_COLOUR, progress))

    def _advance_fade(self, started: float | None, duration: float,
                      *, appearing: bool) -> tuple[bool, float | None]:
        """Advance one text-scene fade and return (still_moving, new_start)."""
        if started is None:
            return False, None
        elapsed = time.monotonic() - started
        done = elapsed >= duration
        progress = motion.cubic_bezier(
            1.0 if done else elapsed / duration, motion.RESIZE_CURVE)
        self._fade_text_scene(progress if appearing else 1.0 - progress)
        if done:
            return False, None
        return True, started

    def _begin_outgoing_fade(self) -> None:
        """Settle non-colour effects before the outgoing lyrics dissolve."""
        for view in self._views.values():
            view.set_active(False)
        if self._echo is not None:
            self._echo.set_active(False)
        self._outgoing_fade_at = time.monotonic()

    def _advance_outgoing_fade(self) -> bool:
        moving, self._outgoing_fade_at = self._advance_fade(
            self._outgoing_fade_at, OUTGOING_FADE_S, appearing=False)
        return moving

    def _advance_lyrics_fade(self) -> bool:
        moving, self._lyrics_fade_at = self._advance_fade(
            self._lyrics_fade_at, LYRICS_FADE_S, appearing=True)
        return moving

    def _advance_cut_fade(self) -> bool:
        """Bring a clock-corrected scene out of the wash without scrolling it."""
        moving, self._cut_fade_at = self._advance_fade(
            self._cut_fade_at, CUT_CORRECTION_FADE_S, appearing=True)
        return moving

    def _mount_static_lyrics(self, snap: Snapshot) -> None:
        """Build one truthful paused frame without advancing presentation time.

        A promotion is still a visual state change when the player is paused:
        its card, geometry and words all belong together.  What pause forbids
        is motion, not construction.  This path therefore lands directly at
        the target size and reported playback position, with no beam, glides,
        bloom or transition clocks.  The word sweep is painted once at that
        position and remains frozen until live transport resumes.
        """
        self._retarget_size(animate=False)
        self._collapse = None
        self._glides.clear()
        self._cuts_discontinuous = False
        self._cut_fade_at = None
        self._lyrics_reveal_pending = False
        self._lyrics_fade_at = None

        lyr = self.lyrics
        if lyr is None or not lyr.synced or not lyr.lines:
            self._clear_views()
            self.line_index = -1
            return

        self._settle_cuts(lyr, snap)
        pos = self._cuts.to_song(snap.live_position()) + self.offset
        index = _display_line_index(lyr, pos)
        waiting = index < 0
        if waiting:
            index = 0
        self._go_to_line(index, lyr, animate=False)
        self._glides.clear()

        active = self._views.get(self.line_index)
        if active is not None:
            if waiting:
                active.show_inactive(self.palette.unsung)
            elif active.words:
                word, fraction = lyr.word_progress_at(
                    self.line_index, pos + WORD_LEAD_S)
                active.show_sweep(word, fraction)
            else:
                active.show_lit()

        backing_pos = pos + _backing_lead_s(lyr, self.line_index)
        self._show_backing(lyr, backing_pos, effects=False)
        # A previous scene may have left presentation colours between the wash
        # and their targets. Static mounting has no fade clock to finish them.
        self._fade_text_scene(1.0)

    # --- render ---
    def _tick(self):
        self._drain_actions()
        if self._closing:
            # Nothing may run after this: every line below touches a widget.
            self.root.destroy()
            return
        if self._hidden:
            # Nothing to draw and nobody watching. The session reader keeps
            # polling on its own thread, so this stays cheap without going
            # stale: bringing it back resyncs on the very next tick.
            self.root.after(SLOW_TICK_MS, self._tick)
            return

        snap = self.reader.snapshot
        if snap.ok and snap.track_key() != self._fetching_key:
            # Only starts the assembling. Nothing on screen moves until the new
            # song is whole, so the one playing keeps its thumbnail, its name
            # and its last line until there is a complete one to replace them.
            self._fetching_key = snap.track_key()
            self._start_fetch(snap)
        # After observing the session change. A result for the outgoing track
        # can land in the same tick as the new snapshot; advancing the
        # generation first keeps that late answer from repainting the frame we
        # deliberately froze.
        self._drain_worker_results()
        interval = SLOW_TICK_MS
        promoted = False
        if self._loading is not self._shown:
            target_scene = self._promotion_scene()
            if self._outgoing_fade_at is not None:
                if self._advance_outgoing_fade():
                    interval = FAST_TICK_MS
                else:
                    # An answer may change while the outgoing words fade. Use
                    # its newest state: a late positive answer can replace the
                    # empty scene and reveal its lyrics from the settled panel.
                    target_scene = self._promotion_scene() or SCENE_CARD_ONLY
                    self._promote(
                        target_scene,
                        fade_lyrics=(target_scene == SCENE_READY
                                     and self._loading.lyrics_state == LYRICS_PRESENT))
                    promoted = True
            elif (target_scene is not None
                  and self._loading.lyrics_state != LYRICS_PRESENT
                  and (self._views or self._echo)):
                # Both a timed-out unknown answer and a definite no-lyrics
                # answer lead to an empty column. Dissolve the old words first;
                # promotion then starts the card contraction on a later frame.
                self._begin_outgoing_fade()
                self._advance_outgoing_fade()
                interval = FAST_TICK_MS
            elif target_scene is not None:
                self._promote(target_scene)
                promoted = True
        # Read from the shown track rather than kept as a copy of it. A song put
        # up on the deadline goes up not knowing whether it has words, and its
        # answer lands in the same `Track` a moment later — copied at the
        # promotion, that answer was written where nobody looked, and a song
        # with no lyrics stayed expanded until the next track change. It is the
        # same song, so there is nothing crossed about hearing it out.
        self.lyrics = self._shown.lyrics
        self._lyrics_state = self._shown.lyrics_state
        late_reveal = False
        scene_changed = promoted
        if (self._shown.scene == SCENE_CARD_ONLY
                and self._lyrics_state != LYRICS_UNKNOWN):
            self._shown.scene = SCENE_READY
            scene_changed = True
            if self._lyrics_state == LYRICS_PRESENT:
                # CARD_ONLY contains no lyric items. The first render below
                # builds the right three rows directly at the current playback
                # time, then this clock reveals them as one scene.
                if self._compact:
                    self._lyrics_reveal_pending = True
                    self._lyrics_fade_at = None
                else:
                    self._lyrics_fade_at = time.monotonic()
                late_reveal = True

        # The old visual state may be held while the next track is assembling,
        # but it must never move to the next track's clock. The same gate stops
        # every time-based visual when playback is paused or the session drops.
        live_transport = transport_is_live(self._shown, snap)
        current_art_waiting = (self._pending_art is not None
                               and self._loading is self._shown)
        if live_transport or promoted or current_art_waiting:
            # A promotion owns a single, atomic visual change even when the
            # newly selected track is paused. Otherwise artwork waits with the
            # frozen frame instead of quietly changing beneath it.
            self._apply_art()
        card = self._card_for(self._shown.snapshot if self._shown.snapshot.ok
                              else snap)
        if card != self._card_text:
            self._card_text = card
            title, artists = card
            self.canvas.itemconfigure(self._title_item, text=title)
            self.canvas.itemconfigure(self._artist_item, text=artists)
            self._lay_out_card(title, artists)
            self._place_thumb()

        paused_current = (
            snap.ok and not snap.playing and self._shown.snapshot.ok
            and snap.track_key() == self._shown.snapshot.track_key())
        static_mounted = scene_changed and paused_current
        if static_mounted:
            self._mount_static_lyrics(snap)
        elif live_transport:
            if self._advance_beam():
                interval = BEAM_TICK_MS
            self._retarget_size()
            if self._advance_collapse():
                interval = FAST_TICK_MS
            self._refit_views()

        # `_advance_collapse` clears its clock only after applying the exact
        # final geometry. Start the lyric clock after that point, never on the
        # last resize frame, so the two effects cannot visually overlap.
        if (self._lyrics_reveal_pending and not self._compact
                and self._collapse is None):
            self._lyrics_reveal_pending = False
            self._lyrics_fade_at = time.monotonic()

        lyr = self.lyrics
        lyrics_revealing = (
            self._lyrics_reveal_pending or self._lyrics_fade_at is not None)
        render_lyrics = (not static_mounted
                         and (live_transport or late_reveal or lyrics_revealing))
        if render_lyrics and lyr is not None and lyr.synced and lyr.lines:
            self._settle_cuts(lyr, snap)
            # Through the cuts first: the player's position is a place in a
            # video, and the lyrics are written against the recording. Without
            # any they are the same number.
            pos = self._cuts.to_song(snap.live_position()) + self.offset
            if self._awaiting_seek is not None:
                target, since = self._awaiting_seek
                # Keep counting from the jump rather than sitting on it. Holding
                # the position still was what made the highlight freeze after a
                # click: the sweep had nothing to advance along until the poll
                # caught up, and then resumed with a lurch.
                assumed = target + (time.monotonic() - since)
                if abs(pos - assumed) <= SEEK_SETTLED_S:
                    self._awaiting_seek = None      # the player caught up
                else:
                    pos = assumed                   # trust the jump, not the poll
            index = _display_line_index(lyr, pos)
            # Before the first line there is no active line, and the panel used
            # to sit empty until the singing started — which on a video with a
            # twenty-second intro is twenty seconds of the overlay pretending it
            # knows less than it does. The song is identified and its words are
            # already here, so the first line waits on screen instead, unlit.
            waiting = index < 0
            if waiting:
                index = 0
            corrected = self._cuts_discontinuous
            if corrected:
                # A late intro/outro answer is a clock discontinuity, like a
                # seek. Land on the truthful row in one frame; gliding through
                # the intervening lyrics would briefly present every one as if
                # it were being sung.
                self._glides.clear()
                if not lyrics_revealing:
                    self._cut_fade_at = time.monotonic()
                self._cuts_discontinuous = False
            if index != self.line_index:
                self._go_to_line(
                    index, lyr,
                    animate=False if corrected or late_reveal else None)

            if self._advance_glides():
                # Brightness follows position, so it has to be recomputed while
                # anything is still moving — otherwise a line would travel out
                # of the frame at full strength and be clipped rather than fade.
                self._restyle()
                interval = FAST_TICK_MS
            active = self._views.get(self.line_index)
            if active is None:
                pass
            elif waiting:
                # Held at the unsung level whether or not it has word timings:
                # nothing has been sung yet, and that is precisely what it says.
                active.show_inactive(self.palette.unsung)
            elif active.words:
                word, fraction = lyr.word_progress_at(self.line_index,
                                                      pos + WORD_LEAD_S)
                active.show_sweep(word, fraction)
                # Advanced every frame rather than only when the front moves:
                # the bloom is a decay in time, and between two words the front
                # stands still while the light behind it still has to drain.
                if (self._cut_fade_at is None and not lyrics_revealing
                        and active.advance_bloom(time.monotonic())) or word >= 0:
                    interval = FAST_TICK_MS
            else:
                # No word timing for this line: light all of it. Leaving it dim
                # would say none of it has been sung, about the line playing.
                active.show_lit()
            # Outside every branch, so a line with no word timings takes its
            # backing down instead of leaving the last one frozen over it.
            if self._cut_fade_at is None:
                backing_pos = pos + _backing_lead_s(lyr, self.line_index)
                self._show_backing(
                    lyr, backing_pos, effects=not lyrics_revealing)
            else:
                self._clear_backing()
            if self._advance_cut_fade():
                interval = FAST_TICK_MS
            if self._lyrics_reveal_pending:
                # The renderer updates true target colours on every frame;
                # overwrite only their presentation colour until the resize
                # has completely settled.
                self._fade_text_scene(0.0)
                if live_transport and self._collapse is not None:
                    interval = FAST_TICK_MS
            elif self._advance_lyrics_fade():
                interval = FAST_TICK_MS

        self.root.after(interval, self._tick)

    def _advance_beam(self) -> bool:
        """Move the border light. True while it needs the loop kept awake."""
        if self.beam is None:
            return False
        now = time.monotonic()
        dt = min(0.25, now - (self._beam_at or now))
        self._beam_at = now
        self.beam.advance(dt, self.meter.character(dt or 1 / 60), self.palette)
        return True

    def _visibility(self, view: LineView) -> float:
        """How present a line is, from where it sits rather than from its index.

        A line fades as it nears either edge and is gone before it reaches one.
        That is what stops a line ever being seen half-clipped by the frame: it
        has already faded out by the time the edge would cut it.

        The upper edge reserves the card lane, so even a soft halo must be gone
        before entering it. At the lower edge the complete halo is already kept
        on-canvas by ``_safe_view_y``; fade against the grown glyph box there.
        A wrapped upcoming row otherwise lands with its halo exactly at the
        canvas edge and is assigned zero visibility despite all of its text
        being safely present.
        """
        fade = max(1, self.chrome.px(FADE_ZONE))
        top, _visual_bottom = view.visual_vertical_span()
        _glyph_top, bottom = view.glyph_vertical_span()
        room = min((top - self._content_top) / fade,
                   (self.height - bottom) / fade, 1.0)
        return max(0.0, room)

    def _single_row_context_visibility(self, view: LineView) -> float:
        """Visibility the upcoming row would receive in a 1 -> 1 layout."""
        fade = max(1, self.chrome.px(FADE_ZONE))
        wanted = self.anchor_y + view.line_height + self.row_gap
        low = float(view.effect_padding)
        high = float(self.height - view.line_height - view.effect_padding)
        reference_y = max(low, min(wanted, high))
        top = reference_y - view.effect_padding
        bottom = reference_y + view.line_height + view.glyph_padding
        room = min((top - self._content_top) / fade,
                   (self.height - bottom) / fade, 1.0)
        return max(0.0, room)

    def _context_visibility(self, index: int, view: LineView) -> float:
        """Consistent preview strength for incoming one- or two-row lyrics."""
        visibility = self._visibility(view)
        if self._is_incoming_context(index):
            visibility = max(
                visibility, self._single_row_context_visibility(view),
                INCOMING_VISIBILITY_FLOOR)
        return visibility

    def _is_incoming_context(self, index: int) -> bool:
        """Whether a neighbouring row is waiting below or rising into place."""
        glide = self._glides.get(index)
        entering_from_below = glide is None or glide.distance >= 0
        return index > self.line_index and entering_from_below

    def _relay_outgoing_visibility(self, index: int, view: LineView) -> float:
        """Fade a wrapped outgoing row before it enters the card's lane.

        A fixed three-slot relay deliberately sends the outgoing row farther
        than the ordinary lyric lane can display. Start from its actual resting
        position at full strength and reach zero exactly when the complete
        visual box touches ``_content_top``. The glide may then finish behind
        the reserved lane, but no lyric pixel can appear beneath the cover or
        either card label.
        """
        glide = self._glides.get(index)
        target = self._targets.get(index)
        if glide is None or target is None:
            return 0.0
        start_y = target + glide.distance
        hidden_y = self._content_top + view.effect_padding
        span = start_y - hidden_y
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (view.y - hidden_y) / span))

    def _restyle(self, indices: list[int] | None = None) -> None:
        for index in (indices if indices is not None else list(self._views)):
            view = self._views.get(index)
            if view is None:
                continue
            active = index == self.line_index
            view.set_active(active)
            if active:
                view.set_visible(True)
                continue
            if (index in getattr(self, "_relay_hold", set())
                    and index in self._glides):
                visibility = self._relay_outgoing_visibility(index, view)
                view.set_visible(visibility > 0.0)
                view.show_inactive(self.palette.faded(
                    abs(index - self.line_index), visibility))
                continue
            visibility = self._context_visibility(index, view)
            view.set_visible(visibility > 0.0)
            # The immediate upcoming lyric is reading context, not an exiting
            # edge ornament.  Fading ``side`` halfway into the artwork wash
            # produced #41413e in the neutral palette: technically non-zero,
            # visually absent.  Keep the established pale unsung rung while
            # it waits below or rises; geometric fading still belongs to rows
            # moving out of the scene in either direction.
            colour = (self.palette.unsung if self._is_incoming_context(index)
                      else self.palette.faded(
                          abs(index - self.line_index), visibility))
            view.show_inactive(colour)
        self._order_text_layers()

    def run(self):
        self.reader.start()
        self.hotkeys.start()
        self.tray.start()
        chrome_mod.hold_timer_resolution(True)
        self._tick()
        self.root.mainloop()
        chrome_mod.hold_timer_resolution(False)
        self.tray.stop()
        self.hotkeys.stop()
        self.reader.stop()
        self.meter.close()


def setup_logging() -> Path:
    """Send logs to a rotating file and return its path.

    The overlay has no console: started from the Start menu or a packaged
    executable, anything written to stderr goes nowhere. Without a file, the
    handled exceptions would be logged into the void, which is worse than not
    logging them because the code reads as though failures are being recorded.
    """
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Lyrica"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "lyrica.log"
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=512_000, backupCount=2, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path


def main():
    setup_logging()
    # Before anything reads a token: the .env is the fallback for values that
    # must not be committed, and an exported variable still wins over it.
    config.load()
    Overlay().run()


if __name__ == "__main__":
    main()
