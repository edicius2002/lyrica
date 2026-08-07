"""Metadata normalization, tested against payloads captured from live sessions.

Every `Snapshot` below was recorded by `research/viability/probe_browser_session.py`
on 2026-08-06 and is reproduced verbatim. The previous version of this file
asserted that Chrome leaves `artist` empty, which Chrome never does — the tests
passed while describing a payload that does not occur. Cases here are only added
from something a session actually published.
"""
from lyrica.sessions.base import (
    Snapshot,
    clean_title,
    split_browser_title,
    strip_artist_prefix,
)

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
