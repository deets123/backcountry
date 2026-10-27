"""The generate workflow, shared by the CLI and the API.

GenerateRequest validates the user's settings; plan_trip resolves the start, runs the
loop search, and attaches elevation, day plans and along-route features to every
candidate. Both callers then only format and store the result.
"""

import math
import re
import time
from dataclasses import dataclass, field, replace

from backcountry import ingest
from backcountry.destination import check_ref
from backcountry.difficulty import LEVEL_TARGETS, LEVELS, level_index, rate_stretch, route_terrain
from backcountry.elevation import densify
from backcountry.features import along_route
from backcountry.loops import Candidate, avoided_keys, search
from backcountry.route import add_elevation, resolve_point
from backcountry.scoring import M_PER_MI, Weights
from backcountry.split import resolve_pins, split_route

KINDS = {"loop": ("loop",), "lollipop": ("lollipop",), "out_and_back": ("out_and_back",), "one_way": ("one_way",),
         "any": ("loop", "lollipop", "out_and_back")}  # one way needs a second car or a shuttle: only when chosen
LEVEL_WORDS = "easy, moderate, hard or strenuous"


class RequestError(ValueError):
    """A bad setting; `field` names it and the message reuses the CLI wording."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def parse_range(value, field: str) -> tuple[float, float]:
    """'18-26' or [18, 26] -> (18.0, 26.0), with 0 < lo < hi."""
    try:
        if isinstance(value, str):
            lo, hi = (float(x) for x in value.split("-"))
        else:
            lo, hi = (float(x) for x in value)
        if not 0 < lo < hi:
            raise ValueError
    except (ValueError, TypeError):
        raise RequestError(field, f"{field} must look like 18-26 (miles, low-high); got {value!r}")
    return lo, hi


def _level(value, field: str, prefix: str = "") -> str | None:
    if value is None or value in LEVELS:
        return value
    raise RequestError(field, f"{prefix}level must be {LEVEL_WORDS}")


def _positive(data: dict, key: str, prefix: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RequestError("days", f"{prefix}{key} must be a number above 0; got {value!r}")
    return float(value)


def _mi(x: float) -> str:
    return f"{x:g}"


@dataclass
class DayTarget:
    """One day's targets. Explicit numbers win over the level's table values, which win over the trip's."""

    day: int
    level: str | None = None
    miles: tuple[float, float] | None = None
    max_gain_ft: float | None = None
    max_grade_pct: float | None = None

    @classmethod
    def from_dict(cls, data) -> "DayTarget":
        if not isinstance(data, dict) or not _is_int(data.get("day")):
            raise RequestError("days", f"each day target needs a day number; got {data!r}")
        day = data["day"]
        prefix = f"day {day}: "
        if day < 1:
            raise RequestError("days", f"{prefix}days are numbered from 1")
        miles = None
        if data.get("miles") is not None:
            try:
                miles = parse_range(data["miles"], "miles")
            except RequestError:
                raise RequestError("days", f"{prefix}miles must look like 8-12 (low-high); got {data['miles']!r}")
        return cls(day=day, level=_level(data.get("level"), "days", prefix), miles=miles,
                   max_gain_ft=_positive(data, "max_gain_ft", prefix), max_grade_pct=_positive(data, "max_grade_pct", prefix))

    def to_dict(self) -> dict:
        return {"day": self.day, "level": self.level, "miles": list(self.miles) if self.miles else None,
                "max_gain_ft": self.max_gain_ft, "max_grade_pct": self.max_grade_pct}

    def resolved(self, scale: str, trip: "GenerateRequest") -> dict:
        table = LEVEL_TARGETS[scale][self.level] if self.level else {}
        miles = self.miles or table.get("miles") or trip.daily
        gain = self.max_gain_ft if self.max_gain_ft is not None else table.get("max_gain_ft", trip.max_gain_day)
        grade = self.max_grade_pct if self.max_grade_pct is not None else table.get("max_grade_pct")
        return {"day": self.day, "level": self.level, "miles": [float(m) for m in miles] if miles else None,
                "max_gain_ft": float(gain) if gain is not None else None,
                "max_grade_pct": float(grade) if grade is not None else None, "targeted": True,
                "explicit": [k for k in ("miles", "max_gain_ft", "max_grade_pct") if getattr(self, k) is not None],
                # numbers that came from the trip-wide settings (no number of the day's own, no level): still checked
                "from_trip": [k for k, own, tab in (("miles", self.miles, table.get("miles")),
                                                    ("max_gain_ft", self.max_gain_ft, table.get("max_gain_ft")))
                              if own is None and tab is None]}


def _day_targets(data: dict, nights, daily, total) -> list[DayTarget]:
    raw = data.get("days") or []
    if not isinstance(raw, list):
        raise RequestError("days", f"days must be a list of day targets; got {raw!r}")
    days = sorted((DayTarget.from_dict(d) for d in raw), key=lambda t: t.day)
    if not days:
        return []
    if nights is None:
        raise RequestError("days", "per-day targets need nights")
    for a, b in zip(days, days[1:]):
        if a.day == b.day:
            raise RequestError("days", f"day {a.day} is set twice")
    if days[-1].day > nights + 1:
        raise RequestError("days", f"day {days[-1].day}: this trip has {nights + 1} days")
    by_day = {t.day: t for t in days}
    ranges = [(by_day[i].miles or (LEVEL_TARGETS["backpacking"][by_day[i].level]["miles"] if by_day[i].level else None))
              if i in by_day else None for i in range(1, nights + 2)]
    ranges = [r or daily for r in ranges]
    lo, hi = sum(r[0] for r in ranges), sum(r[1] for r in ranges)
    if lo > total[1] or hi < total[0]:
        raise RequestError("days", f"the day targets add up to {_mi(lo)}-{_mi(hi)} mi, outside the total "
                                   f"{_mi(total[0])}-{_mi(total[1])} mi")
    return days


@dataclass
class GenerateRequest:
    trailhead: str
    total: tuple[float, float]
    nights: int | None = None
    daily: tuple[float, float] | None = None
    max_gain_day: float | None = None
    type: str = "any"
    count: int = 3
    no_roads: bool = False
    road_weight: float = 1.0
    repeat_weight: float = 0.5
    camps: list[str] = field(default_factory=list)
    camps_at_sites: bool = False
    must_visit: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    direction: str | None = None
    days: list[DayTarget] = field(default_factory=list)
    level: str | None = None  # trip-level filter: keep only loops whose hardest day is this level
    destination: str | None = None  # a searched place's OSM ref: every trip goes to and through it
    finish: str | None = None  # one way: where it ends (a trailhead id, an OSM node id, or lat,lon)

    @classmethod
    def from_dict(cls, data: dict) -> "GenerateRequest":
        if not data.get("trailhead"):
            raise RequestError("trailhead", "trailhead is required: a trailhead id, an OSM node id, or lat,lon")
        total = parse_range(data.get("total"), "total")
        nights = data.get("nights")
        if nights is not None and (not _is_int(nights) or nights < 1):
            raise RequestError("nights", "nights must be a whole number of at least 1")
        daily = parse_range(data["daily"], "daily") if data.get("daily") is not None else None
        if nights is not None and daily is None:
            raise RequestError("daily", "nights needs daily (e.g. 6-11)")
        type_ = data.get("type") or "any"
        if type_ not in KINDS:
            raise RequestError("type", "type must be loop, lollipop, out_and_back, one_way or any")
        if type_ == "one_way" and not data.get("finish"):
            raise RequestError("finish", "A one-way trip needs a finish trailhead.")
        direction = data.get("direction") or None
        if direction not in (None, "cw", "ccw"):
            raise RequestError("direction", "direction must be cw or ccw")
        count = data.get("count", 3)
        if not _is_int(count) or count < 1:
            raise RequestError("count", "count must be a whole number of at least 1")
        return cls(
            trailhead=str(data["trailhead"]), total=total, nights=nights, daily=daily,
            max_gain_day=_number(data, "max_gain_day", None), type=type_, count=count,
            no_roads=_flag(data, "no_roads"), road_weight=_number(data, "road_weight", 1.0),
            repeat_weight=_number(data, "repeat_weight", 0.5), camps=_strings(data, "camps"),
            camps_at_sites=_flag(data, "camps_at_sites"), must_visit=_strings(data, "must_visit"),
            avoid=_strings(data, "avoid"), direction=direction,
            days=_day_targets(data, nights, daily, total), level=_level(data.get("level"), "level"),
            destination=check_ref(data["destination"]) if data.get("destination") is not None else None,
            finish=str(data["finish"]) if type_ == "one_way" else None,
        )

    def to_dict(self) -> dict:
        return {
            "trailhead": self.trailhead, "total": list(self.total), "nights": self.nights,
            "daily": list(self.daily) if self.daily else None, "max_gain_day": self.max_gain_day, "type": self.type,
            "count": self.count, "no_roads": self.no_roads, "road_weight": self.road_weight,
            "repeat_weight": self.repeat_weight, "camps": list(self.camps), "camps_at_sites": self.camps_at_sites,
            "must_visit": list(self.must_visit), "avoid": list(self.avoid), "direction": self.direction,
            "days": [t.to_dict() for t in self.days], "level": self.level, "destination": self.destination,
            "finish": self.finish,
        }

    def targets_for(self, ndays: int) -> list[dict]:
        """One resolved target per day; days without a target take the trip-wide daily range and gain cap."""
        by_day = {t.day: t for t in self.days}
        return [by_day[i].resolved("backpacking", self) if i in by_day else
                {"day": i, "level": None, "miles": [float(m) for m in self.daily] if self.daily else None,
                 "max_gain_ft": self.max_gain_day, "max_grade_pct": None, "targeted": False, "explicit": [],
                 "from_trip": ["miles", "max_gain_ft"]}
                for i in range(1, ndays + 1)]

    @property
    def kinds(self) -> tuple:
        return KINDS[self.type]

    @property
    def total_m(self) -> tuple[float, float]:
        return self.total[0] * M_PER_MI, self.total[1] * M_PER_MI

    @property
    def daily_m(self) -> tuple[float, float] | None:
        return (self.daily[0] * M_PER_MI, self.daily[1] * M_PER_MI) if self.daily else None


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(data: dict, key: str, default):
    """A JSON number (or missing -> default). Strings and null are errors, named by field."""
    if key not in data or (data[key] is None and default is None):
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError(key, f"{key} must be a number; got {value!r}")
    return float(value)


def _flag(data: dict, key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise RequestError(key, f"{key} must be true or false; got {value!r}")
    return value


def _strings(data: dict, key: str) -> list[str]:
    value = data.get(key) or []
    if not isinstance(value, list):
        raise RequestError(key, f"{key} must be a list of strings; got {value!r}")
    return [str(v) for v in value]


def candidate_level(c) -> str | None:
    """The trip's level: its hardest day's (a day hike: the whole route's). None if any day is unknown."""
    levels = [d.rating["level"] if d.rating else None for d in c.plan.days] if c.plan else [(c.rating or {}).get("level")]
    return None if not levels or None in levels else max(levels, key=LEVELS.index)


def _relaxations(t: DayTarget, got: str | None):
    """Single setting changes for one day's target, in the order they're tried: (new target, wording)."""
    n = t.day
    if t.miles:
        lo, hi = t.miles
        yield replace(t, miles=(lo, hi + 1)), f"Allowing day {n} up to {_mi(hi + 1)} mi would fix it."
        if lo - 1 > 0:
            yield replace(t, miles=(lo - 1, hi)), f"Allowing day {n} down to {_mi(lo - 1)} mi would fix it."
    if t.max_gain_ft is not None:
        gain = t.max_gain_ft + 500
        yield replace(t, max_gain_ft=gain), f"Allowing day {n} up to {gain:,.0f} ft of climbing would fix it."
    if t.max_grade_pct is not None:
        grade = t.max_grade_pct + 5
        yield replace(t, max_grade_pct=grade), f"Allowing day {n} up to {_mi(grade)}% grade would fix it."
    if t.level and got and got != t.level:
        step = level_index(t.level) + (1 if level_index(got) > level_index(t.level) else -1)
        for level in dict.fromkeys([LEVELS[step], got]):  # one step toward what it came out, then what it came out
            yield replace(t, level=level), f"Setting day {n} to {level} would fix it."


def suggest_fix(c, req: "GenerateRequest", resplit) -> dict | None:
    """One setting change that clears every miss on this candidate's days, re-checked by re-splitting.

    `resplit(request)` returns the DayPlan for this candidate's route under another request. Only days with
    a target are relaxed; returns {"text", "days"} (the day targets with the change), or None if no single
    change works. Nothing is suggested that wasn't re-checked.
    """
    plan = c.plan
    if plan is None or not plan.violations or not req.days:
        return None
    missed = sorted({int(m[1]) for v in plan.violations if (m := re.match(r"Day (\d+)", v))})
    by_day = {t.day: t for t in req.days}
    for n in missed:
        if n not in by_day:
            continue
        rating = plan.days[n - 1].rating or {}
        for change, text in _relaxations(by_day[n], rating.get("level")):
            days = [(change if t.day == n else t).to_dict() for t in req.days]
            try:
                changed = GenerateRequest.from_dict(req.to_dict() | {"days": days})
            except RequestError:
                continue
            if not resplit(changed).violations:
                return {"text": text, "days": days}
    return None


def _filter_by_level(report: dict, req: "GenerateRequest") -> None:
    """Keep loops whose trip level is req.level; if none, the closest, with a note saying so."""
    cands = report["candidates"]
    match = [c for c in cands if candidate_level(c) == req.level]
    if match:
        report["candidates"] = match[:req.count]
        return
    if cands and all(candidate_level(c) is None for c in cands):
        report["candidates"] = cands[:req.count]
        report["notes"].append("Levels are unknown here (no elevation data), so the trip-level filter wasn't applied.")
        return
    want = level_index(req.level)
    closest = sorted(cands, key=lambda c: (abs(level_index(candidate_level(c)) - want) if candidate_level(c) else 99,
                                           c.penalty))[:req.count]
    report["candidates"] = closest
    if closest:
        levels = ", ".join(dict.fromkeys(candidate_level(c) or "unknown" for c in closest))
        report["notes"].append(f"No loop here comes out {req.level}; showing the closest ({levels}).")


def _empty_report() -> dict:
    return {"candidates": [], "loops_found": 0, "lollipops_found": 0, "out_and_backs_found": 0, "one_ways_found": 0,
            "stems_tried": 0,
            "via_pairs_tried": 0, "scored": 0, "seconds": 0.0, "widened": False, "truncated": False,
            "avoided_edges": 0, "notes": [], "must_visit_notes": [], "partial": False}


def _destination_notes(tg, report: dict, dest, req: "GenerateRequest", evaluate, start: int) -> None:
    """Side-trip notes; if nothing fits the range, the shortest out and back to the destination instead."""
    from backcountry.loops import out_and_backs
    from backcountry.scoring import penalty

    for c in report["candidates"]:
        if c.detour_keys and dest.kind != "trail":
            miles = 2 * sum(c.lengths[k] for k in c.detour_keys) / M_PER_MI
            c.route.notes.append(f"Side trip to {dest.name}, {miles:.1f} mi round trip.")
    if report["candidates"]:
        return
    lo, hi = req.total
    obs = [c for c in out_and_backs(tg, start, 0.0, math.inf, req.no_roads, through=dest.node)
           if c.turnaround == dest.node]
    if not obs:
        return
    short = min(obs, key=lambda c: c.length_m)
    short.penalty = penalty(short.length_m, short.road_m, short.last_resort_m, short.repeated_m, *req.total_m, Weights())
    if evaluate is not None:
        short.penalty += evaluate(short)
    report["candidates"] = [short]
    report["notes"].append(f"No trip from this start reaches {dest.name} within {_mi(lo)}–{_mi(hi)} mi. "
                           f"The shortest out and back to it is {short.length_m / M_PER_MI:.1f} mi.")


@dataclass
class PlanResult:
    report: dict
    start: dict
    request: GenerateRequest


def must_spec(tg, region, text: str) -> dict:
    """A must-visit value: a point (trailhead id, node id, lat,lon) or a trail name."""
    try:
        return {"node": resolve_point(tg, region, text)["node"]}
    except ValueError as point_err:
        if any((d["name"] or "").lower() == text.lower() for *_, d in tg.graph.edges(data=True)):
            return {"name": text}
        raise RequestError("must_visit", f"must_visit {text!r}: not a trail name here, and {point_err}")


def usfs_campgrounds(region, raw_dir=None) -> list[dict]:
    usfs = ingest.load_usfs(region, raw_dir=raw_dir) if region.usfs_crosscheck else None
    feats = (usfs or {"layers": {}})["layers"].get("rec_sites", {}).get("geojson", {}).get("features", [])
    return [{"source": f"usfs:site/{f['properties'].get('site_id')}", "subtype": f["properties"].get("site_subtype"),
             "name": f["properties"].get("public_site_name") or f["properties"].get("site_name"),
             "lat": f["geometry"]["coordinates"][1], "lon": f["geometry"]["coordinates"][0]}
            for f in feats if f["properties"].get("site_subtype") == "CAMPGROUND"]


class _Steps:
    """What plan_trip does to each candidate route: elevation, terrain, days and camps, features, rating. Shared
    with plan_route, so a saved route followed on newer data is planned exactly like a fresh one."""

    def __init__(self, region, raw, tg, req: "GenerateRequest", dem="auto", raw_dir=None):
        self.tg, self.req = tg, req
        self.weights = Weights(road=req.road_weight, repeat=req.repeat_weight)
        self.dem = ingest.load_dem(region, raw_dir=raw_dir) if isinstance(dem, str) and dem == "auto" else dem
        self.sites = ingest.camp_sites(raw)
        self.water = ingest.water_features(raw)
        self.campgrounds = usfs_campgrounds(region, raw_dir)

    def elevate(self, c):
        if self.dem is not None:
            add_elevation(c.route, self.dem, self.tg.crs)
        else:
            d, _ = densify(c.route.line_utm)  # distance-only profile so days can still be split
            c.route.profile = {"d_m": d, "z_m": [None] * len(d)}
            c.route.notes.append("elevation unknown: no DEM cached (run `backcountry dem`)")

    def split(self, c, r: "GenerateRequest", pins):
        lo, hi = r.daily_m
        return split_route(c.route, r.nights, lo, hi, r.max_gain_day, self.weights, self.sites, pins_m=pins,
                           sites_only=r.camps_at_sites, targets=r.targets_for(r.nights + 1) if r.days else None,
                           terrain=c.terrain)

    def evaluate(self, c):
        req = self.req
        self.elevate(c)
        c.terrain = route_terrain(c.route)
        pins, problems = resolve_pins(c.route, req.camps, self.sites)
        c.pins_m = pins
        try:
            c.plan = self.split(c, req, pins)
        except ValueError as e:  # e.g. more camps pinned than nights
            raise RequestError("camps", str(e))
        for p in problems:
            c.plan.notes.append(f"Pinned {p}")
            c.plan.ok = False
        c.features = along_route(c.route, self.sites, self.water, self.campgrounds, c.plan)
        return c.plan.penalty + 100.0 * len(problems)

    def day_hike(self, c):
        self.elevate(c)
        c.terrain = route_terrain(c.route)
        prof = c.route.profile
        c.rating = rate_stretch(prof["d_m"], prof["z_m"], c.terrain, 0.0, prof["d_m"][-1], c.route.gain_ft,
                                "day_hike").to_dict()
        c.features = along_route(c.route, self.sites, self.water, self.campgrounds)

    def suggest(self, c):
        if c.plan is not None:
            c.suggestion = suggest_fix(c, self.req, lambda r, c=c: self.split(c, r, c.pins_m))


def plan_route(region, raw, tg, req: "GenerateRequest", route, kind: str, stem_keys=(), dem="auto",
               raw_dir=None) -> PlanResult:
    """Plan one given route (e.g. a saved trip followed on newer data) the way plan_trip plans a found loop."""
    lengths = {e["key"]: e["length_m"] for e in route.edges}
    c = Candidate(kind=kind, start=route.nodes[0], keys=[e["key"] for e in route.edges], nodes=list(route.nodes),
                  stem_keys=[k for k in stem_keys if k in lengths], route=route, length_m=route.length_m,
                  road_m=route.road_m, last_resort_m=route.last_resort_m,
                  repeated_m=sum(lengths[k] for k in stem_keys if k in lengths), lengths=lengths,
                  finish=route.nodes[-1] if kind == "one_way" else None,
                  turnaround=route.nodes[len(route.nodes) // 2] if kind == "out_and_back" else None)
    steps = _Steps(region, raw, tg, req, dem, raw_dir)
    if req.nights is None:
        steps.day_hike(c)
    else:
        c.penalty = steps.evaluate(c)
    steps.suggest(c)
    report = {"candidates": [c], "loops_found": 0, "lollipops_found": 0, "stems_tried": 0, "via_pairs_tried": 0,
              "scored": 1, "seconds": 0.0, "widened": False, "truncated": False, "avoided_edges": 0, "notes": [],
              "must_visit_notes": [], "followed": True}
    if kind == "one_way":
        report["finish"] = {"node": c.finish, "how": f"osm:node/{c.finish}"}
    return PlanResult(report=report, start={"node": c.start, "how": f"osm:node/{c.start}", "snap_m": 0.0}, request=req)


def _resolve_finish(tg, region, req: "GenerateRequest", start: int) -> tuple[int | None, str | None]:
    """A one-way trip's finish node, checked: on the network, not the start, on the start's piece of it."""
    import networkx as nx

    if req.type != "one_way":
        return None, None
    try:
        found = resolve_point(tg, region, req.finish)
    except ValueError as e:
        raise RequestError("finish", str(e))
    node = found["node"]
    if node == start:
        raise RequestError("finish", "The finish is the same as the start; pick another finish, or a loop.")
    if node not in nx.node_connected_component(tg.graph, start):
        raise RequestError("finish", "The finish isn't on the same trail network as the start.")
    return node, found["how"]


MAX_FINISHES = 5
ONE_WAY_OVERLAP = 0.9  # one-way trips may share this much of their trail with a better one and still be offered
FINISH_SPREAD_M = 800.0  # suggested finishes closer than this (straight line) to a better one are left out
_FINISH_RANK = {"osm_trailhead": 1, "parking_nearby": 2, "trail_road_junction": 3}


def _finish_rank(th: dict) -> int:
    """Region trailheads first, then tagged trailheads, then parking, then where a trail meets a road."""
    if th.get("source") == "yaml":
        return 0
    return min((_FINISH_RANK.get(k, 4) for k in th.get("kinds") or []), default=4)


def suggest_finishes(tg, trailheads: list[dict], start: int, lo: float, hi: float, through: int | None = None,
                     dem=None) -> list[dict]:
    """Up to MAX_FINISHES trailheads (from the region's list) a one-way trip from `start` can end at within
    [lo, hi] meters (via `through` if given): real trailheads before road crossings, then closest to the range's
    middle, leaving out any within FINISH_SPREAD_M of one already picked. Each has its shortest route's miles,
    gain and loss (None without elevation) and the straight line between the two trailheads."""
    import math

    import networkx as nx

    from backcountry.route import routing_graph, walk

    R = routing_graph(tg)
    if start not in R:
        return []
    G = tg.graph
    _, from_start = nx.single_source_dijkstra(R, start, weight="w")
    via = nx.single_source_dijkstra(R, through, weight="w")[1] if through is not None else None
    found, seen = [], set()
    for th in trailheads:
        n = th.get("node")
        if not th.get("in_graph") or n is None or n == start or n in seen or n not in from_start:
            continue
        if via is not None and (through not in from_start or n not in via):
            continue
        seen.add(n)
        nodes = from_start[n] if via is None else from_start[through] + via[n][1:]
        keys = [R[a][b]["key"] for a, b in zip(nodes, nodes[1:])]
        length = sum(G[a][b][k]["length_m"] for (a, b), k in zip(zip(nodes, nodes[1:]), keys))
        if lo <= length <= hi:
            found.append((_finish_rank(th), abs(length - (lo + hi) / 2), th.get("name") or th["id"], th, nodes, keys, length))
    found.sort(key=lambda f: f[:3])
    picked = []
    for f in found:
        n = f[3]["node"]
        if all(math.hypot(G.nodes[n]["x"] - G.nodes[p[3]["node"]]["x"], G.nodes[n]["y"] - G.nodes[p[3]["node"]]["y"])
               >= FINISH_SPREAD_M for p in picked):
            picked.append(f)
        if len(picked) == MAX_FINISHES:
            break
    out = []
    for _, _, _, th, nodes, keys, length in picked:
        route = walk(tg, keys, nodes)
        if dem is not None:
            add_elevation(route, dem, tg.crs)
        out.append({"id": th["id"], "name": th.get("name"), "node": th["node"], "lat": th.get("lat"), "lon": th.get("lon"),
                    "source": th.get("source"), "verified": bool(th.get("verified")), "length_m": round(length, 1),
                    "gain_ft": route.gain_ft, "loss_ft": route.loss_ft,
                    "straight_line_m": round(math.hypot(G.nodes[start]["x"] - G.nodes[th["node"]]["x"],
                                                        G.nodes[start]["y"] - G.nodes[th["node"]]["y"]), 1)})
    return out


def _destination_search(tg, start: int, req: "GenerateRequest", index) -> tuple:
    """(dest, search settings, notes) for a trip to a destination; dest None without one."""
    from backcountry import destination as dmod

    if req.destination is None:
        return None, {}, []
    if index is None:
        raise RequestError("destination", "Destinations need the trail index on this server.")
    dest = dmod.resolve(index, tg, req.destination)
    lo, hi = req.total_m
    if not dmod.reachable_from(tg, start, dest):
        return dest, None, [f"{dest.name} can't be reached from this start on the mapped trails."]
    spur = dmod.spur_m(tg, dest.node)
    must = {"name": dest.trail_name} if dest.kind == "trail" else {"node": dest.node}
    kw = {"keep": lambda c: lo <= c.length_m <= hi and dmod.reached(c.route, dest), "through": dest.node,
          "max_detour_m": hi, "must": must,
          # loops reach a spur's end by a side trip: look for loops that much shorter
          "min_m": max(0.1 * lo, lo - 2 * spur) if spur < hi else lo}
    return dest, kw, [dest.note] if dest.note else []


def plan_trip(region, raw, tg, req: GenerateRequest, dem="auto", raw_dir=None, on_snapshot=None, index=None,
              **search_kw) -> PlanResult:
    """Resolve the start, search, and attach elevation/days/features.

    `dem="auto"` loads the cached DEM from `raw_dir` (default: the repo's data/raw); pass a DEM or None to override.
    `on_snapshot(PlanResult)`: the first trips found, planned like the final ones, while the search goes on
    (see loops.search; `search_kw` passes its `first_s` and `clock`). `index`: the US index connection, needed
    for a destination (its place is looked up there).
    """
    t0 = time.perf_counter()
    try:
        start = resolve_point(tg, region, req.trailhead)
    except ValueError as e:
        raise RequestError("trailhead", str(e))
    musts = [must_spec(tg, region, m) for m in req.must_visit]
    if req.avoid:
        try:
            avoided_keys(tg, req.avoid)
        except ValueError as e:
            raise RequestError("avoid", str(e))
    finish_node, finish_how = _resolve_finish(tg, region, req, start["node"])
    dest, dest_kw, dest_notes = _destination_search(tg, start["node"], req, index)
    steps = _Steps(region, raw, tg, req, dem, raw_dir)
    split_s = [0.0]  # time spent planning days, inside and after the search

    def timed(fn):
        def run(c):
            t = time.perf_counter()
            try:
                return fn(c)
            finally:
                split_s[0] += time.perf_counter() - t
        return run

    def finish(report: dict, timed_day_hike) -> None:
        if req.nights is None:
            for c in report["candidates"]:
                timed_day_hike(c)
        if req.level:
            _filter_by_level(report, req)
        for c in report["candidates"]:
            steps.suggest(c)

    def snapshot(report: dict) -> None:
        finish(report, steps.day_hike)
        on_snapshot(PlanResult(report=report, start=start, request=req))

    t1 = time.perf_counter()
    lo, hi = req.total_m
    evaluate = timed(steps.evaluate) if req.nights is not None else None
    if dest is not None and dest_kw is None:  # can't be reached at all
        report = _empty_report()
    else:
        kw = dict(dest_kw or {})
        if dest is not None:
            musts = musts + [kw.pop("must")]
            lo = kw.pop("min_m")
        # the level filter drops loops after the search, so it asks for more to choose from
        if req.type == "one_way":
            # ways between the same two trailheads share most of their trail: let close variants through
            kw.setdefault("max_overlap", ONE_WAY_OVERLAP)
        report = search(tg, start["node"], lo, hi, kinds=req.kinds, count=req.count * 4 if req.level else req.count,
                        no_roads=req.no_roads, weights=steps.weights, evaluate=evaluate, avoid=req.avoid,
                        must_visit=musts, direction=req.direction, on_snapshot=snapshot if on_snapshot else None,
                        finish=finish_node, **kw, **search_kw)
    if finish_node is not None:
        report["finish"] = {"node": finish_node, "how": finish_how}
    if dest is not None:
        report["destination"] = dest.summary()
        report["notes"] += dest_notes
        if dest_kw is not None:
            _destination_notes(tg, report, dest, req, evaluate, start["node"])
    t2 = time.perf_counter()
    in_search = split_s[0]
    finish(report, timed(steps.day_hike))
    t3 = time.perf_counter()
    # seconds per step, for scripts/measure-speed (day planning is counted once, in day_split)
    report["timings"] = {"resolve": round(t1 - t0, 2), "search": round(max(0.0, t2 - t1 - in_search), 2),
                         "day_split": round(split_s[0], 2),
                         "finish": round(max(0.0, t3 - t2 - (split_s[0] - in_search)), 2)}
    return PlanResult(report=report, start=start, request=req)
