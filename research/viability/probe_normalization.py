"""Probe: what the current normalization does to real captured payloads.

Every case below was captured from a live media session by
`probe_browser_session.py`, not invented. This replays them through
`Snapshot.norm_artist_title()` and then through LRCLIB, so the cost of the
current rules is measured rather than argued about.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from lyrica.providers.lrclib import LrclibProvider
from lyrica.smtc import Snapshot

# (label, app, artist, title, duration) — all captured live on 2026-08-06
CASES = [
    ("Spotify",             "Spotify.exe", "Porter Robinson", "Goodbye To A World", 328.5),
    ("YT Music",            "chrome.exe",  "NewJeans", "Supernatural", 191.0),
    ("YouTube video",       "chrome.exe",  "Dua Lipa",
     "Dua Lipa - Levitating Featuring DaBaby (Official Music Video)", 230.1),
    ("SoundCloud reupload", "chrome.exe",  "Minh Prime",
     "The Weeknd - Blinding Lights full", 762.8),
    ("SoundCloud mix",      "chrome.exe",  "nigeldelviero",
     "Chill Study Beats - Lofi Hip Hop Mix [2018]", 7200.3),
]


def main():
    provider = LrclibProvider()
    for label, app, artist, title, duration in CASES:
        snap = Snapshot(app=app, artist=artist, title=title, duration=duration, ok=True)
        norm_artist, norm_title = snap.norm_artist_title()
        result = provider.fetch(norm_artist, norm_title, duration)
        verdict = "MISS" if result is None else f"{result.source} synced={result.synced}"
        print(f"{label}")
        print(f"  raw    artist={artist!r} title={title!r} duration={duration:.1f}s")
        print(f"  normed artist={norm_artist!r} title={norm_title!r}")
        print(f"  lookup {verdict}\n")


if __name__ == "__main__":
    main()
