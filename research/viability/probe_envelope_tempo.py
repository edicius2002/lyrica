"""Can the character of the music be read from loudness alone?

The endpoint meter has no spectrum, so a beat cannot be *heard* in it. But a
beat is a rise in loudness, and loudness is exactly what it reports — so the
question is not whether the information is there but whether 60 Hz of a peak
meter, already smoothed by the audio engine, keeps enough of it.

Looking the tempo up was measured and rejected: 50 % coverage and sources
disagreeing by 45 BPM (`probe_bpm.py`). This measures the other route, against
click tracks generated at tempos we already know, so the answer is checkable
rather than plausible.

Two things are wanted from it, and they are not equally hard:

- **dynamics** — how much the level moves. A steady wall of sound and a track
  with air between the hits look completely different here, and that is a
  variance, which needs no beat at all.
- **tempo** — the rate of the rises. This one has to survive the smoothing.

    python research/viability/probe_envelope_tempo.py
"""
import itertools
import math
import statistics
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("Windows only")

import winsound

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lyrica.meter import create_meter

RATE = 44100
SECONDS = 7.0
SAMPLE_HZ = 60

# What a "hit" has to clear over the recent floor to count as one. Relative, not
# absolute, so it survives the track being quiet or loud.
ONSET_RISE = 0.16
# Nothing faster than this is a separate beat; it is the same one still ringing.
MIN_GAP_S = 0.20

CASES = [
    ("lento, marcado", 90, 0.9),
    ("medio, marcado", 128, 0.9),
    ("rapido, marcado", 174, 0.9),
    ("medio, plano", 128, 0.15),
]


def click_track(bpm: float, punch: float) -> Path:
    """A tone with a hit every beat. `punch` is how far it drops between them."""
    period = 60.0 / bpm
    frames = bytearray()
    for i in range(int(RATE * SECONDS)):
        t = i / RATE
        phase = (t % period) / period
        # A sharp attack with an exponential decay, over a floor that `punch`
        # sets: 0.9 leaves near-silence between hits, 0.15 barely dips.
        hit = math.exp(-phase * 9.0)
        envelope = (1 - punch) + punch * hit
        frames += struct.pack(
            "<h", int(math.sin(2 * math.pi * 180 * t) * envelope * 0.28 * 32767))
    path = Path(tempfile.gettempdir()) / f"lyrica_tempo_{int(bpm)}_{punch}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(bytes(frames))
    return path


def sample(meter, seconds: float) -> list[float]:
    out = []
    step = 1 / SAMPLE_HZ
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        out.append(meter.raw())
        time.sleep(step)
    return out


def onsets(levels: list[float]) -> list[int]:
    """Indices where the level rose sharply over its recent floor."""
    found = []
    window = max(4, SAMPLE_HZ // 6)
    for i in range(window, len(levels)):
        recent = levels[i - window:i]
        floor = min(recent)
        if levels[i] - floor < ONSET_RISE:
            continue
        if levels[i] <= levels[i - 1]:
            continue        # only the leading edge, not the whole swell
        if found and (i - found[-1]) < MIN_GAP_S * SAMPLE_HZ:
            continue
        found.append(i)
    return found


def tempo_from(found: list[int]) -> float | None:
    if len(found) < 4:
        return None
    gaps = [(b - a) / SAMPLE_HZ for a, b in itertools.pairwise(found)]
    return 60.0 / statistics.median(gaps)


def main() -> int:
    meter = create_meter()
    if not meter.available:
        raise SystemExit("no hay medidor")

    print(f"{'caso':18s} {'real':>6s} {'medido':>8s} {'error':>7s} "
          f"{'golpes':>7s} {'dinamica':>9s}")
    for label, bpm, punch in CASES:
        path = click_track(bpm, punch)
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        time.sleep(0.6)                     # let the device start
        levels = sample(meter, SECONDS - 1.2)
        winsound.PlaySound(None, winsound.SND_PURGE)
        path.unlink(missing_ok=True)

        found = onsets(levels)
        got = tempo_from(found)
        spread = statistics.pstdev(levels) / (statistics.mean(levels) or 1)
        error = f"{abs(got - bpm) / bpm * 100:6.1f}%" if got else "      -"
        print(f"{label:18s} {bpm:6.0f} "
              f"{(f'{got:8.1f}' if got else '       -')} {error} "
              f"{len(found):7d} {spread:9.3f}")
        time.sleep(0.4)

    meter.close()
    print("\ndinamica = desviacion / media del nivel: cuanto se mueve la musica,")
    print("que no necesita encontrar ningun golpe para ser util.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
