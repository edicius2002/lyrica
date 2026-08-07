"""TTML parsing for word-timed lyrics.

The community sources both serve Apple Music's TTML dialect, whose shape is:

    <tt itunes:timing="Word">
      <body dur="3:20.046">
        <div begin="27.395" end="48.621">
          <p begin="27.395" end="28.960" ttm:agent="v1">
            <span begin="27.395" end="27.549">word</span>
            ...

Every span states both `begin` and `end`, so unlike Musixmatch's richsync there
is nothing to infer — a gap between two words is real silence and stays silent.

Background vocals arrive as spans nested inside a wrapper marked
`ttm:role="x-bg"`. They are dropped: they overlap the main line in time rather
than following it, so folding them into one sequence would interleave two
simultaneous vocals into nonsense.
"""
import re
import xml.etree.ElementTree as ET

from lyrica.lyrics import Lyrics

TTML_NS = "http://www.w3.org/ns/ttml"
TTM_NS = "http://www.w3.org/ns/ttml#metadata"
ITUNES_NS = "http://music.apple.com/lyric-ttml-internal"

# "27.395", "27.395s", "3:20.046", "1:03:20.046"
CLOCK = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")


def parse_time(value: str | None) -> float | None:
    """Seconds from a TTML time expression, or None if it is not one."""
    if not value:
        return None
    v = value.strip().rstrip("s")
    m = CLOCK.match(v)
    if not m:
        return None
    a, b, c = m.groups()
    seconds = float(c)
    if b is not None:      # a:b:c -> hours:minutes:seconds
        seconds += int(b) * 60 + int(a) * 3600
    elif a is not None:    # a:c -> minutes:seconds
        seconds += int(a) * 60
    return seconds


def _local(tag: str) -> str:
    """Tag name without its namespace, so documents that omit or rename the
    default namespace still parse."""
    return tag.rsplit("}", 1)[-1]


def _is_background(el: ET.Element) -> bool:
    return el.get(f"{{{TTM_NS}}}role") == "x-bg" or el.get("role") == "x-bg"


def _collect_spans(node: ET.Element) -> list:
    """Timed spans under a line, in order, with the text that separated them.

    Each entry carries the span's own text *and* whatever followed it before
    the next span. That tail is the only record of whether two spans were
    adjacent in the source or had a space between them, and it is what a line
    has to be rebuilt from.
    """
    spans = []
    for child in node:
        if _local(child.tag) != "span":
            continue
        if _is_background(child):
            continue
        start = parse_time(child.get("begin"))
        end = parse_time(child.get("end"))
        text = "".join(child.itertext())
        if start is not None and end is not None and text.strip():
            spans.append((start, end, text, child.tail or ""))
        else:
            # A span may wrap further spans without being timed or a background
            # marker; its children are the real words.
            spans.extend(_collect_spans(child))
    return spans


def _words(spans: list) -> list:
    """Just the timings, with each token trimmed for display."""
    return [(start, end, text.strip()) for start, end, text, _tail in spans]


def _line_text(node: ET.Element, spans: list) -> str:
    """The full line, with the source's own spacing.

    Rebuilt by concatenating each span with what followed it, rather than by
    joining the trimmed tokens with spaces. Word-timed documents split below
    the word — "ofenderte" arrives as "ofender" and "te", two spans with
    nothing between them — so joining with a space put one inside the word and
    the line displayed as "Sin ofender te". The tails say which pairs were
    adjacent; the join could only guess, and guessed the same way every time.
    """
    if spans:
        joined = "".join(text + tail for _s, _e, text, tail in spans)
        return " ".join(joined.split())
    return " ".join("".join(node.itertext()).split())


KNOWN_NS = {"ttm": TTM_NS, "itunes": ITUNES_NS, "tt": TTML_NS,
            "xml": "http://www.w3.org/XML/1998/namespace"}
PREFIX_USE = re.compile(r"[<\s]([a-zA-Z][\w.-]*):[a-zA-Z]")
PREFIX_DECL = re.compile(r"xmlns:([a-zA-Z][\w.-]*)\s*=")


def declare_missing_namespaces(body: str) -> str:
    """Add xmlns declarations for prefixes the document uses but never declares.

    Documents in the wild do ship this way — an `itunes:timing` attribute with
    no `xmlns:itunes` anywhere — and a strict parser rejects the whole file over
    it. Since the prefixes are a known, small set, declaring them is recoverable
    where discarding the lyrics is not.
    """
    declared = set(PREFIX_DECL.findall(body)) | {"xml"}
    used = {p for p in PREFIX_USE.findall(body) if p in KNOWN_NS}
    missing = used - declared
    if not missing:
        return body

    match = re.search(r"<([a-zA-Z][\w.-]*)((?:\s[^>]*?)?)(/?)>", body)
    if not match:
        return body
    additions = "".join(f' xmlns:{p}="{KNOWN_NS[p]}"' for p in sorted(missing))
    start, end = match.span()
    patched = f"<{match.group(1)}{match.group(2)}{additions}{match.group(3)}>"
    return body[:start] + patched + body[end:]


DOCTYPE = re.compile(r"<!DOCTYPE", re.IGNORECASE)


def _safe_fromstring(body: str) -> ET.Element | None:
    """Parse XML from an untrusted source, or None.

    These documents arrive over the network from a community service. Python's
    ElementTree never resolves external entities, so the only real attack left
    is entity-expansion — a small document that inflates until it exhausts
    memory — and that requires a DTD. A lyrics file has no legitimate reason to
    carry one, so refusing DOCTYPE outright closes the vector without pulling in
    a parsing dependency.
    """
    if DOCTYPE.search(body[:4000]):
        return None
    try:
        return ET.fromstring(body)  # noqa: S314 — DTDs refused above
    except ET.ParseError:
        return None


def parse_ttml(body: str) -> Lyrics | None:
    """Parse a TTML document into Lyrics, or None if it carries no timed lines."""
    root = _safe_fromstring(body)
    if root is None:
        root = _safe_fromstring(declare_missing_namespaces(body))
    if root is None:
        return None

    lines: list = []
    words: list = []
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        start = parse_time(p.get("begin"))
        if start is None:
            continue
        spans = _collect_spans(p)
        line_words = _words(spans)
        text = _line_text(p, spans)
        if not text:
            continue
        lines.append((start, text))
        words.append(line_words)

    if not lines:
        return None

    # Sort together so a document listing sections out of order still plays in
    # order, and the word lists stay matched to their lines.
    order = sorted(range(len(lines)), key=lambda i: lines[i][0])
    lines = [lines[i] for i in order]
    words = [words[i] for i in order]

    return Lyrics(lines=lines, words=words, synced=True)


def declared_timing(body: str) -> str:
    """The document's own claim about its granularity: Word, Line or None.

    Only a claim — a document can say Word and carry untimed lines — so it is
    used to skip fetching, never to decide what was actually parsed.
    """
    m = re.search(r'itunes:timing\s*=\s*"([^"]+)"', body[:2000])
    return m.group(1) if m else ""
