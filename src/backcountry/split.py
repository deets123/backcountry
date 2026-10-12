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
                weights: Weights = Weights(), camp_sites=()) -> DayPlan:
    if route.profile is None:
        raise ValueError("route has no profile; call add_elevation first (a DEM is needed even for distance-only splits)")
    d = np.asarray(route.profile["d_m"], dtype=float)
    ndays = nights + 1
    notes = []
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

    # Candidate boundaries: every ~CANDIDATE_STEP_M along the profile, plus both ends.
    stride = max(1, int(round(CANDIDATE_STEP_M / max(d[1] - d[0], 1e-9)))) if len(d) > 1 else 1
    idx = sorted(set(range(0, len(d), stride)) | {len(d) - 1})
    cd = d[idx]
    cg = G[idx] if gain_known else None
    P = len(idx)
    target = total_m / ndays

    inf = float("inf")
    dp = np.full((ndays + 1, P), inf)
    back = np.zeros((ndays + 1, P), dtype=int)
    dp[0, 0] = 0.0
    for k in range(1, ndays + 1):
        last = k == ndays
        for j in (range(P - 1, P) if last else range(1, P - 1)):
            prev = dp[k - 1, :j]
            if not np.isfinite(prev).any():
                continue
            m = cd[j] - cd[:j]
            cost = (weights.daily_out_of_range * (np.maximum(0.0, daily_min_m - m) + np.maximum(0.0, m - daily_max_m))
                    + weights.imbalance * np.abs(m - target)) / M_PER_MI
            if gain_known and max_gain_ft is not None:
                cost = cost + weights.gain_over_cap * np.maximum(0.0, cg[j] - cg[:j] - max_gain_ft) / 1000.0
            total = prev + cost
            b = int(np.argmin(total))
            dp[k, j], back[k, j] = total[b], b

    bounds = [P - 1]
    for k in range(ndays, 0, -1):
        bounds.append(int(back[k, bounds[-1]]))
    bounds = bounds[::-1]  # candidate indices of day boundaries: 0, camp1, ..., P-1

    days, violations = [], []
    for k in range(ndays):
        i, j = idx[bounds[k]], idx[bounds[k + 1]]
        length = d[j] - d[i]
        gain = (G[j] - G[i]) if gain_known else None
        loss = (L[j] - L[i]) if gain_known else None
        days.append(Day(k + 1, d[i], d[j], length, gain, loss,
                        "Start" if k == 0 else f"Camp {k}", "End" if k == ndays - 1 else f"Camp {k + 1}"))
        if length < daily_min_m:
            violations.append(f"Day {k + 1}: {length / M_PER_MI:.2f} mi is under the {daily_min_m / M_PER_MI:.2f} mi daily minimum")
        if length > daily_max_m:
            violations.append(f"Day {k + 1}: {length / M_PER_MI:.2f} mi is over the {daily_max_m / M_PER_MI:.2f} mi daily max")
        if gain is not None and max_gain_ft is not None and gain > max_gain_ft:
            violations.append(f"Day {k + 1}: {gain:,.0f} ft of climbing is over the {max_gain_ft:,.0f} ft daily gain cap")

    camps = [_camp(route, k + 1, days[k].end_m, camp_sites) for k in range(nights)]
    return DayPlan(nights, daily_min_m, daily_max_m, max_gain_ft, days, camps, ok=not violations,
                   violations=violations, notes=notes, penalty=float(dp[ndays, P - 1]))


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
