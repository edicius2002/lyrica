"""Where the cache lives, and why folder sync is enough (offline)."""
import importlib

from lyrica.config import cache_root
from lyrica.providers import default_cache_dir


def test_the_override_moves_the_whole_store(monkeypatch, tmp_path):
    # It points at the store, not at one folder inside it, so covers and
    # lyrics travel together.
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path / "synced"))
    assert default_cache_dir() == tmp_path / "synced" / "cache"
    assert cache_root() == tmp_path / "synced"


def test_without_an_override_it_sits_under_local_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("LYRICA_CACHE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_cache_dir() == tmp_path / "Lyrica" / "cache"


def test_the_lyrics_folder_keeps_its_name(monkeypatch, tmp_path):
    # Renaming it would orphan everything already looked up, and a cache that
    # silently starts empty is worse than a name that is merely unspecific.
    monkeypatch.delenv("LYRICA_CACHE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_cache_dir().name == "cache"


def test_an_empty_override_is_ignored(monkeypatch, tmp_path):
    # Set-but-blank is how a shell reports an unset variable often enough that
    # treating it as a path would put the cache at the filesystem root.
    monkeypatch.setenv("LYRICA_CACHE_DIR", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_cache_dir() == tmp_path / "Lyrica" / "cache"


def test_the_override_is_read_at_import(monkeypatch, tmp_path):
    # The point of the variable is that a second machine can be pointed at a
    # synced folder before the app starts.
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path / "elsewhere"))
    import lyrica.providers as providers_module
    reloaded = importlib.reload(providers_module)
    try:
        assert reloaded.CACHE_DIR == tmp_path / "elsewhere" / "cache"
        assert reloaded.CACHE_DIR.exists(), "the directory is created on startup"
    finally:
        monkeypatch.delenv("LYRICA_CACHE_DIR", raising=False)
        importlib.reload(providers_module)


def test_entries_are_write_once_so_two_machines_cannot_conflict(tmp_path, monkeypatch):
    """The property that makes plain folder sync safe.

    A cache entry is named by a hash of the track and written once. Two machines
    playing different music add different files; playing the same music they
    write identical content to the same name. Neither case is a conflict, which
    is why this needs no database and no server.
    """
    import lyrica.providers as providers_module
    monkeypatch.setattr(providers_module, "CACHE_DIR", tmp_path)

    first = providers_module._cache_path("Artist", "Song", 200.0)
    again = providers_module._cache_path("Artist", "Song", 200.0)
    other = providers_module._cache_path("Artist", "Other Song", 200.0)

    assert first == again, "the same track always names the same file"
    assert first != other
    assert first.parent == tmp_path
