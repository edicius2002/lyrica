"""NetEase provider: match scoring and response handling (offline).

The scoring rules exist because the live probe found the search returning a
different artist's song of the same name, with a duration close enough to pass
a duration check on its own. These tests pin that behaviour with synthetic
payloads shaped like the real responses. Placeholder text only.
"""
import pytest
import requests

from lyrica.lyrics import Precision
from lyrica.providers import netease
from lyrica.providers.netease import NeteaseProvider, _score

LRC_BODY = "[00:10.00]first\n[00:20.00]second\n"


def song(name: str, artists: list[str], duration_ms: int = 200_000, id_: int = 1) -> dict:
    return {"id": id_, "name": name, "duration": duration_ms,
            "artists": [{"name": a} for a in artists]}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def wired(monkeypatch):
    """Route search and lyric calls to payloads the test controls."""
    state = {"songs": [], "lyric": {}, "search_error": None, "lyric_error": None}

    def fake_post(url, **kwargs):
        if state["search_error"]:
            raise state["search_error"]
        return FakeResponse({"result": {"songs": state["songs"]}})

    def fake_get(url, **kwargs):
        if state["lyric_error"]:
            raise state["lyric_error"]
        return FakeResponse(state["lyric"])

    monkeypatch.setattr(netease.requests, "post", fake_post)
    monkeypatch.setattr(netease.requests, "get", fake_get)
    return state


# --- scoring ----------------------------------------------------------------

def test_an_exact_match_scores_well():
    s = song("Blinding Lights", ["The Weeknd"], 200_000)
    assert _score(s, "The Weeknd", "Blinding Lights", 200.0) >= NeteaseProvider.MIN_SCORE


def test_a_wrong_artist_is_rejected_even_when_title_and_duration_agree():
    # Exactly the failure the probe found: same title, plausible duration,
    # different performer.
    s = song("Supernatural", ["noli"], 189_000)
    assert _score(s, "NewJeans", "Supernatural", 191.0) < NeteaseProvider.MIN_SCORE


def test_a_partial_artist_still_matches():
    s = song("Monaco", ["Bad Bunny", "Feid"], 267_000)
    assert _score(s, "Bad Bunny", "Monaco", 267.0) >= NeteaseProvider.MIN_SCORE


def test_a_wildly_wrong_duration_costs_score():
    close = song("Yellow", ["Coldplay"], 269_000)
    far = song("Yellow", ["Coldplay"], 900_000)
    assert _score(close, "Coldplay", "Yellow", 269.0) > _score(far, "Coldplay", "Yellow", 269.0)


def test_scoring_ignores_case_and_punctuation():
    s = song("HUMBLE.", ["Kendrick Lamar"], 177_000)
    assert _score(s, "kendrick lamar", "humble", 177.0) >= NeteaseProvider.MIN_SCORE


# --- fetch ------------------------------------------------------------------

def test_a_good_match_returns_synced_lyrics(wired):
    wired["songs"] = [song("Blinding Lights", ["The Weeknd"], 200_000)]
    wired["lyric"] = {"lrc": {"lyric": LRC_BODY}}
    result = NeteaseProvider().fetch("The Weeknd", "Blinding Lights", 200.0)
    assert result.precision is Precision.LINE
    assert result.source == "netease"
    assert len(result.lines) == 2


def test_a_bad_match_is_discarded_rather_than_shown(wired):
    wired["songs"] = [song("Supernatural", ["noli"], 189_000)]
    wired["lyric"] = {"lrc": {"lyric": LRC_BODY}}
    assert NeteaseProvider().fetch("NewJeans", "Supernatural", 191.0) is None


def test_the_best_of_several_results_is_chosen(wired):
    wired["songs"] = [
        song("Blinding Lights", ["Cover Band"], 200_000, id_=1),
        song("Blinding Lights", ["The Weeknd"], 200_000, id_=2),
    ]
    wired["lyric"] = {"lrc": {"lyric": LRC_BODY}}
    assert NeteaseProvider().fetch("The Weeknd", "Blinding Lights", 200.0) is not None


def test_untimed_lyrics_come_back_as_plain(wired):
    wired["songs"] = [song("Some Song", ["Some Artist"], 200_000)]
    wired["lyric"] = {"lrc": {"lyric": "first line\nsecond line"}}
    result = NeteaseProvider().fetch("Some Artist", "Some Song", 200.0)
    assert result.precision is Precision.PLAIN


def test_no_results_returns_none(wired):
    wired["songs"] = []
    assert NeteaseProvider().fetch("Nobody", "Nothing", 100.0) is None


def test_an_empty_lyric_body_returns_none(wired):
    wired["songs"] = [song("Some Song", ["Some Artist"], 200_000)]
    wired["lyric"] = {"lrc": {"lyric": ""}}
    assert NeteaseProvider().fetch("Some Artist", "Some Song", 200.0) is None


def test_a_network_failure_is_a_miss_not_a_crash(wired):
    wired["search_error"] = requests.ConnectionError("down")
    assert NeteaseProvider().fetch("The Weeknd", "Blinding Lights", 200.0) is None


def test_a_lyric_endpoint_failure_is_a_miss(wired):
    wired["songs"] = [song("Blinding Lights", ["The Weeknd"], 200_000)]
    wired["lyric_error"] = requests.Timeout("slow")
    assert NeteaseProvider().fetch("The Weeknd", "Blinding Lights", 200.0) is None


def test_an_empty_title_never_reaches_the_network(wired):
    wired["search_error"] = AssertionError("must not be called")
    assert NeteaseProvider().fetch("The Weeknd", "", 200.0) is None
