"""Exercise real provider parsers with deterministic network responses."""
import json

import pytest
import requests

from lyrica.artist_names import ArtistReading
from lyrica.providers import community, lrclib, musixmatch, netease
from lyrica.providers.base import OutcomeKind
from lyrica.providers.identity import SongQuery

TTML = '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1s" end="2s">line</p></div></body></tt>'


class Response:
    status_code = 200
    headers = {}
    text = TTML

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def wire_provider(monkeypatch, name, artist, title='Song'):
    """Only transport is replaced; matching, parsing and outcomes stay real."""
    if name == 'lrclib':
        record = {'artistName': artist, 'trackName': title,
                  'syncedLyrics': '[00:01.00]line', 'duration': 180}
        monkeypatch.setattr(requests, 'get', lambda url, **kw: Response(
            [record] if url.endswith('/search') else record))
        return lrclib.LrclibProvider()
    if name == 'community':
        record = {'artist_name': artist, 'track_name': title, 'duration': 180,
                  'lyricsUrl': 'https://example.test/lyrics', 'timing_type': 'line'}
        monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response({'results': [record]}))
        return community.CommunityTtmlProvider()
    if name == 'netease':
        record = {'artists': [{'name': artist}], 'name': title,
                  'duration': 180000, 'id': 1}
        monkeypatch.setattr(requests, 'post', lambda *a, **kw: Response({'result': {'songs': [record]}}))
        monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response({'lrc': {'lyric': '[00:01.00]line'}}))
        return netease.NeteaseProvider()
    provider = musixmatch.MusixmatchProvider()
    def call(endpoint, params):
        if endpoint == 'matcher.track.get':
            body = {'track': {'artist_name': artist, 'track_name': title,
                              'has_richsync': 1, 'track_id': 1}}
        else:
            body = {'richsync': {'richsync_body': json.dumps([
                {'ts': 1.0, 'te': 2.0, 'x': 'line', 'l': [{'c': 'line', 'o': 0.0}]}
            ])}}
        return {'message': {'header': {'status_code': 200}, 'body': body}}
    monkeypatch.setattr(provider, '_call', call)
    monkeypatch.setattr(provider, '_token_outcome', lambda: ('test-token', None))
    return provider


@pytest.mark.parametrize('provider_name', ['lrclib', 'community', 'netease', 'musixmatch'])
@pytest.mark.parametrize(('requested', 'returned'), [('BTS - Topic', 'BTS'), ('U2 - Topic', 'U2')])
def test_provider_accepts_topic_and_records_catalogue_name(monkeypatch, provider_name, requested, returned):
    provider = wire_provider(monkeypatch, provider_name, returned)
    outcome = provider.lookup(SongQuery(requested, 'Song', 180))
    assert outcome.kind is OutcomeKind.HIT
    assert outcome.lyrics.resolved == (returned, 'Song')


@pytest.mark.parametrize('provider_name', ['lrclib', 'community', 'netease', 'musixmatch'])
def test_provider_rejects_name_substring(monkeypatch, provider_name):
    provider = wire_provider(monkeypatch, provider_name, 'Queensryche')
    assert provider.lookup(SongQuery('Queen', 'Song', 180)).kind is OutcomeKind.NO_MATCH


@pytest.mark.parametrize('provider_name', ['lrclib', 'community', 'netease', 'musixmatch'])
def test_provider_vevo_alias_requires_provenance(monkeypatch, provider_name):
    provider = wire_provider(monkeypatch, provider_name, 'Billie Eilish')
    assert provider.lookup(SongQuery('BillieEilish', 'Song', 180)).kind is OutcomeKind.NO_MATCH
    reading = ArtistReading('BillieEilish', 'BillieEilishVEVO', 'vevo')
    outcome = provider.lookup(SongQuery('BillieEilish', 'Song', 180, artist_reading=reading))
    assert outcome.kind is OutcomeKind.HIT
    assert outcome.lyrics.resolved == ('Billie Eilish', 'Song')


@pytest.fixture
def lyric_cache(tmp_path, monkeypatch):
    from lyrica import providers
    monkeypatch.setattr(providers, 'CACHE_DIR', tmp_path)
    return providers


def test_vevo_cache_mode_does_not_poison_ordinary_lookup(lyric_cache, monkeypatch):
    from lyrica.sessions.base import Snapshot
    provider = wire_provider(monkeypatch, 'lrclib', 'Billie Eilish')
    monkeypatch.setattr(lyric_cache, 'PROVIDERS', [provider])
    snap = Snapshot(app='chrome.exe', artist='BillieEilishVEVO', title='Song')
    got = lyric_cache.fetch_for_candidates(snap.search_candidates(), 180)
    assert got.resolved == ('Billie Eilish', 'Song')
    assert got.queried == ('BillieEilish', 'Song')
    assert lyric_cache.fetch_lyrics('BillieEilish', 'Song', 180) is None
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('must use cache'))
    again = lyric_cache.fetch_for_candidates(snap.search_candidates(), 180)
    assert again.resolved == got.resolved


def test_clean_topic_lookup_reuses_existing_hit(lyric_cache, monkeypatch):
    from lyrica.sessions.base import Snapshot
    provider = wire_provider(monkeypatch, 'lrclib', 'BTS')
    monkeypatch.setattr(lyric_cache, 'PROVIDERS', [provider])
    lyric_cache.fetch_lyrics('BTS', 'Song', 180)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('clean cache exists'))
    snap = Snapshot(app='chrome.exe', artist='BTS - Topic', title='Song')
    assert lyric_cache.fetch_for_candidates(snap.search_candidates(), 180).resolved == ('BTS', 'Song')


def test_generic_alternative_does_not_replace_valid_original(lyric_cache, monkeypatch):
    from lyrica.sessions.base import Snapshot
    provider = wire_provider(monkeypatch, 'lrclib', 'Artist Official')
    monkeypatch.setattr(lyric_cache, 'PROVIDERS', [provider])
    snap = Snapshot(app='chrome.exe', artist='Artist Official', title='Song')
    got = lyric_cache.fetch_for_candidates(snap.search_candidates(), 180)
    assert got.resolved == ('Artist Official', 'Song')
    assert not lyric_cache._cache_path('Artist', 'Song', 180).exists()
