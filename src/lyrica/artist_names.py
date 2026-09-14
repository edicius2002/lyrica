"""Artist identity, keeping channel-name guesses separate from musical names."""

import re
from dataclasses import dataclass
from typing import Literal

from lyrica.textmatch import fold

ArtistRule = Literal["original", "topic", "vevo", "decorated"]
ArtistRelation = Literal["unknown", "exact", "credit", "channel_alias", "mismatch"]
_DASHES = "-‐‑–—"
_TOPIC = re.compile(rf"\s+[{_DASHES}]\s+topic$", re.IGNORECASE)
_VEVO = re.compile(r"vevo$", re.IGNORECASE)
_VEVO_EXTRA = re.compile(r"(official|music)$", re.IGNORECASE)
_LABEL = r"(?:official|oficial|tv|channel|canal|youtube|productions|producciones)"
_DECORATED = re.compile(
    rf"\s+(?:[{_DASHES}]\s+)?(?:{_LABEL}|\({_LABEL}\)|\[{_LABEL}\])$",
    re.IGNORECASE,
)
_CREDIT_SEPARATOR = re.compile(
    r"\s*(?:,|&|\+|×|\b(?:feat|ft|featuring|with|and|x|vs|con|y)\.?\b)\s*",
    re.IGNORECASE,
)
_COLLECTIVE = {"various artists", "varios artistas"}


@dataclass(frozen=True)
class ArtistReading:
    name: str
    original: str
    rule: ArtistRule = "original"


def clean_artist(value: str) -> str:
    """Remove one unambiguous Topic label; never alter the original snapshot."""
    name = " ".join(value.split())
    stripped = _TOPIC.sub("", name)
    return stripped if stripped and not _TOPIC.search(stripped) else name


def artist_readings(value: str, *, channel_hint: bool) -> tuple[ArtistReading, ...]:
    name = clean_artist(value)
    rule: ArtistRule = "topic" if name != " ".join(value.split()) else "original"
    readings = [ArtistReading(name, value, rule)]
    if channel_hint:
        body = _VEVO.sub("", name).rstrip(" " + _DASHES)
        if body and body != name:
            readings.append(ArtistReading(body, value, "vevo"))
            shorter = _VEVO_EXTRA.sub("", body).rstrip(" " + _DASHES)
            if shorter and shorter != body:
                readings.append(ArtistReading(shorter, value, "vevo"))
        else:
            shorter = _DECORATED.sub("", name)
            if shorter and shorter != name:
                readings.append(ArtistReading(shorter, value, "decorated"))
    return tuple(readings)


def _key(value: str) -> str:
    return " ".join(fold(clean_artist(value)).split())


def artist_relation(requested: ArtistReading | str, returned: str) -> ArtistRelation:
    reading = (
        requested if isinstance(requested, ArtistReading) else ArtistReading(requested, requested)
    )
    left, right = _key(reading.name), _key(returned)
    if not left or not right or left in _COLLECTIVE or right in _COLLECTIVE:
        return "unknown"
    if left == right:
        return "exact"
    if reading.rule == "vevo" and left.replace(" ", "") == right.replace(" ", ""):
        return "channel_alias"
    wanted = {_key(p) for p in _CREDIT_SEPARATOR.split(clean_artist(reading.name))}
    got = {_key(p) for p in _CREDIT_SEPARATOR.split(clean_artist(returned))}
    if (wanted & got) - {""}:
        return "credit"
    return "mismatch"


def artist_cache_mode(reading: ArtistReading | None) -> str:
    return "vevo" if reading is not None and reading.rule == "vevo" else ""


def resolved_name(artist: str, title: str) -> tuple:
    """Only a complete, non-collective catalogue credit can name the card."""
    if title.strip() and artist_relation(artist, artist) == "exact":
        return clean_artist(artist), title.strip()
    return ()
