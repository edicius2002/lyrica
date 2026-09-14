"""Lookup interpretations shared by lyrics and artwork consumers."""
from dataclasses import dataclass

from lyrica.artist_names import ArtistReading, artist_readings


@dataclass(frozen=True)
class LookupCandidate:
    artist: ArtistReading
    title: str
    raw_title: str


def as_candidate(value: LookupCandidate | tuple) -> LookupCandidate:
    if isinstance(value, LookupCandidate):
        return value
    if (not isinstance(value, tuple) or len(value) not in (2, 3)
            or not all(isinstance(part, str) for part in value)):
        raise ValueError('expected an artist/title pair or artist/title/raw-title triple')
    artist, title = value[:2]
    return LookupCandidate(artist_readings(artist, channel_hint=False)[0], title,
                           value[2] if len(value) == 3 else title)
