"""Shortcuts that reach the overlay while something else has the keyboard.

Offline: nothing here claims a real key combination, because a test that did
would fight whatever the developer has running and fail for reasons that have
nothing to do with the code.
"""
import sys

import pytest

from lyrica import hotkeys


def test_every_binding_names_an_action_the_overlay_handles():
    from lyrica.app import Overlay
    for binding in hotkeys.BINDINGS:
        assert binding.action in Overlay.HOTKEY_ACTIONS, binding.label


def test_the_actions_worth_a_global_shortcut_are_bound():
    assert {b.action for b in hotkeys.BINDINGS} == {
        "toggle", "quit", "bigger", "smaller", "reset"}


def test_a_hidden_overlay_can_still_be_closed():
    # It has no window to right click and no Esc to reach, so without this the
    # only way to close one would be the task manager.
    assert "quit" in {b.action for b in hotkeys.BINDINGS}


def test_nothing_is_claimed_on_control_alone():
    # These are taken system-wide. Control+= alone would take zoom away from
    # every application on the machine for as long as the overlay runs.
    for binding in hotkeys.BINDINGS:
        assert binding.modifiers & hotkeys.MOD_ALT, binding.label
        assert binding.modifiers & hotkeys.MOD_CONTROL, binding.label


def test_the_size_actions_are_bound_on_the_numeric_pad_too():
    # Only these three: which code a keyboard sends for + - and 0 depends on
    # which of the two places you press them, and a shortcut that works on only
    # one of them is half a shortcut. A letter has no such twin.
    by_action = {}
    for binding in hotkeys.BINDINGS:
        by_action.setdefault(binding.action, []).append(binding.key)
    for action in ("bigger", "smaller", "reset"):
        assert len(by_action[action]) >= 2, f"{action} has only one spelling"


def test_a_platform_without_global_shortcuts_says_so():
    listener = hotkeys.NullListener()
    assert not listener.available
    listener.start()
    assert listener.poll() == []
    listener.stop()             # must be safe to call having never started


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only listener")
def test_presses_are_drained_oldest_first():
    listener = hotkeys.WindowsListener(bindings=())
    for action in ("bigger", "bigger", "smaller"):
        listener._events.put(action)
    assert listener.poll() == ["bigger", "bigger", "smaller"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only listener")
def test_draining_twice_does_not_repeat_a_press():
    listener = hotkeys.WindowsListener(bindings=())
    listener._events.put("reset")
    assert listener.poll() == ["reset"]
    assert listener.poll() == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only listener")
def test_stopping_one_that_never_started_is_not_an_error():
    hotkeys.WindowsListener(bindings=()).stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only listener")
def test_a_listener_with_nothing_to_claim_still_starts_and_stops():
    # The path taken when every combination is already held by another
    # application: it must not hang the startup that waits on it.
    listener = hotkeys.WindowsListener(bindings=())
    listener.start()
    assert listener.poll() == []
    listener.stop()


def test_the_platform_picks_the_listener_it_can_use():
    listener = hotkeys.create_listener()
    if sys.platform == "win32":
        assert isinstance(listener, hotkeys.WindowsListener)
    else:
        assert isinstance(listener, hotkeys.NullListener)
