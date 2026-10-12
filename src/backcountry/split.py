"""Day splitting (M5).

Given a route and a number of nights, choose camp points along it so each
day lands in the daily mileage range and under the gain cap. Dynamic
programming over candidate points (about every 0.1 mi along the route)
minimizes: miles outside the daily range, gain over the cap, and imbalance
between days. If no split satisfies everything, the best one is returned
and every violated constraint is named.

Camp points are positions on the route line (source geometry), labeled
`camp_location: unverified`. Nearby OSM camp sites are listed with their
source; nothing here says a camp is legal or has water.

Camps can be pinned (`pins_m`, distances along the route): pinned camps
stay put and the planner places the rest. `sites_only` restricts camps to
known camp sites lying on the route, for areas with designated sites.
"""

from dataclasses import dataclass, field

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point

from backcountry.elevation import FT_PER_M, cumulative_gain_loss, smooth
from backcountry.route import Route
from backcountry.scoring import M_PER_MI, Weights

CANDIDATE_STEP_M = 150.0  # candidate camp points about this far apart (~0.1 mi)
NEARBY_CAMP_M = 500.0  # list OSM camp sites within this distance of a camp point (~0.3 mi)
SITE_ON_ROUTE_M = 200.0  # a known site this close to the route counts as "on" it (sites_only mode)


@dataclass
class Day:
    index: int
    start_m: float
    end_m: float
    length_m: float
    gain_ft: float | None
    loss_ft: float | None
    start_label: str
    end_label: str


@dataclass
class Camp:
    index: int
    dist_m: float
    lat: float
    lon: float
    on_source: str
    on_name: str | None
    nearby: list[dict] = field(default_factory=list)
    note: str = "camp_location: unverified"
    pinned: bool = False
    site: str | None = None  # source of the known site this camp was placed at (sites_only mode)
    nearest_water: dict | None = None  # nearest OSM water feature (presence unknown), set by features.along_route


@dataclass
class DayPlan:
    nights: int
    daily_min_m: float
    daily_max_m: float
    max_gain_ft: float | None
    days: list[Day]
    camps: list[Camp]
    ok: bool
    violations: list[str]
    notes: list[str]
    penalty: float


def split_route(route: Route, nights: int, daily_min_m: float, daily_max_m: float, max_gain_ft: float | None = None,
                weights: Weights = Weights(), camp_sites=(), pins_m=(), sites_only: bool = False) -> DayPlan:
    if route.profile is None:
        raise ValueError("route has no profile; call add_elevation first (a DEM is needed even for distance-only splits)")
    d = np.asarray(route.profile["d_m"], dtype=float)
    ndays = nights + 1
    notes = []
    pins = sorted(float(p) for p in pins_m)
    if len(pins) > nights:
        raise ValueError(f"{len(pins)} camps pinned but only {nights} night(s)")
    if any(not 0 < p < d[-1] for p in pins):
        raise ValueError(f"pinned camps must lie strictly inside the route (0 to {d[-1] / M_PER_MI:.2f} mi)")
    gain_known = route.gain_ft is not None
    if gain_known:
        G, L = cumulative_gain_loss(smooth(np.array(route.profile["z_m"], dtype=float)))
        G, L = G * FT_PER_M, L * FT_PER_M
    else:
        G = L = None
        notes.append("elevation unknown along this route, so days were split by distance only and gain is not checked")

    total_m = d[-1]
    if not ndays * daily_min_m <= total_m <= ndays * daily_max_m:
        notes.append(f"This route can't fit {ndays} days of {daily_min_m / M_PER_MI:.1f}-{daily_max_m / M_PER_MI:.1f} mi: "
                     f"it is {total_m / M_PER_MI:.2f} mi, and {ndays} such days need between "
                     f"{ndays * daily_min_m / M_PER_MI:.1f} and {ndays * daily_max_m / M_PER_MI:.1f} mi. "
                     "The split below is the least-bad one.")

    # Candidate boundaries: both ends, pinned camps, and either known sites on the route (sites_only)
    # or points every ~CANDIDATE_STEP_M along the profile.
    site_at = {}  # candidate distance -> site source
    if sites_only:
        for s in _sites_on_route(route, camp_sites):
            site_at.setdefault(s["dist_m"], s["source"])
        if len(site_at) + len(pins) < nights:
            notes.append(f"Designated-site mode: no known camp site (or too few) lies within {SITE_ON_ROUTE_M:.0f} m of this "
                         f"route ({len(site_at)} found, {nights} needed), so camps were placed by mileage instead.")
            sites_only = False
    if sites_only:
        cd = np.array(sorted({0.0, float(d[-1]), *pins, *site_at}))
    else:
        stride = max(1, int(round(CANDIDATE_STEP_M / max(d[1] - d[0], 1e-9)))) if len(d) > 1 else 1
        cd = np.array(sorted({*d[::stride].tolist(), float(d[-1]), *pins}))
    cg = np.interp(cd, d, G) if gain_known else None
    cl = np.interp(cd, d, L) if gain_known else None
    P = len(cd)
    target = total_m / ndays
    # A day may not pass over a pinned camp: its start must be at or after the last pin before its end.
    pin_pos = {int(np.argmin(np.abs(cd - p))) for p in pins}
    lower = np.zeros(P, dtype=int)
    last = 0
    for j in range(P):
        lower[j] = last
        if j in pin_pos:
            last = j

    inf = float("inf")
    dp = np.full((ndays + 1, P), inf)
    back = np.zeros((ndays + 1, P), dtype=int)
    dp[0, 0] = 0.0
    for k in range(1, ndays + 1):
        last = k == ndays
        for j in (range(P - 1, P) if last else range(1, P - 1)):
            lo_i = lower[j]
            prev = dp[k - 1, lo_i:j]
            if not np.isfinite(prev).any():
                continue
            m = cd[j] - cd[lo_i:j]
            cost = (weights.daily_out_of_range * (np.maximum(0.0, daily_min_m - m) + np.maximum(0.0, m - daily_max_m))
                    + weights.imbalance * np.abs(m - target)) / M_PER_MI
            if gain_known and max_gain_ft is not None:
                cost = cost + weights.gain_over_cap * np.maximum(0.0, cg[j] - cg[lo_i:j] - max_gain_ft) / 1000.0
            total = prev + cost
            b = int(np.argmin(total))
            dp[k, j], back[k, j] = total[b], lo_i + b

    if not np.isfinite(dp[ndays, P - 1]):
        raise ValueError("no day split is possible with these pins and candidate points")
    bounds = [P - 1]
    for k in range(ndays, 0, -1):
        bounds.append(int(back[k, bounds[-1]]))
    bounds = bounds[::-1]  # candidate indices of day boundaries: 0, camp1, ..., P-1

    days, violations = [], []
    for k in range(ndays):
        i, j = bounds[k], bounds[k + 1]
        length = cd[j] - cd[i]
        gain = (cg[j] - cg[i]) if gain_known else None
        loss = (cl[j] - cl[i]) if gain_known else None
        days.append(Day(k + 1, cd[i], cd[j], length, gain, loss,
                        "Start" if k == 0 else f"Camp {k}", "End" if k == ndays - 1 else f"Camp {k + 1}"))
        if length < daily_min_m:
            violations.append(f"Day {k + 1}: {length / M_PER_MI:.2f} mi is under the {daily_min_m / M_PER_MI:.2f} mi daily minimum")
        if length > daily_max_m:
            violations.append(f"Day {k + 1}: {length / M_PER_MI:.2f} mi is over the {daily_max_m / M_PER_MI:.2f} mi daily max")
        if gain is not None and max_gain_ft is not None and gain > max_gain_ft:
            violations.append(f"Day {k + 1}: {gain:,.0f} ft of climbing is over the {max_gain_ft:,.0f} ft daily gain cap")

    camps = []
    for k in range(nights):
        camp = _camp(route, k + 1, days[k].end_m, camp_sites)
        camp.pinned = bounds[k + 1] in pin_pos
        camp.site = site_at.get(float(cd[bounds[k + 1]]))
        camps.append(camp)
    ok = not violations and not (sites_only is False and any("Designated-site mode" in n for n in notes))
    return DayPlan(nights, daily_min_m, daily_max_m, max_gain_ft, days, camps, ok=ok,
                   violations=violations, notes=notes, penalty=float(dp[ndays, P - 1]))


def _sites_on_route(route: Route, camp_sites) -> list[dict]:
    """Known camp sites within SITE_ON_ROUTE_M of the route, with their position along it."""
    if not camp_sites:
        return []
    to_utm = Transformer.from_crs("EPSG:4326", route.crs, always_xy=True)
    out = []
    for s in camp_sites:
        pt = Point(*to_utm.transform(s["lon"], s["lat"]))
        off = route.line_utm.distance(pt)
        if off <= SITE_ON_ROUTE_M:
            dist = float(route.line_utm.project(pt))
            if 0 < dist < route.line_utm.length:
                out.append({**s, "dist_m": dist, "off_m": off})
    return sorted(out, key=lambda s: s["dist_m"])


def _camp(route: Route, index: int, dist_m: float, camp_sites) -> Camp:
    pt = route.line_utm.interpolate(dist_m)
    to_ll = Transformer.from_crs(route.crs, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(pt.x, pt.y)
    # Which source edge the point lies on.
    cum, on = 0.0, route.edges[-1]
    for e in route.edges:
        if cum + e["length_m"] >= dist_m - 1e-6:
            on = e
            break
        cum += e["length_m"]
    nearby = []
    if camp_sites:
        to_utm = Transformer.from_crs("EPSG:4326", route.crs, always_xy=True)
        for s in camp_sites:
            x, y = to_utm.transform(s["lon"], s["lat"])
            dist = pt.distance(Point(x, y))
            if dist <= NEARBY_CAMP_M:
                nearby.append({**{k: s[k] for k in ("source", "kind", "name")}, "distance_m": dist})
        nearby.sort(key=lambda n: n["distance_m"])
    return Camp(index, dist_m, lat, lon, on["source"], on["name"], nearby)


PIN_SNAP_M = 200.0


def resolve_pins(route: Route, specs, camp_sites=()) -> tuple[list[float], list[str]]:
    """Turn --camp specs into distances along this route.

    A spec is miles in ("6.5"), "lat,lon" (snapped to the route within PIN_SNAP_M), or a camp site
    source like "osm:node/123". Specs that don't apply to this route come back as problems, not pins.
    """
    import re

    to_utm = Transformer.from_crs("EPSG:4326", route.crs, always_xy=True)
    by_source = {s["source"]: s for s in camp_sites}
    pins, problems = [], []
    for spec in specs:
        spec = str(spec).strip()
        if m := re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", spec):
            lat, lon = float(m[1]), float(m[2])
        elif spec.startswith("osm:"):
            site = by_source.get(spec)
            if site is None:
                problems.append(f"camp {spec}: unknown camp site (not in the OSM data for this region)")
                continue
            lat, lon = site["lat"], site["lon"]
        else:
            try:
                miles = float(spec.lower().removesuffix("mi"))
            except ValueError:
                problems.append(f"camp {spec!r}: not miles, lat,lon, or an osm:node/... camp site")
                continue
            dist = miles * M_PER_MI
            if not 0 < dist < route.length_m:
                problems.append(f"camp at {miles:g} mi is beyond the end of this {route.length_m / M_PER_MI:.2f} mi route")
                continue
            pins.append(dist)
            continue
        pt = Point(*to_utm.transform(lon, lat))
        off = route.line_utm.distance(pt)
        if off > PIN_SNAP_M:
            problems.append(f"camp {spec} is {off / M_PER_MI:.2f} mi from this route (limit {PIN_SNAP_M:.0f} m)")
            continue
        pins.append(float(route.line_utm.project(pt)))
    return pins, problems
