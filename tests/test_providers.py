"""Provider cascade: ranking, early exit and cache supersession (offline).

Fake providers stand in for real sources so the ordering rules can be tested
without a network. Placeholder text only — nothing here is a real lyric.
"""
import time

import pytest

from lyrica import providers
from lyrica.lyrics import Lyrics, Precision


def synced(source: str = "fake") -> Lyrics:
    return Lyrics(lines=[(0.0, "first"), (1.0, "second")], synced=True, source=source)


def plain(source: str = "fake") -> Lyrics:
    return Lyrics(plain="first\nsecond", synced=False, source=source)


def instrumental(source: str = "fake", *, exact: bool = True) -> Lyrics:
    return Lyrics(instrumental=True, source=source, exact=exact)


class Fake:
    """A provider that answers with whatever it was handed, and counts calls."""

    def __init__(self, name: str, result: Lyrics | None,
                 ceiling: Precision = Precision.LINE, delay: float = 0.0):
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


class Exploding:
    name = "exploding"
    max_precision = Precision.LINE

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


def test_only_synced_and_exact_instrumentals_are_definitive():
    assert synced().is_definitive
    assert instrumental().is_definitive
    assert not plain().is_definitive
    assert not Lyrics().is_definitive


def test_a_fuzzy_instrumental_is_not_definitive():
    # A loose search reaches for the nearest thing it can find, and karaoke
    # uploads sit next to the songs they came from. Ending the search on one
    # would report a song as instrumental while another source had its lyrics.
    assert not instrumental(exact=False).is_definitive


# --- ranking ----------------------------------------------------------------

def test_a_later_synced_answer_beats_an_earlier_plain_one(monkeypatch):
    first, second = use(monkeypatch, Fake("plainy", plain()), Fake("syncy", synced()))
    result = providers.fetch_lyrics("A", "B")
    assert result.precision is Precision.LINE
    assert result.source == "fake"
    assert first.calls == 1 and second.calls == 1


def test_a_definitive_answer_ends_the_wait(monkeypatch):
    # Sources are asked together, so the early exit stops the search *waiting*
    # rather than stops it asking — the requests are already out. What it
    # guarantees is that a track with a word-level answer does not sit behind
    # a slower source that could not have improved on it.
    first, _slow = use(monkeypatch, Fake("syncy", synced()),
                       Fake("slow", synced(), delay=2.0))
    started = time.perf_counter()
    result = providers.fetch_lyrics("A", "B")
    elapsed = time.perf_counter() - started
    assert result.precision is Precision.LINE
    assert first.calls == 1
    assert elapsed < 1.0, f"waited {elapsed:.1f}s for a source it did not need"


def test_an_exact_instrumental_ends_the_wait(monkeypatch):
    # The track having no lyrics is a complete answer; waiting on would only
    # invite a weaker source to supply some.
    use(monkeypatch, Fake("inst", instrumental()),
        Fake("slow", synced(), delay=2.0))
    started = time.perf_counter()
    result = providers.fetch_lyrics("A", "B")
    assert result.instrumental
    assert time.perf_counter() - started < 1.0


def test_sources_are_asked_at_the_same_time(monkeypatch):
    # The whole point: two slow sources cost one wait, not two. Asked in turn
    # these measured 1508 ms and then 3208 ms more for one real track.
    use(monkeypatch, Fake("slow-a", None, delay=0.6),
        Fake("slow-b", synced(), delay=0.6))
    started = time.perf_counter()
    providers.fetch_lyrics("A", "B")
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{elapsed:.2f}s looks sequential"


def test_a_fuzzy_instrumental_does_not_stop_the_search(monkeypatch):
    # Found live: LRCLIB's fuzzy search returned an instrumental record for a
    # song that plainly has lyrics, which silently ended the cascade.
    _, second = use(monkeypatch,
                    Fake("guessy", instrumental(exact=False)),
                    Fake("better", synced()))
    result = providers.fetch_lyrics("A", "B")
    assert second.calls == 1, "a guessed instrumental must not end the search"
    assert result.precision is Precision.LINE


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
    # Word level, because the real provider list contains a word-capable source
    # and a line-level answer therefore leaves something worth looking for.
    word_level = Lyrics(lines=[(0.0, "a")], words=[[(0.0, 0.5, "a")]], synced=True)

    def fake_fetch(artist, title, duration=0.0, album=""):
        seen.append(title)
        return word_level if title == "strong" else None

    monkeypatch.setattr(providers, "fetch_lyrics", fake_fetch)
    providers.fetch_for_candidates([("A", "strong"), ("A", "never")])
    assert seen == ["strong"]


def test_no_candidates_returns_none(monkeypatch):
    use(monkeypatch, Fake("x", synced()))
    assert providers.fetch_for_candidates([]) is None
