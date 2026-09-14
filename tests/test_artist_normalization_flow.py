"""Exercise real provider parsers with deterministic network responses."""

import json

import pytest
import requests

from lyrica.artist_names import ArtistReading
from lyrica.providers import community, lrclib, musixmatch, netease
from lyrica.providers.base import OutcomeKind
from lyrica.providers.identity import SongQuery

TTML = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="1s" end="2s">line</p></div></body></tt>')


class Response:
    status_code = 200
    text = TTML

    def __init__(self, payload):
        self.payload = payload
        self.headers = {}

    def json(self):
        return self.payload


def wire_provider(monkeypatch, name, artist, title="Song"):
    """Only transport is replaced; matching, parsing and outcomes stay real."""
    if name == "lrclib":
        record = {
            "artistName": artist,
            "trackName": title,
            "syncedLyrics": "[00:01.00]line",
            "duration": 180,
        }
        monkeypatch.setattr(
            requests,
            "get",
            lambda url, **kw: Response([record] if url.endswith("/search") else record),
        )
        return lrclib.LrclibProvider()
    if name == "community":
        record = {
            "artist_name": artist,
            "track_name": title,
            "duration": 180,
            "lyricsUrl": "https://example.test/lyrics",
            "timing_type": "line",
        }
        monkeypatch.setattr(requests, "get", lambda *a, **kw: Response({"results": [record]}))
        return community.CommunityTtmlProvider()
    if name == "netease":
        record = {"artists": [{"name": artist}], "name": title, "duration": 180000, "id": 1}
        monkeypatch.setattr(
            requests, "post", lambda *a, **kw: Response({"result": {"songs": [record]}})
        )
        monkeypatch.setattr(
            requests, "get", lambda *a, **kw: Response({"lrc": {"lyric": "[00:01.00]line"}})
        )
        return netease.NeteaseProvider()
    provider = musixmatch.MusixmatchProvider()

    def call(endpoint, params):
        if endpoint == "matcher.track.get":
            body = {
                "track": {
                    "artist_name": artist,
                    "track_name": title,
                    "has_richsync": 1,
                    "track_id": 1,
                }
            }
        else:
            body = {
                "richsync": {
                    "richsync_body": json.dumps(
                        [{"ts": 1.0, "te": 2.0, "x": "line", "l": [{"c": "line", "o": 0.0}]}]
                    )
                }
            }
        return {"message": {"header": {"status_code": 200}, "body": body}}

    monkeypatch.setattr(provider, "_call", call)
    monkeypatch.setattr(provider, "_token_outcome", lambda: ("test-token", None))
    return provider


@pytest.mark.parametrize("provider_name", ["lrclib", "community", "netease", "musixmatch"])
@pytest.mark.parametrize(("requested", "returned"), [("BTS - Topic", "BTS"), ("U2 - Topic", "U2")])
def test_provider_accepts_topic_and_records_catalogue_name(
    monkeypatch, provider_name, requested, returned
):
    provider = wire_provider(monkeypatch, provider_name, returned)
    outcome = provider.lookup(SongQuery(requested, "Song", 180))
    assert outcome.kind is OutcomeKind.HIT
    assert outcome.lyrics.resolved == (returned, "Song")


@pytest.mark.parametrize("provider_name", ["lrclib", "community", "netease", "musixmatch"])
def test_provider_rejects_name_substring(monkeypatch, provider_name):
    provider = wire_provider(monkeypatch, provider_name, "Queensryche")
    assert provider.lookup(SongQuery("Queen", "Song", 180)).kind is OutcomeKind.NO_MATCH


@pytest.mark.parametrize("provider_name", ["lrclib", "community", "netease", "musixmatch"])
def test_provider_vevo_alias_requires_provenance(monkeypatch, provider_name):
    provider = wire_provider(monkeypatch, provider_name, "Billie Eilish")
    assert provider.lookup(SongQuery("BillieEilish", "Song", 180)).kind is OutcomeKind.NO_MATCH
    reading = ArtistReading("BillieEilish", "BillieEilishVEVO", "vevo")
    outcome = provider.lookup(SongQuery("BillieEilish", "Song", 180, artist_reading=reading))
    assert outcome.kind is OutcomeKind.HIT
    assert outcome.lyrics.resolved == ("Billie Eilish", "Song")


@pytest.fixture
def lyric_cache(tmp_path, monkeypatch):
    from lyrica import providers

    monkeypatch.setattr(providers, "CACHE_DIR", tmp_path)
    return providers


def test_vevo_cache_mode_does_not_poison_ordinary_lookup(lyric_cache, monkeypatch):
    from lyrica.sessions.base import Snapshot

    provider = wire_provider(monkeypatch, "lrclib", "Billie Eilish")
    monkeypatch.setattr(lyric_cache, "PROVIDERS", [provider])
    snap = Snapshot(app="chrome.exe", artist="BillieEilishVEVO", title="Song")
    got = lyric_cache.fetch_for_candidates(snap.search_candidates(), 180)
    assert got.resolved == ("Billie Eilish", "Song")
    assert got.queried == ("BillieEilish", "Song")
    assert lyric_cache.fetch_lyrics("BillieEilish", "Song", 180) is None
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("must use cache"))
    again = lyric_cache.fetch_for_candidates(snap.search_candidates(), 180)
    assert again.resolved == got.resolved


def test_clean_topic_lookup_reuses_existing_hit(lyric_cache, monkeypatch):
    from lyrica.sessions.base import Snapshot

    provider = wire_provider(monkeypatch, "lrclib", "BTS")
    monkeypatch.setattr(lyric_cache, "PROVIDERS", [provider])
    lyric_cache.fetch_lyrics("BTS", "Song", 180)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("clean cache exists"))
    snap = Snapshot(app="chrome.exe", artist="BTS - Topic", title="Song")
    assert lyric_cache.fetch_for_candidates(snap.search_candidates(), 180).resolved == (
        "BTS",
        "Song",
    )


def test_generic_alternative_does_not_replace_valid_original(lyric_cache, monkeypatch):
    from lyrica.sessions.base import Snapshot

    provider = wire_provider(monkeypatch, "lrclib", "Artist Official")
    monkeypatch.setattr(lyric_cache, "PROVIDERS", [provider])
    snap = Snapshot(app="chrome.exe", artist="Artist Official", title="Song")
    got = lyric_cache.fetch_for_candidates(snap.search_candidates(), 180)
    assert got.resolved == ("Artist Official", "Song")
    assert not lyric_cache._cache_path("Artist", "Song", 180).exists()


def test_resolved_field_does_not_shift_positional_backing():
    from lyrica.lyrics import Lyrics

    lyrics = Lyrics([], [], "", False, "", False, False, (), ["echo"])
    assert lyrics.backing == ["echo"]
    assert lyrics.resolved == ()


@pytest.mark.parametrize("channel", ["ArtistOfficialVEVO", "ArtistMusicVEVO", "Artist Official"])
def test_channel_variants_resolve_through_real_cascade(lyric_cache, monkeypatch, channel):
    from lyrica.sessions.base import Snapshot

    provider = wire_provider(monkeypatch, "lrclib", "Artist")
    monkeypatch.setattr(lyric_cache, "PROVIDERS", [provider])
    snap = Snapshot(app="chrome.exe", artist=channel, title=channel + " - Song")
    got = lyric_cache.fetch_for_candidates(snap.search_candidates(), 180)
    assert got.resolved == ("Artist", "Song")
    assert got.queried == ("Artist", "Song")


def test_legacy_topic_miss_does_not_block_new_clean_search(lyric_cache, monkeypatch):
    import time

    from lyrica.providers.cache import CacheEntry, ProviderState, write_entry
    from lyrica.sessions.base import Snapshot

    provider = wire_provider(monkeypatch, "lrclib", "BTS")
    monkeypatch.setattr(lyric_cache, "PROVIDERS", [provider])
    old = lyric_cache._cache_path("BTS - Topic", "Song", 180)
    write_entry(
        old,
        CacheEntry(None, {"lrclib": ProviderState(OutcomeKind.NO_MATCH, time.time())}),
        {"artist": "bts - topic", "title": "song", "duration": 180, "versions": []},
    )
    original = old.read_bytes()
    snap = Snapshot(app="chrome.exe", artist="BTS - Topic", title="Song")
    assert lyric_cache.fetch_for_candidates(snap.search_candidates(), 180).resolved == (
        "BTS",
        "Song",
    )
    assert old.read_bytes() == original


@pytest.mark.parametrize("artist", ["", "Various Artists", "Varios Artistas"])
@pytest.mark.parametrize("provider_name", ["lrclib", "community", "netease", "musixmatch"])
def test_incomplete_provider_credit_does_not_confirm_name(monkeypatch, artist, provider_name):
    provider = wire_provider(monkeypatch, provider_name, artist)
    outcome = provider.lookup(SongQuery("Artist", "Song", 180))
    assert outcome.kind is OutcomeKind.HIT
    assert outcome.lyrics.resolved == ()


def test_vevo_cover_uses_same_candidates_and_persistent_match(tmp_path, monkeypatch):
    from lyrica import artwork
    from lyrica.sessions.base import Snapshot

    monkeypatch.setattr(artwork, "_match_dir", lambda: tmp_path)

    def get(*a, **kw):
        response = Response({"results": [{"artistName": "Billie Eilish", "trackName": "Song"}]})
        response.raise_for_status = lambda: None
        return response

    monkeypatch.setattr(requests, "get", get)
    snap = Snapshot(app="chrome.exe", artist="BillieEilishVEVO", title="Song")
    assert artwork.identify(snap.search_candidates()).artist == "Billie Eilish"
    monkeypatch.setattr(requests, "get", lambda *a, **kw: pytest.fail("cached match expected"))
    assert artwork.identify(snap.search_candidates()).artist == "Billie Eilish"


@pytest.mark.parametrize(
    ("channel", "query"), [("BillieEilishVEVO", "BillieEilish"), ("Artist Official", "Artist")]
)
def test_card_does_not_present_unconfirmed_channel_guess(channel, query):
    from types import SimpleNamespace

    from lyrica.app import Overlay
    from lyrica.artwork import Release
    from lyrica.lyrics import Lyrics
    from lyrica.sessions.base import Snapshot

    panel = SimpleNamespace(
        _identified=Release(),
        lyrics=Lyrics(queried=(query, "Song")),
        _card_raw=None,
        _card_text=None,
        offset=0,
        wrap=500,
        _thumb_size=0,
        _title_font=None,
        _artist_font=None,
        _fit=lambda text, *args: text,
    )
    panel._resolved_name = lambda: Overlay._resolved_name(panel)
    snap = Snapshot(app="chrome.exe", artist=channel, title="Song", ok=True)
    assert Overlay._card_for(panel, snap) == ("Song", channel)
    panel.lyrics.resolved = ("Confirmed Artist", "Song")
    assert Overlay._card_for(panel, snap) == ("Song", "Confirmed Artist")
