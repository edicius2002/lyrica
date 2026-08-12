"""Musixmatch richsync — the widest word-level coverage, and the most fragile.

Measured at 13/16 tracks carrying word-level data, against 6/10 for the free
community source, so it is worth having. Everything else about it argues for
caution:

- The endpoint is undocumented and belongs to a commercial licensor.
- It throttles. Twenty lookups paced 1.5 s apart tripped it, and the refusal
  arrives as an ordinary "no match" rather than an error, so a run that hits it
  silently reports every remaining track as missing.

Both facts point the same way: ask once per track, cache the answer forever, and
back all the way off the moment it objects. That is affordable because personal
listening is a few dozen new tracks a day and every one of them is asked about
exactly once, ever.

The earlier attempt at this concluded the endpoint was dead — it answered 404
for every track. It keys on the numeric track_id and was being called by name.
"""
import json
import logging
import threading
import time
from pathlib import Path

import requests

from lyrica.lyrics import MAX_INFERRED_WORD_S, Lyrics, Precision, split_parenthetical_adlib
from lyrica.providers.base import LyricsProvider

BASE = "https://apic-desktop.musixmatch.com/ws/1.1"
APP_ID = "web-desktop-app-v1.0"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": "AWSELB=0; AWSELBCORS=0",
}
TIMEOUT = 15

# Tokens outlive a single run, so re-requesting one on every start is both
# needless traffic and a good way to look like abuse.
TOKEN_TTL_S = 600

# After a refusal, stop asking entirely for a while. Retrying into a throttle is
# what turns a temporary limit into a blocked address.
COOLDOWN_S = 900

logger = logging.getLogger(__name__)

# Richsync has no measured word ends.  A parenthetical suffix is displayed on
# its own lane, so its final token needs a small readable tail rather than
# disappearing at the source's next coarse offset.
INFERRED_BACKING_TAIL_S = 0.25


def richsync_to_words(parsed: list) -> tuple[list, list]:
    """Convert richsync's line list into (lines, words).

    Richsync gives each word an offset from its line's start but never a
    duration, so a word's end has to be inferred from the next word's start.
    Left uncapped that misreports an instrumental break as one enormously long
    word, which on screen is a highlight stuck mid-line for seconds.
    """
    lines: list = []
    words: list = []
    for line in parsed:
        start = line.get("ts")
        end = line.get("te")
        events = line.get("l") or []
        if start is None:
            continue

        line_words = []
        raw_parts = []
        for i, event in enumerate(events):
            raw = event.get("c") or ""
            text = raw.strip()
            offset = event.get("o")
            if not text or offset is None:
                continue
            raw_parts.append(raw)
            word_start = start + offset
            if i + 1 < len(events) and events[i + 1].get("o") is not None:
                word_end = start + events[i + 1]["o"]
            elif end is not None:
                word_end = end
            else:
                word_end = word_start + MAX_INFERRED_WORD_S
            word_end = min(word_end, word_start + MAX_INFERRED_WORD_S)
            line_words.append((word_start, max(word_end, word_start), text))

        # Richsync's own line text first. The fallback concatenates the raw
        # events rather than joining the trimmed ones, because this source
        # splits below the word too — "conmigo" arrives as "con" and "migo"
        # with no space between them, and joining with one puts a space inside
        # the word.
        text = (line.get("x") or " ".join("".join(raw_parts).split())).strip()
        if not text:
            continue
        lines.append((start, text))
        words.append(line_words)
    return lines, words


def richsync_to_lyrics(parsed: list) -> Lyrics:
    """Build Richsync lyrics, preserving complete parenthetical suffixes.

    Richsync has word starts but not independently measured word ends. That
    makes a timing-overlap inference untrustworthy, but a complete
    parenthesized suffix is still an explicit editorial convention and keeps
    its own serial word timings. The shared splitter deliberately leaves
    inline parentheses and wholly parenthetical lines in the lead channel.
    """
    lines, words = richsync_to_words(parsed)
    backing: list = []
    backing_words: list = []
    backing_timing: list = []
    backing_modes: list = []
    for index, (start, text) in enumerate(lines):
        inferred = split_parenthetical_adlib(text, words[index])
        if inferred is None:
            backing.append("")
            backing_words.append([])
            backing_timing.append("")
            backing_modes.append("")
            continue
        lead_text, lead_words, backing_text, timed_backing = inferred
        # Preserve every source start and every interior hand-off.  Only the
        # final inferred token is extended, never beyond the same 1.75 s cap
        # that protects an ordinary Richsync word before a silent gap.
        last_start, last_end, last_text = timed_backing[-1]
        timed_backing[-1] = (
            last_start,
            min(last_end + INFERRED_BACKING_TAIL_S,
                last_start + MAX_INFERRED_WORD_S),
            last_text,
        )
        # The suffix stays visually in the backing lane.  Its relation to the
        # lead decides whether that lane is concurrent or appears afterwards.
        lead_end = max(end for _begin, end, _text in lead_words)
        sequential = timed_backing[0][0] >= lead_end
        lines[index] = (start, lead_text)
        words[index] = lead_words
        backing.append(backing_text)
        backing_words.append(timed_backing)
        backing_timing.append("inferred")
        backing_modes.append("sequential" if sequential else "overlapping")
    return Lyrics(lines=lines, words=words, synced=True,
                  backing=backing, backing_words=backing_words,
                  backing_timing=backing_timing, backing_modes=backing_modes,
                  source="musixmatch/richsync", exact=True)


class MusixmatchProvider(LyricsProvider):
    name = "musixmatch"
    max_precision = Precision.WORD

    def __init__(self, token_path: Path | None = None):
        self._token: str | None = None
        self._token_at: float = 0.0
        self._cooldown_until: float = 0.0
        self._lock = threading.Lock()
        self._token_path = token_path

    # --- transport ---
    def _call(self, endpoint: str, params: dict) -> dict | None:
        try:
            r = requests.get(f"{BASE}/{endpoint}",
                             params={**params, "app_id": APP_ID, "format": "json"},
                             headers=HEADERS, timeout=TIMEOUT)
            return r.json()
        except (requests.RequestException, ValueError):
            logger.debug("musixmatch %s failed", endpoint, exc_info=True)
            return None

    @staticmethod
    def _header(payload: dict | None) -> tuple[int, str]:
        if not payload:
            return -1, ""
        head = payload.get("message", {}).get("header", {})
        return head.get("status_code", -1), (head.get("hint") or "")

    def _begin_cooldown(self, why: str) -> None:
        self._cooldown_until = time.monotonic() + COOLDOWN_S
        self._token = None
        logger.info("musixmatch: backing off for %d min (%s)", COOLDOWN_S // 60, why)

    # --- token ---
    def _load_cached_token(self) -> str | None:
        if not self._token_path or not self._token_path.exists():
            return None
        try:
            d = json.loads(self._token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - d.get("issued_at", 0) > TOKEN_TTL_S:
            return None
        return d.get("token")

    def _store_token(self, token: str) -> None:
        if not self._token_path:
            return
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(
                json.dumps({"token": token, "issued_at": time.time()}), encoding="utf-8")
        except OSError:
            logger.debug("musixmatch: could not persist the token", exc_info=True)

    def _get_token(self) -> str | None:
        if self._token and time.monotonic() - self._token_at < TOKEN_TTL_S:
            return self._token
        cached = self._load_cached_token()
        if cached:
            self._token, self._token_at = cached, time.monotonic()
            return cached

        payload = self._call("token.get", {})
        status, hint = self._header(payload)
        if status != 200:
            # A captcha hint means "you are asking too often", never "try again".
            self._begin_cooldown(f"token.get status {status} hint {hint!r}")
            return None
        token = payload["message"]["body"].get("user_token")
        if token:
            self._token, self._token_at = token, time.monotonic()
            self._store_token(token)
        return token

    # --- lookup ---
    def fetch(self, artist: str, title: str, duration: float = 0.0,
              album: str = "") -> Lyrics | None:
        if not title:
            return None
        with self._lock:
            if time.monotonic() < self._cooldown_until:
                logger.debug("musixmatch: still backing off, skipping %r - %r", artist, title)
                return None
            token = self._get_token()
            if not token:
                return None
            track = self._match(token, artist, title, duration)
            if not track or not track.get("has_richsync"):
                return None
            return self._richsync(token, track["track_id"])

    def _match(self, token: str, artist: str, title: str, duration: float) -> dict | None:
        params = {"q_artist": artist, "q_track": title, "usertoken": token}
        if duration > 1:
            params["q_duration"] = round(duration)
        payload = self._call("matcher.track.get", params)
        status, hint = self._header(payload)
        if status == 401:
            self._begin_cooldown(f"matcher hint {hint!r}")
            return None
        if status != 200:
            return None
        return payload["message"]["body"].get("track")

    def _richsync(self, token: str, track_id: int) -> Lyrics | None:
        payload = self._call("track.richsync.get",
                             {"track_id": track_id, "usertoken": token})
        status, hint = self._header(payload)
        if status == 401:
            self._begin_cooldown(f"richsync hint {hint!r}")
            return None
        if status != 200:
            return None
        raw = (payload["message"]["body"].get("richsync") or {}).get("richsync_body")
        if not raw:
            return None
        try:
            parsed = json.loads(raw)  # the body is JSON inside a JSON string
        except ValueError:
            return None
        lyrics = richsync_to_lyrics(parsed)
        if not lyrics.lines:
            return None
        # matcher.track.get matched on artist, title and duration together, so a
        # hit here identifies the recording rather than approximating it.
        return lyrics
