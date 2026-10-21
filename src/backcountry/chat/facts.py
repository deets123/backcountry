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


def _place(value: str) -> str:
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
        "must visit " + (", ".join(_place(v) for v in req.get("must_visit") or []) or "none"),
        "avoid " + (", ".join(_place(v) for v in req.get("avoid") or []) or "none"),
        "camps " + ("pinned at mile " + ", ".join(req["camps"]) if req.get("camps") else "placed by the engine"),
        "camps at known campsites only " + ("yes" if req.get("camps_at_sites") else "no"),
    ]
    return "Current settings: " + "; ".join(parts) + "."


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
    if not p:
        lines.append("Days: not planned (no nights given).")
    else:
        for d in p["days"]:
            gain = f"gain {ft(d['gain_ft'])}, loss {ft(d['loss_ft'])}" if d["gain_ft"] is not None else "gain unknown, loss unknown"
            lines.append(f"Day {d['index']}: {d['start_label']} to {d['end_label']}, {mi(d['length_m'])}, {gain}.")
        for cp in p["camps"]:
            how = "pinned by you" if cp["pinned"] else "at a known campsite" if cp["site"] else "placed for mileage"
            near = (f"{len(cp['nearby'])} known campsite(s) within {NEARBY_FT:,} ft" if cp["nearby"]
                    else f"no known campsite within {NEARBY_FT:,} ft")
            w = cp.get("nearest_water")
            water = (f"nearest water: {_title(w['kind'], w['name'])}, {ft(w['distance_m'] * FT_PER_M)} away (presence unknown)"
                     if w else "nearest water: none listed")
            lines.append(f"Camp {cp['index']}: mile {cp['dist_m'] / M_PER_MI:.2f}, on {cp['on_name'] or 'an unnamed trail'}, "
                         f"{how}, location unverified; {near}; {water}.")
        lines += [f"Engine note: {x}" for x in p["notes"]]
        lines += [f"Constraint not met: {x}" for x in p["violations"]]
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
