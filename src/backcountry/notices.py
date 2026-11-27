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


# --- from OpenStreetMap tags ---------------------------------------------------------------------------

M_PER_MI = 1609.344
OSM = "OpenStreetMap"
CLOSED_WAY_M = 400.0  # a closed way this near a destination is quoted in the "nothing reaches" note
_SITE_RULE_TAGS = ("access", "fee", "reservation", "permit")


def _mi(m: float) -> str:
    return f"{m / M_PER_MI:.1f} mi"


def _trail(name) -> str:
    return name or "an unnamed trail"


def _tag_links(tags: dict) -> list[str]:
    return [tags[k] for k in ("operator", "website") if isinstance(tags.get(k), str)]


def osm_route_notices(edges: list[dict], cut_date: str | None) -> list[dict]:
    """What the route's own ways say: permits per trail, dogs, hours and seasons, and closure notes on trails
    (a road's "closed to private vehicles" is about cars, so roads are left out)."""
    by_name: dict[str, list[dict]] = {}
    for e in edges:
        by_name.setdefault(_trail(e.get("name")), []).append(e)
    out: list[dict] = []
    dogs: dict[str, list[tuple[str, float]]] = {}
    for name, group in by_name.items():
        tags = {}
        for e in group:
            tags = {**e.get("tags", {}), **tags}  # the first edge's values win
        ref = group[0].get("source")
        permit_m = sum(e["length_m"] for e in group if e.get("permit"))
        if permit_m > 0:
            text = f"Permit to hike {name}, {_mi(permit_m)} of this trip."
            if tags.get("reservation") == "required":
                text += " Reservation required."
            elif tags.get("reservation"):
                text += f" Reservation: {tags['reservation']}."
            if tags.get("fee") == "yes":
                text += " Fee."
            if tags.get("permit"):
                text += f" Permit: {tags['permit']}."
            for k in ("note", "description"):
                if tags.get(k):
                    text += f' "{tags[k]}"'
            out.append(notice("permit", text, applies_to=f"trail: {name}", matched=True, source=OSM, ref=ref, as_of=cut_date,
                              links=_tag_links(tags)))
        if tags.get("dog"):
            dogs.setdefault(tags["dog"], []).append((name, sum(e["length_m"] for e in group)))
        for key, label in (("opening_hours", "opening hours"), ("seasonal", "seasonal")):
            if tags.get(key):
                out.append(notice("rule", f"{name}: {label} {tags[key]}.", applies_to=f"trail: {name}", matched=True, source=OSM,
                                  ref=ref, as_of=cut_date))
        if not all(e.get("is_road") for e in group):
            for k in ("note", "description"):
                if tags.get(k) and _CLOSED.search(tags[k]):
                    extra = ", ".join(x for x in (tags.get("damage:type"), tags.get("damage:date")) if x)
                    out.append(notice("closure", f'{name}: "{tags[k]}"' + (f" ({extra})" if extra else ""), applies_to=f"trail: {name}",
                                      matched=True, source=OSM, ref=ref, as_of=cut_date))
                    break
    for value, trails in dogs.items():
        names = ", ".join(n for n, _ in trails)
        out.append(notice("rule", f"Dogs: {value}, on {_mi(sum(m for _, m in trails))} ({names}).",
                          applies_to=f"trail: {trails[0][0]}" if len(trails) == 1 else "route", matched=True, source=OSM,
                          ref=by_name[trails[0][0]][0].get("source"), as_of=cut_date))
    return out


def camp_notices(camps, sites: list[dict], cut_date: str | None) -> list[dict]:
    """What the camp sites near the plan's camps say about camping there (permit, fee, reservation, capacity)."""
    from backcountry.split import NEARBY_CAMP_M

    by_source = {s["source"]: s for s in sites}
    out: list[dict] = []
    for camp in camps:
        src = camp.site
        if src is None and camp.nearby and camp.nearby[0].get("distance_m", 0.0) <= NEARBY_CAMP_M:
            src = camp.nearby[0]["source"]
        site = by_source.get(src) if src else None
        tags = (site or {}).get("tags", {})
        if not site or not any(k in tags for k in _SITE_RULE_TAGS):
            continue
        parts = []
        if tags.get("access") == "permit" or tags.get("permit"):
            parts.append("permit")
        if tags.get("fee") == "yes":
            parts.append("fee")
        if tags.get("reservation") == "required":
            parts.append("reservation required")
        elif tags.get("reservation") == "no":
            parts.append("no reservation")
        if tags.get("capacity:persons"):
            parts.append(f"{tags['capacity:persons']} people")
        if not parts:
            continue
        out.append(notice("camp", f"Camp {camp.index} near {site.get('name') or 'a mapped camp site'} ({OSM}): {', '.join(parts)}.",
                          applies_to=f"camp {camp.index}", matched=True, source=OSM, ref=site["source"], as_of=cut_date,
                          links=_tag_links(tags)))
    return out


def closed_ways_near(tg, lon: float, lat: float, within_m: float = CLOSED_WAY_M) -> list[str]:
    """Why the ways near a point are shut, quoted from OpenStreetMap, nearest first: excluded ways within
    `within_m` that carry a note, description or damage tag."""
    from pyproj import Transformer
    from shapely.geometry import LineString, Point

    to_utm = Transformer.from_crs("EPSG:4326", tg.crs, always_xy=True)
    here = Point(*to_utm.transform(lon, lat))
    found = []
    for rec in tg.excluded:
        tags = rec.get("tags") or {}
        if not any(tags.get(k) for k in ("note", "description", "damage:type")) or len(rec.get("geometry", [])) < 2:
            continue
        line = LineString([to_utm.transform(x, y) for x, y in rec["geometry"]])
        dist = line.distance(here)
        if dist > within_m:
            continue
        name = rec.get("name") or "An unnamed trail"
        quote = tags.get("note") or tags.get("description")
        extra = ", ".join(x for x in (tags.get("damage:type"), tags.get("damage:date")) if x)
        text = f"{name} is tagged closed in {OSM}" + (f': "{quote}"' if quote else "") + (f" ({extra})" if extra else "")
        found.append((dist, text))
    return [text for _, text in sorted(found)]
