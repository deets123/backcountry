"""The generate workflow, shared by the CLI and the API.

GenerateRequest validates the user's settings; plan_trip resolves the start, runs the
loop search, and attaches elevation, day plans and along-route features to every
candidate. Both callers then only format and store the result.
"""

from dataclasses import dataclass, field

from backcountry import ingest
from backcountry.difficulty import LEVEL_TARGETS, LEVELS
from backcountry.elevation import densify
from backcountry.features import along_route
from backcountry.loops import avoided_keys, search
from backcountry.route import add_elevation, resolve_point
from backcountry.scoring import M_PER_MI, Weights
from backcountry.split import resolve_pins, split_route

KINDS = {"loop": ("loop",), "lollipop": ("lollipop",), "any": ("loop", "lollipop")}
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
                "max_grade_pct": float(grade) if grade is not None else None, "targeted": True}


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
            raise RequestError("type", "type must be loop, lollipop, or any")
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
        )

    def to_dict(self) -> dict:
        return {
            "trailhead": self.trailhead, "total": list(self.total), "nights": self.nights,
            "daily": list(self.daily) if self.daily else None, "max_gain_day": self.max_gain_day, "type": self.type,
            "count": self.count, "no_roads": self.no_roads, "road_weight": self.road_weight,
            "repeat_weight": self.repeat_weight, "camps": list(self.camps), "camps_at_sites": self.camps_at_sites,
            "must_visit": list(self.must_visit), "avoid": list(self.avoid), "direction": self.direction,
            "days": [t.to_dict() for t in self.days], "level": self.level,
        }

    def targets_for(self, ndays: int) -> list[dict]:
        """One resolved target per day; days without a target take the trip-wide daily range and gain cap."""
        by_day = {t.day: t for t in self.days}
        return [by_day[i].resolved("backpacking", self) if i in by_day else
                {"day": i, "level": None, "miles": [float(m) for m in self.daily] if self.daily else None,
                 "max_gain_ft": self.max_gain_day, "max_grade_pct": None, "targeted": False}
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


def plan_trip(region, raw, tg, req: GenerateRequest, dem="auto", raw_dir=None) -> PlanResult:
    """Resolve the start, search, and attach elevation/days/features.

    `dem="auto"` loads the cached DEM from `raw_dir` (default: the repo's data/raw); pass a DEM or None to override.
    """
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
    weights = Weights(road=req.road_weight, repeat=req.repeat_weight)
    dem = ingest.load_dem(region, raw_dir=raw_dir) if isinstance(dem, str) and dem == "auto" else dem
    sites = ingest.camp_sites(raw)
    water = ingest.water_features(raw)
    campgrounds = usfs_campgrounds(region, raw_dir)

    def elevate(c):
        if dem is not None:
            add_elevation(c.route, dem, tg.crs)
        else:
            d, _ = densify(c.route.line_utm)  # distance-only profile so days can still be split
            c.route.profile = {"d_m": d, "z_m": [None] * len(d)}
            c.route.notes.append("elevation unknown: no DEM cached (run `backcountry dem`)")

    def evaluate(c):
        elevate(c)
        pins, problems = resolve_pins(c.route, req.camps, sites)
        lo, hi = req.daily_m
        try:
            c.plan = split_route(c.route, req.nights, lo, hi, req.max_gain_day, weights, sites, pins_m=pins,
                                 sites_only=req.camps_at_sites)
        except ValueError as e:  # e.g. more camps pinned than nights
            raise RequestError("camps", str(e))
        for p in problems:
            c.plan.notes.append(f"Pinned {p}")
            c.plan.ok = False
        c.features = along_route(c.route, sites, water, campgrounds, c.plan)
        return c.plan.penalty + 100.0 * len(problems)

    lo, hi = req.total_m
    report = search(tg, start["node"], lo, hi, kinds=req.kinds, count=req.count, no_roads=req.no_roads,
                    weights=weights, evaluate=evaluate if req.nights is not None else None,
                    avoid=req.avoid, must_visit=musts, direction=req.direction)
    if req.nights is None:
        for c in report["candidates"]:
            elevate(c)
            c.features = along_route(c.route, sites, water, campgrounds)
    return PlanResult(report=report, start=start, request=req)
