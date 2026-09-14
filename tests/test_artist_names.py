"""Channel labels must not change a performer's identity."""
import pytest

from lyrica.artist_names import (
    ArtistReading,
    artist_cache_mode,
    artist_readings,
    artist_relation,
    clean_artist,
)


@pytest.mark.parametrize(('raw', 'expected'), [
    ('BTS - Topic', 'BTS'), ('U2 - Topic', 'U2'),
    ('Topic - Topic', 'Topic'), ('Topic', 'Topic'),
    ('Hot Topic', 'Hot Topic'), ('Artist-Topic', 'Artist-Topic'),
    ('  Queen\u00a0 –  TOPIC  ', 'Queen'), ('Artist ‐ Topic', 'Artist'),
    ('Artist ‑ Topic', 'Artist'), ('Artist — Topic', 'Artist'),
    ('Artist - Topic - Topic', 'Artist - Topic - Topic'),
    (' - Topic ', '- Topic'), ('', ''), ('Artist - Tema', 'Artist - Tema'),
])
def test_topic_normalization(raw, expected):
    assert clean_artist(raw) == expected
    assert clean_artist(clean_artist(raw)) == expected


@pytest.mark.parametrize(('raw', 'expected'), [
    ('BillieEilishVEVO', ['BillieEilishVEVO', 'BillieEilish']),
    ('Artist VEVO', ['Artist VEVO', 'Artist']),
    ('Artist - VEVO', ['Artist - VEVO', 'Artist']),
    ('ArtistOfficialVEVO', ['ArtistOfficialVEVO', 'ArtistOfficial', 'Artist']),
    ('ArtistMusicVEVO', ['ArtistMusicVEVO', 'ArtistMusic', 'Artist']),
    ('Artist Official', ['Artist Official', 'Artist']),
    ('Artist - Oficial', ['Artist - Oficial', 'Artist']),
    ('Artist (Channel)', ['Artist (Channel)', 'Artist']),
    ('Artist [TV]', ['Artist [TV]', 'Artist']),
    ('ArtistOfficial', ['ArtistOfficial']), ('VEVO', ['VEVO']),
    ('Music', ['Music']), ('Artist Records', ['Artist Records']),
])
def test_channel_candidates(raw, expected):
    readings = artist_readings(raw, channel_hint=True)
    assert [r.name for r in readings] == expected
    assert all(r.original == raw for r in readings)
    assert [r.name for r in artist_readings(raw, channel_hint=False)] == [raw]


@pytest.mark.parametrize(('left', 'right', 'expected'), [
    ('BTS - Topic', 'BTS', 'exact'), ('U2', 'U2 - Topic', 'exact'),
    ('Queen', 'Queensryche', 'mismatch'),
    ('ROSALÍA', 'Rosalia', 'exact'), ('AC/DC', 'ACDC', 'exact'),
    ('Dua  Lipa', 'Dua Lipa', 'exact'),
    ('Dua Lipa', 'Dua Lipa feat. DaBaby', 'credit'),
    ('Dua Lipa & DaBaby', 'Dua Lipa', 'credit'),
    ('BillieEilish', 'Billie Eilish', 'mismatch'),
    ('', 'Queen', 'unknown'), ('Various Artists - Topic', 'Queen', 'unknown'),
    ('Varios Artistas', 'Queen', 'unknown'),
])
def test_artist_relations(left, right, expected):
    assert artist_relation(left, right) == expected


def test_compact_name_requires_vevo_evidence():
    alias = ArtistReading('BillieEilish', 'BillieEilishVEVO', 'vevo')
    assert artist_relation(alias, 'Billie Eilish') == 'channel_alias'
    assert artist_relation(alias, 'Billie Eilish Tribute') == 'mismatch'
    assert artist_cache_mode(alias) == 'vevo'
    assert artist_cache_mode(None) == ''


@pytest.mark.parametrize('value', [('A',), ('A', 'B', 'C', 'D'), ('A', 2), 'AB'])
def test_legacy_candidate_rejects_malformed_input(value):
    from lyrica.metadata import as_candidate
    with pytest.raises(ValueError):
        as_candidate(value)


def test_legacy_candidate_does_not_invent_channel_evidence():
    from lyrica.metadata import as_candidate
    assert as_candidate(('BillieEilish', 'CHIHIRO')).artist.rule == 'original'
    candidate = as_candidate(('BTS - Topic', 'Song', 'Song (Live)'))
    assert candidate.artist.name == 'BTS'
    assert candidate.raw_title == 'Song (Live)'
