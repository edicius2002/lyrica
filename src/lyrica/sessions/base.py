"""What is playing, and how to make sense of what the platform says about it.

Everything here is platform-agnostic: the snapshot the rest of the app reads,
and the rules for turning a player's idea of "artist" and "title" into something
a lyrics provider can be asked about. Only the reading itself is per-platform,
in the sibling modules.
"""
import logging
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

# Browser apps may leave `artist` empty and encode "Artist - Title" in the title
BROWSER_HINTS = ("chrome", "msedge", "firefox", "opera", "brave", "vivaldi", "safari")

NOISE = re.compile(
    r"[\(\[][^\)\]]*(official|oficial|video|audio|lyric|letra|visualizer|remaster|hd|4k|mv|m/v)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)

# Bare words re-uploaders append to a title. Only stripped from the end, so a
# song actually called "Audio" or "Complete" survives anywhere else in the name.
JUNK_TAIL = re.compile(
    r"(?:\s|^)(full|complete|completa|hq|hd|4k|audio|lyrics?|letra|sub\s*español)\s*$",
    re.IGNORECASE,
)

SEPARATORS = (" - ", " – ", " — ", " | ")

# What can join one artist to the next in a credit: "A, B - Title", "A & B",
# "A feat. B". Used to tell a longer credit apart from a song whose name simply
# begins with the artist's word.
ARTIST_LIST = re.compile(
    r"^\s*(?:[,&×+/]|(?:feat|ft|featuring|with|and|y|x|vs|con)\.?\s)",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


def clean_title(title: str) -> str:
    """Strip video-title noise like "(Official Video)" and stray separators."""
    t = NOISE.sub("", title)
    t = re.sub(r"\s{2,}", " ", t)
    t = t.strip(" -–|·")
    while True:
        stripped = JUNK_TAIL.sub("", t).strip(" -–|·")
        if stripped == t or not stripped:
            return t
        t = stripped


def split_browser_title(title: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title). Empty artist if no separator."""
    for sep in SEPARATORS:
        if sep in title:
            artist, track = title.split(sep, 1)
            return artist.strip(), track.strip()
    return "", title.strip()


def strip_artist_prefix(artist: str, title: str) -> str:
    """Drop a leading repetition of the artist: YouTube states it in both fields.

    Returns the title unchanged when it does not start with the artist, so a
    track whose name genuinely opens with the artist's word survives.
    """
    if not artist:
        return title
    low_t, low_a = title.lower(), artist.lower()
    if not low_t.startswith(low_a):
        return title
    rest = title[len(artist):]
    for sep in SEPARATORS:
        if rest.startswith(sep):
            return rest[len(sep):].strip()
    if rest.startswith(("-", "–", "—", "|", ":")):
        return rest[1:].strip()
    # "Tiago PZK, Myke Towers - Traductor" under an artist field of "Tiago PZK":
    # the stated artist is only the first name in a longer credit, so the whole
    # credit is the prefix and none of it is the song. Accepted only when what
    # follows the artist reads as a credit — punctuation or a joining word — so
    # a track called "Airbag" by an artist called "Air" is left alone.
    if ARTIST_LIST.match(rest):
        for sep in SEPARATORS:
            if sep in rest:
                return rest.split(sep, 1)[1].strip()
    return title


@dataclass(frozen=True)
class Snapshot:
    app: str = ""
    artist: str = ""
    title: str = ""
    album: str = ""
    duration: float = 0.0          # seconds; 0 if unknown
    position: float = 0.0          # seconds at the moment `updated_at`
    updated_at: datetime | None = None  # when that position was reported (UTC)
    playing: bool = False
    ok: bool = False               # a valid session exists

    @property
    def is_browser(self) -> bool:
        return any(h in self.app.lower() for h in BROWSER_HINTS)

    def norm_artist_title(self) -> tuple[str, str]:
        """Best single guess at artist and title. Used for display and as the
        first lookup candidate."""
        artist, title = self.artist.strip(), self.title
        if not artist and self.is_browser:
            artist, title = split_browser_title(title)
        return artist.strip(), clean_title(strip_artist_prefix(artist, title))

    def lookup_candidates(self) -> list[tuple[str, str]]:
        """Artist/title pairs to try, best first.

        Players disagree about what these fields mean, and each interpretation
        is right somewhere:

        - Music apps and YouTube Music state both fields correctly.
        - YouTube states the artist correctly *and* repeats it in the title.
        - SoundCloud puts the uploader's handle in the artist field, so it may
          be a stranger's username while the real artist sits in the title.

        Nothing in the payload says which case applies, so the alternatives are
        ranked rather than guessed between, and the caller stops at the first
        that resolves.
        """
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []

        def add(artist: str, title: str) -> None:
            pair = (artist.strip(), clean_title(title))
            if pair[1] and pair not in seen:
                seen.add(pair)
                out.append(pair)

        add(*self.norm_artist_title())

        # The artist field may be an uploader handle rather than a performer,
        # or only the first name of a longer credit. Either way the title's own
        # left-hand side is a reading worth trying; when it says the same thing
        # as the first candidate, `add` drops it as a duplicate. It used to be
        # skipped whenever the artist appeared anywhere in the title, which also
        # skipped it for "Tiago PZK, Myke Towers - Traductor" — where the split
        # is the only reading that names the song.
        if self.is_browser:
            split_artist, split_title = split_browser_title(self.title)
            if split_artist:
                add(split_artist, split_title)

        add(self.artist, self.title)
        return out

    def track_key(self) -> str:
        return f"{self.app}|{self.artist}|{self.title}"

    def live_position(self) -> float:
        """Playback position interpolated to now.

        Sources report where playback *was* at a stated moment, not where it is.
        Measured against a browser's own clock the error is flat to a
        millisecond over a run, so adding the elapsed time is exact rather than
        an approximation.
        """
        if not self.ok or self.updated_at is None:
            return 0.0
        pos = self.position
        if self.playing:
            pos += (datetime.now(UTC) - self.updated_at).total_seconds()
        if self.duration > 0:
            pos = min(pos, self.duration)
        return max(pos, 0.0)


class SessionReader(ABC):
    """Polls the platform for what is playing and publishes snapshots.

    The reader owns a thread and rebinds `snapshot` whole. Publishing an
    immutable value by one assignment is what lets the render loop read without
    a lock and never see a half-written state.
    """

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.snapshot = Snapshot()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="session-reader")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @abstractmethod
    def _run(self) -> None:
        """Poll until `self._stop` is set, assigning to `self.snapshot`."""

    @staticmethod
    def available() -> bool:
        """Whether this reader can work on the machine it is running on."""
        return False

    def read_artwork(self) -> bytes | None:
        """The current track's artwork, if the platform publishes any."""
        return None

    def seek(self, seconds: float) -> bool:
        """Ask the player to jump to a position. False if it will not.

        Watching and controlling are separate permissions here: an overlay
        outside the player can only ask, and whether the request is honoured is
        up to the app. So this reports whether it worked rather than assuming,
        and the caller is expected to carry on unbothered when it did not.
        """
        return False
