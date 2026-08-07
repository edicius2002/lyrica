# -*- coding: utf-8 -*-
"""Unit tests for browser-title normalization (offline)."""
from lyrica.smtc import Snapshot, clean_title, split_browser_title


def test_clean_title_strips_video_noise():
    assert clean_title("Levitating (Official Music Video)") == "Levitating"
    assert clean_title("Bohemian Rhapsody (Official Video Remastered)") == "Bohemian Rhapsody"
    assert clean_title("Song [Official Audio] ") == "Song"


def test_clean_title_keeps_meaningful_parentheses():
    assert clean_title("Say It (feat. Tove Lo)") == "Say It (feat. Tove Lo)"


def test_split_browser_title_variants():
    assert split_browser_title("Queen - Bohemian Rhapsody") == ("Queen", "Bohemian Rhapsody")
    assert split_browser_title("Artist – Track") == ("Artist", "Track")
    assert split_browser_title("Just A Title") == ("", "Just A Title")


def test_snapshot_normalizes_browser_metadata():
    snap = Snapshot(app="chrome.exe", artist="", title="Dua Lipa - Levitating (Official Music Video)",
                    ok=True)
    assert snap.is_browser
    assert snap.norm_artist_title() == ("Dua Lipa", "Levitating")


def test_snapshot_keeps_clean_metadata_untouched():
    snap = Snapshot(app="Spotify.exe", artist="Dr. Dre", title="Still D.R.E.", ok=True)
    assert not snap.is_browser
    assert snap.norm_artist_title() == ("Dr. Dre", "Still D.R.E.")
