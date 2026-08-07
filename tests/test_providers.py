"""Provider cascade: ranking, early exit and cache supersession (offline).

Fake providers stand in for real sources so the ordering rules can be tested
without a network. Placeholder text only — nothing here is a real lyric.
"""
import pytest

from lyrica import providers
from lyrica.lyrics import Lyrics, Precision


def synced(source: str = "fake") -> Lyrics:
    return Lyrics(lines=[(0.0, "first"), (1.0, "second")], synced=True, source=source)


def plain(source: str = "fake") -> Lyrics:
    return Lyrics(plain="first\nsecond", synced=False, source=source)


def instrumental(source: str = "fake") -> Lyrics:
    return Lyrics(instrumental=True, source=source)


class Fake:
    """A provider that answers with whatever it was handed, and counts calls."""

    def __init__(self, name: str, result: Lyrics | None):
        self.name = name
        self.result = result
        self.calls = 0

    def fetch(self, artist, title, duration=0.0, album=""):
        self.calls += 1
        return self.result


class Exploding:
    name = "exploding"

    def __init__(self):
        self.calls = 0

    def fetch(self, artist, title, duration=0.0, album=""):
        self.calls += 1
        raise RuntimeError("provider is broken")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "CACHE_DIR", tmp_path)


def use(monkeypatch, *provider_list):
    monkeypatch.setattr(providers, "PROVIDERS", list(provider_list))
    return provider_list


# --- precision model --------------------------------------------------------

def test_precision_is_ordered():
    assert Precision.NONE < Precision.PLAIN < Precision.LINE < Precision.WORD


def test_precision_reflects_content():
    assert synced().precision is Precision.LINE
    assert plain().precision is Precision.PLAIN
    assert Lyrics().precision is Precision.NONE


def test_synced_flag_without_lines_is_not_line_precision():
    # A source can claim sync and return nothing parseable.
    assert Lyrics(synced=True, lines=[]).precision is Precision.NONE


def test_only_synced_and_instrumental_are_definitive():
    assert synced().is_definitive
    assert instrumental().is_definitive
    assert not plain().is_definitive
    assert not Lyrics().is_definitive


# --- ranking ----------------------------------------------------------------

def test_a_later_synced_answer_beats_an_earlier_plain_one(monkeypatch):
    first, second = use(monkeypatch, Fake("plainy", plain()), Fake("syncy", synced()))
    result = providers.fetch_lyrics("A", "B")
    assert result.precision is Precision.LINE
    assert result.source == "fake"
    assert first.calls == 1 and second.calls == 1


def test_a_definitive_answer_stops_the_search(monkeypatch):
    first, second = use(monkeypatch, Fake("syncy", synced()), Fake("never", synced()))
    providers.fetch_lyrics("A", "B")
    assert first.calls == 1
    assert second.calls == 0, "no request should be spent once the answer is definitive"


def test_an_instrumental_stops_the_search(monkeypatch):
    # The track having no lyrics is a complete answer; asking on would only
    # invite a weaker source to supply some.
    _, second = use(monkeypatch, Fake("inst", instrumental()), Fake("never", synced()))
    result = providers.fetch_lyrics("A", "B")
    assert result.instrumental
    assert second.calls == 0


def test_plain_is_kept_when_nothing_better_exists(monkeypatch):
    use(monkeypatch, Fake("plainy", plain()), Fake("empty", None))
    result = providers.fetch_lyrics("A", "B")
    assert result.precision is Precision.PLAIN


def test_all_misses_return_none(monkeypatch):
    use(monkeypatch, Fake("a", None), Fake("b", None))
    assert providers.fetch_lyrics("A", "B") is None


def test_a_broken_provider_does_not_deny_the_track(monkeypatch):
    boom = Exploding()
    use(monkeypatch, boom, Fake("good", synced()))
    result = providers.fetch_lyrics("A", "B")
    assert boom.calls == 1
    assert result.precision is Precision.LINE


# --- caching ----------------------------------------------------------------

def test_a_definitive_result_is_served_from_cache(monkeypatch):
    (only,) = use(monkeypatch, Fake("syncy", synced()))
    providers.fetch_lyrics("A", "B")
    providers.fetch_lyrics("A", "B")
    assert only.calls == 1


def test_a_miss_is_cached_too(monkeypatch):
    (only,) = use(monkeypatch, Fake("none", None))
    assert providers.fetch_lyrics("A", "B") is None
    assert providers.fetch_lyrics("A", "B") is None
    assert only.calls == 1, "a known-empty answer must not be re-fetched"


def test_a_new_provider_supersedes_a_cached_plain_hit(monkeypatch):
    (weak,) = use(monkeypatch, Fake("plainy", plain()))
    assert providers.fetch_lyrics("A", "B").precision is Precision.PLAIN

    # A better source appears; the weak cached answer must not shadow it.
    use(monkeypatch, weak, Fake("syncy", synced()))
    assert providers.fetch_lyrics("A", "B").precision is Precision.LINE


def test_a_new_provider_supersedes_a_cached_miss(monkeypatch):
    (empty,) = use(monkeypatch, Fake("none", None))
    assert providers.fetch_lyrics("A", "B") is None

    use(monkeypatch, empty, Fake("syncy", synced()))
    assert providers.fetch_lyrics("A", "B") is not None


def test_an_unchanged_provider_set_does_not_re_query_a_plain_hit(monkeypatch):
    (weak,) = use(monkeypatch, Fake("plainy", plain()))
    providers.fetch_lyrics("A", "B")
    providers.fetch_lyrics("A", "B")
    assert weak.calls == 1, "nothing new could beat it, so nothing should be spent"


def test_a_corrupt_cache_entry_is_replaced(monkeypatch):
    (only,) = use(monkeypatch, Fake("syncy", synced()))
    providers.fetch_lyrics("A", "B")
    path = providers._cache_path("A", "B", 0.0)
    path.write_text("{not json", encoding="utf-8")
    assert providers.fetch_lyrics("A", "B").precision is Precision.LINE
    assert only.calls == 2


# --- candidates -------------------------------------------------------------

def test_candidates_prefer_the_best_reading_not_the_first(monkeypatch):
    # The first reading of the metadata finds only plain text; a later reading
    # finds synced lyrics, and that is the one worth showing.
    answers = {("A", "weak"): plain(), ("A", "strong"): synced()}
    use(monkeypatch, Fake("x", None))
    monkeypatch.setattr(providers, "fetch_lyrics",
                        lambda a, t, d=0.0, al="": answers.get((a, t)))
    result = providers.fetch_for_candidates([("A", "weak"), ("A", "strong")])
    assert result.precision is Precision.LINE


def test_candidates_stop_at_a_definitive_answer(monkeypatch):
    seen = []

    def fake_fetch(artist, title, duration=0.0, album=""):
        seen.append(title)
        return synced() if title == "strong" else None

    monkeypatch.setattr(providers, "fetch_lyrics", fake_fetch)
    providers.fetch_for_candidates([("A", "strong"), ("A", "never")])
    assert seen == ["strong"]


def test_no_candidates_returns_none(monkeypatch):
    use(monkeypatch, Fake("x", synced()))
    assert providers.fetch_for_candidates([]) is None
