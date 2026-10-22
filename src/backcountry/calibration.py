"""Check difficulty levels against trips the user has hiked (tests/calibration/).

A reference track (a GPX the user recorded or exported) only guides which OSM
ways the route follows: the route is rebuilt from graph edges near the track,
and its numbers come from our own elevation data. Nothing from the track's
geometry ends up in the route (CLAUDE.md rule 1).

Calibration areas are region files in tests/calibration/regions/, not regions
in the app. Their data is fetched once with
`backcountry ingest|dem --region <id> --regions-dir tests/calibration/regions`.
"""

from pathlib import Path

import gpxpy
import networkx as nx
import numpy as np
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point

from backcountry import ingest
from backcountry.difficulty import rate_stretch, route_terrain, trip_level
from backcountry.graph import build_graph
from backcountry.region import REPO_ROOT, load_region
from backcountry.route import Route, add_elevation, routing_graph, walk
from backcountry.scoring import M_PER_MI
from backcountry.split import resolve_pins, split_route

CALIBRATION_DIR = REPO_ROOT / "tests" / "calibration"
LOCAL_TRACKS = REPO_ROOT / "tests" / "reference" / "local"
LENGTH_MISMATCH = 0.10  # note it when the rebuilt route differs from the track by more than this share


def route_along_track(tg, track: LineString, step_m: float = 10.0, snap_m: float = 30.0) -> Route:
    """The walk over graph edges that follows `track` (in tg.crs).

    Samples the track every `step_m`, takes the nearest graph node within `snap_m` of each sample, and joins
    consecutive nodes by shortest paths. Raises ValueError naming the gap when two of them aren't connected;
    never draws a line.
    """
    G = tg.graph
    ids = sorted(G.nodes)
    tree = STRtree([Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in ids])
    seq, at = [], []
    for dist in np.arange(0.0, track.length + step_m, step_m):
        pt = track.interpolate(min(dist, track.length))
        hit = tree.query_nearest(pt, max_distance=snap_m, all_matches=False)
        if len(hit) and (not seq or seq[-1] != ids[hit[0]]):
            seq.append(ids[hit[0]])
            at.append(dist)
    if len(seq) < 2:
        raise ValueError(f"the track passes within {snap_m:.0f} m of fewer than two trail junctions")
    R = routing_graph(tg)
    nodes, keys = [seq[0]], []
    for (a, b), da, db in zip(zip(seq, seq[1:]), at, at[1:]):
        try:
            path = nx.shortest_path(R, a, b, weight="w")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            raise ValueError(f"no trail connects mile {da / M_PER_MI:.2f} and mile {db / M_PER_MI:.2f} of the track "
                             f"(osm:node/{a} to osm:node/{b}); the route is not drawn across the gap")
        for u, v in zip(path, path[1:]):
            if len(nodes) >= 2 and nodes[-2] == v and keys[-1] == R[u][v]["key"]:
                nodes.pop()  # an immediate back-and-forth over one edge is snapping noise
                keys.pop()
            else:
                nodes.append(v)
                keys.append(R[u][v]["key"])
    route = walk(tg, keys, nodes)
    if abs(route.length_m - track.length) > LENGTH_MISMATCH * track.length:
        route.notes.append(f"rebuilt route is {route.length_m / M_PER_MI:.2f} mi, the track {track.length / M_PER_MI:.2f} mi")
    return route


def read_track(path: Path, crs: str) -> LineString:
    g = gpxpy.parse(path.read_text())
    pts = [(p.longitude, p.latitude) for t in g.tracks for s in t.segments for p in s.points]
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(*zip(*pts))
    return LineString(list(zip(xs, ys)))


def rate_reference_trip(case: dict, regions_dir: Path = CALIBRATION_DIR / "regions",
                        tracks_dir: Path = LOCAL_TRACKS) -> dict:
    """Rebuild a reference trip from OSM and rate it like the planner would.

    Raises FileNotFoundError when the track or the region's cached data is missing.
    """
    region = load_region(case["region"], regions_dir)
    path = tracks_dir / case["track"]
    if not path.exists():
        raise FileNotFoundError(f"reference track {path} not found (local only, never committed)")
    raw = ingest.load_raw(region)
    dem = ingest.load_dem(region)
    if dem is None:
        raise FileNotFoundError(f"no cached DEM for {region.id}; run `backcountry dem --region {region.id} "
                                f"--regions-dir {regions_dir}`")
    tg = build_graph(raw, region.crs)
    route = add_elevation(route_along_track(tg, read_track(path, region.crs)), dem, region.crs)
    terrain = route_terrain(route)
    d, z = route.profile["d_m"], route.profile["z_m"]
    nights = case.get("nights") or 0
    if nights:
        total = route.length_m
        pins = [m * M_PER_MI for m in case.get("camps_mi") or []]
        if case.get("camps"):  # camps by OSM site id (where the user camped), placed from the data
            pins, problems = resolve_pins(route, case["camps"], ingest.camp_sites(raw))
            if problems:
                raise ValueError(f"{case['id']}: {'; '.join(problems)}")
        plan = split_route(route, nights, 0.2 * total / (nights + 1), total, pins_m=pins)
        stretches = [(day.start_m, day.end_m, day.gain_ft) for day in plan.days]
        scale = "backpacking"
    else:
        stretches, scale = [(0.0, route.length_m, route.gain_ft)], "day_hike"
    ratings = [rate_stretch(d, z, terrain, a, b, g, scale) for a, b, g in stretches]
    days = [{"miles": round(float(b - a) / M_PER_MI, 2), "gain_ft": round(g) if g is not None else None,
             "effort": round(r.effort, 1) if r.effort is not None else None, "level": r.level, "reasons": r.reasons,
             "steep": r.steep and {k: round(v, 1) for k, v in r.steep.items()}, "untagged_share": round(float(r.untagged_share), 2)}
            for (a, b, g), r in zip(stretches, ratings)]
    tags = sorted({f"{k}={v}" for e in route.edges for k, v in (e.get("tags") or {}).items()
                   if k in ("sac_scale", "trail_visibility")})
    return {"id": case["id"], "expected": case["expected"], "got": trip_level(ratings), "days": days,
            "notes": list(route.notes) + [f"route {route.length_m / M_PER_MI:.2f} mi, +{route.gain_ft or 0:,.0f} ft; "
                                          f"terrain tags on the route: {', '.join(tags) or 'none'}"]}
