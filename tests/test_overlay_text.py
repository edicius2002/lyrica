# -*- coding: utf-8 -*-
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
