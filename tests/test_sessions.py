"""Platform selection and the macOS payload mapping (offline, any platform).

These run on Windows, macOS and CI alike — nothing here touches a real media
session. The macOS mapping is covered against the documented output shape,
which is the only verification available without a Mac.
"""
import sys
from datetime import UTC, datetime

import pytest

from lyrica import sessions
from lyrica.sessions import NullSessionReader, Snapshot, create_reader
from lyrica.sessions.macos import parse_timestamp, snapshot_from_payload

# Shape documented by `media-control get`. Only bundleIdentifier, playing and
# title are guaranteed; everything else is optional.
PAYLOAD = {
    "bundleIdentifier": "com.spotify.client",
    "playing": True,
    "title": "Some Song",
    "artist": "Some Artist",
    "album": "Some Album",
    "duration": 210.5,
    "elapsedTime": 42.25,
    "timestamp": "2026-08-06T21:00:00Z",
}


# --- selection --------------------------------------------------------------

def test_the_package_imports_on_every_platform():
    # The whole point of the split: importing lyrica must not require the
    # current platform's media libraries to exist.
    assert sessions.Snapshot is Snapshot


def test_a_reader_is_always_returned(monkeypatch):
    monkeypatch.setattr(sessions, "reader_classes", list)
    reader = create_reader()
    assert isinstance(reader, NullSessionReader)
    assert reader.reason


def test_an_available_reader_is_chosen(monkeypatch):
    class Available(sessions.SessionReader):
        @staticmethod
        def available():
            return True

        def _run(self):
            pass

    monkeypatch.setattr(sessions, "reader_classes", lambda: [Available])
    assert isinstance(create_reader(), Available)


def test_an_unavailable_reader_is_skipped(monkeypatch):
    class Unavailable(sessions.SessionReader):
        @staticmethod
        def available():
            return False

        def _run(self):
            pass

    monkeypatch.setattr(sessions, "reader_classes", lambda: [Unavailable])
    assert isinstance(create_reader(), NullSessionReader)


def test_a_reader_that_raises_while_checking_does_not_stop_startup(monkeypatch):
    class Exploding(sessions.SessionReader):
        @staticmethod
        def available():
            raise RuntimeError("broken")

        def _run(self):
            pass

    monkeypatch.setattr(sessions, "reader_classes", lambda: [Exploding])
    assert isinstance(create_reader(), NullSessionReader)


def test_the_null_reader_reports_nothing_playing():
    assert NullSessionReader("because").snapshot.ok is False


def test_the_reason_names_the_fix_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "media-control" in sessions.unavailable_reason()


def test_an_unsupported_platform_says_so(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert sessions.reader_classes() == []
    assert "linux" in sessions.unavailable_reason()


# --- macOS payload mapping --------------------------------------------------

def test_a_full_payload_maps_across():
    snap = snapshot_from_payload(PAYLOAD)
    assert snap.ok
    assert (snap.artist, snap.title, snap.album) == ("Some Artist", "Some Song", "Some Album")
    assert snap.duration == 210.5
    assert snap.position == 42.25
    assert snap.playing


def test_only_the_guaranteed_fields_are_required():
    # duration, artist, album and timestamp are all documented as optional.
    snap = snapshot_from_payload({"bundleIdentifier": "x", "playing": False,
                                  "title": "Just A Title"})
    assert snap.ok
    assert snap.title == "Just A Title"
    assert snap.duration == 0.0


def test_nothing_playing_maps_to_an_empty_snapshot():
    assert snapshot_from_payload(None).ok is False
    assert snapshot_from_payload({}).ok is False
    assert snapshot_from_payload({"bundleIdentifier": "x", "playing": True}).ok is False


def test_a_browser_bundle_id_is_recognised_as_a_browser():
    # The candidate ranking depends on this, and macOS reports reverse-DNS ids.
    snap = snapshot_from_payload({**PAYLOAD, "bundleIdentifier": "com.google.Chrome"})
    assert snap.is_browser
    assert not snapshot_from_payload(PAYLOAD).is_browser


def test_safari_counts_as_a_browser():
    snap = snapshot_from_payload({**PAYLOAD, "bundleIdentifier": "com.apple.Safari"})
    assert snap.is_browser


# --- timestamps -------------------------------------------------------------

def test_an_iso_timestamp_is_read_as_stated():
    assert parse_timestamp("2026-08-06T21:00:00Z") == datetime(2026, 8, 6, 21, tzinfo=UTC)


def test_a_zoneless_timestamp_is_read_as_utc():
    # Reading it as local time would silently shift the position by hours.
    assert parse_timestamp("2026-08-06T21:00:00").tzinfo is UTC


def test_epoch_seconds_and_milliseconds_are_both_understood():
    seconds = parse_timestamp(1786065752)
    millis = parse_timestamp(1786065752000)
    assert abs((seconds - millis).total_seconds()) < 1


def test_a_missing_or_unusable_timestamp_falls_back_to_now():
    # Very nearly true: the value was read by a call that just returned.
    for value in (None, "not a date", object()):
        drift = (datetime.now(UTC) - parse_timestamp(value)).total_seconds()
        assert drift == pytest.approx(0, abs=5)


# --- the mapping feeds the rest of the app ----------------------------------

def test_a_macos_snapshot_still_produces_lookup_candidates():
    snap = snapshot_from_payload({
        "bundleIdentifier": "com.google.Chrome", "playing": True,
        "title": "Dua Lipa - Levitating (Official Music Video)", "artist": "Dua Lipa",
    })
    assert snap.lookup_candidates()[0] == ("Dua Lipa", "Levitating")
