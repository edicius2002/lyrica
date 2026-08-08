"""How two strings are compared when deciding whether they name the same track.

Comparison only. Nothing here is ever shown to anyone, and nothing here is ever
a cache key — folding a key would invalidate every entry already on disk, and
would fuse two records the display still has to tell apart.
"""
import unicodedata


def fold(s: str) -> str:
    """Lowercased, unaccented, and stripped of everything but letters, digits
    and spaces.

    The accents go because the sources disagree about them and the disagreement
    carries no information: a browser reports "Despecha" where the catalogue
    holds "DESPECHÁ". Keeping them made those two neither equal nor substrings
    of one another, and in the community provider's scoring that is not a near
    miss — it is the -5 reserved for *a different performer*. Measured on the
    correct record: 9.5 with the accents, -3.5 without, against a floor of 3.0.
    The right lyrics were thrown away over one acute accent. The cover search
    lost the same track outright.

    Decomposed rather than transliterated, so scripts that are not Latin come
    through intact. NetEase and the community source both answer with Chinese
    and Japanese titles, and a transliterator would flatten those to nothing.
    """
    decomposed = unicodedata.normalize("NFKD", s or "")
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in bare.lower() if c.isalnum() or c.isspace()).strip()
