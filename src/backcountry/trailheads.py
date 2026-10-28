"""Candidate trailheads, found only in source data.

A candidate is a graph node you could start a route from. Three sources:
  - osm_trailhead:       a highway=trailhead node, snapped to the nearest graph node
  - parking_nearby:      an amenity=parking feature near a node on a trail edge
  - trail_road_junction: a node where a trail meets any road (routable or not)

Nothing here says a trailhead is open, legal to park at, or reachable by car.
"""

import math

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point

from backcountry.ingest import feature_center
from backcountry.route import resolve_point

SNAP_MAX_M = 200.0
KIND_ORDER = ("osm_trailhead", "parking_nearby", "trail_road_junction")


def find_candidates(tg, raw: dict, node_component: dict, snap_max_m: float = SNAP_MAX_M) -> dict:
    G = tg.graph
    to_crs = Transformer.from_crs("EPSG:4326", tg.crs, always_xy=True)

    all_nodes = sorted(G.nodes)
    trail_nodes = sorted(n for n in G.nodes if any(not d["is_road"] for *_, d in G.edges(n, data=True)))
    all_tree = STRtree([Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in all_nodes])
    trail_tree = STRtree([Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in trail_nodes])

    found: dict[int, dict] = {}
    unsnapped = []

    def add(node, kind, name, source, snap_m):
        c = found.setdefault(node, {"id": node, "kinds": set(), "names": [], "sources": [], "snap_m": snap_m})
        c["kinds"].add(kind)
        if name and name not in c["names"]:
            c["names"].append(name)
        if source not in c["sources"]:
            c["sources"].append(source)
        c["snap_m"] = min(c["snap_m"], snap_m)

    def snap(lon, lat, tree, nodes):
        pt = Point(*to_crs.transform(lon, lat))
        i = tree.query_nearest(pt, all_matches=False)[0]
        node = nodes[i]
        return node, math.dist((pt.x, pt.y), (G.nodes[node]["x"], G.nodes[node]["y"]))

    for e in raw["response"]["elements"]:
        tags = e.get("tags", {})
        if e["type"] == "node" and tags.get("highway") == "trailhead":
            source = f"osm:node/{e['id']}"
            node, dist = (e["id"], 0.0) if e["id"] in G else snap(e["lon"], e["lat"], all_tree, all_nodes)
            if dist <= snap_max_m:
                add(node, "osm_trailhead", tags.get("name"), source, dist)
            else:
                unsnapped.append({"source": source, "name": tags.get("name"), "lat": e["lat"], "lon": e["lon"],
                                  "nearest_node": node, "distance_m": dist})
        elif tags.get("amenity") == "parking" and trail_nodes:
            lon, lat = feature_center(e)
            node, dist = snap(lon, lat, trail_tree, trail_nodes)
            if dist <= snap_max_m:
                add(node, "parking_nearby", tags.get("name"), f"osm:{e['type']}/{e['id']}", dist)

    for n in trail_nodes:
        road_names = [d["name"] or f"unnamed {d['highway']}" for *_, d in G.edges(n, data=True) if d["is_road"]]
        road_sources = [d["source"] for *_, d in G.edges(n, data=True) if d["is_road"]]
        for w in G.nodes[n]["other_highway_ways"]:
            other = tg.other_highways[w]
            road_names.append(other["name"] or f"unnamed {other['highway']}")
            road_sources.append(other["source"])
        if road_sources:
            trail_names = sorted({d["name"] for *_, d in G.edges(n, data=True) if not d["is_road"] and d["name"]})
            label = " / ".join(trail_names or ["unnamed trail"]) + " @ " + " / ".join(sorted(set(road_names)))
            for source in road_sources:
                add(n, "trail_road_junction", label, source, 0.0)

    candidates = []
    for c in found.values():
        attrs = G.nodes[c["id"]]
        c["kinds"] = sorted(c["kinds"], key=KIND_ORDER.index)
        c.update(lat=attrs["lat"], lon=attrs["lon"], component=node_component[c["id"]])
        candidates.append(c)
    candidates.sort(key=lambda c: (KIND_ORDER.index(c["kinds"][0]), int(c["component"][1:]), c["names"][:1], c["id"]))
    return {"candidates": candidates, "unsnapped": unsnapped}


def trailhead_list(region, tg, raw: dict, node_component: dict) -> list[dict]:
    """The region's trailheads (yaml entries first), then start points found in the data."""
    G = tg.graph
    out = []
    for th in region.trailheads:
        node = th.get("node")
        if node is None and th.get("lat") is not None:  # verified lat/lon entry: snap to the nearest graph node
            try:
                node = resolve_point(tg, region, f"{th['lat']},{th['lon']}")["node"]
            except ValueError:
                node = None
        in_graph = node in G
        out.append({
            "id": th["id"], "name": th.get("name"), "node": node, "source": "yaml", "verified": bool(th.get("verified")),
            "in_graph": in_graph, "usfs": th.get("usfs"), "note": th.get("note"),
            "lat": G.nodes[node]["lat"] if in_graph else None, "lon": G.nodes[node]["lon"] if in_graph else None,
            "component": node_component.get(node),
        })
    known = {t["node"] for t in out if t["node"] is not None}
    for c in find_candidates(tg, raw, node_component)["candidates"]:
        if c["id"] in known:
            continue
        out.append({
            "id": str(c["id"]), "name": " | ".join(c["names"]) or None, "node": c["id"], "source": "data",
            "verified": False, "in_graph": True, "kinds": c["kinds"], "lat": c["lat"], "lon": c["lon"],
            "component": c["component"], "snap_m": c["snap_m"],
        })
    return out


def trailhead_names(trailheads: list[dict]) -> dict[int, str]:
    """node -> what to call a start or finish there (a yaml trailhead's name, or the data's), for output text."""
    return {t["node"]: t["name"] for t in trailheads if t.get("node") is not None and t.get("name")}
