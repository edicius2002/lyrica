"""Recovering which video is playing from what the media session says."""
import pytest
import requests

from lyrica import youtube
from lyrica.youtube import parse_duration, video_id_for


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
    monkeypatch.setenv("LYRICA_YOUTUBE_KEY", "a-key")


def test_iso_durations():
    assert parse_duration("PT4M42S") == 282
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT30S") == 30
    assert parse_duration("PT13M") == 780
    assert parse_duration("") is None
    assert parse_duration("4:42") is None


def _search(*ids):
    return {"items": [{"id": {"videoId": i}} for i in ids]}


def _details(pairs):
    return {"items": [{"id": i, "contentDetails": {"duration": d}}
                      for i, d in pairs]}


def test_the_length_is_what_picks_the_video(store, monkeypatch):
    # Without it a SoundCloud stream, which reaches the session looking much the
    # same, would be handed somebody's YouTube upload and given its intro.
    def get(url, params=None, **kw):
        if "search" in url:
            return FakeResponse(_search("wrong1", "right", "wrong2"))
        return FakeResponse(_details([("wrong1", "PT2M0S"), ("right", "PT4M42S"),
                                      ("wrong2", "PT9M0S")]))

    monkeypatch.setattr(youtube.requests, "get", get)
    assert video_id_for("Despacito (Official Video)", 282.0) == "right"


def test_nothing_of_the_right_length_is_no_answer(store, monkeypatch):
    def get(url, params=None, **kw):
        if "search" in url:
            return FakeResponse(_search("a", "b"))
        return FakeResponse(_details([("a", "PT2M0S"), ("b", "PT9M0S")]))

    monkeypatch.setattr(youtube.requests, "get", get)
    assert video_id_for("something", 282.0) is None


def test_relevance_breaks_a_tie_between_equal_lengths(store, monkeypatch):
    def get(url, params=None, **kw):
        if "search" in url:
            return FakeResponse(_search("first", "second"))
        # Returned in the other order on purpose: the API does not promise one.
        return FakeResponse(_details([("second", "PT4M42S"), ("first", "PT4M42S")]))

    monkeypatch.setattr(youtube.requests, "get", get)
    assert video_id_for("x", 282.0) == "first"


def test_without_a_key_nothing_is_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("LYRICA_YOUTUBE_KEY", raising=False)
    monkeypatch.setattr(youtube.requests, "get",
                        lambda *a, **k: pytest.fail("no key, no request"))
    assert video_id_for("Despacito", 282.0) is None


def test_a_blank_key_counts_as_absent(monkeypatch):
    monkeypatch.setenv("LYRICA_YOUTUBE_KEY", "   ")
    assert youtube.api_key() == ""


def test_the_key_never_reaches_the_log(store, monkeypatch, caplog):
    monkeypatch.setattr(youtube.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.HTTPError("403 a-key")))
    with caplog.at_level("DEBUG"):
        assert video_id_for("x", 282.0) is None
    assert "a-key" not in caplog.text


def test_the_answer_is_kept(store, monkeypatch):
    calls = []

    def get(url, params=None, **kw):
        calls.append(url)
        if "search" in url:
            return FakeResponse(_search("right"))
        return FakeResponse(_details([("right", "PT4M42S")]))

    monkeypatch.setattr(youtube.requests, "get", get)
    assert video_id_for("x", 282.0) == "right"
    assert video_id_for("x", 282.0) == "right"
    assert len(calls) == 2, "the second lookup came off disk"


def test_a_track_that_is_not_on_youtube_is_not_searched_for_twice(store,
                                                                  monkeypatch):
    calls = []

    def get(url, params=None, **kw):
        calls.append(url)
        return FakeResponse(_search()) if "search" in url else FakeResponse({})

    monkeypatch.setattr(youtube.requests, "get", get)
    assert video_id_for("obscure", 282.0) is None
    assert video_id_for("obscure", 282.0) is None
    assert len(calls) == 1


def test_a_failed_search_is_not_remembered_as_a_missing_video(store, monkeypatch):
    calls = []

    def down(*a, **k):
        calls.append(1)
        raise requests.ConnectionError()

    monkeypatch.setattr(youtube.requests, "get", down)
    assert video_id_for("x", 282.0) is None
    assert video_id_for("x", 282.0) is None
    assert len(calls) == 2, "a dropped connection is not an answer"


def test_a_track_with_no_duration_is_not_guessed_at(store, monkeypatch):
    monkeypatch.setattr(youtube.requests, "get",
                        lambda *a, **k: pytest.fail("nothing to verify against"))
    assert video_id_for("x", 0.0) is None
