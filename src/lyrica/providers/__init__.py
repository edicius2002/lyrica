"""Provider cascade with a shared on-disk cache.

`fetch_lyrics()` walks the provider list in order and returns the first hit.
Results (including misses) are cached under %LOCALAPPDATA%/Lyrica/cache so a
track is only ever looked up once.
"""
import hashlib
import json
import os
from pathlib import Path

from lyrica.lyrics import Lyrics
from lyrica.providers.base import LyricsProvider
from lyrica.providers.lrclib import LrclibProvider

PROVIDERS: list[LyricsProvider] = [
    LrclibProvider(),
]

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Lyrica" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(artist: str, title: str, duration: float) -> Path:
    key = f"{artist.lower()}|{title.lower()}|{int(duration)}"
    # Naming a file, not protecting anything: the digest just turns arbitrary
    # track text into a safe filename.
    digest = hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()
    return CACHE_DIR / (digest + ".json")


def _cache_read(path: Path) -> Lyrics | None:
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("miss"):
        return None
    lyr = Lyrics(**{k: d[k] for k in ("plain", "synced", "source", "instrumental")})
    lyr.lines = [tuple(x) for x in d["lines"]]
    return lyr


def _cache_write(path: Path, result: Lyrics | None) -> None:
    if result is None:
        path.write_text(json.dumps({"miss": True}), encoding="utf-8")
    else:
        path.write_text(json.dumps({
            "lines": result.lines, "plain": result.plain, "synced": result.synced,
            "source": result.source, "instrumental": result.instrumental,
        }, ensure_ascii=False), encoding="utf-8")


def fetch_lyrics(artist: str, title: str, duration: float = 0.0,
                 album: str = "") -> Lyrics | None:
    """Cascade through providers for one artist/title pair.

    None when no source has the track. The answer, hit or miss, is cached: a
    track with no lyrics anywhere is the common case on SoundCloud, and without
    caching that the full cascade would run again on every replay.
    """
    if not title:
        return None
    cpath = _cache_path(artist, title, duration)
    if cpath.exists():
        try:
            return _cache_read(cpath)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # unreadable cache entry: fall through and fetch again

    result = None
    for provider in PROVIDERS:
        result = provider.fetch(artist, title, duration, album)
        if result is not None:
            break

    try:
        _cache_write(cpath, result)
    except OSError:
        pass
    return result


def fetch_for_candidates(candidates: list[tuple[str, str]], duration: float = 0.0,
                         album: str = "") -> Lyrics | None:
    """Try each artist/title reading in turn and keep the first that resolves.

    Browsers disagree about what their metadata fields mean, so a payload can
    have several defensible readings. Each is cached separately, which means a
    miss on an early candidate costs one request the first time and nothing
    afterwards.
    """
    for artist, title in candidates:
        result = fetch_lyrics(artist, title, duration, album)
        if result is not None:
            return result
    return None
