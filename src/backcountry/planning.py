"""The generate workflow, shared by the CLI and the API.

GenerateRequest validates the user's settings; plan_trip resolves the start, runs the
loop search, and attaches elevation, day plans and along-route features to every
candidate. Both callers then only format and store the result.
"""

from dataclasses import dataclass, field

from backcountry import ingest
from backcountry.elevation import densify
from backcountry.features import along_route
from backcountry.loops import avoided_keys, search
from backcountry.route import add_elevation, resolve_point
from backcountry.scoring import M_PER_MI, Weights
from backcountry.split import resolve_pins, split_route

KINDS = {"loop": ("loop",), "lollipop": ("lollipop",), "any": ("loop", "lollipop")}


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

    @classmethod
    def from_dict(cls, data: dict) -> "GenerateRequest":
        if not data.get("trailhead"):
            raise RequestError("trailhead", "trailhead is required: a trailhead id, an OSM node id, or lat,lon")
        total = parse_range(data.get("total"), "total")
        nights = data.get("nights")
        if nights is not None and (not isinstance(nights, int) or nights < 1):
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
        if not isinstance(count, int) or count < 1:
            raise RequestError("count", "count must be a whole number of at least 1")
        return cls(
            trailhead=str(data["trailhead"]), total=total, nights=nights, daily=daily,
            max_gain_day=data.get("max_gain_day"), type=type_, count=count,
            no_roads=bool(data.get("no_roads", False)), road_weight=float(data.get("road_weight", 1.0)),
            repeat_weight=float(data.get("repeat_weight", 0.5)), camps=[str(c) for c in data.get("camps") or []],
            camps_at_sites=bool(data.get("camps_at_sites", False)),
            must_visit=[str(m) for m in data.get("must_visit") or []], avoid=[str(a) for a in data.get("avoid") or []],
            direction=direction,
        )

    def to_dict(self) -> dict:
        return {
            "trailhead": self.trailhead, "total": list(self.total), "nights": self.nights,
            "daily": list(self.daily) if self.daily else None, "max_gain_day": self.max_gain_day, "type": self.type,
            "count": self.count, "no_roads": self.no_roads, "road_weight": self.road_weight,
            "repeat_weight": self.repeat_weight, "camps": list(self.camps), "camps_at_sites": self.camps_at_sites,
            "must_visit": list(self.must_visit), "avoid": list(self.avoid), "direction": self.direction,
        }

    @property
    def kinds(self) -> tuple:
        return KINDS[self.type]

    @property
    def total_m(self) -> tuple[float, float]:
        return self.total[0] * M_PER_MI, self.total[1] * M_PER_MI

    @property
    def daily_m(self) -> tuple[float, float] | None:
        return (self.daily[0] * M_PER_MI, self.daily[1] * M_PER_MI) if self.daily else None


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


def usfs_campgrounds(region) -> list[dict]:
    usfs = ingest.load_usfs(region) if region.usfs_crosscheck else None
    feats = (usfs or {"layers": {}})["layers"].get("rec_sites", {}).get("geojson", {}).get("features", [])
    return [{"source": f"usfs:site/{f['properties'].get('site_id')}", "subtype": f["properties"].get("site_subtype"),
             "name": f["properties"].get("public_site_name") or f["properties"].get("site_name"),
             "lat": f["geometry"]["coordinates"][1], "lon": f["geometry"]["coordinates"][0]}
            for f in feats if f["properties"].get("site_subtype") == "CAMPGROUND"]


def plan_trip(region, raw, tg, req: GenerateRequest) -> PlanResult:
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
    dem = ingest.load_dem(region)
    sites = ingest.camp_sites(raw)
    water = ingest.water_features(raw)
    campgrounds = usfs_campgrounds(region)

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
        c.plan = split_route(c.route, req.nights, lo, hi, req.max_gain_day, weights, sites, pins_m=pins,
                             sites_only=req.camps_at_sites)
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
