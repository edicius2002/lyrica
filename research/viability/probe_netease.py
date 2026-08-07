"""Probe: can NetEase serve as a second synced provider, queried directly?

`syncedlyrics` already reaches NetEase, but depending on it would pull
beautifulsoup4, rapidfuzz and their trees into the runtime for one source. This
checks whether the two endpoints it wraps can be called directly instead.

Reports sizes and flags only, never lyric text.
"""
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

SEARCH = "https://music.163.com/api/search/get"
LYRIC = "https://music.163.com/api/song/lyric"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"}

# Western, Spanish and Korean, since NetEase is a Chinese service and its
# coverage outside Mandarin pop is the open question.
QUERIES = [
    "Porter Robinson Goodbye To A World",
    "Soda Stereo De Musica Ligera",
    "NewJeans Supernatural",
    "The Weeknd Blinding Lights",
    "Radiohead Karma Police",
    "Bad Bunny Monaco",
]


def probe(query: str) -> None:
    started = time.perf_counter()
    try:
        r = requests.post(SEARCH, data={"s": query, "type": 1, "limit": 3, "offset": 0},
                          headers=HEADERS, timeout=12)
        songs = (r.json().get("result") or {}).get("songs") or []
    except Exception as e:
        print(f"{query!r}: search failed - {type(e).__name__}: {e}")
        return
    if not songs:
        print(f"{query!r}: no search results")
        return

    song = songs[0]
    artists = ", ".join(a.get("name", "") for a in song.get("artists", []))
    try:
        r2 = requests.get(LYRIC, params={"id": song["id"], "lv": 1, "kv": 1, "tv": -1},
                          headers=HEADERS, timeout=12)
        d = r2.json()
    except Exception as e:
        print(f"{query!r}: lyric fetch failed - {type(e).__name__}: {e}")
        return

    elapsed = (time.perf_counter() - started) * 1000
    lrc = (d.get("lrc") or {}).get("lyric") or ""
    klyric = (d.get("klyric") or {}).get("lyric") or ""
    first = lrc.splitlines()[0] if lrc else ""
    synced = first.startswith("[")
    duration_s = song.get("duration", 0) / 1000

    print(f"{query!r}")
    print(f"   match    {song.get('name')!r} by {artists!r} ({duration_s:.0f}s)")
    print(f"   lrc      {len(lrc)} bytes, synced={synced}")
    print(f"   klyric   {len(klyric)} bytes (word-level)")
    print(f"   elapsed  {elapsed:.0f} ms")


def main() -> None:
    print("=== NetEase direct API ===\n")
    for query in QUERIES:
        probe(query)
        print()

    print("=== Burst: 15 searches, looking for throttling ===")
    codes = []
    started = time.perf_counter()
    for _ in range(15):
        r = requests.post(SEARCH, data={"s": "Coldplay Yellow", "type": 1, "limit": 1},
                          headers=HEADERS, timeout=12)
        codes.append(r.status_code)
    total = time.perf_counter() - started
    from collections import Counter
    print(f"15 requests in {total:.1f}s -> {dict(Counter(codes))}")


if __name__ == "__main__":
    main()
