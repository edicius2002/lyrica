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
    from lyrica.lyrics import BACKING_CROSS_SOURCE_ALIGNED
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
    assert hybrid.backing_timing_at(1) == BACKING_CROSS_SOURCE_ALIGNED
    assert hybrid.backing_alignment_at(1) == {
        "source": "community-ttml/word",
        "offset": 0.2,
        "rate": 1.0,
        "local_residual": 0.0,
        "anchors": 3,
    }


def test_repeated_choruses_are_aligned_by_the_whole_sequence_and_time():
    from lyrica.providers import _line_pairs

    rich = Lyrics(lines=[(0.0, "intro"), (10.0, "chorus"),
                         (20.0, "chorus"), (30.0, "outro")])
    community = Lyrics(lines=[(0.0, "intro"), (19.8, "chorus"),
                              (30.0, "outro")])

    assert _line_pairs(rich, community) == [(0, 0), (2, 1), (3, 2)]


def test_n95_split_phrases_align_many_ttml_lines_to_one_richsync_line():
    """N95 groups long Richsync sentences into several TTML screen rows."""
    from lyrica.providers import _line_groups

    rich = Lyrics(lines=[
        (0.0, "Intro anchor"),
        (10.0, "Take off your idols take off the runway take off to Cairo"),
        (40.0, "Middle anchor"),
        (80.0, "Closing anchor"),
    ])
    community = Lyrics(lines=[
        (0.1, "Intro anchor"),
        (10.1, "Take off your idols"),
        (11.1, "Take off the runway"),
        (12.1, "Take off to Cairo"),
        (40.1, "Middle anchor"),
        (80.1, "Closing anchor"),
    ])

    assert ((1,), (1, 2, 3)) in _line_groups(rich, community)


def _n95_split_sources(rich_start=12.6):
    rich_lines = [
        (0.0, "Intro anchor"),
        (10.0, "Take off your idols take off the runway take off to Cairo"),
        (40.0, "Middle anchor"),
        (80.0, "Closing anchor"),
    ]
    rich = Lyrics(
        lines=rich_lines,
        words=[[(start, start + 0.5, text)] for start, text in rich_lines],
        synced=True, source="musixmatch/richsync",
        backing=["", "(Take that off)", "", ""],
        backing_words=[[], [(rich_start, rich_start + 0.45, "(Take that off)")],
                       [], []],
        backing_timing=["", "inferred", "", ""],
        backing_modes=["", "sequential", "", ""],
    )
    community_lines = [
        (0.1, "Intro anchor"),
        (10.1, "Take off your idols"),
        (11.1, "Take off the runway"),
        (12.1, "Take off to Cairo"),
        (40.1, "Middle anchor"),
        (80.1, "Closing anchor"),
    ]
    community = Lyrics(
        lines=community_lines,
        words=[[(start, start + 0.5, text)] for start, text in community_lines],
        synced=True, source="community-ttml/word",
        backing=["", "", "", "(Take that off)", "", ""],
        backing_words=[[], [], [], [(12.7, 13.9, "(Take that off)")], [], []],
    )
    community.recording_duration = 195.0
    return rich, community


def test_n95_verified_response_keeps_its_entry_and_gains_the_measured_end():
    from lyrica.lyrics import BACKING_CROSS_SOURCE_ALIGNED
    from lyrica.providers import _merge_community_backing

    rich, community = _n95_split_sources()

    hybrid = _merge_community_backing(rich, community, 195.0)

    assert hybrid is not None
    words = hybrid.backing_at(1)[1]
    assert words[0][0] == pytest.approx(12.6), "the Richsync entry must not jump"
    assert words[-1][1] == pytest.approx(13.8), "TTML supplies the complete tail"
    assert hybrid.backing_timing_at(1) == BACKING_CROSS_SOURCE_ALIGNED
    assert hybrid.backing_alignment_at(1)["local_residual"] <= 0.2


def test_n95_response_over_200ms_from_its_richsync_entry_is_refused():
    from lyrica.providers import _merge_community_backing

    rich, community = _n95_split_sources(rich_start=12.91)

    assert _merge_community_backing(rich, community, 195.0) is None


def test_n95_repeated_responses_keep_entries_but_no_longer_end_prematurely():
    """Two real N95 timings that survived the strict cross-source checks."""
    from lyrica.providers import _merge_community_backing

    rich_starts = [134.428, 136.84, 138.68, 140.37, 141.96,
                   145.03, 147.28, 148.47, 152.13, 155.35, 159.04]
    community_starts = [134.57, 137.01, 138.74, 140.45, 142.21,
                        145.19, 147.32, 148.53, 152.01, 155.55, 158.92]
    texts = ["reaction", "think", "aesthetic", "soul", "leverage",
             "relevant", "hypocrites again", "relevant again", "pocket",
             "mediocre", "toxic"]

    def source(name, starts):
        lines = list(zip(starts, texts, strict=True))
        return Lyrics(
            lines=lines,
            words=[[(start, start + 0.5, text)] for start, text in lines],
            synced=True, source=name,
            backing=["" for _ in lines], backing_words=[[] for _ in lines],
            backing_timing=["" for _ in lines],
            backing_modes=["" for _ in lines],
        )

    rich = source("musixmatch/richsync", rich_starts)
    rich.backing[2] = rich.backing[7] = "(Let's go)"
    rich.backing_words[2] = [(139.89, 140.298, "(Let's go)")]
    rich.backing_words[7] = [(150.18, 150.684, "(Let's go)")]
    rich.backing_timing[2] = rich.backing_timing[7] = "inferred"
    rich.backing_modes[2] = rich.backing_modes[7] = "sequential"

    community = source("community-ttml/word", community_starts)
    community.backing[2] = community.backing[7] = "(Let's go)"
    community.backing_words[2] = [(140.00, 140.85, "(Let's go)")]
    community.backing_words[7] = [(150.36, 151.16, "(Let's go)")]
    community.recording_duration = 195.0

    hybrid = _merge_community_backing(rich, community, 195.0)

    assert hybrid is not None
    first, repeated = hybrid.backing_at(2)[1], hybrid.backing_at(7)[1]
    assert first[0][0] == 139.89 and repeated[0][0] == 150.18
    assert first[-1][1] > 140.7 and repeated[-1][1] > 150.9


def test_an_adlib_failing_its_nearest_anchors_is_not_grafted():
    from lyrica.providers import _merge_community_backing

    texts = ("one", "two", "three", "four", "five")
    community_starts = [0.0, 40.0, 80.0, 120.0, 160.0]
    rich_starts = [0.0, 40.0, 80.3, 120.0, 160.0]

    def source(name, starts, backing):
        lines = list(zip(starts, texts, strict=True))
        words = [[(start, start + 0.4, text)] for start, text in lines]
        backing_words = [[] for _ in lines]
        backing_words[2] = [(80.1, 80.5, "(echo)")]
        return Lyrics(lines=lines, words=words, synced=True, source=name,
                      backing=backing, backing_words=backing_words)

    rich = source("musixmatch/richsync", rich_starts, ["", "", "", "", ""])
    community = source(
        "community-ttml/word", community_starts,
        ["", "", "(echo)", "", ""])
    community.recording_duration = 180.0

    assert _merge_community_backing(rich, community, 180.0) is None


def test_a_grafted_suffix_recovers_its_sequential_mode():
    from lyrica.providers import _merge_community_backing

    rich = _hybrid_source("musixmatch/richsync", [10.2, 20.2, 30.2, 40.2])
    community = _hybrid_source("community-ttml/word", [10, 20, 30, 40],
                               ["", "(echo)", "", ""])
    community.backing_words[1] = [(20.7, 21.0, "(echo)")]
    community.recording_duration = 180.0

    hybrid = _merge_community_backing(rich, community, 180.0)

    assert hybrid is not None
    assert hybrid.backing_mode_at(1) == "sequential"


def test_a_tiny_cross_source_rate_difference_is_applied_to_the_adlib():
    from lyrica.providers import _merge_community_backing

    community_starts = [10.0, 50.0, 90.0, 130.0]
    rich_starts = [0.2 + 1.001 * start for start in community_starts]
    rich = _hybrid_source("musixmatch/richsync", rich_starts)
    community = _hybrid_source("community-ttml/word", community_starts,
                               ["", "", "(echo)", ""])
    community.recording_duration = 180.0

    hybrid = _merge_community_backing(rich, community, 180.0)

    assert hybrid is not None
    start, end, _text = hybrid.backing_at(2)[1][0]
    assert start == pytest.approx(0.2 + 1.001 * 90.1)
    assert end == pytest.approx(0.2 + 1.001 * 90.3)
    assert hybrid.backing_alignment_at(2)["rate"] == pytest.approx(1.001)


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
