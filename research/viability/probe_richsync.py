"""Probe: can Musixmatch richsync (word-level) be reached at all?

The phase 0 probe called `track.richsync.get` by artist and track name and got
404 for every song, including ones the same API reported as `has_richsync=True`.
That reads like the endpoint being gone — but the more likely explanation is
that it keys on the numeric `track_id`, not on names.

This tries the full chain the desktop app appears to use:

    token.get  ->  matcher.track.get  ->  track.richsync.get

Reports structure and counts only, never lyric text.
"""
import json
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://apic-desktop.musixmatch.com/ws/1.1"
APP_ID = "web-desktop-app-v1.0"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": "AWSELB=0; AWSELBCORS=0",
}

# Mixed on purpose: current global hits, older catalogue, and Spanish-language
# material, since coverage outside English chart pop is the open question.
TRACKS = [
    ("The Weeknd", "Blinding Lights"),
    ("Taylor Swift", "Anti-Hero"),
    ("Bad Bunny", "Monaco"),
    ("Soda Stereo", "De Musica Ligera"),
    ("Radiohead", "Karma Police"),
    ("Dr. Dre", "Still D.R.E."),
    ("Drake", "9"),
    ("Romeo Santos", "Odio"),
    ("Porter Robinson", "Goodbye To A World"),
    ("NewJeans", "Supernatural"),
    ("Dua Lipa", "Levitating"),
    ("Kendrick Lamar", "HUMBLE."),
    ("Billie Eilish", "BIRDS OF A FEATHER"),
    ("Peso Pluma", "Ella Baila Sola"),
    ("Los Prisioneros", "Tren al sur"),
    ("Daft Punk", "Get Lucky"),
]


def call(endpoint: str, params: dict) -> dict | None:
    try:
        r = requests.get(f"{BASE}/{endpoint}", params={**params, "app_id": APP_ID, "format": "json"},
                         headers=HEADERS, timeout=15)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"   {endpoint}: transport failure {type(e).__name__}: {e}")
        return None


def status_of(payload: dict | None) -> int:
    if not payload:
        return -1
    return payload.get("message", {}).get("header", {}).get("status_code", -1)


def get_token() -> str | None:
    d = call("token.get", {})
    if status_of(d) != 200:
        print(f"token.get failed: status {status_of(d)}")
        return None
    return d["message"]["body"]["user_token"]


def match_track(token: str, artist: str, title: str) -> dict | None:
    d = call("matcher.track.get", {"q_artist": artist, "q_track": title, "usertoken": token})
    if status_of(d) != 200:
        return None
    return d["message"]["body"].get("track")


def richsync_by_id(token: str, track_id: int) -> tuple[int, list | None]:
    d = call("track.richsync.get", {"track_id": track_id, "usertoken": token})
    status = status_of(d)
    if status != 200:
        return status, None
    body = d["message"]["body"].get("richsync") or {}
    raw = body.get("richsync_body") or ""
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def describe(parsed: list) -> None:
    """Structure only — counts and timings, never the words themselves."""
    total_words = sum(len(line.get("l", [])) for line in parsed)
    first = parsed[0]
    print(f"   RICHSYNC OK: {len(parsed)} lines, {total_words} word events")
    print(f"   line shape : keys={sorted(first.keys())}")
    print(f"   line 1 spans {first.get('ts')}s -> {first.get('te')}s "
          f"with {len(first.get('l', []))} word offsets")
    if first.get("l"):
        offsets = [w.get("o") for w in first["l"][:5]]
        print(f"   word offset field 'o', first five: {offsets}")


def main() -> None:
    print("=== 1. Token ===")
    token = get_token()
    if not token:
        return
    print(f"got a token ({len(token)} chars), no registration\n")

    print("=== 2. matcher.track.get -> track.richsync.get ===")
    word, line_only, no_match = [], [], []
    for artist, title in TRACKS:
        time.sleep(1.5)
        label = f"{artist} - {title}"
        print(label)
        track = match_track(token, artist, title)
        if not track:
            print("   no match")
            no_match.append(label)
            continue
        tid = track.get("track_id")
        has_rich = bool(track.get("has_richsync"))
        has_subs = bool(track.get("has_subtitles"))
        print(f"   matched track_id={tid} has_richsync={has_rich} has_subtitles={has_subs}")
        if not has_rich:
            (line_only if has_subs else no_match).append(label)
            print("   flagged as having no richsync")
            continue
        time.sleep(1.5)
        status, parsed = richsync_by_id(token, tid)
        if parsed:
            word.append(label)
            describe(parsed)
        else:
            line_only.append(label)
            print(f"   flagged richsync but the fetch failed: status {status}")
        print()

    total = len(TRACKS)
    print("=== Coverage ===")
    print(f"word-level   : {len(word)}/{total}")
    print(f"line-only    : {len(line_only)}/{total}")
    print(f"nothing      : {len(no_match)}/{total}")
    if line_only:
        print("\nno word-level for:")
        for label in line_only:
            print(f"   {label}")


if __name__ == "__main__":
    main()
