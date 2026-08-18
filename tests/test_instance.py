"""One overlay at a time.

Nothing here leaves anything behind: the mutex is named for the test and
released at the end of it, so a re-run in the same process still passes.
"""
import sys

import pytest

from lyrica import instance

NAME = "Lyrica-test-single-instance"


@pytest.fixture(autouse=True)
def released():
    yield
    instance.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only guard")
def test_the_first_overlay_is_allowed_to_start():
    assert instance.claim(NAME)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only guard")
def test_a_second_overlay_is_turned_away():
    """Each one adds its own notification-area icon over the same lyrics.

    Which is what "ten of them in the tray" was: the overlay only appears once
    something is playing, so a launch that seems to have done nothing invites
    another, and a crash invites one more.
    """
    assert instance.claim(NAME)
    assert not instance.claim(NAME)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only guard")
def test_the_name_is_free_again_once_the_first_one_lets_go():
    assert instance.claim(NAME)
    instance.release()
    assert instance.claim(NAME)


def test_a_platform_without_the_guard_never_blocks_a_start(monkeypatch):
    """Refusing to start because the question could not be asked is worse."""
    monkeypatch.setattr(instance.sys, "platform", "linux")
    assert instance.claim(NAME)


def test_releasing_without_claiming_is_not_an_error():
    instance.release()


# --- what the launcher does with the answer ----------------------------------

def test_a_second_launch_does_not_build_a_second_overlay(monkeypatch):
    from lyrica import app

    built = []
    monkeypatch.setattr(app, "setup_logging", lambda: None)
    monkeypatch.setattr(app.config, "load", lambda: None)
    monkeypatch.setattr(app.instance, "claim", lambda: False)
    monkeypatch.setattr(app, "Overlay", lambda: built.append(True))
    app.main()
    assert not built, "a second overlay was built beside the first"


def test_the_first_launch_builds_one(monkeypatch):
    from lyrica import app

    ran = []

    class Fake:
        def run(self):
            ran.append(True)

    monkeypatch.setattr(app, "setup_logging", lambda: None)
    monkeypatch.setattr(app.config, "load", lambda: None)
    monkeypatch.setattr(app.instance, "claim", lambda: True)
    monkeypatch.setattr(app, "Overlay", Fake)
    app.main()
    assert ran
