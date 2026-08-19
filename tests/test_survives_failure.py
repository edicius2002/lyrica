"""Two ways the overlay could stop dead, and the guards against them.

Both were found by review rather than by use, and both share a shape: something
raised or destroyed from inside the render tick, so the tick never reached the
line that re-arms it. A frozen overlay writes its traceback to a stderr the
packaged build discards, which is why neither would have been diagnosable.
"""
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only meter")
def test_a_dead_endpoint_does_not_raise_into_the_tick():
    # `ctypes.HRESULT` as a restype raises OSError instead of returning the
    # failing code, so every `if hr:` in the module was dead on the one path it
    # existed for. Unplugging the default output invalidates the endpoint.
    import ctypes

    from lyrica.meter import Envelope, WindowsMeter

    meter = WindowsMeter.__new__(WindowsMeter)
    meter._meter = ctypes.c_void_p(1234)
    meter._value, meter.available = 0.0, True
    meter._envelope = Envelope()
    meter._call = lambda *a, **k: (_ for _ in ()).throw(OSError("device gone"))
    meter._release = staticmethod(lambda obj: None)

    assert meter.raw() == 0.0
    assert meter.available is False, "a dead meter must stop being asked"
    assert meter.character(1 / 60).level == 0.0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only meter")
def test_a_failing_status_code_is_read_rather_than_thrown():
    import ctypes

    from lyrica.meter import Envelope, WindowsMeter

    meter = WindowsMeter.__new__(WindowsMeter)
    meter._meter = ctypes.c_void_p(1234)
    meter._value, meter.available = 0.0, True
    meter._envelope = Envelope()
    meter._call = lambda *a, **k: -2147023728       # a failing HRESULT
    meter._release = staticmethod(lambda obj: None)
    assert meter.raw() == 0.0
    assert meter.available is False


def test_quitting_is_recorded_rather_than_done_where_it_is_asked():
    # The shortcuts and the tray are drained from inside the tick. Destroying
    # the root there left the rest of that tick measuring text and recolouring
    # a canvas that no longer existed.
    from lyrica.app import Overlay

    assert "quit" in Overlay.ACTIONS
    source = Overlay.ACTIONS["quit"].__code__.co_names
    assert "destroy" not in source, "quit must not destroy where it is received"
    assert "_close" in source


def test_every_way_out_goes_through_the_same_door():
    # Esc, right click, the shortcut and the tray all set the same flag, so
    # there is one place that decides when the window is actually torn down.
    import inspect

    from lyrica.app import Overlay

    bindings = inspect.getsource(Overlay._bind)
    assert "self.root.destroy()" not in bindings
    assert bindings.count("self._close()") >= 2

    tick = inspect.getsource(Overlay._tick)
    assert "self._closing" in tick
    body = tick.split("self._closing")[1]
    assert "destroy" in body.split("return")[0], "the tick must tear it down"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only meter")
def test_a_dead_endpoint_is_opened_again_rather_than_given_up_on():
    # Dropping an endpoint that stopped answering is right — it cannot be read
    # from again. Never looking for another one is what made unplugging a
    # speaker cost the border for the rest of the session.
    from lyrica.meter import DEVICE_CHECK_S, Envelope, WindowsMeter

    meter = WindowsMeter.__new__(WindowsMeter)
    meter._meter, meter._value, meter.available = None, 0.0, False
    meter._envelope = Envelope()
    meter._device_id, meter._since_check = "old-device", 0.0
    meter._release = staticmethod(lambda obj: None)
    meter._default_id = lambda: "a-live-device"
    meter.raw = lambda: 0.0

    opened = []

    def open_one():
        opened.append(True)
        meter._meter = object()

    meter._open = open_one

    meter.level(DEVICE_CHECK_S / 4)
    assert not opened, "checked on every frame rather than on a clock"

    meter.level(DEVICE_CHECK_S)
    assert opened, "a dead endpoint was never looked at again"
    assert meter.available is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only meter")
def test_the_meter_moves_to_the_device_the_sound_is_going_to():
    # The fault this exists for is silent: send the sound to a headset and the
    # endpoint opened at startup keeps answering, truthfully, that nothing is
    # coming out of it. Nothing fails, and the border simply stops moving.
    from lyrica.meter import DEVICE_CHECK_S, Envelope, WindowsMeter

    meter = WindowsMeter.__new__(WindowsMeter)
    meter._meter, meter._value, meter.available = object(), 0.0, True
    meter._envelope = Envelope()
    meter._device_id, meter._since_check = "speakers", 0.0
    meter._release = staticmethod(lambda obj: None)
    meter.raw = lambda: 0.0

    reopened = []
    meter._open = lambda: reopened.append(meter._device_id)

    meter._default_id = lambda: "speakers"
    meter.level(DEVICE_CHECK_S)
    assert not reopened, "rebuilt itself while the device had not changed"

    # And a device that will not name itself is not a different device: taking
    # `None` for a change would rebuild the meter on every check.
    meter._default_id = lambda: None
    meter.level(DEVICE_CHECK_S)
    assert not reopened, "an unanswered question was read as a new device"

    meter._default_id = lambda: "headset"
    meter.level(DEVICE_CHECK_S)
    assert reopened, "the sound moved and the meter stayed"
