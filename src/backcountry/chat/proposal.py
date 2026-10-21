"""What the model is told, the shape it must answer in, and how its proposed changes become settings.

Proposed changes are merged into the current settings and validated by the engine's own GenerateRequest, so the
chat can never ask for anything the form couldn't. The user sees the before/after lines and decides to run it.
"""

from difflib import get_close_matches

from backcountry.planning import GenerateRequest, RequestError

SYSTEM_PROMPT = """You help plan a backpacking trip in a trip-planning app. You can do two things:
1. Change the trip's settings when the user asks (kind "change"). Put only the settings that should change in
   "changes", using these fields: nights; total [low, high] miles; daily [low, high] miles per day; max_gain_day
   (feet per day, or null for no cap); type (loop, lollipop or any); no_roads (true or false); direction (cw, ccw
   or null); must_visit_add, must_visit_remove, avoid_add, avoid_remove (trail names exactly as listed in the
   FACTS); camps (the mile along the route where each night's camp should be, one per night, in order, or [] to
   let the app place them); camps_at_sites (true or false); trailhead (a trailhead name from the FACTS).
   The app shows the change to the user, who decides whether to run it. You never run anything, and you never
   draw or describe new routes. To make one day shorter or longer, move the camps: to shorten day 2, move camp 2
   to an earlier mile or camp 1 to a later mile.
2. Answer questions about this trip from the FACTS below only (kind "explain", changes {}).
If a request is unclear, ask one short question (kind "unclear", changes {}).
Rules:
- Use only numbers and names that appear in the FACTS, in miles and feet, rounded like the FACTS.
- Water, campsites, permits, closures, trail conditions, weather and trailhead access are not known to this app.
  Say they are unknown or not checked. Never say water is there or reliable, never say anything is open, legal or
  permitted, and never say a route is safe.
- Keep replies to two or three sentences.
Answer with JSON only: {"reply": "...", "kind": "change" | "explain" | "unclear", "changes": {...}}."""

_RANGE = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_NAMES = {"type": "array", "items": {"type": "string"}}
CHANGE_FIELDS = {
    "nights": {"type": "integer"},
    "total": _RANGE,
    "daily": _RANGE,
    "max_gain_day": {"type": ["number", "null"]},
    "type": {"type": "string", "enum": ["loop", "lollipop", "any"]},
    "no_roads": {"type": "boolean"},
    "direction": {"type": ["string", "null"], "enum": ["cw", "ccw", None]},
    "must_visit_add": _NAMES, "must_visit_remove": _NAMES, "avoid_add": _NAMES, "avoid_remove": _NAMES,
    "camps": {"type": "array", "items": {"type": "number"}},
    "camps_at_sites": {"type": "boolean"},
    "trailhead": {"type": "string"},
}
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reply", "kind", "changes"],
    "properties": {
        "reply": {"type": "string"},
        "kind": {"type": "string", "enum": ["change", "explain", "unclear"]},
        "changes": {"type": "object", "additionalProperties": False, "properties": CHANGE_FIELDS},
    },
}

LABELS = {"trailhead": "Start", "type": "Shape", "total": "Total miles", "nights": "Nights", "daily": "Miles per day",
          "max_gain_day": "Gain cap per day", "no_roads": "Roads", "direction": "Direction", "must_visit": "Must visit",
          "avoid": "Avoid", "camps": "Camps", "camps_at_sites": "Camps at known sites only"}


class ProposalError(ValueError):
    """A proposed change that can't be used; the message says why, for the user."""


def _trail(name: str, trails: list[str]) -> str:
    by_lower = {t.lower(): t for t in trails}
    if name.lower() in by_lower:
        return by_lower[name.lower()]
    guess = get_close_matches(name, trails, n=1, cutoff=0.6)
    raise ProposalError(f"There's no trail named {name!r} in this region" + (f"; did you mean {guess[0]}?" if guess else "."))


def _trailhead(name: str, starts: list[dict]) -> str:
    for s in starts:
        if name.lower() in (s["id"].lower(), s["name"].lower()):
            return s["id"]
    raise ProposalError(f"No trailhead named {name!r}. Trailheads here: {', '.join(s['name'] for s in starts)}.")


def _edit(current: list[str], add: list[str], remove: list[str]) -> list[str]:
    out = [v for v in current if v.lower() not in {r.lower() for r in remove}]
    return out + [a for a in add if a.lower() not in {v.lower() for v in out}]


def apply_changes(request: dict, changes: dict, trails: list[str], starts: list[dict]) -> tuple[dict, list[str]]:
    new = {k: (list(v) if isinstance(v, list) else v) for k, v in request.items()}
    for key in ("nights", "total", "daily", "max_gain_day", "type", "no_roads", "direction", "camps_at_sites"):
        if key in changes:
            new[key] = changes[key]
    if new.get("max_gain_day") is not None and new["max_gain_day"] <= 0:
        raise ProposalError("That change doesn't work: the gain cap must be more than 0 ft (or none).")
    for field in ("must_visit", "avoid"):
        add = [_trail(n, trails) for n in changes.get(f"{field}_add", [])]
        remove = [_trail(n, trails) for n in changes.get(f"{field}_remove", [])]
        new[field] = _edit(new.get(field) or [], add, remove)
    if "trailhead" in changes:
        new["trailhead"] = _trailhead(changes["trailhead"], starts)
    nights = new.get("nights") or 0
    if "camps" in changes:
        miles = [float(m) for m in changes["camps"]]
        if len(miles) > nights:
            raise ProposalError(f"That change doesn't work: {len(miles)} camps for {nights} nights.")
        if any(b <= a for a, b in zip(miles, miles[1:])) or any(m <= 0 for m in miles):
            raise ProposalError("That change doesn't work: Camp miles must go up from one night to the next and be more than 0.")
        new["camps"] = [f"{m:g}" for m in miles]
    elif len(new.get("camps") or []) > nights:
        new["camps"] = []  # fewer nights than pins: let the engine place the camps again
    try:
        after = GenerateRequest.from_dict(new).to_dict()
        before = GenerateRequest.from_dict(request).to_dict()
    except RequestError as e:
        raise ProposalError(f"That change doesn't work: {e}") from e
    return after, describe_changes(before, after, starts)


def _show(key: str, v, starts: list[dict]) -> str:
    if key in ("total", "daily"):
        return "not set" if not v else f"{v[0]:g}-{v[1]:g} mi"
    if key == "max_gain_day":
        return "none" if v is None else f"{v:,.0f} ft"
    if key == "no_roads":
        return "avoided" if v else "allowed"
    if key == "direction":
        return {"cw": "clockwise", "ccw": "counterclockwise"}.get(v, "either")
    if key in ("must_visit", "avoid"):
        return ", ".join(v) or "none"
    if key == "camps":
        return "pinned at mile " + ", ".join(v) if v else "placed by the engine"
    if key == "camps_at_sites":
        return "yes" if v else "no"
    if key == "nights":
        return "none" if v is None else str(v)
    if key == "trailhead":
        return next((s["name"] for s in starts if s["id"] == v), "a start point from the trail data")
    return str(v)


def describe_changes(before: dict, after: dict, starts: list[dict]) -> list[str]:
    return [f"{label}: {_show(k, before.get(k), starts)} → {_show(k, after.get(k), starts)}"
            for k, label in LABELS.items() if before.get(k) != after.get(k)]
