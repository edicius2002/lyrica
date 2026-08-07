"""Can the output level be read without capturing the audio itself?

The obvious route to a reactive border is WASAPI loopback capture, and it comes
with a dependency, continuous CPU, and the fact that it hands the process every
sample the machine plays — calls included. For a visual that only needs "how
loud is it right now", that is a great deal of audio to touch.

Windows already publishes that number. `IAudioMeterInformation::GetPeakValue`
reports the current peak of the render endpoint as a single float, updated by
the audio engine itself. No samples cross into this process, no dependency, and
the call is a COM vtable jump rather than a buffer copy.

This measures whether it is usable for the job: does it move with the music,
how much does one read cost, and does it survive the device being idle.

    python research/viability/probe_audio_meter.py
"""
import ctypes
import statistics
import sys
import time
from ctypes import POINTER, byref, c_float, c_void_p
from ctypes.wintypes import DWORD, LPCWSTR

if sys.platform != "win32":
    raise SystemExit("Windows only")

ole32 = ctypes.windll.ole32
CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2

# Data flow and role for the default endpoint: what the speakers are playing.
E_RENDER = 0
E_CONSOLE = 0


class GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        ole32.CLSIDFromString(LPCWSTR(text), byref(self))


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioMeterInformation = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")

# Vtable slots, counting the three IUnknown entries every interface starts with.
GET_DEFAULT_ENDPOINT = 4        # IMMDeviceEnumerator
ACTIVATE = 3                    # IMMDevice
GET_PEAK_VALUE = 3              # IAudioMeterInformation
RELEASE = 2


def _call(obj, slot, restype, argtypes, *args):
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    return proto(vtable[slot])(obj, *args)


def _release(obj):
    if obj:
        proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        proto(vtable[RELEASE])(obj)


def open_meter():
    """The default speaker's peak meter, or raise."""
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    enumerator = c_void_p()
    hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None,
                                CLSCTX_ALL, byref(IID_IMMDeviceEnumerator),
                                byref(enumerator))
    if hr:
        raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08x}")

    device = c_void_p()
    hr = _call(enumerator, GET_DEFAULT_ENDPOINT, None,
               [ctypes.c_int, ctypes.c_int, POINTER(c_void_p)],
               E_RENDER, E_CONSOLE, byref(device))
    _release(enumerator)
    if hr:
        raise OSError(f"no default render device: 0x{hr & 0xFFFFFFFF:08x}")

    meter = c_void_p()
    hr = _call(device, ACTIVATE, None,
               [POINTER(GUID), DWORD, c_void_p, POINTER(c_void_p)],
               byref(IID_IAudioMeterInformation), CLSCTX_ALL, None,
               byref(meter))
    _release(device)
    if hr:
        raise OSError(f"no meter on the endpoint: 0x{hr & 0xFFFFFFFF:08x}")
    return meter


def peak(meter) -> float:
    value = c_float()
    hr = _call(meter, GET_PEAK_VALUE, None, [POINTER(c_float)], byref(value))
    return value.value if not hr else -1.0


def main() -> int:
    meter = open_meter()
    print("leyendo el medidor 8 s — pon musica\n")
    print(f"{'t':>5s}  {'pico':>6s}  nivel")

    samples, costs = [], []
    start = time.time()
    while time.time() - start < 8:
        t0 = time.perf_counter()
        value = peak(meter)
        costs.append((time.perf_counter() - t0) * 1000)
        samples.append(value)
        if len(samples) % 12 == 0:
            bar = "#" * int(value * 50)
            print(f"{time.time() - start:5.1f}  {value:6.3f}  {bar}")
        time.sleep(1 / 60)

    _release(meter)
    moving = max(samples) - min(samples)
    print(f"\nlecturas          {len(samples)}")
    print(f"coste por lectura median {statistics.median(costs):.4f} ms, "
          f"peor {max(costs):.3f} ms")
    print(f"rango observado   {min(samples):.3f} a {max(samples):.3f} "
          f"(amplitud {moving:.3f})")
    print(f"no nulas          {sum(1 for s in samples if s > 0.001)}/{len(samples)}")
    print("\n-> util" if moving > 0.05 else
          "\n-> plano: nada sonando, o el medidor no sigue esta salida")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
