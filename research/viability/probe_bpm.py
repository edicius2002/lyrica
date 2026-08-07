"""Can a track's tempo be looked up for free, and how often does it answer?

A border beam locked to the beat is worth exactly as much as the coverage of
whatever supplies the beat. This measures that before anything is drawn,
because a feature that works for a third of a library is not a feature.

Three candidates, all keyless or free:

- **Deezer** publishes `bpm` on its public track endpoint. No key, no
  registration, and the search is the same shape the cover sources already use.
- **AcousticBrainz** has analysed tempo keyed by MusicBrainz recording id, so
  it costs two requests and only answers for tracks that were submitted before
  the project stopped accepting them.
- **GetSongBPM** is a dedicated database, free with an API key and a required
  attribution link back to the site.

Also reports what a *rate* alone can and cannot do: the phase — where the
downbeat actually falls — is not in any of these, and a beam that runs at the
right speed from the wrong offset is a different thing from one that lands on
the beat.

    python research/viability/probe_bpm.py
"""
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

HEADERS = {"User-Agent": "lyrica/0.1.0 (personal overlay; tempo probe)"}

# A spread on purpose: reggaeton, pop, rock, electronic, latin, and something
# obscure enough to test the tail rather than the head of each catalogue.
TRACKS = [
    ("Bad Bunny", "Me Porto Bonito"),
    ("Bad Bunny", "Tarot"),
    ("Bad Bunny", "La Difícil"),
    ("Quevedo", "YANKEE"),
    ("Aventura", "Todavía"),
    ("Daft Punk", "Aerodynamic"),
    ("Daft Punk", "Harder, Better, Faster, Stronger"),
    ("TWICE", "OXYGEN"),
    ("Yotto", "Song From The Sun"),
    ("Zaxx", "Signal"),
    ("Michael Brun", "All I Ever Wanted"),
    ("Boards of Canada", "Roygbiv"),
    ("Kobosil", "300G"),
    ("Marlon Hoffstadt", "Shuga"),
]


def deezer(artist: str, title: str) -> float | None:
    """Tempo from Deezer's public catalogue. No key, no registration."""
    query = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
    try:
        r = requests.get(f"https://api.deezer.com/search?q={query}&limit=1",
                         headers=HEADERS, timeout=10)
        r.raise_for_status()
        hits = r.json().get("data") or []
        if not hits:
            return None
        # The search result does not carry bpm; the track endpoint does.
        track = requests.get(f"https://api.deezer.com/track/{hits[0]['id']}",
                             headers=HEADERS, timeout=10)
        track.raise_for_status()
        bpm = track.json().get("bpm")
        return float(bpm) if bpm else None
    except (requests.RequestException, ValueError, KeyError):
        return None


def acousticbrainz(artist: str, title: str) -> float | None:
    """Tempo by way of MusicBrainz, which is what keys it."""
    try:
        query = f'artist:"{artist}" AND recording:"{title}"'
        r = requests.get("https://musicbrainz.org/ws/2/recording",
                         params={"query": query, "fmt": "json", "limit": 1},
                         headers=HEADERS, timeout=12)
        r.raise_for_status()
        hits = r.json().get("recordings") or []
        if not hits:
            return None
        mbid = hits[0]["id"]
        time.sleep(1.1)     # MusicBrainz asks for one request a second
        a = requests.get(
            f"https://acousticbrainz.org/api/v1/{mbid}/low-level",
            headers=HEADERS, timeout=12)
        if a.status_code != 200:
            return None
        return float(a.json()["rhythm"]["bpm"])
    except (requests.RequestException, ValueError, KeyError):
        return None


SOURCES = (("deezer", deezer), ("acousticbrainz", acousticbrainz))


def main() -> int:
    print(f"{'track':44s} " + " ".join(f"{n:>16s}" for n, _ in SOURCES))
    found = {name: [] for name, _ in SOURCES}
    timings = {name: [] for name, _ in SOURCES}

    for artist, title in TRACKS:
        cells = []
        for name, fn in SOURCES:
            start = time.perf_counter()
            bpm = fn(artist, title)
            timings[name].append((time.perf_counter() - start) * 1000)
            found[name].append(bpm)
            cells.append(f"{bpm:16.1f}" if bpm else f"{'—':>16s}")
        print(f"{artist + ' - ' + title:44.44s} " + " ".join(cells))

    print()
    for name, _ in SOURCES:
        hits = [b for b in found[name] if b]
        print(f"{name:16s} {len(hits):2d}/{len(TRACKS)} "
              f"({100 * len(hits) / len(TRACKS):3.0f}%)   "
              f"median {statistics.median(timings[name]):6.0f} ms")

    both = [(d, a) for d, a in zip(found["deezer"], found["acousticbrainz"],
                                   strict=True) if d and a]
    if both:
        gaps = [abs(d - a) for d, a in both]
        halves = sum(1 for d, a in both if abs(d * 2 - a) < 4 or abs(a * 2 - d) < 4)
        print(f"\nboth answered for {len(both)}: median disagreement "
              f"{statistics.median(gaps):.1f} BPM, {halves} of them a "
              f"half/double-time reading of each other")

    print("\nNeither source carries phase — where the downbeat falls — only rate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
