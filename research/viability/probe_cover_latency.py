"""Where the time actually goes between a track starting and its cover appearing.

Two paths, and they have nothing in common. A track played before is answered
from disk and the whole cost is decoding and deriving; a new one waits on the
network and everything else is noise beside it. Optimising the wrong one is
easy, so this measures both.

The render-thread column is the one that matters most for smoothness: work on
the worker thread delays the cover, but work on the render thread delays the
lyrics.

    python research/viability/probe_cover_latency.py [--network]
"""
import statistics
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lyrica import artwork, config, songcolour

WINDOW = (900, 300)
THUMB = 78
REPEATS = 7


def timed(fn, *args, repeats: int = REPEATS):
    best, total = 1e9, []
    for _ in range(repeats):
        start = time.perf_counter()
        fn(*args)
        took = time.perf_counter() - start
        best = min(best, took)
        total.append(took)
    return best * 1000, statistics.mean(total) * 1000


def main() -> int:
    covers = sorted((config.cache_root() / "covers").glob("*.img"))
    covers = [p for p in covers if p.stat().st_size > 4096]
    if not covers:
        print("no cached covers — play something first")
        return 1
    print(f"{len(covers)} cached covers\n")

    root = tk.Tk()
    root.withdraw()
    from PIL import ImageTk

    rows = []
    for path in covers[:12]:
        data = path.read_bytes()
        read, _ = timed(path.read_bytes)
        thumb, _ = timed(artwork.make_thumbnail, data, THUMB)
        back, _ = timed(artwork.make_backdrop, data, *WINDOW)
        colour, _ = timed(songcolour.extract, data)

        prepared = artwork.make_backdrop(data, *WINDOW)
        small = artwork.make_thumbnail(data, THUMB)
        to_tk_back, _ = timed(ImageTk.PhotoImage, prepared.image)
        to_tk_thumb, _ = timed(ImageTk.PhotoImage, small)
        rows.append((path.stem[:10], len(data) // 1024, read, thumb, back,
                     colour, to_tk_back, to_tk_thumb))

    head = ("cover", "KB", "read", "thumb", "backdrop", "colour",
            "tk:back", "tk:thumb")
    print(f"{head[0]:11s} {head[1]:>5s} " + " ".join(f"{h:>9s}" for h in head[2:]))
    for r in rows:
        print(f"{r[0]:11s} {r[1]:5d} " + " ".join(f"{v:9.2f}" for v in r[2:]))

    worker = [r[3] + r[4] + r[5] for r in rows]
    render = [r[6] + r[7] for r in rows]
    print(f"\n{'':11s} {'':5s} "
          f"worker thread (decode + derive): median {statistics.median(worker):6.1f} ms"
          f"   worst {max(worker):6.1f} ms")
    print(f"{'':11s} {'':5s} "
          f"render thread (Tk conversion):   median {statistics.median(render):6.1f} ms"
          f"   worst {max(render):6.1f} ms")

    root.destroy()

    if "--network" not in sys.argv:
        print("\n(pass --network to also time the two sources on a cache miss)")
        return 0

    # Without this Discogs answers instantly and falsely: no token means it
    # short-circuits, which reads as a very fast miss rather than as unconfigured.
    config.load()
    if not artwork.discogs_token():
        print("\nno Discogs token configured; only Apple can be timed")

    print("\ntime to a cover on a cache miss, per source")
    probes = [("Daft Punk", "Aerodynamic", ""),
              ("Quevedo", "YANKEE", ""),
              ("TWICE", "OXYGEN", ""),
              ("Aventura", "Todavia", "")]
    for artist, title, album in probes:
        sources = (("apple  ", artwork.fetch_cover),
                   ("discogs", artwork.fetch_cover_discogs))
        for name, fetch in sources:
            start = time.perf_counter()
            got = fetch(artist, title, album)
            took = (time.perf_counter() - start) * 1000
            size = f"{len(got) // 1024} KB" if got else "MISS"
            print(f"  {artist[:16]:16s} {name} {took:7.0f} ms  {size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
