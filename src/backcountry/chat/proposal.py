"""What the model is told, the shape it must answer in, and how its proposed changes become settings.

Proposed changes are merged into the current settings and validated by the engine's own GenerateRequest, so the
chat can never ask for anything the form couldn't. The user sees the before/after lines and decides to run it.
"""

from difflib import get_close_matches

from backcountry.chat.facts import place_text
from backcountry.difficulty import LEVEL_TARGETS, LEVELS
from backcountry.planning import DayTarget, GenerateRequest, RequestError

SYSTEM_PROMPT = """You help plan a backpacking trip in a trip-planning app. You can do two things:
1. Change the trip's settings when the user asks (kind "change"). Put only the settings that should change in
   "changes", using these fields: nights (or null for a one-day hike); total [low, high] miles; daily [low,
   high] miles per day (null only for a one-day hike); max_gain_day
   (feet per day, or null for no cap); type (loop, lollipop or any); no_roads (true or false); direction (cw, ccw
   or null); must_visit_add, must_visit_remove, avoid_add, avoid_remove (trail names exactly as listed in the
   FACTS); camps (the mile along the route where each night's camp should be, one per night, in order, or [] to
   let the app place them); camps_at_sites (true or false); trailhead (a trailhead name from the FACTS);
   days (per-day targets, a list of {day, level, miles, max_gain_ft, max_grade_pct}: level is easy, moderate,
   hard or strenuous, miles a [low, high] range; leave out what the user didn't ask for; a day with everything
   null clears it, and days [] clears all); level (the whole trip's level, or null for any).
   The app shows the change to the user, who decides whether to run it. You never run anything, and you never
   draw or describe new routes. When the user asks for a change, propose it now with kind "change"; don't ask
   whether to do it, because tapping Run is the user's yes.
   Every range needs a low below the high, both above 0, e.g. total [20, 26]; never [x, x] or [0, 0].
   A trip lasts nights + 1 days. When the number of days changes, also change total so it fits: about
   (nights + 1) x daily low to (nights + 1) x daily high miles. "2 days shorter" means 2 fewer nights. A day
   hike (one day, no camping) is nights null and daily null; a one-night trip is nights 1 (two days).
   To change only one end of a range ("at most 9 miles a day"), keep the other end as it is now. To loosen the per-day limit ("ignore miles per day"), widen daily, e.g. from
   1 up to the total's high; there is no "no limit" while the trip has nights.
   To make one day shorter or longer, move the camps. "camps" lists every camp, one mile per night, in order;
   the current camps are the "Camp N: mile X" lines in the FACTS. Keep the camps you don't need to move. Day N
   ends at camp N: to shorten day N, move camp N to an earlier mile (for the last day, move the last camp to a
   later mile); to lengthen it, do the opposite. For example, on a 30 mi route with camps at 10 and 20: shorter
   day 2 is camps [10, 17]; longer day 1 is camps [13, 20]; shorter day 3 is camps [10, 23]. Use the real camp
   miles from the FACTS, never the example's. For a limit on miles per day ("at most 9 miles a day"), change
   daily, not camps.
   A level for one day ("day 1 easy, days 2 and 3 hard", "keep the last day light") goes in days (not daily),
   one entry per day: {"day": 1, "level": "easy"}. "We start late on day 1" is {"day": 1, "miles": [2, 5]}.
   Light, chill or relaxed mean easy; brutal or punishing mean strenuous. Leave camps out when you set days.
   When the user names a level for the whole trip ("find me something hard", "I want a moderate trip"), set
   level to that word now. "Make it easier" or "make it harder" names no level and no day: ask which.
2. Answer questions about this trip from the FACTS below only (kind "explain", changes {}).
If a request is unclear, ask one short question (kind "unclear", changes {}).
Rules:
- Use only numbers and names that appear in the FACTS, in miles and feet, rounded like the FACTS.
- Water, campsites, permits, closures, trail conditions, weather and trailhead access are not known to this app.
  Say they are unknown or not checked. Never say water is there or reliable, never say anything is open, legal or
  permitted, and never say a route is safe.
- Never call a day easy, moderate, hard or strenuous unless the FACTS give it that level. Which day is hardest is
  the "Hardest day" line in the FACTS.
- Keep replies to two or three sentences.
Answer with JSON only: {"reply": "...", "kind": "change" | "explain" | "unclear", "changes": {...}}."""

_RANGE = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
_NAMES = {"type": "array", "items": {"type": "string"}}
CHANGE_FIELDS = {
    "nights": {"type": ["integer", "null"]},
    "total": _RANGE,
    "daily": {**_RANGE, "type": ["array", "null"]},
    "max_gain_day": {"type": ["number", "null"]},
    "type": {"type": "string", "enum": ["loop", "lollipop", "any"]},
    "no_roads": {"type": "boolean"},
    "direction": {"type": ["string", "null"], "enum": ["cw", "ccw", None]},
    "must_visit_add": _NAMES, "must_visit_remove": _NAMES, "avoid_add": _NAMES, "avoid_remove": _NAMES,
    "camps": {"type": "array", "items": {"type": "number"}},
    "camps_at_sites": {"type": "boolean"},
    "trailhead": {"type": "string"},
    "days": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["day"],
        "properties": {
            "day": {"type": "integer"},
            "level": {"type": ["string", "null"], "enum": [*LEVELS, None]},
            "miles": {**_RANGE, "type": ["array", "null"]},
            "max_gain_ft": {"type": ["number", "null"]},
            "max_grade_pct": {"type": ["number", "null"]},
        }}},
    "level": {"type": ["string", "null"], "enum": [*LEVELS, None]},
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
          "avoid": "Avoid", "camps": "Camps", "camps_at_sites": "Camps at known sites only", "level": "Trip level"}
TARGET_FIELDS = ("level", "miles", "max_gain_ft", "max_grade_pct")


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


def apply_changes(request: dict, changes: dict, trails: list[str], starts: list[dict],
                  current_camps: list[float] | None = None) -> tuple[dict, list[str]]:
    """`current_camps`: where the plan's camps are now (miles); pins at those same places change nothing."""
    new = {k: (list(v) if isinstance(v, list) else v) for k, v in request.items()}
    for key in ("nights", "total", "daily", "max_gain_day", "type", "no_roads", "direction", "camps_at_sites", "level"):
        if key in changes:
            new[key] = changes[key]
    before_days = [dict(d) for d in request.get("days") or []]
    if "days" in changes:
        new["days"] = _merge_days(before_days, changes["days"])
    if new.get("nights") is not None and new["nights"] != request.get("nights"):  # fewer days: their targets go too
        new["days"] = [d for d in new.get("days") or [] if not isinstance(d.get("day"), int) or d["day"] <= new["nights"] + 1]
    if new.get("nights") == 0:
        new["nights"] = None  # "0 nights" is a one-day hike
    if new.get("nights") is None:
        new["daily"], new["camps"], new["days"] = None, [], []  # a one-day hike has no per-day range, camps or targets
    elif "daily" in changes and changes["daily"] is None and isinstance(new.get("total"), list):
        new["daily"] = [1, new["total"][1]]  # "no limit" on a trip with nights: the loosest range, as the prompt says
    for key, label, example in (("total", "Total miles need a low below the high, both above 0", "20-26"),
                                ("daily", "Miles per day need a low below the high, both above 0", "6-11")):
        r = new.get(key)
        if isinstance(r, list) and len(r) == 2 and all(isinstance(x, (int, float)) for x in r) and not 0 < r[0] < r[1]:
            raise ProposalError(f"That change doesn't work: {label} (e.g. {example}); got {r[0]:g}-{r[1]:g}.")
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
        same = current_camps is not None and len(miles) == len(current_camps) and all(
            abs(m - c) < 0.01 for m, c in zip(miles, current_camps))
        if not same:
            new["camps"] = [f"{m:g}" for m in miles]
    elif len(new.get("camps") or []) > nights:
        new["camps"] = []  # fewer nights than pins: let the engine place the camps again
    if "days" in changes and new.get("days") != before_days:
        new["camps"] = []  # day targets place the camps; pins would fight them
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
        return ", ".join(place_text(x) for x in v) or "none"
    if key == "camps":
        return "pinned at mile " + ", ".join(v) if v else "placed by the engine"
    if key == "camps_at_sites":
        return "yes" if v else "no"
    if key == "nights":
        return "none" if v is None else str(v)
    if key == "trailhead":
        return next((s["name"] for s in starts if s["id"] == v), "a start point from the trail data")
    return str(v)


def _merge_days(current: list[dict], entries: list[dict]) -> list[dict]:
    """Per-day changes merged by day number: an entry replaces that day's target; all-null clears it; [] clears all."""
    if not entries:
        return []
    by_day = {d["day"]: d for d in current}
    for e in entries:
        target = {k: e.get(k) for k in TARGET_FIELDS}
        if all(v is None for v in target.values()):
            by_day.pop(e.get("day"), None)
        else:
            by_day[e.get("day")] = {"day": e.get("day"), **target}
    return [by_day[k] for k in sorted(by_day, key=lambda k: (not isinstance(k, int), k if isinstance(k, int) else 0))]


def _day_text(t: dict | None, full: bool) -> str:
    """'Easy (2-5 mi, ≤ 250 ft, ≤ 10%)': a level with the numbers it sets (numbers given override the level's)."""
    if not t:
        return "not set"
    table = LEVEL_TARGETS["backpacking"][t["level"]] if t.get("level") else {}
    miles = t.get("miles") or table.get("miles")
    gain = t["max_gain_ft"] if t.get("max_gain_ft") is not None else table.get("max_gain_ft")
    grade = t["max_grade_pct"] if t.get("max_grade_pct") is not None else table.get("max_grade_pct")
    nums = [f"{miles[0]:g}-{miles[1]:g} mi" if miles else None, f"≤ {gain:,.0f} ft" if gain is not None else None,
            f"≤ {grade:g}%" if grade is not None else None]
    nums = ", ".join(n for n in nums if n)
    if not t.get("level"):
        return nums or "not set"
    name = t["level"].capitalize()
    return f"{name} ({nums})" if full and nums else name


def describe_changes(before: dict, after: dict, starts: list[dict]) -> list[str]:
    lines = []
    for k, label in LABELS.items():
        if k == "level":
            continue
        if before.get(k) != after.get(k):
            lines.append(f"{label}: {_show(k, before.get(k), starts)} → {_show(k, after.get(k), starts)}")
    b_days = {d["day"]: d for d in before.get("days") or []}
    a_days = {d["day"]: d for d in after.get("days") or []}
    for day in sorted(set(b_days) | set(a_days)):
        if b_days.get(day) != a_days.get(day):
            lines.append(f"Day {day}: {_day_text(b_days.get(day), False)} → {_day_text(a_days.get(day), True)}")
    if before.get("level") != after.get("level"):
        show = lambda v: v.capitalize() if v else "any"  # noqa: E731
        lines.append(f"Trip level: {show(before.get('level'))} → {show(after.get('level'))}")
    return lines
