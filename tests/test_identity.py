"""Song identity guards shared by lyrics providers (offline)."""

from lyrica.providers import lrclib
from lyrica.providers.identity import validate_identity
from lyrica.providers.lrclib import LrclibProvider


class Response:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_lrclib_search_rejects_a_known_wrong_artist(monkeypatch):
    """A same-title, same-duration stranger must never supply the lyrics."""
    wrong = {
        "artistName": "Wrong Artist",
        "trackName": "Same Title",
        "duration": 200,
        "syncedLyrics": "[00:01.00]not the requested performer",
    }

    def get(url, **_kwargs):
        return Response(404) if url.endswith("/get") else Response(200, [wrong])

    monkeypatch.setattr(lrclib.requests, "get", get)

    assert LrclibProvider().fetch("Wanted Artist", "Same Title", 200.0) is None


def decision(requested_artist="Dua Lipa", requested_title="Levitating",
             returned_artist="Dua Lipa", returned_title="Levitating",
             requested_raw_title=None):
    return validate_identity(
        requested_artist=requested_artist,
        requested_title=requested_title,
        requested_raw_title=requested_raw_title or requested_title,
        returned_artist=returned_artist,
        returned_title=returned_title,
    )


def test_featured_artist_credits_are_compatible():
    assert decision(returned_artist="Dua Lipa feat. DaBaby").accepted
    assert decision(requested_artist="Dua Lipa & DaBaby").accepted


def test_punctuation_and_accents_do_not_create_an_artist_contradiction():
    assert decision(requested_artist="ROSALÍA", returned_artist="Rosalia").accepted
    assert decision(requested_artist="AC/DC", returned_artist="ACDC").accepted


def test_missing_artist_metadata_remains_unknown():
    assert decision(requested_artist="", returned_artist="Someone").accepted
    assert decision(returned_artist="").accepted


def test_an_explicit_alternate_recording_is_not_a_studio_match():
    result = decision(returned_title="Levitating (Live at Wembley)")
    assert not result.accepted
    assert result.reason == "version_mismatch"


def test_raw_player_title_preserves_a_matching_version_qualifier():
    result = decision(
        requested_title="Song",
        requested_raw_title="Song (2011 Remaster)",
        returned_title="Song (Remastered 2011)",
    )
    assert result.accepted


def test_two_known_incompatible_versions_are_rejected():
    result = decision(
        requested_title="Song",
        requested_raw_title="Song (Acoustic)",
        returned_title="Song (Club Remix)",
    )
    assert not result.accepted


def test_missing_returned_version_evidence_is_not_a_contradiction():
    assert decision(
        requested_title="Song",
        requested_raw_title="Song (Acoustic)",
        returned_title="Song",
    ).accepted


def test_a_known_different_title_is_rejected_even_if_artist_and_duration_fit():
    result = decision(returned_title="A Different Song")
    assert not result.accepted
    assert result.reason == "title_mismatch"


def test_feature_credit_in_the_title_is_not_a_different_song():
    assert decision(returned_title="Levitating (feat. DaBaby)").accepted
