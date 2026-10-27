"""Point-to-point routing on the trail graph (M3).

A route is an ordered walk over graph edges. Its geometry is the edges'
source vertices joined end to end: nothing is added, moved, or straightened.

Weights are real distance, except last-resort roads (paved county roads)
count LAST_RESORT_FACTOR times their length so they're used only when
nothing else connects. --no-roads drops every road edge instead.
"""

import math
import re
from dataclasses import dataclass, field

import networkx as nx
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point

from backcountry.elevation import FT_PER_M, line_profile

SNAP_MAX_M = 200.0
LAST_RESORT_FACTOR = 10.0
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass
class Route:
    nodes: list[int]
    edges: list[dict]  # in walking order; each has the edge attrs it needs plus `reversed`
    line_wgs84: LineString
    line_utm: LineString
    length_m: float
    road_m: float
    last_resort_m: float
    gain_ft: float | None = None
    loss_ft: float | None = None
    profile: dict | None = None
    elevation_source: dict | None = None
    notes: list[str] = field(default_factory=list)
    crs: str | None = None


def resolve_point(tg, region, text: str) -> dict:
    """Turn a trailhead id, an OSM node id, or 'lat,lon' into a graph node."""
    G = tg.graph
    for th in region.trailheads:
        if th.get("id") == text:
            if th.get("node") is None:  # a verified lat/lon entry: snap it like any point
                snapped = resolve_point(tg, region, f"{th['lat']},{th['lon']}")
                return snapped | {"how": f"trailhead {text} ({th.get('name')}), {snapped['how']}"}
            if th["node"] not in G:
                raise ValueError(f"trailhead {text!r} points at node {th['node']}, which is not in the graph")
            return {"node": th["node"], "how": f"trailhead {text} ({th.get('name')})", "snap_m": 0.0}
    if m := _LATLON.match(text):
        lat, lon = float(m[1]), float(m[2])
        x, y = Transformer.from_crs("EPSG:4326", tg.crs, always_xy=True).transform(lon, lat)
        nodes = sorted(G.nodes)
        tree = STRtree([Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes])
        pt = Point(x, y)
        hit = tree.query_nearest(pt, all_matches=False) if math.isfinite(x) and math.isfinite(y) else []
        if len(hit) == 0:  # a point this map projection can't place (far outside the region)
            raise ValueError(f"{lat},{lon} is far outside this region")
        node = nodes[hit[0]]
        dist = pt.distance(Point(G.nodes[node]["x"], G.nodes[node]["y"]))
        if dist > SNAP_MAX_M:
            raise ValueError(f"{lat},{lon} is {dist * FT_PER_M:,.0f} ft from the nearest graph node "
                             f"(osm:node/{node}); the limit is {SNAP_MAX_M * FT_PER_M:.0f} ft")
        return {"node": node, "how": f"{lat},{lon} snapped to osm:node/{node}", "snap_m": dist}
    if text.isdigit():
        if int(text) not in G:
            raise ValueError(f"{text} is not a graph node (OSM node id) in this region")
        return {"node": int(text), "how": f"osm:node/{text}", "snap_m": 0.0}
    known = ", ".join(th["id"] for th in region.trailheads) or "(none in the region yaml)"
    raise ValueError(f"{text!r} is not a trailhead id, an OSM node id, or lat,lon. Trailheads: {known}")


def edge_weight(d: dict, road_factor: float = 1.0) -> float:
    """Routing cost: real distance, times road_factor on roads, times LAST_RESORT_FACTOR on last-resort roads."""
    if d["last_resort"]:
        return d["length_m"] * LAST_RESORT_FACTOR * road_factor
    return d["length_m"] * (road_factor if d["is_road"] else 1.0)


def routing_graph(tg, no_roads: bool = False, road_factor: float = 1.0, exclude_keys=frozenset()) -> nx.Graph:
    """Simple weighted graph: for parallel edges keep the lightest, remembering its key."""
    R = nx.Graph()
    for u, v, k, d in tg.graph.edges(keys=True, data=True):
        if u == v or (no_roads and d["is_road"]) or k in exclude_keys:
            continue
        w = edge_weight(d, road_factor)
        if not R.has_edge(u, v) or w < R[u][v]["w"]:
            R.add_edge(u, v, w=w, key=k)
    return R


def shortest_route(tg, start: int, end: int, no_roads: bool = False, road_factor: float = 1.0,
                   exclude_keys=frozenset()) -> Route:
    R = routing_graph(tg, no_roads, road_factor, exclude_keys)
    try:
        nodes = nx.shortest_path(R, start, end, weight="w")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        why = "trail-only path" if no_roads else "path in the graph"
        raise ValueError(f"No route: there is no {why} between osm:node/{start} and osm:node/{end}")
    return walk(tg, [R[u][v]["key"] for u, v in zip(nodes, nodes[1:])], nodes)


def walk(tg, keys: list[str], nodes: list[int]) -> Route:
    """Build a Route from edge keys walked in order along `nodes`."""
    G = tg.graph
    edges, coords_ll, coords_utm = [], [], []
    for (u, v), k in zip(zip(nodes, nodes[1:]), keys):
        d = G[u][v][k]
        rev = d["osm_nodes"][0] != u
        ll, utm = list(d["geom_wgs84"].coords), list(d["geom_utm"].coords)
        if rev:
            ll, utm = ll[::-1], utm[::-1]
        coords_ll += ll if not coords_ll else ll[1:]
        coords_utm += utm if not coords_utm else utm[1:]
        edges.append({key: d[key] for key in ("source", "way_id", "name", "highway", "is_road", "last_resort",
                                                "length_m", "retrieved_at", "tags")}
                     | {"key": k, "reversed": rev, "u": u, "v": v, "oneway": d.get("oneway", 0)})
    if len(nodes) == 1:  # start == end: a zero-length route
        n = G.nodes[nodes[0]]
        coords_ll, coords_utm = [(n["lon"], n["lat"])] * 2, [(n["x"], n["y"])] * 2
    line_utm = LineString(coords_utm)
    route = Route(
        nodes=list(nodes), edges=edges, line_wgs84=LineString(coords_ll), line_utm=line_utm, crs=tg.crs,
        length_m=sum(e["length_m"] for e in edges),
        road_m=sum(e["length_m"] for e in edges if e["is_road"]),
        last_resort_m=sum(e["length_m"] for e in edges if e["last_resort"]),
    )
    for name in against_one_way(route):
        route.notes.append(f"{name} is tagged one-way in OpenStreetMap and this trip walks it the other way; the tag "
                           "says nothing about whether walking it is allowed.")
    return route


def against_one_way(route: Route) -> list[str]:
    """Names of trails this route walks against their OpenStreetMap one-way tag, in walking order, once each."""
    names = []
    for e in route.edges:
        sense = e.get("oneway", 0)
        if sense and (sense == 1) == e["reversed"]:
            name = e["name"] or f"an unnamed {e['highway']}"
            if name not in names:
                names.append(name)
    return names


def add_elevation(route: Route, dem, crs: str) -> Route:
    """Gain/loss from a profile of the whole joined line (not summed per edge)."""
    p = line_profile(route.line_utm, dem, crs)
    route.profile = {"d_m": p["d_m"], "z_m": p["z_m"]}
    route.elevation_source = dem.source
    if p["missing"]:
        route.gain_ft = route.loss_ft = None
        route.notes.append(f"elevation unknown: {p['missing']} of {len(p['d_m'])} samples have no DEM value")
    else:
        route.gain_ft, route.loss_ft = p["gain_m"] * FT_PER_M, p["loss_m"] * FT_PER_M
    return route
