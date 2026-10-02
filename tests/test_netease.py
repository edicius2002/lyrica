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
from lyrica.providers.base import OutcomeKind
from lyrica.providers.identity import SongQuery
from lyrica.providers.netease import NeteaseProvider, _score

LRC_BODY = "[00:10.00]first\n[00:20.00]second\n"


def song(name: str, artists: list[str], duration_ms: int = 200_000, id_: int = 1) -> dict:
    return {"id": id_, "name": name, "duration": duration_ms,
            "artists": [{"name": a} for a in artists]}


def test_partial_collaboration_keeps_compatible_artist_weight():
    duet = song('Song', ['Queen', 'David Bowie'], 180_000)
    solo = song('Song', ['Queen'], 180_000)
    assert _score(duet, 'Queen', 'Song', 180) == 5.5
    assert _score(duet, 'Queen', 'Song', 180) < NeteaseProvider.EXACT_SCORE
    assert _score(solo, 'Queen', 'Song', 180) == 6.5
    assert _score(duet, 'Queen & David Bowie', 'Song', 180) == 6.5


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

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


def test_opening_netease_credits_are_not_shown_as_lyrics(wired):
    wired["songs"] = [song("Song", ["Artist"], 200_000)]
    wired["lyric"] = {"lrc": {"lyric": (
        "[00:00.00]作词 : Someone\n"
        "[00:00.60]作曲：Someone\n"
        "[00:02.00]制作人 : Someone\n"
        "[00:03.00]编曲：Someone\n"
        "[00:18.00]First sung line\n"
        "[00:22.00]制作人：a lyric, not an opening credit"
    )}}
    result = NeteaseProvider().fetch("Artist", "Song", 200.0)
    assert result.lines == [
        (18.0, "First sung line"),
        (22.0, "制作人：a lyric, not an opening credit"),
    ]


def test_netease_credit_only_body_is_not_treated_as_plain_lyrics(wired):
    wired["songs"] = [song("Song", ["Artist"], 200_000)]
    wired["lyric"] = {"lrc": {"lyric": "[00:00.00]作词：Someone\n[00:01.00]作曲：Someone"}}
    assert NeteaseProvider().fetch("Artist", "Song", 200.0) is None


def test_closing_netease_mastering_credit_is_not_shown(wired):
    wired["songs"] = [song("Song", ["Artist"], 200_000)]
    wired["lyric"] = {"lrc": {"lyric": (
        "[00:10.00]First sung line\n"
        "[03:10.00]Last sung line\n"
        "[03:13.00]母带工程师 : Someone"
    )}}
    result = NeteaseProvider().fetch("Artist", "Song", 200.0)
    assert result.lines == [(10.0, "First sung line"), (190.0, "Last sung line")]


def test_closing_netease_credit_block_is_not_shown(wired):
    wired["songs"] = [song("Song", ["Artist"], 210_000)]
    labels = ("Remix", "音频工程师", "混音师", "附加制作", "母带工程师",
              "人声", "贝斯", "音频助理", "录音", "混音助理")
    credits = "\n".join(
        f"[03:{11 + index:02d}.00]{label} : Someone"
        for index, label in enumerate(labels)
    )
    wired["lyric"] = {"lrc": {"lyric": (
        "[00:10.00]First sung line\n[03:10.00]Last sung line\n" + credits
    )}}
    result = NeteaseProvider().fetch("Artist", "Song", 210.0)
    assert result.lines == [(10.0, "First sung line"), (190.0, "Last sung line")]


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


def test_lookup_distinguishes_timeout_from_a_valid_miss(wired):
    query = SongQuery("The Weeknd", "Blinding Lights", 200.0)
    wired["search_error"] = requests.Timeout("slow")
    assert NeteaseProvider().lookup(query).kind is OutcomeKind.RETRYABLE

    wired["search_error"] = None
    wired["songs"] = []
    assert NeteaseProvider().lookup(query).kind is OutcomeKind.NO_MATCH


def test_lookup_does_not_exhaust_a_failed_lyric_download(wired):
    wired["songs"] = [song("Blinding Lights", ["The Weeknd"], 200_000)]
    wired["lyric_error"] = requests.ConnectionError("down")
    outcome = NeteaseProvider().lookup(
        SongQuery("The Weeknd", "Blinding Lights", 200.0))
    assert outcome.kind is OutcomeKind.RETRYABLE


def test_lookup_surfaces_search_rate_limiting(monkeypatch):
    monkeypatch.setattr(
        netease.requests,
        "post",
        lambda *_args, **_kwargs: FakeResponse(
            {}, status=429, headers={"Retry-After": "45"}),
    )
    outcome = NeteaseProvider().lookup(
        SongQuery("The Weeknd", "Blinding Lights", 200.0))
    assert outcome.kind is OutcomeKind.UNAVAILABLE
    assert outcome.retry_after == 45.0


def test_an_empty_title_never_reaches_the_network(wired):
    wired["search_error"] = AssertionError("must not be called")
    assert NeteaseProvider().fetch("The Weeknd", "", 200.0) is None
