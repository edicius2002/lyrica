"""Probe: how much word-level coverage do the no-auth community sources have?

Both reference extensions reach word-level TTML through community services that
need no token, no captcha and no signing — the cheapest possible source. Their
coverage has never been measured here, and that number decides whether
Musixmatch is needed at all.

Measured against tracks actually played on this machine (read from the media
session during earlier sessions) rather than a chart list, because coverage on
someone's real listening is the only number that decides anything.

Reports sizes, formats and flags only — never lyric text.
"""
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

BINI_SEARCH = "https://lyrics-api.binimum.org/getLyrics"
UNISON = "https://unison.boidu.dev/lyrics"
HEADERS = {"User-Agent": "lyrica-viability/0.1 (personal research)"}
TIMEOUT = 15

# Seen playing on this machine, plus a few well-covered controls.
TRACKS = [
    ("Dr. Dre", "Still D.R.E.", 270),
    ("Drake", "9", 256),
    ("Romeo Santos", "Odio", 260),
    ("Porter Robinson", "Goodbye To A World", 328),
    ("NewJeans", "Supernatural", 191),
    ("Dua Lipa", "Levitating", 203),
    ("The Weeknd", "Blinding Lights", 200),
    ("Bad Bunny", "Monaco", 267),
    ("Soda Stereo", "De Musica Ligera", 208),
    ("Kendrick Lamar", "HUMBLE.", 177),
]


def looks_word_level(body: str) -> bool:
    """TTML carries word timing as timed spans inside each <p>."""
    return "<span" in body and "begin=" in body.split("<span", 1)[-1][:200]


def probe_bini(artist: str, title: str) -> str:
    try:
        r = requests.get(BINI_SEARCH, params={"q": f"{title} {artist}"},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        results = r.json().get("results") or []
        if not results:
            return "no results"
        best = results[0]
        url = best.get("lyricsUrl")
        if not url:
            return "result without lyricsUrl"
        r2 = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r2.status_code != 200:
            return f"lyricsUrl HTTP {r2.status_code}"
        body = r2.text
        kind = "WORD" if looks_word_level(body) else "line/plain"
        return f"{kind} ({len(body)} bytes, matched {best.get('track_name')!r})"
    except requests.RequestException as e:
        return f"transport {type(e).__name__}"
    except ValueError:
        return "bad JSON"


def probe_unison(artist: str, title: str, duration: int) -> str:
    try:
        r = requests.get(UNISON, params={"song": title, "artist": artist,
                                         "duration": duration},
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        d = r.json()
        data = d.get("data") or d
        body = data.get("lyrics") or ""
        if not body:
            return "no lyrics"
        return (f"{data.get('syncType', '?')} / {data.get('format', '?')} "
                f"({len(body)} bytes)")
    except requests.RequestException as e:
        return f"transport {type(e).__name__}"
    except ValueError:
        return "bad JSON"


def main() -> None:
    print("=== Community word-level sources, no auth ===\n")
    bini_word = unison_word = 0
    for artist, title, duration in TRACKS:
        print(f"{artist} - {title}")
        b = probe_bini(artist, title)
        print(f"   bini    {b}")
        if b.startswith("WORD"):
            bini_word += 1
        time.sleep(0.8)
        u = probe_unison(artist, title, duration)
        print(f"   unison  {u}")
        if "richsync" in u:
            unison_word += 1
        time.sleep(0.8)
        print()

    total = len(TRACKS)
    print("=== Word-level coverage ===")
    print(f"bini   : {bini_word}/{total}")
    print(f"unison : {unison_word}/{total}")


if __name__ == "__main__":
    main()
