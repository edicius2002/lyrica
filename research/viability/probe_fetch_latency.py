"""How long a track waits for its lyrics, and where that time goes.

The cascade is walked in order and stops as soon as nothing unasked could beat
what it holds. That is the right *policy* — it is what stops a line-level answer
ending the search while a word-level source goes unasked — but it is paid for in
wall clock, because every provider that misses is a full round trip before the
next one is even started.

This measures each provider separately for the same track, then reports what
the current order costs against two alternatives:

- **parallel, best-of** — ask everyone at once, wait for all, keep the best.
  Latency becomes the slowest single provider instead of the sum.
- **parallel, first-at-ceiling** — same, but return the moment an answer
  arrives that nothing else could beat.

Both spend every provider's quota on every track, which the sequential order
does not. That trade is the decision this is here to inform.

    python research/viability/probe_fetch_latency.py
"""
import concurrent.futures as cf
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lyrica.providers import PROVIDERS, Precision

# Real tracks, chosen to span "everyone has it" to "nobody does".
TRACKS = [
    ("Bad Bunny", "Me Porto Bonito", 178.0),
    ("Quevedo", "AHORA QUÉ", 0.0),
    ("TWICE", "OXYGEN", 0.0),
    ("Aventura", "Todavía", 0.0),
    ("Kanye West", "Runaway", 0.0),
    ("Funk Tribu", "Nakir", 0.0),
    ("Marlon Hoffstadt", "Turn It Up", 0.0),
]


def ask(provider, artist, title, duration):
    started = time.perf_counter()
    try:
        result = provider.fetch(artist, title, duration, "")
    except Exception:
        result = None
    return (provider.name, (time.perf_counter() - started) * 1000,
            result.precision if result else Precision.NONE)


def main() -> int:
    names = [p.name for p in PROVIDERS]
    print(f"{'track':34s} " + " ".join(f"{n:>17s}" for n in names))

    sequential, parallel, first_ceiling = [], [], []
    for artist, title, duration in TRACKS:
        # Every provider asked, so the same numbers can answer all three models.
        with cf.ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
            rows = list(pool.map(
                lambda p, a=artist, ti=title, d=duration: ask(p, a, ti, d),
                PROVIDERS))
        by_name = {n: (ms, tier) for n, ms, tier in rows}
        print(f"{artist + ' - ' + title:34.34s} " + " ".join(
            f"{by_name[n][1].name[:4]:>4s} {by_name[n][0]:6.0f}ms" +
            " " * 4 for n in names))

        # What the current order costs: every provider up to and including the
        # one that satisfied the ceiling.
        best, total = Precision.NONE, 0.0
        for i, p in enumerate(PROVIDERS):
            ms, tier = by_name[p.name]
            total += ms
            best = max(best, tier)
            ceiling = max((q.max_precision for q in PROVIDERS[i + 1:]),
                          default=Precision.NONE)
            if best != Precision.NONE and best >= ceiling:
                break
        sequential.append(total)

        parallel.append(max(ms for ms, _ in by_name.values()))

        # First answer that nothing else could beat, in arrival order.
        ceiling = max(p.max_precision for p in PROVIDERS)
        arrivals = sorted(rows, key=lambda r: r[1])
        stop = arrivals[-1][1]
        for _n, ms, tier in arrivals:
            if tier >= ceiling:
                stop = ms
                break
        first_ceiling.append(stop)

    print(f"\n{'model':26s} {'median':>9s} {'worst':>9s}")
    for label, series in (("sequential (today)", sequential),
                          ("parallel, best-of", parallel),
                          ("parallel, first-at-ceiling", first_ceiling)):
        print(f"{label:26s} {statistics.median(series):7.0f}ms "
              f"{max(series):7.0f}ms")

    saved = statistics.median(sequential) - statistics.median(first_ceiling)
    print(f"\nfirst-at-ceiling saves {saved:.0f} ms at the median, and spends "
          f"{len(PROVIDERS)} requests a track instead of stopping early.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
