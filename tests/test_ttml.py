"""TTML parsing and the word-level model (offline).

The document below has the shape captured from the live community source —
namespaces, `itunes:timing`, spans carrying both `begin` and `end`, and a
background-vocal wrapper — with invented placeholder words.
"""
from lyrica import ttml
from lyrica.lyrics import Lyrics, Precision
from lyrica.ttml import declare_missing_namespaces, declared_timing, parse_time, parse_ttml

DOC = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    itunes:timing="Word" xml:lang="en">
  <head><metadata>
    <ttm:agent type="person" xml:id="v1"><ttm:name type="full">Someone</ttm:name></ttm:agent>
  </metadata></head>
  <body dur="3:20.046">
    <div begin="10.0" end="20.0" itunes:songPart="Verse">
      <p begin="10.0" end="12.0" itunes:key="L1" ttm:agent="v1">
        <span begin="10.0" end="10.5">alpha</span>
        <span begin="10.5" end="11.2">beta</span>
        <span begin="11.2" end="12.0">gamma</span>
      </p>
      <p begin="13.0" end="15.0" itunes:key="L2" ttm:agent="v1">
        <span begin="13.0" end="14.0">delta</span>
        <span ttm:role="x-bg">
          <span begin="13.1" end="13.6">shadow</span>
        </span>
        <span begin="14.0" end="15.0">epsilon</span>
      </p>
    </div>
  </body>
</tt>
"""

# Deliberately declares no xmlns:itunes while using the prefix. Documents do
# ship this way, and a strict parser rejects the whole file over it.
LINE_ONLY_DOC = """<tt xmlns="http://www.w3.org/ns/ttml" itunes:timing="Line">
  <body><div>
    <p begin="5.0" end="7.0">whole line here</p>
    <p begin="8.0" end="9.0">another line</p>
  </div></body>
</tt>
"""


# --- time expressions -------------------------------------------------------

def test_parse_time_accepts_every_shape_the_format_allows():
    assert parse_time("27.395") == 27.395
    assert parse_time("27.395s") == 27.395
    assert parse_time("3:20.046") == 200.046
    assert parse_time("1:03:20.5") == 3800.5
    assert parse_time("0") == 0.0


def test_parse_time_rejects_nonsense():
    assert parse_time(None) is None
    assert parse_time("") is None
    assert parse_time("later") is None


# --- document parsing -------------------------------------------------------

def test_lines_and_word_timings_are_extracted():
    lyr = parse_ttml(DOC)
    assert lyr is not None
    assert [t for t, _ in lyr.lines] == [10.0, 13.0]
    assert lyr.words[0] == [(10.0, 10.5, "alpha"), (10.5, 11.2, "beta"), (11.2, 12.0, "gamma")]


def test_word_timings_make_it_word_precision():
    assert parse_ttml(DOC).precision is Precision.WORD


def test_background_vocals_are_dropped():
    # They overlap the main line in time rather than following it, so folding
    # them in would interleave two simultaneous vocals.
    lyr = parse_ttml(DOC)
    assert [w[2] for w in lyr.words[1]] == ["delta", "epsilon"]
    assert "shadow" not in lyr.lines[1][1]


def test_line_text_is_built_from_its_timed_words():
    assert parse_ttml(DOC).lines[0][1] == "alpha beta gamma"


def test_a_line_timed_document_still_parses_as_lines():
    lyr = parse_ttml(LINE_ONLY_DOC)
    assert lyr.precision is Precision.LINE
    assert lyr.lines[0][1] == "whole line here"
    assert lyr.words == [[], []]


def test_lines_come_back_in_time_order():
    doc = LINE_ONLY_DOC.replace('begin="5.0"', 'begin="50.0"')
    assert [t for t, _ in parse_ttml(doc).lines] == [8.0, 50.0]


def test_undeclared_namespace_prefixes_are_recovered():
    # Rejecting the file outright loses the lyrics over a missing attribute.
    assert 'xmlns:itunes="http://music.apple.com/lyric-ttml-internal"' in \
        declare_missing_namespaces(LINE_ONLY_DOC)


def test_a_well_formed_document_is_left_alone():
    assert declare_missing_namespaces(DOC) == DOC


def test_malformed_xml_is_none_not_an_exception():
    assert parse_ttml("<tt><body>") is None


def test_a_document_carrying_a_dtd_is_refused():
    # An entity-expansion bomb needs a DTD, and lyrics never legitimately have
    # one, so the whole class is refused rather than parsed carefully.
    bomb = (
        '<!DOCTYPE tt [<!ENTITY a "xxxxxxxxxx">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="1.0">&b;</p></div></body></tt>'
    )
    assert parse_ttml(bomb) is None


def test_a_document_with_no_timed_lines_is_none():
    assert parse_ttml('<tt xmlns="http://www.w3.org/ns/ttml"><body><div/></body></tt>') is None


def test_declared_timing_is_read_from_the_root():
    assert declared_timing(DOC) == "Word"
    assert declared_timing(LINE_ONLY_DOC) == "Line"
    assert declared_timing("<tt></tt>") == ""


# --- word lookup ------------------------------------------------------------

def test_word_index_walks_the_line():
    lyr = parse_ttml(DOC)
    assert lyr.word_index_at(0, 9.9) == -1     # before the line starts
    assert lyr.word_index_at(0, 10.0) == 0     # exactly on a word
    assert lyr.word_index_at(0, 10.6) == 1
    assert lyr.word_index_at(0, 99.0) == 2     # past the end holds the last


def test_word_progress_sweeps_and_then_saturates():
    lyr = parse_ttml(DOC)
    i, frac = lyr.word_progress_at(0, 10.25)
    assert i == 0 and abs(frac - 0.5) < 1e-6
    # A gap after a word holds the sweep complete rather than running on.
    _, frac_after = lyr.word_progress_at(0, 12.5)
    assert frac_after == 1.0


def test_word_progress_before_the_first_word():
    assert parse_ttml(DOC).word_progress_at(0, 0.0) == (-1, 0.0)


def test_lines_without_word_data_report_no_words():
    lyr = parse_ttml(LINE_ONLY_DOC)
    assert lyr.words_at(0) == []
    assert lyr.word_index_at(0, 6.0) == -1


def test_words_at_tolerates_an_out_of_range_line():
    assert Lyrics().words_at(5) == []


def test_a_zero_length_word_is_treated_as_complete():
    lyr = Lyrics(lines=[(0.0, "x")], words=[[(1.0, 1.0, "x")]], synced=True)
    assert lyr.word_progress_at(0, 1.0) == (0, 1.0)


# --- words split below the word ---------------------------------------------

def rejoined(*spans: str) -> str:
    """The line a document of these spans displays as."""
    body = "".join(f'<span begin="{i}s" end="{i + 1}s">{s}</span>'
                   for i, s in enumerate(spans))
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           f'<p begin="0s" end="9s">{body}</p></div></body></tt>')
    return ttml.parse_ttml(doc).lines[0][1]


def test_adjacent_spans_are_not_pushed_apart():
    # Word-timed documents split below the word: "ofenderte" arrives as two
    # spans with nothing between them, and joining the trimmed tokens with a
    # space displayed it as "Sin ofender te".
    assert rejoined("Sin ", "ofender", "te") == "Sin ofenderte"
    assert rejoined("aca", "be", " con", "migo") == "acabe conmigo"


def test_spans_that_were_separate_stay_separate():
    # The other half of it: a fix that simply concatenated would run genuinely
    # separate words together.
    assert rejoined("dos ", "palabras") == "dos palabras"


def test_the_timings_still_carry_the_syllables():
    # The sweep needs the pieces even though the line shows them joined.
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="0s" end="3s">'
           '<span begin="0s" end="1s">ofender</span>'
           '<span begin="1s" end="2s">te</span>'
           '</p></div></body></tt>')
    lyrics = ttml.parse_ttml(doc)
    assert lyrics.lines[0][1] == "ofenderte"
    assert [w[2] for w in lyrics.words[0]] == ["ofender", "te"]


def test_a_line_with_no_timed_spans_still_reads():
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="0s" end="3s">una  linea   suelta</p>'
           '</div></body></tt>')
    assert ttml.parse_ttml(doc).lines[0][1] == "una linea suelta"


def test_a_wrapper_keeps_the_space_that_followed_it():
    # The wrapper is untimed, so its children are the words — but its own tail
    # is the only space between "Hello" and "world", and recursing past it
    # dropped that space and welded the two together.
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="0s" end="3s">'
           '<span><span begin="0s" end="1s">Hello</span></span> '
           '<span begin="1s" end="2s">world</span>'
           '</p></div></body></tt>')
    assert ttml.parse_ttml(doc).lines[0][1] == "Hello world"


def test_a_background_chorus_keeps_the_space_that_followed_it():
    # Background vocals are dropped whole, and the space after one belongs to
    # the word before it.
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml" '
           'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>'
           '<p begin="0s" end="4s">'
           '<span begin="0s" end="1s">canta</span>'
           '<span ttm:role="x-bg"><span begin="1s" end="2s">ooh</span></span> '
           '<span begin="2s" end="3s">conmigo</span>'
           '</p></div></body></tt>')
    lyrics = ttml.parse_ttml(doc)
    assert lyrics.lines[0][1] == "canta conmigo"
    assert [w[2] for w in lyrics.words[0]] == ["canta", "conmigo"]


# --- what is sung behind the line -------------------------------------------

def test_a_backing_vocal_comes_back_apart_from_the_line():
    # It cannot join the line's own sequence: its times overlap rather than
    # follow, and interleaving two simultaneous vocals gives nonsense.
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml" '
           'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>'
           '<p begin="41.684" end="44.193">'
           '<span begin="41.684" end="41.901">I</span> '
           '<span begin="41.901" end="42.172">need</span> '
           '<span begin="42.172" end="42.737">you</span>'
           '<span ttm:role="x-bg">'
           '<span begin="42.688" end="43.406">(You)</span></span>'
           '</p></div></body></tt>')
    lyrics = ttml.parse_ttml(doc)
    assert lyrics.lines[0][1] == "I need you"
    assert [w[2] for w in lyrics.words[0]] == ["I", "need", "you"]
    text, words = lyrics.backing_at(0)
    assert text == "(You)"
    assert words == [(42.688, 43.406, "(You)")]
    # And it really does overlap what it answers.
    assert words[0][0] < lyrics.words[0][-1][1]


def test_a_backing_vocal_split_below_the_word_is_put_back_together():
    # "(You, the moon" and "light)" arrive as separate spans with nothing
    # between them, exactly as the main line's words do.
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml" '
           'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>'
           '<p begin="46.775" end="50.301">'
           '<span begin="46.775" end="47.335">You</span>'
           '<span ttm:role="x-bg">'
           '<span begin="48.517" end="48.895">(You,</span> '
           '<span begin="48.895" end="49.061">the</span> '
           '<span begin="49.061" end="49.482">moon</span>'
           '<span begin="49.482" end="50.301">light)</span></span>'
           '</p></div></body></tt>')
    assert ttml.parse_ttml(doc).backing_at(0)[0] == "(You, the moonlight)"


def test_an_unmarked_overlapping_parenthesis_becomes_a_backing_vocal():
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="0s" end="2s">'
           '<span begin="0s" end="1s">Lead</span> '
           '<span begin="0.4s" end="0.8s">(you</span> '
           '<span begin="0.8s" end="1.2s">know)</span> '
           '<span begin="0.9s" end="1.5s">tonight</span>'
           '</p></div></body></tt>')
    lyrics = ttml.parse_ttml(doc)
    assert lyrics.lines[0][1] == "Lead tonight"
    assert lyrics.words_at(0) == [(0.0, 1.0, "Lead"), (0.9, 1.5, "tonight")]
    assert lyrics.backing_at(0) == (
        "(you know)", [(0.4, 0.8, "(you"), (0.8, 1.2, "know)")])


def test_an_unmarked_parenthetical_suffix_keeps_its_own_timing_as_backing():
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="0s" end="2s">'
           '<span begin="0s" end="0.8s">Lead</span> '
           '<span begin="0.8s" end="1.3s">(yeah)</span>'
           '</p></div></body></tt>')
    lyrics = ttml.parse_ttml(doc)
    assert lyrics.lines[0][1] == "Lead"
    assert lyrics.words_at(0) == [(0.0, 0.8, "Lead")]
    assert lyrics.backing_at(0) == ("(yeah)", [(0.8, 1.3, "(yeah)")])
    assert lyrics.backing_mode_at(0) == "sequential"


def test_a_line_with_nothing_behind_it_says_so():
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="0s" end="3s"><span begin="0s" end="1s">solo</span></p>'
           '</div></body></tt>')
    assert ttml.parse_ttml(doc).backing_at(0) == ("", [])


def test_backing_stays_matched_to_its_line_when_sections_are_out_of_order():
    doc = ('<tt xmlns="http://www.w3.org/ns/ttml" '
           'xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body>'
           '<div><p begin="10s" end="12s">'
           '<span begin="10s" end="11s">segunda</span>'
           '<span ttm:role="x-bg"><span begin="11s" end="12s">(dos)</span></span>'
           '</p></div>'
           '<div><p begin="0s" end="2s">'
           '<span begin="0s" end="1s">primera</span>'
           '<span ttm:role="x-bg"><span begin="1s" end="2s">(uno)</span></span>'
           '</p></div></body></tt>')
    lyrics = ttml.parse_ttml(doc)
    assert [t for _s, t in lyrics.lines] == ["primera", "segunda"]
    assert [lyrics.backing_at(i)[0] for i in (0, 1)] == ["(uno)", "(dos)"]
