"""The cascade stops only when nothing unasked could do better (offline).

Before providers declared a ceiling, a line-level answer either ended the search
while a word-level source went unasked, or every source had to be queried on
every track to find that out. These tests pin the middle.
"""
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
    def __init__(self, name, result, ceiling=Precision.LINE):
        self.name = name
        self.result = result
        self.max_precision = ceiling
        self.calls = 0

    def fetch(self, artist, title, duration=0.0, album=""):
        self.calls += 1
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


def test_a_line_answer_ends_the_search_when_nothing_better_remains(monkeypatch):
    first, second = use(monkeypatch,
                        Fake("liner", synced(), Precision.LINE),
                        Fake("other", synced(), Precision.LINE))
    providers.fetch_lyrics("A", "B")
    assert first.calls == 1 and second.calls == 0


def test_a_word_answer_ends_the_search_immediately(monkeypatch):
    _, second = use(monkeypatch,
                    Fake("worder", worded(), Precision.WORD),
                    Fake("never", worded(), Precision.WORD))
    providers.fetch_lyrics("A", "B")
    assert second.calls == 0


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
