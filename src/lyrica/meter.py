"""How loud the machine is playing, right now.

The obvious way to drive something off the music is to capture the audio —
WASAPI loopback — and it costs a dependency, continuous decoding, and handing
this process every sample the machine plays, calls included. For a visual whose
only question is "how loud is it", that is a great deal of audio to touch.

Windows already publishes the answer. `IAudioMeterInformation::GetPeakValue`
reports the render endpoint's current peak as one float, kept up to date by the
audio engine. No samples cross into this process, nothing is decoded here, and
a read is a vtable jump — measured at 0.21 ms median. Verified against a tone
generated for the purpose: the reading followed its envelope to the exact peak
it was written at.

What it cannot give is any sense of *what* is playing. There is no spectrum, so
bass cannot be told from treble and a beat cannot be found — only its loudness.
That is the ceiling on anything built from this.
"""
import ctypes
import logging
import math
import sys
from collections import deque
from ctypes import POINTER, byref, c_float, c_void_p
from ctypes.wintypes import DWORD, LPCWSTR
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2
E_RENDER = 0        # what the speakers play, rather than what a microphone hears
E_CONSOLE = 0

# Vtable slots, counting the three IUnknown entries every interface begins with.
GET_DEFAULT_ENDPOINT = 4    # IMMDeviceEnumerator
ACTIVATE = 3                # IMMDevice
GET_PEAK_VALUE = 3          # IAudioMeterInformation
RELEASE = 2

# How fast the reading is allowed to fall. A peak meter drops to nothing between
# beats, and a visual following it raw strobes; letting it rise instantly but
# decay over about a fifth of a second turns the same numbers into an envelope.
RISE = 1.0
FALL_PER_SECOND = 5.0


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        ctypes.windll.ole32.CLSIDFromString(LPCWSTR(text), byref(self))


# How long a window the character is read from. Long enough to hold several
# beats of slow music, short enough to notice a track changing.
WINDOW_S = 4.0

# What a rise has to clear over the recent floor, as a fraction of the range the
# level has been moving through. A fraction rather than a fixed step: an
# absolute threshold is set for one particular dynamic range and returned no
# onsets at all for five of eight measured cases, every one of them a
# compressed master.
ONSET_RISE = 0.35
# And an absolute floor underneath it, which the relative rule needs to be safe.
# On a level that barely moves, a fraction of a tiny range is a tiny number, and
# the signal's own ripple clears it over and over — a flat tone measured a rate
# of 0.56 before this, which is a beat read out of noise.
MIN_RISE = 0.02
SILENCE_SPREAD = 0.012
MIN_GAP_S = 0.20

# The range of "dynamics" worth mapping. Measured: 0.05 for a master compressed
# until it barely moves, 0.94 for one with silence between its hits. Most music
# lives well below the top of that, so the useful span is narrower than the
# extremes.
DYNAMICS_LOW, DYNAMICS_HIGH = 0.06, 0.45

# Onsets per minute, over the same measurements. Not a tempo — which multiple of
# the beat this counts is not recoverable from loudness, and a visual that
# claimed one would be wrong about half the time and obviously so.
RATE_LOW, RATE_HIGH = 60.0, 220.0


@dataclass(frozen=True)
class Character:
    """What the music is doing, as far as loudness can say.

    `level` is now, `dynamics` is how much it has been moving, and `rate` is how
    often it rises. All three are 0..1 and none of them is a tempo.
    """
    level: float = 0.0
    dynamics: float = 0.0
    rate: float = 0.0


class Envelope:
    """A rolling read of the level: how much it moves, and how often it jumps.

    Kept separate from the meter that feeds it so it can be tested by handing
    it numbers, without a sound card or a COM call anywhere near it.
    """

    def __init__(self, sample_hz: float = 60.0, window_s: float = WINDOW_S):
        self.sample_hz = sample_hz
        self.size = max(8, int(sample_hz * window_s))
        self._values: deque[float] = deque(maxlen=self.size)
        self._onsets: deque[int] = deque(maxlen=64)
        self._index = 0
        self._last_onset = -10 ** 9

    def push(self, level: float) -> None:
        self._values.append(level)
        self._index += 1
        window = max(4, int(self.sample_hz / 6))
        if len(self._values) < window + 2:
            return

        recent = list(self._values)
        spread = max(recent) - min(recent)
        if spread < SILENCE_SPREAD:
            return
        floor = min(recent[-window - 1:-1])
        if recent[-1] - floor < max(MIN_RISE, ONSET_RISE * spread):
            return
        if recent[-1] <= recent[-2]:
            return          # the leading edge only, not the whole swell
        if self._index - self._last_onset < MIN_GAP_S * self.sample_hz:
            return
        self._last_onset = self._index
        self._onsets.append(self._index)

    def character(self, level: float) -> Character:
        if len(self._values) < self.size // 4:
            return Character(level=level)

        values = list(self._values)
        mean = sum(values) / len(values)
        if mean <= 1e-6:
            return Character(level=level)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        spread = math.sqrt(variance) / mean

        # Only onsets still inside the window count, or a quiet passage would
        # keep reporting the rate of the loud one before it.
        oldest = self._index - self.size
        recent = [i for i in self._onsets if i >= oldest]
        seconds = min(self.size, self._index) / self.sample_hz
        per_minute = 60.0 * len(recent) / seconds if seconds else 0.0

        return Character(
            level=level,
            dynamics=_span(spread, DYNAMICS_LOW, DYNAMICS_HIGH),
            rate=_span(per_minute, RATE_LOW, RATE_HIGH),
        )


def _span(value: float, low: float, high: float) -> float:
    return max(0.0, min(1.0, (value - low) / (high - low)))


class NullMeter:
    """What a platform without an endpoint meter gets: silence, forever."""

    available = False

    def level(self, dt: float = 1 / 60) -> float:
        return 0.0

    def character(self, dt: float = 1 / 60) -> Character:
        return Character()

    def close(self) -> None:
        pass


class WindowsMeter:
    """The default speaker's peak, smoothed into something worth drawing."""

    def __init__(self):
        self._meter = None
        self._value = 0.0
        self._envelope = Envelope()
        self.available = False
        try:
            self._open()
            self.available = True
        except OSError:
            # No endpoint, no meter, or COM refused. The overlay does not need
            # this to work, so it is reported and dropped.
            logger.info("no audio meter available; the border stays still",
                        exc_info=True)

    def _open(self) -> None:
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)

        enumerator = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(_GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")), None,
            CLSCTX_ALL, byref(_GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")),
            byref(enumerator))
        if hr:
            raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08x}")

        device = c_void_p()
        hr = self._call(enumerator, GET_DEFAULT_ENDPOINT,
                        [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)],
                        E_RENDER, E_CONSOLE, byref(device))
        self._release(enumerator)
        if hr:
            raise OSError(f"no default render device: 0x{hr & 0xFFFFFFFF:08x}")

        meter = c_void_p()
        hr = self._call(device, ACTIVATE,
                        [POINTER(_GUID), DWORD, c_void_p, POINTER(c_void_p)],
                        byref(_GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")),
                        CLSCTX_ALL, None, byref(meter))
        self._release(device)
        if hr:
            raise OSError(f"endpoint has no meter: 0x{hr & 0xFFFFFFFF:08x}")
        self._meter = meter

    @staticmethod
    def _call(obj, slot, argtypes, *args):
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
        return proto(vtable[slot])(obj, *args)

    @staticmethod
    def _release(obj) -> None:
        if obj:
            vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[RELEASE])(obj)

    def raw(self) -> float:
        if self._meter is None:
            return 0.0
        value = c_float()
        hr = self._call(self._meter, GET_PEAK_VALUE, [POINTER(c_float)],
                        byref(value))
        return 0.0 if hr else value.value

    def level(self, dt: float = 1 / 60) -> float:
        """The smoothed level, 0..1. Rises at once, falls gently.

        Asymmetric on purpose. A peak meter reads nothing between beats, so a
        visual driven by it raw would strobe rather than pulse; holding the rise
        and easing the fall turns the same readings into an envelope that keeps
        the attack.
        """
        raw = self.raw()
        if raw >= self._value:
            self._value = raw
        else:
            self._value = max(raw, self._value - FALL_PER_SECOND * dt)
        return self._value

    def character(self, dt: float = 1 / 60) -> Character:
        """The level, and what the music has been doing around it."""
        now = self.level(dt)
        self._envelope.push(now)
        return self._envelope.character(now)

    def close(self) -> None:
        self._release(self._meter)
        self._meter = None
        self.available = False


def create_meter():
    return WindowsMeter() if sys.platform == "win32" else NullMeter()
