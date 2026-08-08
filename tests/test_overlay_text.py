"""Outline geometry, tested without a display.

`draw_outlined` needs a live tkinter canvas, so only the offset geometry is
covered here. That is the part with a decision in it; the drawing itself is a
loop over these offsets.
"""
from lyrica.overlay_text import ring_offsets


def test_no_offsets_when_the_outline_is_disabled():
    assert ring_offsets(0) == []
    assert ring_offsets(-1) == []


def test_width_one_is_the_eight_neighbours():
    offsets = ring_offsets(1)
    assert len(offsets) == 8
    assert set(offsets) == {(-1, 0), (1, 0), (0, -1), (0, 1),
                            (-1, -1), (-1, 1), (1, -1), (1, 1)}


def test_wider_outlines_fill_their_corners():
    # Diagonals one step inside stop the corners thinning out.
    offsets = ring_offsets(2)
    assert (2, 2) in offsets
    assert (1, 1) in offsets


def test_the_centre_is_never_drawn():
    # The centre is where the fill goes; an outline copy there would hide it.
    for width in range(1, 5):
        assert (0, 0) not in ring_offsets(width)


def test_offsets_are_unique():
    for width in range(1, 5):
        offsets = ring_offsets(width)
        assert len(offsets) == len(set(offsets))


def test_offsets_stay_within_the_requested_width():
    for width in range(1, 5):
        assert all(max(abs(dx), abs(dy)) <= width for dx, dy in ring_offsets(width))


def test_offsets_are_symmetric():
    # An outline heavier on one side would read as a drop shadow.
    for width in range(1, 5):
        offsets = set(ring_offsets(width))
        assert all((-dx, -dy) in offsets for dx, dy in offsets)


# --- scripts written without spaces ------------------------------------------

def test_a_line_without_spaces_can_still_be_broken():
    # `str.split()` gives a Japanese line back as one token, and a token wider
    # than the limit gets a row of its own and runs off the panel. Measured with
    # the overlay's real font: 702 px inside a 600 px box.
    from lyrica.overlay_text import split_for_wrapping

    pieces = split_for_wrapping("夜に駆ける")
    assert [p for p, _ in pieces] == ["夜", "に", "駆", "け", "る"]
    assert [g for _, g in pieces] == [False, True, True, True, True], \
        "no spaces belong between them"


def test_latin_text_is_left_exactly_as_it_was():
    from lyrica.overlay_text import split_for_wrapping

    text = "y no me digas que ya no me quieres"
    assert [p for p, _ in split_for_wrapping(text)] == text.split()
    assert not any(g for _, g in split_for_wrapping(text))


def test_korean_is_left_alone_because_it_has_spaces():
    from lyrica.overlay_text import split_for_wrapping

    text = "아무노래 지금 이 순간"
    assert [p for p, _ in split_for_wrapping(text)] == text.split()


def test_a_row_never_opens_with_closing_punctuation():
    # Kinsoku shori, the part of it that shows. Attached to its neighbour, so
    # the break can never fall between them in the first place.
    from lyrica.overlay_text import split_for_wrapping

    assert [p for p, _ in split_for_wrapping("走る、そして。")] == \
        ["走", "る、", "そ", "し", "て。"]


def test_a_row_never_closes_with_opening_punctuation():
    from lyrica.overlay_text import split_for_wrapping

    # The opener rides in front of what it opens, and the closer stays with
    # what it closes, so neither can be left alone at the edge of a row.
    assert [p for p, _ in split_for_wrapping("桜「夜」")] == ["桜", "「夜」"]


def test_a_mixed_line_keeps_the_spaces_it_had():
    from lyrica.overlay_text import split_for_wrapping

    pieces = split_for_wrapping("Hello 世界 mixed")
    assert [p for p, _ in pieces] == ["Hello", "世", "界", "mixed"]
    assert [g for _, g in pieces] == [False, False, True, False]
