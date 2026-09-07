"""Conservative song-identity checks, independent of lyric quality."""

import re
from dataclasses import dataclass

from lyrica.textmatch import fold

_CREDIT_SEPARATOR = re.compile(
    r"\s*(?:,|&|\+|×|\b(?:feat|ft|featuring|with|and|x|vs|con|y)\.?\b)\s*",
    re.IGNORECASE,
)

_VERSION_PATTERNS = {
    "live": re.compile(r"\b(?:live|concert|unplugged)\b"),
    "remix": re.compile(r"\b(?:remix|club mix|dance mix|extended mix)\b"),
    "acoustic": re.compile(r"\bacoustic\b"),
    "instrumental": re.compile(r"\b(?:instrumental|karaoke)\b"),
    "demo": re.compile(r"\bdemo\b"),
    "edit": re.compile(r"\b(?:radio edit|single edit)\b"),
    "remaster": re.compile(r"\bremaster(?:ed)?\b"),
    "sped_up": re.compile(r"\bsped up\b"),
    "slowed": re.compile(r"\bslowed(?: down)?\b"),
}


@dataclass(frozen=True)
class IdentityDecision:
    """Whether available provider metadata can describe the requested song."""

    accepted: bool
    reason: str


@dataclass(frozen=True)
class SongQuery:
    """One provider lookup, retaining evidence removed from its clean title."""

    artist: str
    title: str
    duration: float = 0.0
    album: str = ""
    raw_title: str = ""


def _artist_parts(value: str) -> set[str]:
    return {fold(part) for part in _CREDIT_SEPARATOR.split(value) if fold(part)}


def artists_compatible(requested: str, returned: str) -> bool:
    """Accept unknown and shared credits, but reject known strangers."""
    if not requested.strip() or not returned.strip():
        return True
    wanted = _artist_parts(requested)
    got = _artist_parts(returned)
    return any(
        left == right
        or (min(len(left), len(right)) >= 4 and (left in right or right in left))
        for left in wanted for right in got
    )


def version_qualifiers(title: str) -> frozenset[str]:
    """Return only explicit recording/version claims present in a raw title."""
    normalized = fold(title)
    return frozenset(
        name for name, pattern in _VERSION_PATTERNS.items()
        if pattern.search(normalized)
    )


def validate_identity(*, requested_artist: str, requested_title: str,
                      returned_artist: str, returned_title: str,
                      requested_raw_title: str | None = None) -> IdentityDecision:
    """Reject clear artist and recording contradictions before fuzzy ranking."""
    if not artists_compatible(requested_artist, returned_artist):
        return IdentityDecision(False, "artist_mismatch")
    requested_versions = version_qualifiers(requested_raw_title or requested_title)
    returned_versions = version_qualifiers(returned_title)
    if returned_versions and returned_versions != requested_versions:
        return IdentityDecision(False, "version_mismatch")
    return IdentityDecision(True, "compatible")
