"""Explicit provider outcomes preserve the difference between misses and failures."""

import pytest
import requests

from lyrica.providers import base as provider_base
from lyrica.providers import identity as identity_module
from lyrica.providers import lrclib


def test_provider_outcome_contract_names_every_cache_relevant_state():
    kinds = provider_base.OutcomeKind
    assert {kind.value for kind in kinds} == {
        "hit", "no_match", "retryable", "unavailable",
    }


def test_a_hit_requires_usable_lyrics():
    from lyrica.lyrics import Lyrics

    lyrics = Lyrics(plain="placeholder")
    outcome = provider_base.ProviderOutcome.hit(lyrics, reason="accepted")
    assert outcome.lyrics is lyrics
    assert outcome.kind is provider_base.OutcomeKind.HIT
    assert outcome.reason == "accepted"


def test_non_hits_carry_no_lyrics_and_may_bound_a_retry():
    outcome = provider_base.ProviderOutcome.unavailable(
        reason="cooldown", retry_after=120.0,
    )
    assert outcome.lyrics is None
    assert outcome.retry_after == 120.0


def test_invalid_outcome_shapes_are_rejected():
    from lyrica.lyrics import Lyrics

    with pytest.raises(ValueError, match="hit"):
        provider_base.ProviderOutcome(provider_base.OutcomeKind.HIT)
    with pytest.raises(ValueError, match="non-hit"):
        provider_base.ProviderOutcome(
            provider_base.OutcomeKind.NO_MATCH, lyrics=Lyrics(plain="wrong"))


class Response:
    def __init__(self, status: int, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


def query(raw_title="Same Title"):
    return identity_module.SongQuery(
        artist="Wanted Artist",
        title="Same Title",
        raw_title=raw_title,
        duration=200.0,
    )


def test_lrclib_reports_a_transport_failure_as_retryable(monkeypatch):
    monkeypatch.setattr(
        lrclib.requests, "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout("slow")),
    )

    outcome = lrclib.LrclibProvider().lookup(query())

    assert outcome.kind is provider_base.OutcomeKind.RETRYABLE


def test_lrclib_reports_rate_limiting_as_unavailable(monkeypatch):
    monkeypatch.setattr(
        lrclib.requests, "get",
        lambda *_args, **_kwargs: Response(429, headers={"Retry-After": "90"}),
    )

    outcome = lrclib.LrclibProvider().lookup(query())

    assert outcome.kind is provider_base.OutcomeKind.UNAVAILABLE
    assert outcome.retry_after == 90.0


def test_lrclib_reports_a_valid_empty_search_as_no_match(monkeypatch):
    monkeypatch.setattr(
        lrclib.requests, "get",
        lambda url, **_kwargs: Response(404) if url.endswith("/get") else Response(200, []),
    )

    outcome = lrclib.LrclibProvider().lookup(query())

    assert outcome.kind is provider_base.OutcomeKind.NO_MATCH


def test_lrclib_keeps_identity_rejection_provenance(monkeypatch):
    wrong = {
        "artistName": "Wrong Artist",
        "trackName": "Same Title",
        "duration": 200,
        "plainLyrics": "placeholder",
    }
    monkeypatch.setattr(
        lrclib.requests, "get",
        lambda url, **_kwargs: Response(404) if url.endswith("/get") else Response(200, [wrong]),
    )

    outcome = lrclib.LrclibProvider().lookup(query())

    assert outcome.kind is provider_base.OutcomeKind.NO_MATCH
    assert outcome.reason == "artist_mismatch"


def test_lrclib_uses_raw_version_evidence(monkeypatch):
    remix = {
        "artistName": "Wanted Artist",
        "trackName": "Same Title (Club Remix)",
        "duration": 200,
        "plainLyrics": "placeholder",
    }
    monkeypatch.setattr(
        lrclib.requests, "get",
        lambda url, **_kwargs: Response(404) if url.endswith("/get") else Response(200, [remix]),
    )

    outcome = lrclib.LrclibProvider().lookup(query("Same Title (Live)"))

    assert outcome.kind is provider_base.OutcomeKind.NO_MATCH
    assert outcome.reason == "version_mismatch"
