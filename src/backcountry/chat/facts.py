"""The fact sheet: everything the chat model may know about the open trip, as short plain text.

Built only from a stored run (the engine's output) plus the region's trail and trailhead names. Miles and feet,
the app's "unknown" wording, and no raw OSM ids. The honesty check (grounding.py) compares replies against it.
"""

import re

from backcountry.export import kind_label
from backcountry.scoring import M_PER_MI

FT_PER_M = 3.28084
NEARBY_FT = 1640  # 500 m: split.py NEARBY_CAMP_M
MAX_FEATURES = 40
MAX_NAMES = 300
CAMP_KINDS = ("camp_site", "camp_pitch")


def mi(m) -> str:
    return "unknown" if m is None else f"{m / M_PER_MI:.2f} mi"


def ft(x) -> str:
    return "unknown" if x is None else f"{x:,.0f} ft"


def span(r) -> str:
    return "not set" if not r else f"{r[0]:g}-{r[1]:g} mi"


def start_name(trailhead: str, starts: list[dict]) -> str:
    for s in starts:
        if s["id"] == trailhead:
            return s["name"]
    if re.fullmatch(r"-?\d+(\.\d+)?,-?\d+(\.\d+)?", trailhead):
        return f"the map point {trailhead}"
    return "a start point from the trail data"


def place_text(value: str) -> str:
    return "a point from the trail data" if value.isdigit() else value  # never a raw node id


def settings_text(req: dict, starts: list[dict]) -> str:
    gain = req.get("max_gain_day")
    parts = [
        f"start {start_name(str(req['trailhead']), starts)}",
        f"shape {req.get('type') or 'any'}",
        f"total {span(req.get('total'))}",
        f"nights {req['nights'] if req.get('nights') is not None else 'none (one day)'}",
        f"miles per day {span(req.get('daily'))}",
        f"gain cap {ft(gain) + ' per day' if gain is not None else 'none'}",
        "roads " + ("avoided" if req.get("no_roads") else "allowed"),
        "direction " + {"cw": "clockwise", "ccw": "counterclockwise"}.get(req.get("direction"), "either"),
        "must visit " + (", ".join(place_text(v) for v in req.get("must_visit") or []) or "none"),
        "avoid " + (", ".join(place_text(v) for v in req.get("avoid") or []) or "none"),
        "camps " + ("pinned at mile " + ", ".join(req["camps"]) if req.get("camps") else "placed by the engine"),
        "camps at known campsites only " + ("yes" if req.get("camps_at_sites") else "no"),
        f"trip level {req.get('level') or 'any'}",
        "day targets " + (" and ".join(_target_text(d) for d in req.get("days") or []) or "none"),
    ]
    return "Current settings: " + "; ".join(parts) + "."


def _target_text(d: dict) -> str:
    parts = [d["level"]] if d.get("level") else []
    if d.get("miles"):
        parts.append(span(d["miles"]))
    if d.get("max_gain_ft") is not None:
        parts.append(f"up to {ft(d['max_gain_ft'])}")
    if d.get("max_grade_pct") is not None:
        parts.append(f"up to {d['max_grade_pct']:g}% grade")
    return f"day {d['day']} " + ", ".join(parts)


def level_text(rating: dict | None) -> str:
    """'level hard (steep, scramble (tagged))', or 'level unknown (no elevation data)'."""
    if not rating or rating.get("level") is None:
        return "level unknown (no elevation data)"
    reasons = rating.get("reasons") or []
    return f"level {rating['level']}" + (f" ({', '.join(reasons)})" if reasons else "")


LEVEL_ORDER = ("easy", "moderate", "hard", "strenuous")


def hardest_day(days: list[dict]) -> str | None:
    """'Hardest day: Day 3 (level moderate, the most gain of the moderate days).' Computed here, not by the model."""
    rated = [d for d in days if (d.get("rating") or {}).get("level")]
    if not days or len(rated) < len(days):
        known = [d for d in days if d["gain_ft"] is not None]
        if not known or len(known) < len(days):
            return None
        top = max(known, key=lambda d: d["gain_ft"])
        return f"Hardest day: Day {top['index']} (the most gain; levels unknown)."
    top_level = max(LEVEL_ORDER.index(d["rating"]["level"]) for d in days)
    same = [d for d in days if LEVEL_ORDER.index(d["rating"]["level"]) == top_level]
    top = max(same, key=lambda d: d["gain_ft"] or 0)
    level = LEVEL_ORDER[top_level]
    why = f"the most gain of the {level} days" if len(same) > 1 else "the only one at that level"
    return f"Hardest day: Day {top['index']} (level {level}, {why})."


def _trail_order(c: dict) -> str:
    out = []
    for s in c["segments"]:
        if not out or out[-1] != s["label"]:
            out.append(s["label"])
    return " > ".join(out)


def _title(kind: str, name: str | None) -> str:
    return kind_label(kind) + (f": {name}" if name else "")


def _status(kind: str) -> str:
    if kind in CAMP_KINDS:
        return "existence and status unknown"
    if kind.startswith("usfs"):
        return "status unknown"
    return "water presence unknown"


def _capped(items: list[str], cap: int) -> list[str]:
    return items[:cap] + ([f"(and {len(items) - cap} more)"] if len(items) > cap else [])


def fact_sheet(run: dict, n: int, trails: list[str], starts: list[dict]) -> str:
    c = run["candidates"][n - 1]
    lines = [settings_text(run["request"], starts), ""]
    lines.append(f"Route (candidate {n} of {len(run['candidates'])}, {c['kind']}): {mi(c['length_m'])}, "
                 f"gain {ft(c['gain_ft'])}, loss {ft(c['loss_ft'])}, road {mi(c['road_m'])}, walked twice {mi(c['repeated_m'])}.")
    lines.append(f"Trails in order: {_trail_order(c)}.")
    p = c.get("plan")
    if "level" in c:  # runs saved before levels existed have none
        lines.append(f"Trip level: {c['level'] or 'unknown'} (a trip is as hard as its hardest day)."
                     if p else f"Trip level: {level_text(c.get('rating'))[6:]} (a one-day hike).")
    if not p:
        lines.append("Days: not planned (no nights given).")
    else:
        for d in p["days"]:
            gain = f"gain {ft(d['gain_ft'])}, loss {ft(d['loss_ft'])}" if d["gain_ft"] is not None else "gain unknown, loss unknown"
            level = f", {level_text(d.get('rating'))}" if "rating" in d else ""
            lines.append(f"Day {d['index']}: {d['start_label']} to {d['end_label']}, {mi(d['length_m'])}, {gain}{level}.")
        for cp in p["camps"]:
            how = "pinned by you" if cp["pinned"] else "at a known campsite" if cp["site"] else "placed for mileage"
            near = (f"{len(cp['nearby'])} known campsite(s) within {NEARBY_FT:,} ft" if cp["nearby"]
                    else f"no known campsite within {NEARBY_FT:,} ft")
            w = cp.get("nearest_water")
            water = (f"nearest water: {_title(w['kind'], w['name'])}, {ft(w['distance_m'] * FT_PER_M)} away (presence unknown)"
                     if w else "nearest water: none listed")
            lines.append(f"Camp {cp['index']}: mile {cp['dist_m'] / M_PER_MI:.2f}, on {cp['on_name'] or 'an unnamed trail'}, "
                         f"{how}, location unverified; {near}; {water}.")
        hard = hardest_day(p["days"])
        if hard:
            lines.append(hard)
        lines += [f"Engine note: {x}" for x in p["notes"]]
        lines += [f"Constraint not met: {x}" for x in p["violations"]]
        if c.get("suggestion"):
            lines.append(f"Engine suggestion: {c['suggestion']['text']}")
    lines += [f"Route note: {x}" for x in c["notes"]]
    feats = [f"- mile {f['dist_m'] / M_PER_MI:.2f}, day {f.get('day') or '-'}: {_title(f['kind'], f.get('name'))}, "
             f"{ft(f['off_m'] * FT_PER_M)} off the route ({_status(f['kind'])})" for f in c["features"]]
    lines.append("Campsites and water along the route (map features only):" if feats else "Campsites and water along the route: none listed.")
    lines += _capped(feats, MAX_FEATURES)
    lines.append("Candidates are ranked by penalty (lower is better): miles outside your ranges, road miles, miles walked "
                 "twice, and day-split problems.")
    lines += [f"Candidate {o['index']}: {o['kind']}, {mi(o['length_m'])}, gain {ft(o['gain_ft'])}, road {mi(o['road_m'])}, "
              f"penalty {o['penalty']:.2f}" for o in run["candidates"]]
    lines.append("Not checked by this tool: " + "; ".join(run["known_gaps"]) + ".")
    lines.append("Trail names in this region: " + "; ".join(_capped(sorted(set(trails)), MAX_NAMES)))
    lines.append("Trailheads you can start from: " + "; ".join(s["name"] for s in starts))
    return "\n".join(lines)
