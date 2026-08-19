"""Text measurement and layout shared by the line renderer.

Two things live here because they are needed before anything can be drawn:
how wide a piece of text is, and how a line breaks into rows.

Measurements are memoised. The same words are measured on every rebuild, and
`font.measure` is a round trip into Tk — cheap once, wasteful thousands of times.

Outlines ring the centre rather than filling a square. A square of radius 2 is
24 draws per glyph where the ring is 8, and the extra 16 land underneath the
fill where nothing can see them. They are only used in keyed mode, which is the
one mode with nothing drawn behind the text; everywhere else the cover wash is
capped dark enough to be the contrast by itself.
"""
from itertools import islice
from tkinter import font as tkfont

OUTLINE_COLOUR = "#000000"

# Nothing is laid out closer than this to the window edge. In designed units:
# callers multiply by the display scale, or a line at 2x sits twice as close to
# an edge that moved twice as far away.
EDGE_MARGIN = 8

_measure_cache: dict[tuple, int] = {}

# The cache key is (font descriptor, text) and the descriptor carries the size,
# so every Ctrl+Alt +/- step opens a fresh key space and the one before it was
# never released. A session that walked the scale range kept one full set of
# measurements per scale, of every character and token seen since it started,
# and songs added to it too. Tens of KB each: a leak, not a fire, so the answer
# is a cap rather than a policy.
#
# What has to stay resident is the current font's working set — the alphabet
# plus the tokens of the song on screen, some hundreds of entries — and half of
# this is several times that, so trimming never costs the live line a
# remeasure. Insertion order is dict order, so the half dropped is the half
# longest untouched by a *new* measurement, which after a size change is the
# outgoing scale.
_MEASURE_CACHE_MAX = 4096


def ring_offsets(width: int) -> list[tuple[int, int]]:
    """The eight compass directions at `width` pixels, plus the diagonals at
    the step inside it so the corners do not thin out."""
    if width <= 0:
        return []
    w = width
    offsets = [(-w, 0), (w, 0), (0, -w), (0, w), (-w, -w), (-w, w), (w, -w), (w, w)]
    if w > 1:
        i = w - 1
        offsets += [(-i, -i), (-i, i), (i, -i), (i, i)]
    return offsets


def measure(font_obj: tkfont.Font, key: tuple, text: str) -> int:
    """Width of `text`, memoised — the same words are measured on every rebuild."""
    cache_key = (key, text)
    width = _measure_cache.get(cache_key)
    if width is None:
        width = font_obj.measure(text)
        if len(_measure_cache) >= _MEASURE_CACHE_MAX:
            for stale in list(islice(_measure_cache, _MEASURE_CACHE_MAX // 2)):
                del _measure_cache[stale]
        _measure_cache[cache_key] = width
    return width


# Scripts written without spaces between words. A line in any of them arrives
# from `str.split()` as a single token, which `wrap_words` can only give a row
# of its own — measured with the overlay's real font, a Japanese line came to
# 725 px inside a 600 px panel and simply ran off the edge. Korean is
# deliberately absent: it is written with spaces and already wraps.
_UNSPACED = (
    (0x3000, 0x303F),   # CJK punctuation
    (0x3040, 0x309F),   # hiragana
    (0x30A0, 0x30FF),   # katakana
    (0x3400, 0x4DBF),   # unified ideographs, extension A
    (0x4E00, 0x9FFF),   # unified ideographs
    (0xF900, 0xFAFF),   # compatibility ideographs
    (0xFF01, 0xFF60),   # fullwidth forms
    (0xFF61, 0xFF9F),   # halfwidth katakana
)

# Kinsoku shori, the part of it that shows. A row may not open with closing
# punctuation or a small kana, and may not close with opening punctuation. Both
# are answered by attaching the character to its neighbour, so the row can never
# be broken between them in the first place.
_NEVER_STARTS = (
    "、。，．・：；？！ゝゞー々〆〇"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ"
    "）」』】〕｝〉》"
    ")]}»,.!?;:"
)
_NEVER_ENDS = "（「『【〔｛〈《([{«"


def _unspaced(ch: str) -> bool:
    if not ch:
        return False
    point = ord(ch[0])
    return any(lo <= point <= hi for lo, hi in _UNSPACED)


def split_for_wrapping(text: str) -> list:
    """The line as pieces a row may be broken between, each with whether a space
    belongs in front of it.

    Latin text comes back exactly as `str.split()` left it. A run written
    without spaces is broken down to single characters instead, because that is
    where those scripts allow a line to end — subject to the two prohibitions
    that are visible enough to matter, which are handled by keeping the affected
    character attached to its neighbour.
    """
    pieces: list[tuple[str, bool]] = []
    for word in text.split():
        first = True
        pending = ""               # openers waiting for what they open
        for ch in word:
            if ch in _NEVER_ENDS and _unspaced(ch):
                pending += ch
                continue
            # The tests below are always about `ch` itself; `pending` only ever
            # rides along in front of it so the two cannot be split apart.
            piece, pending = pending + ch, ""
            join_back = pieces and not first and (
                ch in _NEVER_STARTS
                or not (_unspaced(ch) or _unspaced(pieces[-1][0][-1])))
            if join_back:
                pieces[-1] = (pieces[-1][0] + piece, pieces[-1][1])
            else:
                pieces.append((piece, not first))
                first = False
        if pending:                # a stray opener at the end of a word
            if pieces:
                pieces[-1] = (pieces[-1][0] + pending, pieces[-1][1])
            else:
                pieces.append((pending, False))
    return pieces


def wrap_words(words: list, font_obj: tkfont.Font, key: tuple, space: int,
               wrap: int, glued: list | None = None) -> list:
    """Greedily group words into rows no wider than `wrap`.

    Returns rows of (word_index, text, width). The index survives wrapping
    because the renderer colours by word, and a wrapped row still has to know
    which word each of its entries was.

    A word wider than the limit gets a row of its own rather than being
    dropped — it will overflow, but a line that is present and too wide beats a
    line that is missing.
    """
    rows: list = []
    row: list = []
    row_width = 0
    for i, entry in enumerate(words):
        text = entry[2]
        w = measure(font_obj, key, text)
        joined = bool(glued) and glued[i]
        extra = w if (not row or joined) else w + space
        if row and row_width + extra > wrap:
            rows.append(row)
            row, row_width = [], 0
            extra = w
        row.append((i, text, w))
        row_width += extra
    if row:
        rows.append(row)
    return rows
