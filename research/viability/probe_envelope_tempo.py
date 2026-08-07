"""Can the character of the music be read from loudness alone?

The endpoint meter has no spectrum, so a beat cannot be *heard* in it. But a
beat is a rise in loudness, and loudness is exactly what it reports — so the
question is not whether the information is there but whether 60 Hz of a peak
meter, already smoothed by the audio engine, keeps enough of it.

Looking the tempo up was measured and rejected: 50 % coverage and sources
disagreeing by 45 BPM (`probe_bpm.py`). This measures the other route, against
signals generated at tempos we already know, so the answer is checkable rather
than plausible.

**The conclusion, so nobody has to re-derive it.** The onset *rate* comes back
within about 4 % on everything tried here, including masters compressed until
their dynamic range is a twentieth of a clean click track. What does not come
back is *which multiple of the beat that rate is*. A kick with an off-beat hat
gives twice the onsets and reads as twice the tempo — measured at 257 for a
track at 128 — and folding the answer into a plausible range fixes that case by
breaking others: a 174 BPM track estimated at 182 crosses the fold and comes out
at 91.

That is not a defect in this detector. It is the same failure the commercial
catalogues have, and it is why two of them disagree by an octave about the same
song. Resolving it needs spectral flux, which a loudness meter cannot supply at
any sampling rate.

So a *rate* is measurable and a *tempo* is not, and anything built on this
should ask only for the first. Something that gets busier with busier music
never looks wrong; something that claims a BPM is wrong about half the time and
obviously so.

The other half is easier and more useful than it sounds. **Dynamics** — the
level's deviation over its mean — needs no beat at all, never fails, and
separates a wall of sound from something with air in it, which is most of what
"the style of music" means to a border light.

    python research/viability/probe_envelope_tempo.py
"""
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
SECONDS = 8.0
SAMPLE_HZ = 60

# What a "hit" has to clear over the recent floor, as a fraction of the range
# the track has been moving through. A fraction rather than a fixed step: an
# absolute one is set for some particular dynamic range and fails outside it,
# which lost five of the eight cases below before this was relative.
ONSET_RISE = 0.35
# Below this the level is not moving enough to hold a beat, only noise.
SILENCE_SPREAD = 0.012
# Nothing faster than this is a separate beat; it is the same one still ringing.
MIN_GAP_S = 0.20

# A clean click track answers easily and proves nothing about real music, so
# each case adds something that makes it harder: a sustained chord filling the
# gaps, compression flattening the range, an off-beat hat offering a second
# defensible answer, a song's own swells moving the floor underneath.
CASES = [
    ("clic limpio", 128, {"punch": 0.90}),
    ("+ acorde de fondo", 128, {"punch": 0.90, "pad": 0.45}),
    ("comprimido (tipico)", 128, {"punch": 0.45, "pad": 0.55}),
    ("muy comprimido", 128, {"punch": 0.25, "pad": 0.70}),
    ("+ hat a contratiempo", 128, {"punch": 0.90, "offbeat": 0.55}),
    ("+ subidas y bajadas", 128, {"punch": 0.70, "pad": 0.35, "swell": 5.0}),
    ("techno rapido", 174, {"punch": 0.55, "pad": 0.55}),
    ("balada lenta", 72, {"punch": 0.50, "pad": 0.50}),
]


def click_track(bpm: float, punch: float, pad: float = 0.0,
                offbeat: float = 0.0, swell: float = 0.0) -> Path:
    """A hit every beat, plus whatever makes it harder to find.

    `punch` is how far the level drops between hits — 0.9 leaves near-silence,
    0.25 is a compressed master. `pad` is a constant tone under everything, as
    real instruments are. `offbeat` puts a second hit halfway between beats, as
    a hat does. `swell` moves the whole level over a cycle of that many seconds.
    """
    period = 60.0 / bpm
    frames = bytearray()
    for i in range(int(RATE * SECONDS)):
        t = i / RATE
        hit = math.exp(-((t % period) / period) * 9.0)
        envelope = (1 - punch) + punch * hit
        if offbeat:
            half = ((t + period / 2) % period) / period
            envelope += offbeat * math.exp(-half * 9.0)
        if swell:
            envelope *= 0.55 + 0.45 * math.sin(2 * math.pi * t / swell)
        signal = math.sin(2 * math.pi * 180 * t) * envelope
        if pad:
            signal += math.sin(2 * math.pi * 110 * t) * pad
        frames += struct.pack(
            "<h", int(max(-1.0, min(1.0, signal * 0.26)) * 32767))
    path = Path(tempfile.gettempdir()) / "lyrica_tempo_probe.wav"
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
    found: list[int] = []
    window = max(4, SAMPLE_HZ // 6)
    span = max(3, SAMPLE_HZ * 2)
    for i in range(window, len(levels)):
        recent = levels[max(0, i - span):i + 1]
        spread = max(recent) - min(recent)
        if spread < SILENCE_SPREAD:
            continue
        floor = min(levels[i - window:i])
        if levels[i] - floor < ONSET_RISE * spread:
            continue
        if levels[i] <= levels[i - 1]:
            continue        # only the leading edge, not the whole swell
        if found and (i - found[-1]) < MIN_GAP_S * SAMPLE_HZ:
            continue
        found.append(i)
    return found


def onset_rate(found: list[int]) -> float | None:
    """Onsets per minute. Deliberately not called a tempo.

    The span over the count rather than the median gap, because the gaps are
    quantised by the sampling rate — at 60 Hz a 0.469 s beat can only measure as
    27 or 28 samples, and always landing on 27 is where a steady +4 % came from.

    What this is *not* is a tempo. Which multiple of the beat this rate
    represents is not recoverable from loudness, and folding it into a plausible
    range makes matters worse rather than better — it fixed the off-beat-hat
    case and turned a 174 BPM track into 91.
    """
    if len(found) < 4:
        return None
    span = (found[-1] - found[0]) / SAMPLE_HZ
    return 60.0 * (len(found) - 1) / span if span else None


def main() -> int:
    meter = create_meter()
    if not meter.available:
        raise SystemExit("no hay medidor")

    print(f"{'caso':22s} {'real':>6s} {'medido':>8s} {'error':>8s} "
          f"{'golpes':>7s} {'dinamica':>9s}")
    answered, errors = 0, []
    for label, bpm, kw in CASES:
        path = click_track(bpm, **kw)
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        time.sleep(0.7)
        levels = sample(meter, SECONDS - 1.4)
        winsound.PlaySound(None, winsound.SND_PURGE)

        found = onsets(levels)
        got = onset_rate(found)
        spread = statistics.pstdev(levels) / (statistics.mean(levels) or 1)
        octave = ""
        if got:
            answered += 1
            errors.append(abs(got - bpm) / bpm * 100)
            error = f"{errors[-1]:7.1f}%"
            if min(abs(got - bpm * 2), abs(got - bpm / 2)) < abs(got - bpm):
                octave = "  <-- octava"
        else:
            error = f"{'-':>8s}"
        print(f"{label:22s} {bpm:6.0f} "
              f"{(f'{got:8.1f}' if got else f'{chr(45):>8s}')} {error} "
              f"{len(found):7d} {spread:9.3f}{octave}")
        time.sleep(0.4)

    path.unlink(missing_ok=True)
    meter.close()
    if errors:
        print(f"\n{answered} de {len(CASES)} dieron una tasa; "
              f"error mediano {statistics.median(errors):.1f}%")
    print("\ndinamica = desviacion / media del nivel: cuanto se mueve la musica,")
    print("que no necesita encontrar ningun golpe para ser util.")
    print("una fila marcada como octava no es un fallo del detector: es la")
    print("ambiguedad que los catalogos comerciales tampoco resuelven.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
