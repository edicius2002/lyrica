"""Who sings a line, and how the panel shows it without moving the column."""
import pytest

from lyrica.lyrics import Lyrics
from lyrica.ttml import parse_ttml

DUET = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    itunes:timing="Word">
  <head><metadata>
    <ttm:agent type="person" xml:id="v1"><ttm:name type="full">A</ttm:name></ttm:agent>
    <ttm:agent type="person" xml:id="v2"><ttm:name type="full">B</ttm:name></ttm:agent>
    <ttm:agent type="group" xml:id="v3"/>
  </metadata></head>
  <body>
    <div>
      <p begin="1.0" end="2.0" ttm:agent="v1">
        <span begin="1.0" end="1.5">Tell</span> <span begin="1.5" end="2.0">me</span>
      </p>
      <p begin="2.0" end="3.0" ttm:agent="v2">
        <span begin="2.0" end="2.5">I</span> <span begin="2.5" end="3.0">am</span>
      </p>
      <p begin="3.0" end="4.0" ttm:agent="v3">
        <span begin="3.0" end="3.5">Far</span> <span begin="3.5" end="4.0">from</span>
      </p>
    </div>
  </body>
</tt>"""


def test_the_document_says_who_sings_each_line():
    lyr = parse_ttml(DUET)
    assert lyr.voices == ["v1", "v2", "v3"]
    assert lyr.singers == {"v1": "person", "v2": "person", "v3": "group"}


def test_a_document_without_agents_carries_none():
    # Not a list of empty strings: "nobody said" and "everybody is v1" are
    # different answers and only one of them is worth keeping.
    lyr = parse_ttml(DUET.replace(' ttm:agent="v1"', "")
                     .replace(' ttm:agent="v2"', "")
                     .replace(' ttm:agent="v3"', ""))
    assert lyr.voices == []
    assert lyr.singers == {}


def test_the_two_sides_go_to_whoever_opens():
    sides = parse_ttml(DUET).voice_sides()
    assert sides["v1"] == -1
    assert sides["v2"] == 1


def test_what_they_sing_together_keeps_the_middle():
    assert parse_ttml(DUET).voice_sides()["v3"] == 0


def test_a_third_singer_keeps_it_too():
    # A duet has two sides. A third position would be claiming a third half.
    lyr = Lyrics(lines=[(0.0, "a"), (1.0, "b"), (2.0, "c")],
                 voices=["v1", "v2", "v4"],
                 singers={"v1": "person", "v2": "person", "v4": "person"})
    assert lyr.voice_sides()["v4"] == 0


def test_one_singer_moves_nothing():
    lyr = Lyrics(lines=[(0.0, "a"), (1.0, "b")], voices=["v1", "v1"],
                 singers={"v1": "person"})
    assert lyr.voice_sides() == {}


def test_a_soloist_and_a_choir_move_nothing():
    # There is only one voice to place; the choir is already where a choir goes.
    lyr = Lyrics(lines=[(0.0, "a"), (1.0, "b")], voices=["v1", "v2"],
                 singers={"v1": "person", "v2": "group"})
    assert lyr.voice_sides() == {}


def test_an_undeclared_voice_still_takes_a_side():
    # Documents do use an agent they never introduce. Refusing to place it
    # would lose the distinction over a missing line of head metadata.
    lyr = Lyrics(lines=[(0.0, "a"), (1.0, "b")], voices=["v1", "v2"])
    assert lyr.voice_sides() == {"v1": -1, "v2": 1}


# --- on screen ---
@pytest.fixture
def panel(overlay):
    from lyrica import app as A
    overlay.lyrics = Lyrics(
        lines=[(0.0, "Tell me"), (2.0, "I am"), (4.0, "Far from")],
        words=[[(0.0, 1.0, "Tell"), (1.0, 2.0, "me")],
               [(2.0, 3.0, "I"), (3.0, 4.0, "am")],
               [(4.0, 5.0, "Far"), (5.0, 6.0, "from")]],
        synced=True, voices=["v1", "v2", "v3"],
        singers={"v1": "person", "v2": "person", "v3": "group"})
    overlay._lyrics_state = A.LYRICS_PRESENT
    overlay._go_to_line(1, overlay.lyrics)
    overlay.root.update()
    return overlay


def _centre(view):
    lo, hi = view._row_spans[0]
    return (lo + hi) / 2


def test_the_two_voices_land_on_opposite_sides(panel):
    middle = panel.width / 2
    assert _centre(panel._views[0]) < middle - 1
    assert _centre(panel._views[1]) > middle + 1


def test_the_default_reads_as_two_lanes_not_a_crooked_column(panel):
    separation = _centre(panel._views[1]) - _centre(panel._views[0])
    assert separation >= panel.width * 0.30


def test_together_stays_on_the_column(panel):
    assert _centre(panel._views[2]) == pytest.approx(panel.width / 2, abs=1.5)


def test_the_step_is_bounded_rather_than_an_alignment(panel):
    step = panel.chrome.px(panel._voice_step)
    for index, way in ((0, -1), (1, 1)):
        moved = _centre(panel._views[index]) - panel.width / 2
        assert moved * way == pytest.approx(step, abs=1.5), (
            "a short line takes the whole step and no more")


def test_a_line_with_no_room_stays_inside_the_box(panel):
    # The clipping half of "a bounded step". In the expanded panel there is
    # always room — the wrap width is a hundred pixels short of the window on
    # each side — so it is the collapse, where the box closes in around lines
    # that are already laid out, that this is for.
    view = panel._views[1]
    lo, hi = view._row_spans[0]
    view.fit(lo - view._shift, hi - view._shift + 4)
    assert view._row_spans[0][1] <= hi - view._shift + 4 + 1
    assert view._shift == 4, "it takes what room there is rather than none"


def test_a_line_is_never_pushed_the_wrong_way(panel):
    # A box already narrower than the line asks for a negative step. Obeying it
    # would move the voice to the side that is not hers.
    view = panel._views[1]
    lo, hi = view._row_spans[0]
    view.fit(lo - view._shift, hi - view._shift - 20)
    assert view._shift == 0


def test_stepping_aside_is_idempotent(panel):
    # Called again whenever the box changes, so a line that crept a step each
    # time would walk off the panel over a single collapse.
    view = panel._views[0]
    before = _centre(view)
    for _ in range(5):
        panel._fit_view(view)
    assert _centre(view) == before


def test_the_sweep_follows_the_line_it_moved(panel):
    # The character centres are what the front is measured against, so a line
    # whose text moved without them would light up from where it no longer is.
    view = panel._views[1]
    for entry in view._items:
        x, _y = panel.canvas.coords(entry[2])
        assert abs(entry[0] - x) < view.line_height, (
            "a character's recorded centre has come adrift of the glyph")


def test_recentring_keeps_the_step(panel):
    # What a collapse does: the panel narrows, every line is re-centred, and
    # the voices have to still be apart when it lands.
    for view in panel._views.values():
        view.recentre(panel.width // 2 - 40)
        panel._fit_view(view)
    assert _centre(panel._views[0]) < _centre(panel._views[1])
