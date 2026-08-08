"""Reading a local .env, for values that must not be committed (offline)."""
import pytest

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

@pytest.fixture
def clean_size(tmp_path, monkeypatch):
    """No saved size and no inherited setting.

    Both matter. A size chosen with the keyboard is written to the cache
    directory and wins over the environment, so without redirecting the cache
    these read whatever size the machine running them happens to be set to —
    which passes until someone resizes their overlay, then fails for reasons
    that have nothing to do with the change under test.
    """
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("LYRICA_SIZE", raising=False)
    return tmp_path


def test_the_designed_size_is_the_default(clean_size):
    assert config.size_scale() == 1.0


def test_a_size_chosen_with_the_keyboard_is_remembered(clean_size):
    config.save_size(1.3)
    assert config.size_scale() == 1.3


def test_a_remembered_size_wins_over_the_setting(clean_size, monkeypatch):
    # The only order that is not surprising: adjusting the window, watching it
    # change, then finding it back where it was on the next run would read as
    # the keyboard being broken.
    monkeypatch.setenv("LYRICA_SIZE", "0.8")
    config.save_size(1.3)
    assert config.size_scale() == 1.3


def test_a_remembered_size_is_clamped_on_the_way_out(clean_size):
    config.size_path().write_text("99", encoding="utf-8")
    assert config.size_scale() == config.SIZE_MAX


def test_an_unreadable_saved_size_falls_back_to_the_setting(clean_size, monkeypatch):
    monkeypatch.setenv("LYRICA_SIZE", "1.2")
    config.size_path().write_text("grande", encoding="utf-8")
    assert config.size_scale() == 1.2


def test_saving_into_an_unwritable_place_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path / "file"))
    (tmp_path / "file").write_text("not a directory", encoding="utf-8")
    config.save_size(1.5)       # must not raise


def test_a_size_is_read_from_the_environment(clean_size, monkeypatch):
    monkeypatch.setenv("LYRICA_SIZE", "1.35")
    assert config.size_scale() == 1.35


def test_a_size_below_the_range_is_clamped(clean_size, monkeypatch):
    # Smaller than this and the lyric font rounds to a size where the sweep
    # lands on whole characters and stops reading as a sweep.
    monkeypatch.setenv("LYRICA_SIZE", "0.2")
    assert config.size_scale() == config.SIZE_MIN


def test_a_size_above_the_range_is_clamped(clean_size, monkeypatch):
    monkeypatch.setenv("LYRICA_SIZE", "9")
    assert config.size_scale() == config.SIZE_MAX


def test_nonsense_falls_back_rather_than_raising(clean_size, monkeypatch):
    # Read at startup on a machine with no console, so raising here would be a
    # window that never appears and no way to find out why.
    monkeypatch.setenv("LYRICA_SIZE", "grande")
    assert config.size_scale() == 1.0


def test_an_empty_setting_is_the_designed_size(clean_size, monkeypatch):
    monkeypatch.setenv("LYRICA_SIZE", "   ")
    assert config.size_scale() == 1.0


# --- how solid the panel is -------------------------------------------------

def test_the_default_opacity_is_used_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv("LYRICA_OPACITY", raising=False)
    assert config.opacity() == config.OPACITY_DEFAULT


def test_an_opacity_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("LYRICA_OPACITY", "0.75")
    assert config.opacity() == 0.75


def test_opacity_is_clamped_to_a_range_that_stays_legible(monkeypatch):
    # The panel's whole job is to be legible over whatever is behind it, and
    # past a point the desktop starts competing with the words.
    monkeypatch.setenv("LYRICA_OPACITY", "0.1")
    assert config.opacity() == config.OPACITY_MIN
    monkeypatch.setenv("LYRICA_OPACITY", "5")
    assert config.opacity() == config.OPACITY_MAX


def test_nonsense_opacity_falls_back_rather_than_raising(monkeypatch):
    monkeypatch.setenv("LYRICA_OPACITY", "opaco")
    assert config.opacity() == config.OPACITY_DEFAULT


def test_a_more_solid_panel_lets_less_of_the_desktop_through():
    from lyrica.glass import alpha_panel
    assert (alpha_panel(0.90).pedestal((255,) * 3)[0]
            < alpha_panel(0.82).pedestal((255,) * 3)[0])


# --- what the overlay remembers between runs --------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))
    for name in ("LYRICA_SIZE", "LYRICA_OPACITY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_nothing_remembered_yet_is_not_an_error(store):
    assert config.settings() == {}
    assert config.saved_place() is None
    assert config.saved_offset() == 0.0


def test_a_place_survives(store):
    config.save_place(340, 900)
    assert config.saved_place() == (340, 900)


def test_an_offset_survives(store):
    config.save_offset(-0.75)
    assert config.saved_offset() == -0.75


def test_settings_do_not_overwrite_each_other(store):
    config.save_place(10, 20)
    config.save_offset(0.5)
    config.save_size(1.3)
    assert config.saved_place() == (10, 20)
    assert config.saved_offset() == 0.5
    assert config.size_scale() == 1.3


def test_a_size_saved_the_old_way_is_still_read(store):
    # Written before there were several things to keep. An upgrade must not
    # silently reset the one setting that already existed.
    config.size_path().write_text("1.4", encoding="utf-8")
    assert config.size_scale() == 1.4


def test_the_new_file_wins_over_the_old_one(store):
    config.size_path().write_text("1.4", encoding="utf-8")
    config.save_size(0.8)
    assert config.size_scale() == 0.8


def test_a_corrupt_store_degrades_to_defaults(store):
    # Read before the window exists, on a machine with no console. A file
    # someone edited into nonsense has to cost the settings, not the overlay.
    config.settings_path().write_text("{not json", encoding="utf-8")
    assert config.settings() == {}
    assert config.saved_place() is None
    assert config.size_scale() == 1.0


def test_a_store_that_is_not_a_mapping_is_ignored(store):
    config.settings_path().write_text("[1, 2, 3]", encoding="utf-8")
    assert config.settings() == {}


def test_a_nonsense_place_is_ignored(store):
    config.settings_path().write_text('{"place": "middle"}', encoding="utf-8")
    assert config.saved_place() is None


def test_a_wild_offset_is_clamped(store):
    # A nudge is a correction, not a seek. Restoring a mis-saved one would show
    # the wrong line with no obvious cause.
    config.settings_path().write_text('{"offset": 9999}', encoding="utf-8")
    assert config.saved_offset() == config.OFFSET_LIMIT_S
    config.settings_path().write_text('{"offset": "late"}', encoding="utf-8")
    assert config.saved_offset() == 0.0


def test_saving_the_same_value_twice_does_not_rewrite(store):
    config.save_place(5, 5)
    before = config.settings_path().stat().st_mtime_ns
    config.save_place(5, 5)
    assert config.settings_path().stat().st_mtime_ns == before


def test_an_unwritable_store_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path / "file"))
    (tmp_path / "file").write_text("not a directory", encoding="utf-8")
    config.save_place(1, 2)      # must not raise
    config.save_offset(0.1)


def test_the_centre_is_kept_because_the_window_is_not_one_width(store):
    # The bug this replaced a corner to fix. The panel collapses to the card
    # when a track has no lyrics and both sizes keep the middle where it was,
    # so a corner saved while compact describes a place the full window was
    # never at — measured as reopening 400 px right of where it had been put.
    wide, compact = 1125, 325
    centre_x = 527 + wide // 2                  # left at 527, full size
    config.save_place(centre_x, 700)

    # Collapsing does not touch the store, and reopening at either width puts
    # the middle back where it was.
    for width in (wide, compact):
        left = config.saved_place()[0] - width // 2
        assert left + width // 2 == centre_x


def test_a_nudge_belongs_to_the_track_it_was_made_for(store):
    # What it corrects is per track: a video with eight seconds of intro before
    # the song needs eight seconds no other track wants.
    config.save_offset(-8.0, "chrome.exe|Tiago PZK|Traductor")
    config.save_offset(0.5, "Spotify.exe|Air|La Femme d'Argent")
    assert config.saved_offset("chrome.exe|Tiago PZK|Traductor") == -8.0
    assert config.saved_offset("Spotify.exe|Air|La Femme d'Argent") == 0.5


def test_a_track_with_no_nudge_of_its_own_starts_at_zero(store):
    config.save_offset(-8.0, "chrome.exe|Tiago PZK|Traductor")
    assert config.saved_offset("chrome.exe|Someone|Else") == 0.0


def test_a_nudge_saved_before_nudges_were_per_track_still_applies(store):
    config.settings_path().write_text('{"offset": 1.25}', encoding="utf-8")
    assert config.saved_offset("chrome.exe|Anyone|Anything") == 1.25


def test_a_per_track_nudge_beats_the_old_global_one(store):
    config.settings_path().write_text('{"offset": 1.25}', encoding="utf-8")
    config.save_offset(-3.0, "chrome.exe|Tiago PZK|Traductor")
    assert config.saved_offset("chrome.exe|Tiago PZK|Traductor") == -3.0


def test_only_so_many_tracks_are_remembered(store):
    for i in range(config.OFFSET_MEMORY + 20):
        config.save_offset(0.25, f"chrome.exe|artist|track {i}")
    kept = config.settings().get("offsets")
    assert len(kept) == config.OFFSET_MEMORY
    assert "chrome.exe|artist|track 0" not in kept
    assert f"chrome.exe|artist|track {config.OFFSET_MEMORY + 19}" in kept


def test_re_nudging_a_track_keeps_it_from_being_forgotten(store):
    config.save_offset(0.25, "old favourite")
    for i in range(config.OFFSET_MEMORY - 1):
        config.save_offset(0.25, f"filler {i}")
    config.save_offset(0.5, "old favourite")          # touched again
    for i in range(config.OFFSET_MEMORY - 1):
        config.save_offset(0.25, f"later filler {i}")
    assert config.saved_offset("old favourite") == 0.5


def test_the_sweep_front_defaults_to_the_designed_width(store, monkeypatch):
    monkeypatch.delenv("LYRICA_SWEEP_FEATHER", raising=False)
    assert config.sweep_feather() == config.FEATHER_DEFAULT


def test_the_sweep_front_can_be_tuned(store, monkeypatch):
    monkeypatch.setenv("LYRICA_SWEEP_FEATHER", "10")
    assert config.sweep_feather() == 10.0


def test_a_front_narrower_than_a_frame_can_draw_is_clamped(store, monkeypatch):
    # Under about ten pixels the transition is shorter than one frame at fast
    # delivery, so there is nothing left to draw; the floor says so rather than
    # letting a zero through.
    monkeypatch.setenv("LYRICA_SWEEP_FEATHER", "0")
    assert config.sweep_feather() == config.FEATHER_MIN
    monkeypatch.setenv("LYRICA_SWEEP_FEATHER", "500")
    assert config.sweep_feather() == config.FEATHER_MAX


def test_an_unparseable_front_is_ignored(store, monkeypatch):
    monkeypatch.setenv("LYRICA_SWEEP_FEATHER", "ancho")
    assert config.sweep_feather() == config.FEATHER_DEFAULT


def test_the_bloom_defaults_and_can_be_tuned(store, monkeypatch):
    monkeypatch.delenv("LYRICA_BLOOM", raising=False)
    assert config.bloom_factor() == config.BLOOM_DEFAULT
    monkeypatch.setenv("LYRICA_BLOOM", "0.4")
    assert config.bloom_factor() == 0.4


def test_the_bloom_can_be_turned_off_by_name(store, monkeypatch):
    for word in ("off", "none", "NO"):
        monkeypatch.setenv("LYRICA_BLOOM", word)
        assert config.bloom_factor() == 0.0


def test_a_wild_bloom_is_clamped_and_a_bad_one_ignored(store, monkeypatch):
    monkeypatch.setenv("LYRICA_BLOOM", "9")
    assert config.bloom_factor() == config.BLOOM_MAX
    monkeypatch.setenv("LYRICA_BLOOM", "brillante")
    assert config.bloom_factor() == config.BLOOM_DEFAULT


def test_the_lift_defaults_and_can_be_tuned(store, monkeypatch):
    monkeypatch.delenv("LYRICA_LIFT", raising=False)
    assert config.lift_factor() == config.LIFT_DEFAULT
    monkeypatch.setenv("LYRICA_LIFT", "0.5")
    assert config.lift_factor() == 0.5
    monkeypatch.setenv("LYRICA_LIFT", "off")
    assert config.lift_factor() == 0.0
    monkeypatch.setenv("LYRICA_LIFT", "99")
    assert config.lift_factor() == config.LIFT_MAX
