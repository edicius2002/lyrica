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
from itertools import islice

logger = logging.getLogger(__name__)

CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2
E_RENDER = 0        # what the speakers play, rather than what a microphone hears
E_CONSOLE = 0

# Vtable slots, counting the three IUnknown entries every interface begins with.
GET_DEFAULT_ENDPOINT = 4    # IMMDeviceEnumerator
ACTIVATE = 3                # IMMDevice
GET_ID = 5                  # IMMDevice
GET_PEAK_VALUE = 3          # IAudioMeterInformation
RELEASE = 2

# How often the endpoint is checked against the one Windows is actually
# playing through. Two seconds is far below noticing a border that has gone
# still, and far above the cost: the check is one call for the default device
# and one for its id, against a peak read that happens sixty times a second.
DEVICE_CHECK_S = 2.0

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

# How often the window statistics are actually recomputed. `level` is what makes
# the border answer a hit, so it stays on the frame; `dynamics` and `rate` are
# read off four seconds of audio and reach nothing but the beam's swing and its
# period (beam.py:294-297), both of which move over seconds. Recomputing them
# sixty times a second buys a resolution neither has: a run of six frames is
# 100 ms, and no four-second average changes visibly in 100 ms.
#
# Counted in samples rather than seconds because that is the clock the rest of
# this class already keeps — `_index` counts pushes and MIN_GAP_S is converted
# through `sample_hz` the same way. A second clock read from the caller's `dt`
# would disagree with that one whenever the frame rate wandered.
STATS_HZ = 10.0


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
        # Fixed for the life of the envelope, and they used to be recomputed on
        # every push.
        self._rise_window = max(4, int(sample_hz / 6))
        self._min_gap = MIN_GAP_S * sample_hz
        self._stats_every = max(1, round(sample_hz / STATS_HZ))
        # The slow half of the character, and the sample it was read at. Same
        # sentinel as `_last_onset`: it makes the first call a recompute without
        # a second "have I ever" flag to keep in step.
        self._stats_at = -10 ** 9
        self._dynamics = 0.0
        self._rate = 0.0

    def push(self, level: float) -> None:
        self._values.append(level)
        self._index += 1
        if len(self._values) < self._rise_window + 2:
            return

        # The two constant-time rejections go first. Both used to sit *behind*
        # two full passes over 240 samples, and between them they turn away most
        # frames: a level that is not rising cannot be a leading edge, and
        # nothing inside MIN_GAP_S of the last onset can be an onset either.
        # Reordering is free — every test in this method is a bare `return` with
        # no side effect, so only the order the work is paid in changes, not
        # which frames come out the far end.
        if self._values[-1] <= self._values[-2]:
            return          # the leading edge only, not the whole swell
        if self._index - self._last_onset < self._min_gap:
            return

        # No `list(self._values)` here any more. `max`/`min` walk the deque
        # directly, and the floor is the `_rise_window` samples before the newest
        # one — reached from the near end through `reversed`, which is eleven
        # steps rather than a 240-element copy to slice. `min` does not care
        # that they arrive newest-first.
        spread = max(self._values) - min(self._values)
        if spread < SILENCE_SPREAD:
            return
        floor = min(islice(reversed(self._values), 1, self._rise_window + 1))
        if self._values[-1] - floor < max(MIN_RISE, ONSET_RISE * spread):
            return
        self._last_onset = self._index
        self._onsets.append(self._index)

    def character(self, level: float) -> Character:
        """The level as of this frame, and the slower reading around it.

        `level` is rebuilt every call — it carries the attack, and a frame of
        lag in it is the one thing here that would show. The other two are
        served from the last time `_statistics` ran, which is at STATS_HZ.
        """
        if self._index - self._stats_at >= self._stats_every:
            self._stats_at = self._index
            self._dynamics, self._rate = self._statistics()
        return Character(level=level, dynamics=self._dynamics, rate=self._rate)

    def _statistics(self) -> tuple[float, float]:
        """How much the window has been moving, and how often it jumps.

        Every full pass over the window lives in here, so that how often they
        are paid for is one decision in one place rather than something spread
        through `character`. Returning zeros is how "not enough to say yet" and
        "silence" are both reported, and both have to be *stored* rather than
        returned early: a frame that answered zero while the cache still held
        the loud passage before it would flicker between the two.
        """
        if len(self._values) < self.size // 4:
            return 0.0, 0.0
        count = len(self._values)
        mean = sum(self._values) / count
        if mean <= 1e-6:
            return 0.0, 0.0
        variance = sum((v - mean) ** 2 for v in self._values) / count
        spread = math.sqrt(variance) / mean

        # Only onsets still inside the window count, or a quiet passage would
        # keep reporting the rate of the loud one before it. Counted rather than
        # collected — the list this used to build was thrown away for its length.
        oldest = self._index - self.size
        inside = sum(1 for i in self._onsets if i >= oldest)
        seconds = min(self.size, self._index) / self.sample_hz
        per_minute = 60.0 * inside / seconds if seconds else 0.0

        return (_span(spread, DYNAMICS_LOW, DYNAMICS_HIGH),
                _span(per_minute, RATE_LOW, RATE_HIGH))


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

    # Carried on the class as well as set in `__init__`, because the tests that
    # prove a dead endpoint cannot raise into the render tick build one through
    # `__new__` and hand it only the parts they are about. A meter that needs
    # its whole constructor to have run before it can be read is a meter those
    # tests cannot make, and what they are testing is exactly the half-built,
    # half-broken state.
    _enumerator = None
    _device_id = None
    _since_check = 0.0

    def __init__(self):
        self._meter = None
        self._enumerator = None
        self._device_id = None
        self._since_check = 0.0
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

        # Kept, where it used to be released as soon as it had answered. The
        # default device is asked for again every couple of seconds now, and
        # building an enumerator each time is most of what that would cost.
        if self._enumerator is None:
            enumerator = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(_GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")), None,
                CLSCTX_ALL,
                byref(_GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")),
                byref(enumerator))
            if hr:
                raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08x}")
            self._enumerator = enumerator

        device = c_void_p()
        hr = self._call(self._enumerator, GET_DEFAULT_ENDPOINT,
                        [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)],
                        E_RENDER, E_CONSOLE, byref(device))
        if hr:
            raise OSError(f"no default render device: 0x{hr & 0xFFFFFFFF:08x}")

        self._device_id = self._id_of(device)

        meter = c_void_p()
        hr = self._call(device, ACTIVATE,
                        [POINTER(_GUID), DWORD, c_void_p, POINTER(c_void_p)],
                        byref(_GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")),
                        CLSCTX_ALL, None, byref(meter))
        self._release(device)
        if hr:
            raise OSError(f"endpoint has no meter: 0x{hr & 0xFFFFFFFF:08x}")
        self._meter = meter

    def _id_of(self, device) -> str | None:
        """The endpoint's identity string, or `None` if it will not say.

        Windows owns the buffer it hands back, so it is copied out and freed
        here rather than held. `None` is not an identity: a device that will
        not name itself must never compare unequal to the one already open, or
        the meter would rebuild itself every time it asked.
        """
        ident = c_void_p()
        if self._call(device, GET_ID, [POINTER(c_void_p)], byref(ident)):
            return None
        if not ident:
            return None
        name = ctypes.wstring_at(ident)
        ctypes.windll.ole32.CoTaskMemFree(ident)
        return name

    def _default_id(self) -> str | None:
        """The identity of the endpoint Windows is playing through now."""
        if self._enumerator is None:
            return None
        device = c_void_p()
        if self._call(self._enumerator, GET_DEFAULT_ENDPOINT,
                      [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)],
                      E_RENDER, E_CONSOLE, byref(device)):
            return None
        name = self._id_of(device)
        self._release(device)
        return name

    def _follow_default_device(self, dt: float) -> None:
        """Rebind to the speaker Windows is actually playing through.

        The endpoint is chosen once, and that choice can stop being true
        without anything failing. Send the sound to a headset and the old
        endpoint keeps answering, truthfully, that nothing is coming out of
        it — so the border goes still and stays still, and nothing anywhere
        says why. It is the same shape of fault as reading the display scale
        once: a thing taken at startup that the machine is free to change
        underneath.

        It is also the only way back from a dead one. `raw` drops an endpoint
        that stops answering, which is right, because it cannot be read from
        again — but until this that was permanent, and unplugging a speaker
        cost the border for the rest of the session.

        Nothing here may raise, for the reason `raw` gives.
        """
        self._since_check += dt
        if self._since_check < DEVICE_CHECK_S:
            return
        self._since_check = 0.0
        try:
            current = self._default_id()
            if self._meter is not None and (current is None
                                            or current == self._device_id):
                return
            self._release(self._meter)
            self._meter = None
            self._open()
            self.available = True
            logger.info("the audio meter moved to the current output device")
        except OSError:
            # Nothing to move to. The next check will look again, which is the
            # whole point of there being one.
            self.available = False

    @staticmethod
    def _call(obj, slot, argtypes, *args):
        """Invoke a COM method and *return* its status code.

        `c_long` rather than `ctypes.HRESULT`, and the difference is not
        cosmetic: `HRESULT` carries a `_check_retval_` that raises OSError on a
        failing code instead of returning it. Every `if hr:` in this module was
        therefore dead on exactly the path it was written for, and the exception
        went where nobody had planned for it — out of `raw()`, through the
        render tick, past the line that re-arms it.
        """
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)
        return proto(vtable[slot])(obj, *args)

    @staticmethod
    def _release(obj) -> None:
        if obj:
            vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[RELEASE])(obj)

    def raw(self) -> float:
        """The endpoint's current peak, or silence.

        Nothing here may raise. This is read once a frame from the render tick,
        and an exception escaping it skips the `after` that re-arms the loop —
        the overlay stops for good, with a traceback going to a stderr the
        packaged build discards. An endpoint can be invalidated at any moment by
        a device being unplugged, so that is a matter of when rather than if.
        """
        if self._meter is None:
            return 0.0
        try:
            value = c_float()
            failed = self._call(self._meter, GET_PEAK_VALUE, [POINTER(c_float)],
                                byref(value))
        except OSError:
            failed = True
        if failed:
            # The device is gone or the endpoint is stale. Reported once, then
            # the border simply stops reacting, which is what it does with no
            # audio anyway.
            logger.info("the audio meter stopped answering; the border will "
                        "hold its resting level", exc_info=True)
            self._release(self._meter)
            self._meter = None
            self.available = False
            return 0.0
        return value.value

    def level(self, dt: float = 1 / 60) -> float:
        """The smoothed level, 0..1. Rises at once, falls gently.

        Asymmetric on purpose. A peak meter reads nothing between beats, so a
        visual driven by it raw would strobe rather than pulse; holding the rise
        and easing the fall turns the same readings into an envelope that keeps
        the attack.
        """
        self._follow_default_device(dt)
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
        self._release(self._enumerator)
        self._enumerator = None
        self.available = False


def create_meter():
    return WindowsMeter() if sys.platform == "win32" else NullMeter()
