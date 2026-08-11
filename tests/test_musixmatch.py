"""Musixmatch richsync: word timing, token handling and backing off (offline).

The payload shapes below were captured from the live API. Placeholder words only.
"""
import json
import time

import pytest
import requests

from lyrica.lyrics import MAX_INFERRED_WORD_S, Precision
from lyrica.providers import musixmatch
from lyrica.providers.musixmatch import MusixmatchProvider, richsync_to_lyrics, richsync_to_words

# Captured shape: ts/te bound the line, x is its text, l lists word events whose
# o is an offset from ts. There is no per-word duration anywhere.
RICHSYNC = [
    {"ts": 10.0, "te": 12.0, "x": "alpha beta",
     "l": [{"c": "alpha", "o": 0.0}, {"c": " beta", "o": 0.8}]},
    {"ts": 20.0, "te": 21.0, "x": "gamma", "l": [{"c": "gamma", "o": 0.0}]},
]

PARENTHETICAL_RICHSYNC = [
    {"ts": 10.0, "te": 12.0, "x": "lead (echo)",
     "l": [{"c": "lead", "o": 0.0}, {"c": " (echo)", "o": 0.8}]},
    {"ts": 20.0, "te": 22.0, "x": "lead (aside) tonight",
     "l": [{"c": "lead", "o": 0.0}, {"c": " (aside)", "o": 0.5},
           {"c": " tonight", "o": 1.0}]},
    {"ts": 30.0, "te": 31.0, "x": "(chorus)",
     "l": [{"c": "(chorus)", "o": 0.0}]},
]


def envelope(status=200, body=None, hint=None):
    header = {"status_code": status}
    if hint:
        header["hint"] = hint
    return {"message": {"header": header, "body": body or {}}}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def wired(monkeypatch):
    state = {"responses": {}, "calls": [], "error": None}

    def fake_get(url, params=None, **kwargs):
        if state["error"]:
            raise state["error"]
        endpoint = url.rsplit("/", 1)[-1]
        state["calls"].append(endpoint)
        return FakeResponse(state["responses"].get(endpoint, envelope(404)))

    monkeypatch.setattr(musixmatch.requests, "get", fake_get)
    return state


def working(track_id=1, has_richsync=True, richsync=RICHSYNC):
    return {
        "token.get": envelope(body={"user_token": "tok"}),
        "matcher.track.get": envelope(body={"track": {"track_id": track_id,
                                                      "has_richsync": has_richsync}}),
        "track.richsync.get": envelope(
            body={"richsync": {"richsync_body": json.dumps(richsync)}}),
    }


# --- word timing ------------------------------------------------------------

def test_offsets_become_absolute_word_times():
    lines, words = richsync_to_words(RICHSYNC)
    assert [t for t, _ in lines] == [10.0, 20.0]
    assert words[0][0][0] == 10.0
    assert words[0][1][0] == 10.8, "the second word starts at ts + its offset"


def test_a_word_ends_where_the_next_one_starts():
    _, words = richsync_to_words(RICHSYNC)
    assert words[0][0][1] == 10.8


def test_the_last_word_of_a_line_ends_at_the_line_end():
    _, words = richsync_to_words(RICHSYNC)
    assert words[0][1][1] == 12.0


def test_an_instrumental_gap_does_not_stretch_a_word():
    # The defining flaw of this format: with no per-word duration, a word before
    # a long break would otherwise stay lit until the next word arrives.
    gap = [{"ts": 0.0, "te": 40.0, "x": "alpha",
            "l": [{"c": "alpha", "o": 0.0}]}]
    _, words = richsync_to_words(gap)
    start, end, _ = words[0][0]
    assert end - start == pytest.approx(MAX_INFERRED_WORD_S)


def test_word_text_is_stripped_but_order_is_kept():
    _, words = richsync_to_words(RICHSYNC)
    assert [w[2] for w in words[0]] == ["alpha", "beta"]


def test_lines_without_usable_events_are_skipped():
    lines, _ = richsync_to_words([{"ts": None, "l": []}, {"te": 5.0, "l": []}])
    assert lines == []


def test_a_parenthetical_richsync_suffix_becomes_a_timed_backing_adlib():
    lyrics = richsync_to_lyrics(PARENTHETICAL_RICHSYNC)
    assert lyrics.lines[0] == (10.0, "lead")
    assert lyrics.words_at(0) == [(10.0, 10.8, "lead")]
    assert lyrics.backing_at(0) == ("(echo)", [(10.8, 12.0, "(echo)")])


def test_richsync_keeps_inline_and_entire_parenthetical_lines_as_lead():
    lyrics = richsync_to_lyrics(PARENTHETICAL_RICHSYNC)
    assert lyrics.lines[1][1] == "lead (aside) tonight"
    assert lyrics.backing_at(1) == ("", [])
    assert lyrics.lines[2][1] == "(chorus)"
    assert lyrics.backing_at(2) == ("", [])


# --- fetch ------------------------------------------------------------------

def test_a_match_returns_word_level_lyrics(wired):
    wired["responses"] = working()
    result = MusixmatchProvider().fetch("A", "B", 200.0)
    assert result.precision is Precision.WORD
    assert result.exact is True
    assert result.source == "musixmatch/richsync"


def test_a_match_keeps_a_parenthetical_suffix_as_a_backing_adlib(wired):
    wired["responses"] = working(richsync=PARENTHETICAL_RICHSYNC)
    result = MusixmatchProvider().fetch("A", "B", 200.0)
    assert result.backing_at(0) == ("(echo)", [(10.8, 12.0, "(echo)")])


def test_a_track_flagged_without_richsync_is_not_fetched(wired):
    wired["responses"] = working(has_richsync=False)
    assert MusixmatchProvider().fetch("A", "B", 200.0) is None
    assert "track.richsync.get" not in wired["calls"]


def test_no_match_is_a_miss(wired):
    wired["responses"] = {"token.get": envelope(body={"user_token": "tok"}),
                          "matcher.track.get": envelope(body={})}
    assert MusixmatchProvider().fetch("A", "B", 200.0) is None


def test_a_network_failure_is_a_miss_not_a_crash(wired):
    wired["error"] = requests.ConnectionError("down")
    assert MusixmatchProvider().fetch("A", "B", 200.0) is None


def test_an_empty_title_never_reaches_the_network(wired):
    wired["error"] = AssertionError("must not be called")
    assert MusixmatchProvider().fetch("", "", 200.0) is None


# --- token ------------------------------------------------------------------

def test_the_token_is_requested_once_and_reused(wired):
    wired["responses"] = working()
    provider = MusixmatchProvider()
    provider.fetch("A", "B", 200.0)
    provider.fetch("C", "D", 200.0)
    assert wired["calls"].count("token.get") == 1


def test_a_stored_token_survives_a_restart(wired, tmp_path):
    wired["responses"] = working()
    path = tmp_path / "token.json"
    MusixmatchProvider(token_path=path).fetch("A", "B", 200.0)
    assert path.exists()

    wired["calls"].clear()
    MusixmatchProvider(token_path=path).fetch("A", "B", 200.0)
    assert "token.get" not in wired["calls"], "a fresh process must reuse the token"


def test_an_expired_stored_token_is_replaced(wired, tmp_path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"token": "old", "issued_at": time.time() - 99_999}))
    wired["responses"] = working()
    MusixmatchProvider(token_path=path).fetch("A", "B", 200.0)
    assert "token.get" in wired["calls"]


def test_a_corrupt_token_file_is_ignored(wired, tmp_path):
    path = tmp_path / "token.json"
    path.write_text("{not json")
    wired["responses"] = working()
    assert MusixmatchProvider(token_path=path).fetch("A", "B", 200.0) is not None


# --- backing off ------------------------------------------------------------

def test_a_captcha_hint_stops_the_provider_asking(wired):
    # Retrying into a throttle is what turns a temporary limit into a blocked
    # address, so a refusal has to stop the source entirely for a while.
    wired["responses"] = {"token.get": envelope(401, hint="captcha")}
    provider = MusixmatchProvider()
    assert provider.fetch("A", "B", 200.0) is None

    wired["calls"].clear()
    wired["responses"] = working()
    assert provider.fetch("C", "D", 200.0) is None
    assert wired["calls"] == [], "nothing may be sent while backing off"


def test_a_refusal_mid_lookup_also_backs_off(wired):
    wired["responses"] = {"token.get": envelope(body={"user_token": "tok"}),
                          "matcher.track.get": envelope(401, hint="renew")}
    provider = MusixmatchProvider()
    provider.fetch("A", "B", 200.0)
    wired["calls"].clear()
    provider.fetch("C", "D", 200.0)
    assert wired["calls"] == []


def test_the_cooldown_ends(wired, monkeypatch):
    wired["responses"] = {"token.get": envelope(401, hint="captcha")}
    provider = MusixmatchProvider()
    provider.fetch("A", "B", 200.0)

    later = time.monotonic() + musixmatch.COOLDOWN_S + 1
    monkeypatch.setattr(musixmatch.time, "monotonic", lambda: later)
    wired["responses"] = working()
    assert provider.fetch("C", "D", 200.0) is not None


def test_the_provider_declares_word_level():
    assert MusixmatchProvider.max_precision is Precision.WORD
