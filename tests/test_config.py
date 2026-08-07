"""Reading a local .env, for values that must not be committed (offline)."""
from lyrica import config
from lyrica.config import find_env, load, parse

# --- parsing ----------------------------------------------------------------

def test_simple_pairs():
    assert parse("A=1\nB=two") == {"A": "1", "B": "two"}


def test_comments_and_blank_lines_are_ignored():
    assert parse("# a note\n\nA=1\n  # indented\n") == {"A": "1"}


def test_surrounding_quotes_are_stripped():
    # A token pasted from a web page often arrives wearing them, and they are
    # not part of the value.
    assert parse('A="quoted"\nB=\'single\'') == {"A": "quoted", "B": "single"}


def test_a_quote_inside_a_value_is_kept():
    assert parse('A=say "hi"') == {"A": 'say "hi"'}


def test_whitespace_around_the_equals_is_trimmed():
    assert parse("  A  =  spaced  ") == {"A": "spaced"}


def test_a_value_may_contain_an_equals():
    # Tokens and paths do.
    assert parse("A=x=y=z") == {"A": "x=y=z"}


def test_an_empty_value_is_kept_as_empty():
    assert parse("A=") == {"A": ""}


def test_lines_without_an_equals_are_skipped():
    assert parse("nonsense\nA=1") == {"A": "1"}


# --- finding ----------------------------------------------------------------

def test_the_file_is_found_in_a_parent_directory(tmp_path):
    # So it works whichever directory the overlay is launched from.
    (tmp_path / ".env").write_text("A=1")
    nested = tmp_path / "one" / "two"
    nested.mkdir(parents=True)
    assert find_env(nested) == tmp_path / ".env"


def test_no_file_is_not_an_error(tmp_path):
    assert find_env(tmp_path) is None
    assert load(tmp_path) == {}


# --- loading ----------------------------------------------------------------

def test_values_reach_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("LYRICA_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text("LYRICA_TEST_KEY=from-file")
    assert load(tmp_path) == {"LYRICA_TEST_KEY": "from-file"}
    import os
    assert os.environ["LYRICA_TEST_KEY"] == "from-file"


def test_an_exported_variable_wins_over_the_file(tmp_path, monkeypatch):
    # Exporting a value is a deliberate act for that session; a file on disk
    # should not quietly override it.
    monkeypatch.setenv("LYRICA_TEST_KEY", "from-shell")
    (tmp_path / ".env").write_text("LYRICA_TEST_KEY=from-file")
    assert load(tmp_path) == {}
    import os
    assert os.environ["LYRICA_TEST_KEY"] == "from-shell"


def test_an_unreadable_file_is_not_an_error(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("A=1")

    def boom(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr(config.Path, "read_text", boom)
    assert load(tmp_path) == {}


# --- how big the overlay is drawn -------------------------------------------

def test_the_designed_size_is_the_default(monkeypatch):
    monkeypatch.delenv("LYRICA_SIZE", raising=False)
    assert config.size_scale() == 1.0


def test_a_size_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("LYRICA_SIZE", "1.35")
    assert config.size_scale() == 1.35


def test_a_size_below_the_range_is_clamped(monkeypatch):
    # Smaller than this and the lyric font rounds to a size where the sweep
    # lands on whole characters and stops reading as a sweep.
    monkeypatch.setenv("LYRICA_SIZE", "0.2")
    assert config.size_scale() == config.SIZE_MIN


def test_a_size_above_the_range_is_clamped(monkeypatch):
    monkeypatch.setenv("LYRICA_SIZE", "9")
    assert config.size_scale() == config.SIZE_MAX


def test_nonsense_falls_back_rather_than_raising(monkeypatch):
    # Read at startup on a machine with no console, so raising here would be a
    # window that never appears and no way to find out why.
    monkeypatch.setenv("LYRICA_SIZE", "grande")
    assert config.size_scale() == 1.0


def test_an_empty_setting_is_the_designed_size(monkeypatch):
    monkeypatch.setenv("LYRICA_SIZE", "   ")
    assert config.size_scale() == 1.0
