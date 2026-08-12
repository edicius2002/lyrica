"""The cascade stops only when nothing unasked could do better (offline).

Before providers declared a ceiling, a line-level answer either ended the search
while a word-level source went unasked, or every source had to be queried on
every track to find that out. These tests pin the middle.
"""
import time

import pytest

from lyrica import providers
from lyrica.lyrics import Lyrics, Precision


def synced(source="fake"):
    return Lyrics(lines=[(0.0, "a"), (1.0, "b")], synced=True, source=source)


def worded(source="fake"):
    return Lyrics(lines=[(0.0, "a")], words=[[(0.0, 0.5, "a")]], synced=True, source=source)


def plain(source="fake"):
    return Lyrics(plain="a\nb", source=source)


class Fake:
    def __init__(self, name, result, ceiling=Precision.LINE, delay=0.0):
        self.name = name
        self.result = result
        self.max_precision = ceiling
        self.delay = delay
        self.calls = 0

    def fetch(self, artist, title, duration=0.0, album=""):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return self.result


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "CACHE_DIR", tmp_path)


def use(monkeypatch, *provider_list):
    monkeypatch.setattr(providers, "PROVIDERS", list(provider_list))
    return provider_list


def test_a_line_answer_does_not_end_the_search_while_a_word_source_waits(monkeypatch):
    _, worder = use(monkeypatch,
                    Fake("liner", synced(), Precision.LINE),
                    Fake("worder", worded(), Precision.WORD))
    result = providers.fetch_lyrics("A", "B")
    assert worder.calls == 1, "the word-level source must still be asked"
    assert result.precision is Precision.WORD


def test_a_line_answer_ends_the_wait_when_nothing_better_remains(monkeypatch):
    # Nothing outstanding could improve on it, so there is nothing to wait for.
    use(monkeypatch,
        Fake("liner", synced(), Precision.LINE),
        Fake("slow", synced(), Precision.LINE, delay=2.0))
    started = time.perf_counter()
    result = providers.fetch_lyrics("A", "B")
    assert result.precision is Precision.LINE
    assert time.perf_counter() - started < 1.0


def test_a_word_answer_ends_the_wait_immediately(monkeypatch):
    # Sources are asked together now, so this stops the search waiting rather
    # than stops it asking. Nothing can beat a word-level answer, so the track
    # must not sit behind a slower source that could only have matched it.
    use(monkeypatch,
        Fake("worder", worded(), Precision.WORD),
        Fake("slow", worded(), Precision.WORD, delay=2.0))
    started = time.perf_counter()
    result = providers.fetch_lyrics("A", "B")
    assert result.precision is Precision.WORD
    assert time.perf_counter() - started < 1.0


def test_a_word_source_that_misses_falls_back_to_the_line_source(monkeypatch):
    worder, liner = use(monkeypatch,
                        Fake("worder", None, Precision.WORD),
                        Fake("liner", synced(), Precision.LINE))
    result = providers.fetch_lyrics("A", "B")
    assert worder.calls == 1 and liner.calls == 1
    assert result.precision is Precision.LINE


def test_a_word_source_returning_only_lines_still_yields_lines(monkeypatch):
    # The source can reach word level in principle and simply did not here.
    use(monkeypatch, Fake("worder", synced(), Precision.WORD))
    assert providers.fetch_lyrics("A", "B").precision is Precision.LINE


def test_plain_never_ends_the_search(monkeypatch):
    _, liner = use(monkeypatch,
                   Fake("plainy", plain(), Precision.PLAIN),
                   Fake("liner", synced(), Precision.LINE))
    result = providers.fetch_lyrics("A", "B")
    assert liner.calls == 1
    assert result.precision is Precision.LINE


# --- cache supersession under ceilings --------------------------------------

def test_adding_a_word_source_supersedes_a_cached_line_hit(monkeypatch):
    (liner,) = use(monkeypatch, Fake("liner", synced(), Precision.LINE))
    assert providers.fetch_lyrics("A", "B").precision is Precision.LINE

    use(monkeypatch, liner, Fake("worder", worded(), Precision.WORD))
    assert providers.fetch_lyrics("A", "B").precision is Precision.WORD


def test_a_cached_word_hit_is_never_re_queried(monkeypatch):
    (worder,) = use(monkeypatch, Fake("worder", worded(), Precision.WORD))
    providers.fetch_lyrics("A", "B")

    liner = Fake("liner", synced(), Precision.LINE)
    use(monkeypatch, worder, liner)
    assert providers.fetch_lyrics("A", "B").precision is Precision.WORD
    assert worder.calls == 1 and liner.calls == 0


def test_adding_a_line_source_costs_nothing_against_a_cached_line_hit(monkeypatch):
    # The new source could only match what is already held, so asking it is
    # pure cost. Only a source that could do *better* is worth a request.
    (liner,) = use(monkeypatch, Fake("liner", synced(), Precision.LINE))
    providers.fetch_lyrics("A", "B")

    other = Fake("other", synced(), Precision.LINE)
    use(monkeypatch, liner, other)
    providers.fetch_lyrics("A", "B")
    assert other.calls == 0


def test_candidates_keep_looking_for_word_level(monkeypatch):
    answers = {("A", "one"): synced(), ("A", "two"): worded()}
    use(monkeypatch, Fake("x", None, Precision.WORD))
    monkeypatch.setattr(providers, "fetch_lyrics",
                        lambda a, t, d=0.0, al="": answers.get((a, t)))
    result = providers.fetch_for_candidates([("A", "one"), ("A", "two")])
    assert result.precision is Precision.WORD


# --- what precision does not say --------------------------------------------

def test_a_word_answer_without_backing_waits_for_one_that_has_it():
    # Measured against a real cache: Levitating was held as
    # `musixmatch/richsync` — the same precision as the source that has its
    # twelve backing vocals, and none of them. The first answer to arrive had
    # won a race it should not have been allowed to end.
    from lyrica.lyrics import Lyrics, Precision
    from lyrica.providers import _may_add_backing

    plain = Lyrics(lines=[(0.0, "a")], words=[[(0.0, 1.0, "a")]], synced=True)
    rich = Lyrics(lines=[(0.0, "a")], words=[[(0.0, 1.0, "a")]], synced=True,
                  backing=["(oh)"], backing_words=[[(0.5, 0.9, "(oh)")]])

    class Bare:
        max_precision = Precision.WORD
        carries_backing = False

    class Knows:
        max_precision = Precision.WORD
        carries_backing = True

    assert _may_add_backing(plain, [Knows()]) is True
    assert _may_add_backing(plain, [Bare()]) is False, "nothing to wait for"
    assert _may_add_backing(rich, [Knows()]) is False, "already has it"
    assert _may_add_backing(None, [Knows()]) is False


def test_backing_breaks_a_tie_between_equal_precisions():
    from lyrica.lyrics import Lyrics
    from lyrica.providers import _better

    plain = Lyrics(lines=[(0.0, "a")], words=[[(0.0, 1.0, "a")]], synced=True)
    rich = Lyrics(lines=[(0.0, "a")], words=[[(0.0, 1.0, "a")]], synced=True,
                  backing=["(oh)"], backing_words=[[(0.5, 0.9, "(oh)")]])
    assert _better(rich, plain) is True
    assert _better(plain, rich) is False, "and it does not swing back"


# --- CommunityTTML backing graft -------------------------------------------

def _hybrid_source(source, starts, backing=None):
    lines = [(start, text) for start, text in zip(starts, ("one", "two", "three", "four"),
                                                   strict=True)]
    words = [[(start, start + 0.4, text)] for start, text in lines]
    backing = backing or ["", "", "", ""]
    backing_words = [[] for _ in lines]
    for i, text in enumerate(backing):
        if text:
            backing_words[i] = [(starts[i] + 0.1, starts[i] + 0.3, text)]
    return Lyrics(lines=lines, words=words, synced=True, source=source,
                  backing=backing, backing_words=backing_words,
                  backing_timing=["inferred" if text else "" for text in backing])


def test_verified_ttml_adlibs_are_grafted_onto_richsync_lead():
    from lyrica.providers import _merge_community_backing

    rich = _hybrid_source("musixmatch/richsync", [10.2, 20.2, 30.2, 40.2])
    community = _hybrid_source("community-ttml/word", [10, 20, 30, 40],
                               ["", "(echo)", "", ""])
    community.recording_duration = 180.0

    hybrid = _merge_community_backing(rich, community, 180.0)

    assert hybrid is not None
    assert hybrid.lines == rich.lines, "Richsync remains the lead lyric and clock"
    assert hybrid.source.endswith("community-ttml-adlibs")
    assert hybrid.backing_at(1) == ("(echo)", [(20.3, 20.5, "(echo)")])
    assert hybrid.backing_timing_at(1) == "exact"


def test_ttml_adlibs_are_never_borrowed_from_a_different_duration_release():
    from lyrica.providers import _merge_community_backing

    rich = _hybrid_source("musixmatch/richsync", [10, 20, 30, 40])
    community = _hybrid_source("community-ttml/word", [10, 20, 30, 40],
                               ["", "(echo)", "", ""])
    community.recording_duration = 191.0

    assert _merge_community_backing(rich, community, 180.0) is None


def test_ttml_adlibs_are_rejected_when_their_clock_drifts_between_anchors():
    from lyrica.providers import _merge_community_backing

    rich = _hybrid_source("musixmatch/richsync", [10, 22, 35, 49])
    community = _hybrid_source("community-ttml/word", [10, 20, 30, 40],
                               ["", "(echo)", "", ""])
    community.recording_duration = 180.0

    assert _merge_community_backing(rich, community, 180.0) is None


def test_a_failed_hybrid_falls_back_to_richsync_not_the_ttml_race_winner(monkeypatch):
    from lyrica import providers

    rich = _hybrid_source("musixmatch/richsync", [10, 20, 30, 40])
    community = _hybrid_source("community-ttml/word", [10, 20, 30, 40],
                               ["", "(echo)", "", ""])
    community.recording_duration = 191.0
    use(monkeypatch,
        Fake("community-ttml", community, Precision.WORD),
        Fake("musixmatch", rich, Precision.WORD, delay=0.01))

    result, _asked = providers._ask_providers("A", "B", 180.0, "")

    assert result is rich
