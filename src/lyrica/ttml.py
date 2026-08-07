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


def _collect_words(node: ET.Element) -> list:
    """Timed spans under a line, in document order, skipping background vocals."""
    words = []
    for child in node:
        if _local(child.tag) != "span":
            continue
        if _is_background(child):
            continue
        start = parse_time(child.get("begin"))
        end = parse_time(child.get("end"))
        text = "".join(child.itertext())
        if start is not None and end is not None and text.strip():
            # Trailing whitespace belongs between words, not inside one, but a
            # word that is only whitespace has already been filtered out.
            words.append((start, end, text.strip()))
        # A span may wrap further spans without being a background marker.
        words.extend(_collect_words(child))
    return words


def _line_text(node: ET.Element, words: list) -> str:
    """The full line. Prefer joining the timed words, since that drops the
    background vocals the timing already excluded."""
    if words:
        return " ".join(w[2] for w in words)
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
        line_words = _collect_words(p)
        text = _line_text(p, line_words)
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
