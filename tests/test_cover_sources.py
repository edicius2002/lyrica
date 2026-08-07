"""Choosing a cover, and keeping the token out of everything (offline)."""
import pytest
import requests

from lyrica import artwork
from lyrica.artwork import _closest, discogs_token, fetch_cover_discogs


class FakeResponse:
    def __init__(self, payload=None, content=b"", status=200):
        self._payload, self.content, self.status_code = payload, content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


# --- matching ---------------------------------------------------------------

def test_the_right_track_is_chosen():
    results = [{"artistName": "The Weeknd", "trackName": "Blinding Lights",
                "collectionName": "After Hours"}]
    assert _closest(results, "The Weeknd", "Blinding Lights", "After Hours") is results[0]


def test_a_stranger_is_rejected_rather_than_shown():
    # Someone else's cover over your lyrics is worse than the small correct one
    # already on screen.
    results = [{"artistName": "Someone Else", "trackName": "A Different Song"}]
    assert _closest(results, "The Weeknd", "Blinding Lights", "") is None


def test_a_title_match_alone_is_not_enough():
    # Songs share titles constantly.
    results = [{"artistName": "A Cover Band", "trackName": "Blinding Lights"}]
    assert _closest(results, "The Weeknd", "Blinding Lights", "") is None


def test_no_results_matches_nothing():
    assert _closest([], "A", "B", "") is None


# --- the token --------------------------------------------------------------

def test_no_token_means_the_source_is_simply_absent(monkeypatch):
    monkeypatch.delenv("LYRICA_DISCOGS_TOKEN", raising=False)
    assert discogs_token() == ""
    assert fetch_cover_discogs("A", "B") is None


def test_a_blank_token_counts_as_absent(monkeypatch):
    # Set-but-empty is how a shell reports an unset variable often enough that
    # treating it as real would send an unauthenticated request every track.
    monkeypatch.setenv("LYRICA_DISCOGS_TOKEN", "   ")
    assert discogs_token() == ""


def test_the_token_never_reaches_the_network_without_being_asked(monkeypatch):
    monkeypatch.delenv("LYRICA_DISCOGS_TOKEN", raising=False)

    def explode(*a, **k):
        raise AssertionError("no request should be made without a token")

    monkeypatch.setattr(artwork.requests, "get", explode)
    assert fetch_cover_discogs("A", "B") is None


def test_the_token_is_sent_as_a_header_not_a_query_parameter(monkeypatch):
    # A URL ends up in logs, proxies and error messages; a header does not.
    seen = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen["params"] = params or {}
        seen["headers"] = headers or {}
        return FakeResponse(payload={"results": []})

    monkeypatch.setenv("LYRICA_DISCOGS_TOKEN", "secret-value")
    monkeypatch.setattr(artwork.requests, "get", fake_get)
    fetch_cover_discogs("A", "B")

    assert "secret-value" not in str(seen["params"])
    assert "secret-value" in seen["headers"].get("Authorization", "")


# --- responses --------------------------------------------------------------

@pytest.fixture
def wired(monkeypatch):
    state = {"results": [], "image": b"binary-image-data"}

    def fake_get(url, params=None, headers=None, **kwargs):
        if url == artwork.DISCOGS_URL:
            return FakeResponse(payload={"results": state["results"]})
        return FakeResponse(content=state["image"])

    monkeypatch.setenv("LYRICA_DISCOGS_TOKEN", "t")
    monkeypatch.setattr(artwork.requests, "get", fake_get)
    return state


def test_a_release_with_a_scan_returns_its_image(wired):
    wired["results"] = [{"cover_image": "https://example.invalid/cover.jpg"}]
    assert fetch_cover_discogs("A", "B") == b"binary-image-data"


def test_the_placeholder_icon_is_skipped(wired):
    # Discogs serves a generic record icon when a release has no scan at all.
    wired["results"] = [{"cover_image": "https://example.invalid/spacer.gif"},
                        {"cover_image": "https://example.invalid/real.jpg"}]
    assert fetch_cover_discogs("A", "B") == b"binary-image-data"


def test_releases_without_any_image_are_a_miss(wired):
    wired["results"] = [{"cover_image": ""}, {}]
    assert fetch_cover_discogs("A", "B") is None


def test_a_network_failure_is_a_miss_not_a_crash(monkeypatch):
    monkeypatch.setenv("LYRICA_DISCOGS_TOKEN", "t")
    monkeypatch.setattr(artwork.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    assert fetch_cover_discogs("A", "B") is None
