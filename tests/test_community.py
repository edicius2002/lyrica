"""Community TTML provider: variant matching and parsing (offline).

The search payloads below have the shape captured live, including the cluster of
variants a single title returns — album cut, guest single, live take, remix —
which is the case the scoring exists for. Placeholder words only.
"""
import pytest
import requests

from lyrica.lyrics import Precision
from lyrica.providers import community
from lyrica.providers.community import CommunityTtmlProvider, _score

TTML = """<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    itunes:timing="Word"><body><div>
  <p begin="10.0" end="12.0">
    <span begin="10.0" end="10.6">alpha</span>
    <span begin="10.6" end="12.0">beta</span>
  </p>
</div></body></tt>"""

# Captured shape: one title, four recordings, distinguished mainly by duration.
VARIANTS = [
    {"id": "1", "track_name": "Levitating", "artist_name": "Dua Lipa",
     "duration": 204, "timing_type": "word", "lyricsUrl": "u1"},
    {"id": "2", "track_name": "Levitating (feat. DaBaby)", "artist_name": "Dua Lipa",
     "duration": 203, "timing_type": "word", "lyricsUrl": "u2"},
    {"id": "3", "track_name": "Levitating (Live from the Royal Albert Hall)",
     "artist_name": "Dua Lipa", "duration": 265, "timing_type": "line", "lyricsUrl": "u3"},
    {"id": "4", "track_name": "Levitating (The Blessed Madonna Remix)",
     "artist_name": "Dua Lipa", "duration": 250, "timing_type": "line", "lyricsUrl": "u4"},
]


class FakeResponse:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def wired(monkeypatch):
    state = {"results": [], "documents": {}, "error": None, "fetched": []}

    def fake_get(url, **kwargs):
        if state["error"]:
            raise state["error"]
        if url == community.SEARCH_URL:
            return FakeResponse(payload={"results": state["results"]})
        state["fetched"].append(url)
        body = state["documents"].get(url)
        return FakeResponse(text=body, status=200 if body else 404)

    monkeypatch.setattr(community.requests, "get", fake_get)
    return state


# --- variant scoring --------------------------------------------------------

def test_duration_picks_the_right_recording_among_variants():
    # Every variant matches on artist and nearly on title; only length separates
    # the studio cut from the live take, whose timings would be wrong.
    scored = {r["id"]: _score(r, "Dua Lipa", "Levitating", 203.0) for r in VARIANTS}
    assert max(scored, key=scored.get) in {"1", "2"}
    assert scored["3"] < scored["1"], "a live take must not outrank the studio cut"
    assert scored["4"] < scored["1"]


def test_a_far_off_duration_is_penalised_hard():
    near = {"track_name": "Song", "artist_name": "A", "duration": 200}
    far = {"track_name": "Song", "artist_name": "A", "duration": 400}
    assert _score(near, "A", "Song", 200.0) > _score(far, "A", "Song", 200.0) + 5


def test_word_timing_is_a_tiebreak_not_a_reason_to_mismatch():
    right = {"track_name": "Song", "artist_name": "A", "duration": 200, "timing_type": "line"}
    wrong = {"track_name": "Other", "artist_name": "B", "duration": 400, "timing_type": "word"}
    assert _score(right, "A", "Song", 200.0) > _score(wrong, "A", "Song", 200.0)


def test_a_wrong_artist_is_rejected():
    rec = {"track_name": "Levitating", "artist_name": "Someone Else", "duration": 203}
    assert _score(rec, "Dua Lipa", "Levitating", 203.0) < CommunityTtmlProvider.MIN_SCORE


# --- fetch ------------------------------------------------------------------

def test_a_match_returns_word_level_lyrics(wired):
    wired["results"] = [VARIANTS[1]]
    wired["documents"] = {"u2": TTML}
    result = CommunityTtmlProvider().fetch("Dua Lipa", "Levitating", 203.0)
    assert result.precision is Precision.WORD
    assert result.words[0] == [(10.0, 10.6, "alpha"), (10.6, 12.0, "beta")]
    assert result.source.startswith("community-ttml")


def test_the_live_take_and_the_remix_are_never_fetched(wired):
    # Which studio cut wins is a toss-up — they differ by a second and a guest
    # credit, and either one's timings fit. What must never happen is picking a
    # recording whose timings are wrong for what is playing.
    wired["results"] = VARIANTS
    wired["documents"] = dict.fromkeys(["u1", "u2", "u3", "u4"], TTML)
    CommunityTtmlProvider().fetch("Dua Lipa", "Levitating", 203.0)
    assert wired["fetched"] in (["u1"], ["u2"])
    assert "u3" not in wired["fetched"], "the live take must not be chosen"
    assert "u4" not in wired["fetched"], "the remix must not be chosen"


def test_no_results_is_a_miss(wired):
    wired["results"] = []
    assert CommunityTtmlProvider().fetch("Nobody", "Nothing", 100.0) is None


def test_a_poor_match_is_discarded_before_downloading(wired):
    wired["results"] = [{"track_name": "Other Song", "artist_name": "Someone Else",
                         "duration": 400, "lyricsUrl": "u9"}]
    assert CommunityTtmlProvider().fetch("Dua Lipa", "Levitating", 203.0) is None
    assert wired["fetched"] == [], "a rejected match must not cost a download"


def test_an_unparseable_document_is_a_miss(wired):
    wired["results"] = [VARIANTS[1]]
    wired["documents"] = {"u2": "<tt><body>"}
    assert CommunityTtmlProvider().fetch("Dua Lipa", "Levitating", 203.0) is None


def test_a_missing_document_is_a_miss(wired):
    wired["results"] = [VARIANTS[1]]
    wired["documents"] = {}
    assert CommunityTtmlProvider().fetch("Dua Lipa", "Levitating", 203.0) is None


def test_a_network_failure_is_a_miss_not_a_crash(wired):
    wired["error"] = requests.ConnectionError("down")
    assert CommunityTtmlProvider().fetch("Dua Lipa", "Levitating", 203.0) is None


def test_an_empty_title_never_reaches_the_network(wired):
    wired["error"] = AssertionError("must not be called")
    assert CommunityTtmlProvider().fetch("Dua Lipa", "", 203.0) is None


def test_the_provider_declares_it_can_reach_word_level():
    # The cascade uses this to decide whether a line-level answer may end the
    # search, so it has to be right.
    assert CommunityTtmlProvider.max_precision is Precision.WORD


def test_a_missing_accent_is_not_a_different_performer():
    # The -5 exists to sink a different artist with the same title. A lost acute
    # accent was triggering it: the correct record scored 9.5 with the accents
    # and -3.5 without, against a floor of 3.0, so the right lyrics were thrown
    # away over one character.
    from lyrica.providers.community import CommunityTtmlProvider, _score

    record = {"artist_name": "ROSALÍA", "track_name": "DESPECHÁ",
              "duration": 155, "timing_type": "word"}
    assert _score(record, "Rosalia", "Despecha", 155.0) >= CommunityTtmlProvider.MIN_SCORE
    assert _score(record, "ROSALÍA", "DESPECHÁ", 155.0) >= CommunityTtmlProvider.MIN_SCORE
    # And the guard it was tripping still works.
    stranger = {"artist_name": "Karaoke Band", "track_name": "DESPECHÁ",
                "duration": 155}
    assert _score(stranger, "Rosalia", "Despecha", 155.0) < CommunityTtmlProvider.MIN_SCORE
