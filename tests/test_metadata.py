"""Metadata normalization, tested against payloads captured from live sessions.

Every `Snapshot` below was recorded by `research/viability/probe_browser_session.py`
on 2026-08-06 and is reproduced verbatim. The previous version of this file
asserted that Chrome leaves `artist` empty, which Chrome never does — the tests
passed while describing a payload that does not occur. Cases here are only added
from something a session actually published.
"""
import pytest

from lyrica.sessions.base import (
    Snapshot,
    clean_title,
    split_browser_title,
    strip_artist_prefix,
)


@pytest.fixture
def store_offsets(tmp_path, monkeypatch):
    """A settings file of this test's own, so a saved nudge goes nowhere real."""
    monkeypatch.setenv("LYRICA_CACHE_DIR", str(tmp_path))


# --- captured payloads ------------------------------------------------------

SPOTIFY = Snapshot(app="Spotify.exe", artist="Porter Robinson",
                   title="Goodbye To A World", album="Worlds", duration=328.5, ok=True)

YT_MUSIC = Snapshot(app="chrome.exe", artist="NewJeans",
                    title="Supernatural", duration=191.0, ok=True)

YOUTUBE = Snapshot(app="chrome.exe", artist="Dua Lipa",
                   title="Dua Lipa - Levitating Featuring DaBaby (Official Music Video)",
                   duration=230.1, ok=True)

SOUNDCLOUD_REUPLOAD = Snapshot(app="chrome.exe", artist="Minh Prime",
                               title="The Weeknd - Blinding Lights full",
                               duration=762.8, ok=True)

SOUNDCLOUD_MIX = Snapshot(app="chrome.exe", artist="nigeldelviero",
                          title="Chill Study Beats - Lofi Hip Hop Mix [2018]",
                          duration=7200.3, ok=True)

# Captured 2026-08-07. The artist field holds only the first name of a two-name
# credit, which is the ordinary shape of a collaboration on YouTube. The
# timeline was not recorded for this one, so it carries no duration.
YOUTUBE_FEATURE = Snapshot(app="chrome.exe", artist="Tiago PZK",
                           title="Tiago PZK, Myke Towers - Traductor (Video Oficial)",
                           ok=True)


# --- clean_title ------------------------------------------------------------

def test_clean_title_strips_parenthesised_noise():
    assert clean_title("Levitating (Official Music Video)") == "Levitating"
    assert clean_title("Song [Official Audio] ") == "Song"


def test_clean_title_strips_reuploader_tail_words():
    assert clean_title("Blinding Lights full") == "Blinding Lights"
    assert clean_title("Some Song HQ") == "Some Song"
    assert clean_title("Otra Canción letra") == "Otra Canción"


def test_clean_title_keeps_tail_words_that_are_the_song():
    # Only a trailing junk word is dropped, so these must survive intact.
    assert clean_title("Full Circle") == "Full Circle"
    assert clean_title("Audio Video Disco") == "Audio Video Disco"


def test_clean_title_keeps_meaningful_parentheses():
    assert clean_title("Say It (feat. Tove Lo)") == "Say It (feat. Tove Lo)"


def test_clean_title_never_empties_a_title():
    # A title made only of junk words keeps its last form rather than vanishing.
    assert clean_title("Full") == "Full"


# --- split_browser_title ----------------------------------------------------

def test_split_browser_title_variants():
    assert split_browser_title("Queen - Bohemian Rhapsody") == ("Queen", "Bohemian Rhapsody")
    assert split_browser_title("Artist – Track") == ("Artist", "Track")
    assert split_browser_title("Just A Title") == ("", "Just A Title")


# --- strip_artist_prefix ----------------------------------------------------

def test_strip_artist_prefix_removes_the_repetition():
    assert strip_artist_prefix("Dua Lipa", "Dua Lipa - Levitating") == "Levitating"
    assert strip_artist_prefix("Queen", "Queen: Bohemian Rhapsody") == "Bohemian Rhapsody"


def test_strip_artist_prefix_leaves_unrelated_titles_alone():
    assert strip_artist_prefix("Minh Prime", "The Weeknd - Blinding Lights") == \
        "The Weeknd - Blinding Lights"
    assert strip_artist_prefix("", "Anything") == "Anything"


def test_strip_artist_prefix_needs_a_separator():
    # The artist's name opening the title is not the same as it prefixing it.
    assert strip_artist_prefix("Madonna", "Madonna Of The Wasps") == "Madonna Of The Wasps"


# --- Snapshot.norm_artist_title --------------------------------------------

def test_clean_sources_pass_through_untouched():
    assert SPOTIFY.norm_artist_title() == ("Porter Robinson", "Goodbye To A World")
    assert YT_MUSIC.norm_artist_title() == ("NewJeans", "Supernatural")


def test_youtube_repeats_the_artist_and_it_is_removed():
    # Chrome populates `artist` *and* repeats it in the title; asking LRCLIB for
    # "Dua Lipa" / "Dua Lipa - Levitating..." missed the exact endpoint.
    assert YOUTUBE.norm_artist_title() == ("Dua Lipa", "Levitating Featuring DaBaby")


def test_is_browser_detection():
    assert YOUTUBE.is_browser
    assert not SPOTIFY.is_browser


# --- Snapshot.lookup_candidates --------------------------------------------

def test_clean_source_yields_one_candidate():
    assert SPOTIFY.lookup_candidates() == [("Porter Robinson", "Goodbye To A World")]


def test_soundcloud_offers_the_title_split_because_the_artist_is_an_uploader():
    # "Minh Prime" is whoever re-uploaded it; the real artist is in the title.
    candidates = SOUNDCLOUD_REUPLOAD.lookup_candidates()
    assert candidates[0] == ("Minh Prime", "The Weeknd - Blinding Lights")
    assert ("The Weeknd", "Blinding Lights") in candidates


def test_youtube_does_not_offer_a_split_that_loses_the_artist():
    # The artist already appears in the title, so splitting would only re-derive
    # what the stripped candidate already says.
    assert ("Dua Lipa", "Levitating Featuring DaBaby") == YOUTUBE.lookup_candidates()[0]
    assert all(a for a, _ in YOUTUBE.lookup_candidates())


def test_candidates_are_deduplicated_and_non_empty():
    for snap in (SPOTIFY, YT_MUSIC, YOUTUBE, SOUNDCLOUD_REUPLOAD, SOUNDCLOUD_MIX):
        candidates = snap.lookup_candidates()
        assert candidates, f"{snap.title!r} produced no candidate"
        assert len(candidates) == len(set(candidates))
        assert all(title for _, title in candidates)


# --- a credit longer than the artist field ----------------------------------

def test_a_second_artist_in_the_title_is_still_a_prefix():
    # "Tiago PZK, Myke Towers - Traductor" under an artist field of "Tiago PZK".
    # The old rule needed the separator to follow the artist immediately, so the
    # whole credit stayed in the title and the card read it as the song's name.
    assert YOUTUBE_FEATURE.norm_artist_title() == ("Tiago PZK", "Traductor")


def test_a_song_that_merely_starts_with_the_artists_word_is_left_alone():
    # "Air" is a real artist and "Airbag" is a real song. Nothing here reads as
    # a credit, so the title keeps every character.
    assert strip_artist_prefix("Air", "Airbag") == "Airbag"
    assert strip_artist_prefix("Love", "Love Story - Taylor Swift") == \
        "Love Story - Taylor Swift"


def test_the_joining_words_of_a_credit_are_recognised():
    for credit in ("Bad Bunny & Jhayco - Dakiti", "Bad Bunny feat. Drake - Mia",
                   "Bad Bunny x Rosalia - La Noche", "Bad Bunny con Ozuna - Pa Ti"):
        assert strip_artist_prefix("Bad Bunny", credit) == credit.split(" - ")[1]


def test_the_split_is_offered_even_when_the_artist_is_in_the_title():
    # It used to be skipped whenever the artist appeared anywhere in the title,
    # which skipped the one reading that names both performers.
    assert ("Tiago PZK, Myke Towers", "Traductor") in \
        YOUTUBE_FEATURE.lookup_candidates()


def test_offering_the_split_adds_no_duplicate_for_a_plain_repeat():
    # Where the artist is repeated with the separator right after it, the split
    # says exactly what the first candidate already does.
    assert YOUTUBE.lookup_candidates().count(("Dua Lipa", "Levitating Featuring DaBaby")) == 1


# --- the reading that resolved ----------------------------------------------

def test_a_result_remembers_which_reading_found_it(tmp_path, monkeypatch):
    # The card is named from this: naming it any other way would contradict the
    # lyrics on screen.
    from lyrica import providers
    from lyrica.lyrics import Lyrics

    monkeypatch.setattr(providers, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(providers, "_ask_providers",
                        lambda a, t, d, al: (
                            (Lyrics(lines=[(0.0, "x")], synced=True), ["stub"])
                            if a == "Billie Eilish" else (None, ["stub"])))
    got = providers.fetch_for_candidates(
        [("BillieEilishVEVO", "Billie Eilish - CHIHIRO"),
         ("Billie Eilish", "CHIHIRO")])
    assert got.queried == ("Billie Eilish", "CHIHIRO")


def test_the_reading_survives_the_cache(tmp_path, monkeypatch):
    from lyrica import providers
    from lyrica.lyrics import Lyrics

    monkeypatch.setattr(providers, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(providers, "_ask_providers",
                        lambda *a: (Lyrics(lines=[(0.0, "x")], synced=True,
                                           source="s"), providers._provider_names()))
    providers.fetch_lyrics("Billie Eilish", "CHIHIRO")
    monkeypatch.setattr(providers, "_ask_providers",
                        lambda *a: pytest.fail("the cache should have answered"))
    again = providers.fetch_lyrics("Billie Eilish", "CHIHIRO")
    assert again.queried == ("Billie Eilish", "CHIHIRO")


class Panel:
    """Only the attributes `_resolved_name` reads."""

    def __init__(self, lyrics=None, identified=None):
        from lyrica.artwork import Release
        self.lyrics = lyrics
        self._identified = identified or Release()


def test_the_card_is_named_after_the_reading_that_resolved():
    from lyrica.app import Overlay
    from lyrica.lyrics import Lyrics

    lyr = Lyrics(lines=[(0.0, "x")], synced=True, queried=("Billie Eilish", "CHIHIRO"))
    assert Overlay._resolved_name(Panel(lyr)) == ("Billie Eilish", "CHIHIRO")


def test_the_card_keeps_the_players_own_words_until_something_resolves():
    # Nothing found yet, or nothing found at all: the reading the player gave is
    # all anything knows, and it is what the compact card shows.
    from lyrica.app import Overlay
    from lyrica.lyrics import Lyrics

    assert Overlay._resolved_name(Panel(None)) == ()
    assert Overlay._resolved_name(Panel(Lyrics())) == ()
    assert Overlay._resolved_name(Panel(Lyrics(queried=("", "CHIHIRO")))) == ()


# --- aligning a video that starts with an intro ------------------------------

class Aligning:
    """The overlay's offset state, without Tk or a session."""

    def __init__(self, lyrics, position, key="chrome.exe|A|B"):
        from types import SimpleNamespace
        self.lyrics = lyrics
        self.offset = 0.0
        self.track_key = key
        self.reader = SimpleNamespace(
            snapshot=SimpleNamespace(ok=True, live_position=lambda: position))

    def _set_offset(self, seconds):
        from lyrica.app import Overlay
        Overlay._set_offset(self, seconds)


def test_aligning_takes_the_whole_intro_in_one_press(store_offsets):
    # The lyrics say the first line is at 2.17 s. The video is 20 s further in,
    # so it carries a 20 s intro and the lyrics must run 20 s later.
    from lyrica.app import Overlay
    from lyrica.lyrics import Lyrics

    panel = Aligning(Lyrics(lines=[(2.17, "To take my love away")], synced=True),
                     position=22.17)
    Overlay._align(panel)
    assert panel.offset == -20.0


def test_aligning_is_remembered_for_that_track(store_offsets):
    from lyrica import config
    from lyrica.app import Overlay
    from lyrica.lyrics import Lyrics

    panel = Aligning(Lyrics(lines=[(2.17, "x")], synced=True), position=22.17)
    Overlay._align(panel)
    assert config.saved_offset(panel.track_key) == -20.0
    assert config.saved_offset("chrome.exe|somebody|else") == 0.0


def test_aligning_does_nothing_without_lyrics(store_offsets):
    from lyrica.app import Overlay
    from lyrica.lyrics import Lyrics

    for lyr in (None, Lyrics()):
        panel = Aligning(lyr, position=22.17)
        Overlay._align(panel)
        assert panel.offset == 0.0


def test_an_intro_longer_than_the_limit_is_clamped(store_offsets):
    from lyrica import config
    from lyrica.app import Overlay
    from lyrica.lyrics import Lyrics

    panel = Aligning(Lyrics(lines=[(2.0, "x")], synced=True), position=500.0)
    Overlay._align(panel)
    assert panel.offset == -config.OFFSET_LIMIT_S


def test_a_catalogue_entry_outranks_the_reading_that_found_the_lyrics():
    # The reading is the wording that happened to work; the entry is a record of
    # what the track is.
    from lyrica.app import Overlay
    from lyrica.artwork import Release
    from lyrica.lyrics import Lyrics

    panel = Panel(Lyrics(queried=("Tiago PZK", "Traductor")),
                  Release(artist="Tiago PZK & Myke Towers", title="Traductor",
                          album="Gotti"))
    assert Overlay._resolved_name(panel) == ("Tiago PZK & Myke Towers", "Traductor")


def test_a_half_empty_catalogue_entry_is_not_used():
    from lyrica.app import Overlay
    from lyrica.artwork import Release
    from lyrica.lyrics import Lyrics

    panel = Panel(Lyrics(queried=("Tiago PZK", "Traductor")),
                  Release(artist="", title="Traductor"))
    assert Overlay._resolved_name(panel) == ("Tiago PZK", "Traductor")
