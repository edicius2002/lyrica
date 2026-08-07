"""Probe: does NetEase expose word-level lyrics (yrc) as well as line-level?

The first NetEase probe asked for `lv/kv/tv` and found `klyric` empty every
time, which was read as "no word-level data here". But NetEase's newer
word-level format is `yrc`, requested with `yv`, and the older `klyric` is a
karaoke format that most entries never had. So the earlier conclusion may have
been an artefact of asking the wrong question.

Reports structure and counts only, never lyric text.
"""
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

SEARCH = "https://music.163.com/api/search/get"
LYRIC_V1 = "https://music.163.com/api/song/lyric/v1"
LYRIC = "https://music.163.com/api/song/lyric"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"}

TRACKS = [
    "Jay Chou Blue and White Porcelain",
    "Eason Chan Ten Years",
    "The Weeknd Blinding Lights",
    "Dua Lipa Levitating",
    "NewJeans Supernatural",
    "Bad Bunny Monaco",
]


def search_id(query: str) -> int | None:
    try:
        r = requests.post(SEARCH, data={"s": query, "type": 1, "limit": 1},
                          headers=HEADERS, timeout=12)
        songs = (r.json().get("result") or {}).get("songs") or []
        return songs[0]["id"] if songs else None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def fetch_lyrics(song_id: int) -> dict:
    """Ask both endpoints for every format they know about."""
    out = {}
    for label, url, params in (
        ("v1", LYRIC_V1, {"id": song_id, "cp": "false", "lv": 0, "kv": 0, "tv": 0, "rv": 0, "yv": 0, "ytv": 0, "yrv": 0}),
        ("legacy", LYRIC, {"id": song_id, "lv": 1, "kv": 1, "tv": -1, "yv": 1}),
    ):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=12)
            d = r.json()
        except (requests.RequestException, ValueError):
            continue
        for key in ("lrc", "klyric", "yrc", "ytlrc", "yromalrc"):
            body = (d.get(key) or {}).get("lyric") or ""
            if body:
                out.setdefault(key, (label, len(body), body[:1]))
    return out


def main() -> None:
    print("=== NetEase: which lyric formats actually come back ===\n")
    word_capable = 0
    for query in TRACKS:
        time.sleep(1.0)
        song_id = search_id(query)
        if song_id is None:
            print(f"{query!r}: no search result\n")
            continue
        formats = fetch_lyrics(song_id)
        print(f"{query!r} (id={song_id})")
        if not formats:
            print("   nothing returned\n")
            continue
        for key, (endpoint, size, _) in sorted(formats.items()):
            note = "  <-- WORD LEVEL" if key in ("yrc", "klyric") else ""
            print(f"   {key:9s} {size:6d} bytes  via {endpoint}{note}")
        if "yrc" in formats or "klyric" in formats:
            word_capable += 1
        print()

    print(f"=== {word_capable}/{len(TRACKS)} tracks offered a word-level format ===")


if __name__ == "__main__":
    main()
