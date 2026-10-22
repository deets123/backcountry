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

Per-day targets (`targets`, one per day, from GenerateRequest.targets_for)
replace the daily range and gain cap day by day, and can add a steepness cap
and a level: a day set to a level costs `level_miss` per level step it lands
away from it, so the split leans toward days that come out at their level.
Every day carries its difficulty rating (backpacking scale).

Camps can be pinned (`pins_m`, distances along the route): pinned camps
stay put and the planner places the rest. `sites_only` restricts camps to
known camp sites lying on the route, for areas with designated sites.
"""

from dataclasses import dataclass, field

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point

from backcountry.difficulty import RULES, level_index, rate_stretch, window_grades
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
    rating: dict | None = None  # difficulty.Rating.to_dict() on the backpacking scale


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
    targets: list[dict] | None = None  # the per-day targets used, if any


def _pair_levels(cd, cg, d, z, terrain, rules) -> tuple[np.ndarray, np.ndarray]:
    """For every pair of candidate points (i < j): the day's level index and steepest sustained climb (% grade,
    NaN = none), by the same rules as difficulty.rate_stretch, so the split can score levels and grade caps."""
    P = len(cd)
    _, grade, end = window_grades(d, z, rules["steep"]["window_m"])
    grade = np.where(np.isnan(grade), -np.inf, grade)
    start_idx = np.searchsorted(d, cd - 1e-6, "left")
    steep = np.full((P, P), np.nan)
    for j in range(1, P):
        valid = d[end] <= cd[j] + 1e-6
        pmax = int(np.nonzero(valid)[0].max()) if valid.any() else -1
        if pmax < 0:
            continue
        r = np.maximum.accumulate(grade[:pmax + 1][::-1])[::-1]  # r[p] = steepest window starting at or after p
        s = start_idx[:j]
        ok = s <= pmax
        vals = np.full(j, -np.inf)
        vals[ok] = r[s[ok]]
        steep[:j, j] = np.where(np.isfinite(vals) & (vals > 0), vals, np.nan)
    gain = np.maximum(cg[None, :] - cg[:, None], 0.0)
    miles = np.maximum(cd[None, :] - cd[:, None], 0.0) / M_PER_MI
    effort = np.sqrt(gain * 2 * miles)
    hard = level_index("hard")
    # scale is filled in by the caller's rules; split days are backpacking days
    level = np.searchsorted(np.asarray(rules["scales"]["backpacking"]["cutoffs"], dtype=float), effort, "right")
    raise_ = np.nan_to_num(steep, nan=-1.0) >= rules["steep"]["raise_pct"]
    level = np.where(raise_ & (level < hard), level + 1, level)
    tagged = [t for t in terrain if t["reason"]]
    if tagged:
        starts = np.array([t["start_m"] for t in tagged])
        ends = np.array([t["end_m"] for t in tagged])
        # a day [cd_i, cd_j] touches a tagged stretch if one starts before cd_j and ends after cd_i
        max_end = np.array([ends[starts < cd[j]].max() if (starts < cd[j]).any() else -np.inf for j in range(P)])
        level = np.where(max_end[None, :] > cd[:, None], np.maximum(level, hard), level)
    return level, steep


def split_route(route: Route, nights: int, daily_min_m: float, daily_max_m: float, max_gain_ft: float | None = None,
                weights: Weights = Weights(), camp_sites=(), pins_m=(), sites_only: bool = False,
                targets: list[dict] | None = None, terrain: list[dict] | None = None, rules: dict = RULES) -> DayPlan:
    if route.profile is None:
        raise ValueError("route has no profile; call add_elevation first (a DEM is needed even for distance-only splits)")
    d = np.asarray(route.profile["d_m"], dtype=float)
    ndays = nights + 1
    notes = []
    terrain = terrain or []
    if targets is not None and len(targets) != ndays:
        raise ValueError(f"{len(targets)} day targets for {ndays} days")
    # Per-day limits in meters: a day target's numbers, else the trip-wide daily range and gain cap.
    day_lo = [t["miles"][0] * M_PER_MI if t and t["miles"] else daily_min_m for t in (targets or [None] * ndays)]
    day_hi = [t["miles"][1] * M_PER_MI if t and t["miles"] else daily_max_m for t in (targets or [None] * ndays)]
    day_cap = [t["max_gain_ft"] if t else max_gain_ft for t in (targets or [None] * ndays)]
    day_grade = [t["max_grade_pct"] if t else None for t in (targets or [None] * ndays)]
    day_level = [level_index(t["level"]) if t and t["level"] else None for t in (targets or [None] * ndays)]
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
    if targets is not None and not sum(day_lo) <= total_m <= sum(day_hi):
        notes.append(f"This route can't fit your day targets: it is {total_m / M_PER_MI:.2f} mi, and they need between "
                     f"{sum(day_lo) / M_PER_MI:.1f} and {sum(day_hi) / M_PER_MI:.1f} mi. The split below is the least-bad one.")
    elif targets is None and not ndays * daily_min_m <= total_m <= ndays * daily_max_m:
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
    # the imbalance pull: an equal split, or with day targets each day's own midpoint
    day_mid = [(lo + hi) / 2 for lo, hi in zip(day_lo, day_hi)] if targets is not None else [total_m / ndays] * ndays
    pair_level = pair_steep = None
    if gain_known and (any(lv is not None for lv in day_level) or any(g is not None for g in day_grade)):
        pair_level, pair_steep = _pair_levels(cd, cg, d, route.profile["z_m"], terrain, rules)
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
            lo_m, hi_m, cap, grade_cap, lvl = day_lo[k - 1], day_hi[k - 1], day_cap[k - 1], day_grade[k - 1], day_level[k - 1]
            cost = (weights.daily_out_of_range * (np.maximum(0.0, lo_m - m) + np.maximum(0.0, m - hi_m))
                    + weights.imbalance * np.abs(m - day_mid[k - 1])) / M_PER_MI
            if gain_known and cap is not None:
                cost = cost + weights.gain_over_cap * np.maximum(0.0, cg[j] - cg[lo_i:j] - cap) / 1000.0
            if pair_steep is not None and grade_cap is not None:
                over = np.nan_to_num(pair_steep[lo_i:j, j], nan=0.0) - grade_cap
                cost = cost + weights.gain_over_cap * np.maximum(0.0, over)
            if pair_level is not None and lvl is not None:
                cost = cost + weights.level_miss * np.abs(pair_level[lo_i:j, j] - lvl)
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
        rating = rate_stretch(route.profile["d_m"], route.profile["z_m"], terrain, float(cd[i]), float(cd[j]),
                              float(gain) if gain is not None else None, "backpacking", rules)
        days.append(Day(k + 1, cd[i], cd[j], length, gain, loss,
                        "Start" if k == 0 else f"Camp {k}", "End" if k == ndays - 1 else f"Camp {k + 1}",
                        rating=rating.to_dict()))
        violations += _day_violations(k + 1, length, gain, rating, (targets or [None] * ndays)[k],
                                      day_lo[k], day_hi[k], day_cap[k])
        want = (targets or [None] * ndays)[k]
        if want and want["level"] and rating.level is None:
            notes.append(f"Day {k + 1}: level unknown (no elevation), so its {want['level']} target can't be checked")

    camps = []
    for k in range(nights):
        camp = _camp(route, k + 1, days[k].end_m, camp_sites)
        camp.pinned = bounds[k + 1] in pin_pos
        camp.site = site_at.get(float(cd[bounds[k + 1]]))
        camps.append(camp)
    ok = not violations and not (sites_only is False and any("Designated-site mode" in n for n in notes))
    return DayPlan(nights, daily_min_m, daily_max_m, max_gain_ft, days, camps, ok=ok,
                   violations=violations, notes=notes, penalty=float(dp[ndays, P - 1]), targets=targets)


def _day_violations(n: int, length: float, gain, rating, target: dict | None, lo: float, hi: float, cap) -> list[str]:
    """What a day misses. Numbers the user set for the day are worded "your ... target"; a day set only to a
    level is held to the level (the level table's numbers only shape the split), so only a level miss shows."""
    out = []
    targeted = bool(target and target["targeted"])
    mine = set(target.get("explicit", [])) if targeted else set()
    check = (lambda field: field in mine) if targeted else (lambda field: True)
    if check("miles") and length < lo:
        out.append(f"Day {n}: {length / M_PER_MI:.2f} mi is under your {lo / M_PER_MI:.2f} mi target" if mine else
                   f"Day {n}: {length / M_PER_MI:.2f} mi is under the {lo / M_PER_MI:.2f} mi daily minimum")
    if check("miles") and length > hi:
        out.append(f"Day {n}: {length / M_PER_MI:.2f} mi is over your {hi / M_PER_MI:.2f} mi target" if mine else
                   f"Day {n}: {length / M_PER_MI:.2f} mi is over the {hi / M_PER_MI:.2f} mi daily max")
    if check("max_gain_ft") and gain is not None and cap is not None and gain > cap:
        out.append(f"Day {n}: {gain:,.0f} ft of climbing is over your {cap:,.0f} ft target" if mine else
                   f"Day {n}: {gain:,.0f} ft of climbing is over the {cap:,.0f} ft daily gain cap")
    grade_cap = target["max_grade_pct"] if target else None
    if "max_grade_pct" in mine and grade_cap is not None and rating.steep and round(rating.steep["grade_pct"]) > grade_cap:
        s = rating.steep
        out.append(f"Day {n}: {s['grade_pct']:.0f}% grade for {(s['end_m'] - s['start_m']) / M_PER_MI:.1f} mi "
                   f"is over your {grade_cap:g}% target")
    if target and target["level"] and rating.level and rating.level != target["level"]:
        out.append(f"Day {n} came out {rating.level}, not {target['level']}")
    return out


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
                nearby.append({**{k: s.get(k) for k in ("source", "kind", "name", "url", "meta")}, "distance_m": dist})
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
