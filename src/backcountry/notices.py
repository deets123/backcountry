"""Notices: quoted, dated, sourced statements about permits, fees, closures and rules.

A notice is one statement a source makes (an OpenStreetMap tag, a National Park Service alert or record),
with what it applies to on the trip and the source's date. Nothing here concludes anything: no notice
says a trip needs no permit, is open, or is allowed. Where the sources are silent the block ends with
UNKNOWN_LINE.
"""

import re

from html.parser import HTMLParser

KINDS = ("closure", "permit", "ticket", "camp", "fee", "rule", "caution", "info", "unknown")
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}
UNKNOWN_LINE = "Everything else (water, conditions, permits and closures the sources don't list): unknown."
MAX_TEXT = 400  # characters of source text kept per notice

# Words that never match a notice to a route item on their own: a run of two or more words needs one other word.
GENERIC = frozenset("trail trailhead trailheads road loop lot parking mountain peak lake creek north south east west "
                    "upper lower the to and of".split())
MAX_RUN = 6  # longest word run tried when matching

_HTTP = re.compile(r"^https?://", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_APOSTROPHE = re.compile(r"[’']")
_CLOSED = re.compile(r"\b(closed|closure|closures)\b", re.IGNORECASE)
_TICKET = re.compile(r"\btickets?\b", re.IGNORECASE)
_PERMIT = re.compile(r"\b(permits?|reservations?)\b", re.IGNORECASE)


def notice(kind: str, text: str, *, applies_to: str | None = None, matched: bool = False, source: str | None = None,
           ref: str | None = None, as_of: str | None = None, checked_at: str | None = None, links=()) -> dict:
    """One notice as the API, the summaries and the chat see it."""
    if kind not in KINDS:
        raise ValueError(f"unknown notice kind {kind!r}")
    text = " ".join(str(text).split())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "…"
    kept: list[str] = []
    for link in links:
        if isinstance(link, str) and _HTTP.match(link) and link not in kept:
            kept.append(link)
    return {"kind": kind, "text": text, "applies_to": applies_to, "matched": bool(matched),
            "source": None if source is None else {"name": source, "ref": ref, "as_of": as_of, "checked_at": checked_at},
            "links": kept}


class _Text(HTMLParser):
    """Collects the text of an HTML fragment (script and style contents dropped) and its http(s) links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href and _HTTP.match(href) and href not in self.links:
                self.links.append(href)
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, list[str]]:
    """Plain words from a source's HTML, and the http(s) links it carried."""
    p = _Text()
    p.feed(str(html or ""))  # character references are converted as the parser reads them
    p.close()
    return " ".join("".join(p.parts).split()), p.links


def tokens(text: str) -> list[str]:
    """Lowercase words; apostrophes vanish inside words ("Barney's" is "barneys"), other punctuation separates."""
    return _WORD.findall(_APOSTROPHE.sub("", str(text or "").lower()))


def name_parts(name: str) -> list[str]:
    """A data trailhead name like "West Rim Trailhead North | West Rim Trail @ Lava Point Road" is several names."""
    return [p.strip() for p in re.split(r"[|@]", str(name or "")) if p.strip()]


def _runs(words: list[str]):
    for n in range(min(MAX_RUN, len(words)), 1, -1):
        for i in range(len(words) - n + 1):
            run = words[i:i + n]
            if any(w not in GENERIC for w in run):
                yield run


def _contains(seq: list[str], run: list[str]) -> bool:
    n = len(run)
    return any(seq[i:i + n] == run for i in range(len(seq) - n + 1))


def match_items(text: str, items: list[tuple[str, str]]) -> list[str]:
    """Route items ("label", "name") that `text` names: the two share a run of two or more consecutive words
    with at least one word outside GENERIC. Returns "label: name" in item order."""
    words = tokens(text)
    out: list[str] = []
    for label, name in items:
        key = f"{label}: {name}"
        if key in out:
            continue
        for part in name_parts(name):
            if any(_contains(words, run) for run in _runs(tokens(part))):
                out.append(key)
                break
    return out


def alert_kind(title: str, text: str, category: str) -> str:
    """A National Park Service alert's kind: the title first, then the park's category, then the text."""
    title, text, category = str(title or ""), str(text or ""), str(category or "")
    if _CLOSED.search(title):
        return "closure"
    if _TICKET.search(title):
        return "ticket"
    if _PERMIT.search(title):
        return "permit"
    if category == "Park Closure":
        return "closure"
    if category in ("Danger", "Caution"):
        return "caution"
    if _TICKET.search(text):
        return "ticket"
    if _PERMIT.search(text):
        return "permit"
    return "info"
