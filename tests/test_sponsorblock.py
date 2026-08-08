"""The mapping between a video's clock and the recording's, and how it is asked for."""
import json

import pytest
import requests

from lyrica import sponsorblock
from lyrica.sponsorblock import Cuts, cuts_for


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))


# --- the mapping ------------------------------------------------------------

def test_no_cuts_is_the_identity():
    # An unannotated video and a video annotated as needing nothing must behave
    # the same, so nothing downstream has to know which it is looking at.
    plain = Cuts()
    for t in (0.0, 12.5, 300.0):
        assert plain.to_song(t) == t
        assert plain.to_video(t) == t


def test_an_intro_shifts_the_whole_recording():
    # Despacito: the song starts 21.8 s into the video.
    cuts = Cuts(((0.0, 21.808),))
    assert cuts.intro == pytest.approx(21.808)
    assert cuts.to_song(21.808) == pytest.approx(0.0)
    assert cuts.to_song(121.808) == pytest.approx(100.0)
    assert cuts.to_video(100.0) == pytest.approx(121.808)


def test_a_stretch_in_the_middle_is_removed_as_well():
    # Two videos in fifteen carry one. Past it a constant offset would be wrong
    # by its whole length, which is why this is a mapping and not a number.
    cuts = Cuts(((0.0, 253.179), (481.784, 508.178)))
    assert cuts.to_song(300.0) == pytest.approx(46.821)      # before it
    assert cuts.to_song(600.0) == pytest.approx(320.427)     # after it
    assert cuts.to_video(cuts.to_song(600.0)) == pytest.approx(600.0)


def test_inside_a_cut_the_recording_stands_still():
    # Through an intro there is no next sung moment yet, so the lyrics hold
    # rather than running backwards.
    cuts = Cuts(((0.0, 20.0),))
    assert cuts.to_song(0.0) == 0.0
    assert cuts.to_song(10.0) == 0.0
    assert cuts.to_song(20.0) == 0.0
    assert cuts.to_song(25.0) == pytest.approx(5.0)


def test_an_outro_alone_still_says_the_song_starts_at_once():
    # See You Again: somebody marked the credits and nothing at the front, which
    # is an assertion that there is no intro rather than an absence of one.
    cuts = Cuts(((229.408, 237.381),))
    assert cuts.intro == 0.0
    assert cuts.to_song(100.0) == 100.0


# --- asking for them --------------------------------------------------------

def _bundle(video_id, segments):
    return [{"videoID": "somebody-else", "segments": []},
            {"videoID": video_id,
             "segments": [{"category": "music_offtopic", "segment": list(s),
                           "locked": 1, "votes": 5} for s in segments]}]


def test_the_service_is_never_told_which_video(store, monkeypatch):
    # It is sent four characters of a digest and answers with everything that
    # shares them, so it cannot know which one is playing.
    seen = {}

    def get(url, params=None, **kw):
        seen["url"] = url
        return FakeResponse(_bundle("kJQP7kiw5Fk", [(0, 21.8)]))

    monkeypatch.setattr(sponsorblock.requests, "get", get)
    assert cuts_for("kJQP7kiw5Fk").intro == pytest.approx(21.8)
    assert "kJQP7kiw5Fk" not in seen["url"]
    assert len(seen["url"].rsplit("/", 1)[1]) == sponsorblock.PREFIX


def test_a_video_nobody_has_annotated_is_not_the_same_as_no_intro(store,
                                                                  monkeypatch):
    monkeypatch.setattr(sponsorblock.requests, "get",
                        lambda *a, **k: FakeResponse(_bundle("someone", [(0, 5)])))
    assert cuts_for("unannotated") is None


def test_an_unvouched_segment_is_refused(store, monkeypatch):
    # Anyone may submit one, and this moves the lyrics by whole seconds.
    payload = [{"videoID": "v", "segments": [
        {"category": "music_offtopic", "segment": [0, 40], "locked": 0, "votes": 0}]}]
    monkeypatch.setattr(sponsorblock.requests, "get",
                        lambda *a, **k: FakeResponse(payload))
    assert cuts_for("v") == Cuts(())


def test_overlapping_submissions_are_not_counted_twice(store, monkeypatch):
    # Two people mark the same intro to slightly different ends.
    monkeypatch.setattr(sponsorblock.requests, "get",
                        lambda *a, **k: FakeResponse(_bundle("v", [(0, 20), (18, 25)])))
    cuts = cuts_for("v")
    assert cuts.spans == ((0, 25),)
    assert cuts.to_song(30.0) == pytest.approx(5.0)


def test_the_answer_is_kept(store, monkeypatch):
    calls = []
    monkeypatch.setattr(sponsorblock.requests, "get",
                        lambda *a, **k: calls.append(1) or FakeResponse(
                            _bundle("v", [(0, 21.8)])))
    cuts_for("v")
    cuts_for("v")
    assert len(calls) == 1


def test_a_failed_ask_is_not_remembered_as_a_missing_video(store, monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError()

    monkeypatch.setattr(sponsorblock.requests, "get", down)
    assert cuts_for("v") is None
    from lyrica.sponsorblock import _path
    assert not _path("v").exists(), "a dropped connection is not an answer"


def test_a_service_that_returns_nonsense_is_survived(store, monkeypatch):
    monkeypatch.setattr(sponsorblock.requests, "get",
                        lambda *a, **k: FakeResponse(None))
    assert cuts_for("v") is None


def test_nothing_is_asked_without_a_video(store, monkeypatch):
    monkeypatch.setattr(sponsorblock.requests, "get",
                        lambda *a, **k: pytest.fail("no request should be made"))
    assert cuts_for("") is None


def test_a_cached_miss_is_believed(store, monkeypatch):
    calls = []
    monkeypatch.setattr(sponsorblock.requests, "get",
                        lambda *a, **k: calls.append(1) or FakeResponse(
                            _bundle("someone-else", [(0, 5)])))
    assert cuts_for("v") is None
    assert cuts_for("v") is None
    assert len(calls) == 1
    from lyrica.sponsorblock import _path
    assert json.loads(_path("v").read_text(encoding="utf-8")) is None
