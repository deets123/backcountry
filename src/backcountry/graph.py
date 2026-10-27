"""Build and QA the trail graph.

Graph nodes are OSM nodes that are either
  - an endpoint of a routable way,
  - shared by two or more routable ways (a junction), or
  - shared with a non-routable highway (e.g. a trail crossing a residential road).

Each edge is a contiguous run of nodes from exactly one OSM way between two
graph nodes, so every edge maps back to one source way. No geometry is added,
moved, or joined: a gap in OSM stays a gap here and shows up in the QA.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

import networkx as nx
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point

from backcountry.ingest import KEEP_TAGS, access_exclusion_reason, foot_overrides_access, is_last_resort, way_class
from backcountry.region import Region

SHORT_EDGE_M = 20.0
NEAR_MISS_M = 25.0  # dead end this close to another edge = possible missing junction
NEAR_MISS_DETOUR_M = 200.0  # ...unless that edge is already reachable within this network distance
FT_PER_M = 3.28084


@dataclass
class TrailGraph:
    graph: nx.MultiGraph
    crs: str
    retrieved_at: str
    excluded: list[dict]  # routable-type ways dropped for access reasons
    access_overrides: list[dict]  # access=no|private kept because foot=yes|designated|permissive
    other_highways: dict[int, dict]  # highway ways outside the routable types, kept for QA only


def _way_record(way: dict, **extra) -> dict:
    tags = way.get("tags", {})
    return {
        "way_id": way["id"],
        "source": f"osm:way/{way['id']}",
        "highway": tags.get("highway"),
        "name": tags.get("name"),
        "nodes": way["nodes"],
        "geometry": [(g["lon"], g["lat"]) for g in way.get("geometry", [])],
        **extra,
    }


def build_graph(raw: dict, crs: str) -> TrailGraph:
    retrieved_at = raw["retrieved_at"]
    ways = [e for e in raw["response"]["elements"] if e["type"] == "way" and "highway" in e.get("tags", {})]

    routable, excluded, overrides, other = [], [], [], {}
    for w in ways:
        tags = w["tags"]
        if way_class(tags) is None:
            other[w["id"]] = _way_record(w)
            continue
        reason = access_exclusion_reason(tags)
        if reason is None and len(w["nodes"]) < 2:
            reason = "fewer than 2 nodes"
        if reason:
            excluded.append(_way_record(w, reason=reason))
            continue
        if foot_overrides_access(tags):
            overrides.append(_way_record(w))
        routable.append(w)

    coords: dict[int, tuple[float, float]] = {}
    for w in routable:
        if len(w["geometry"]) != len(w["nodes"]):
            raise ValueError(f"osm:way/{w['id']}: geometry and node list lengths differ")
        for nid, g in zip(w["nodes"], w["geometry"]):
            coords[nid] = (g["lon"], g["lat"])

    other_at: dict[int, list[int]] = defaultdict(list)
    for way_id, rec in other.items():
        for nid in set(rec["nodes"]):
            if nid in coords:
                other_at[nid].append(way_id)

    uses = Counter(nid for w in routable for nid in w["nodes"])
    split_nodes = {nid for nid, count in uses.items() if count >= 2} | set(other_at)
    for w in routable:
        split_nodes.update((w["nodes"][0], w["nodes"][-1]))

    to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    G = nx.MultiGraph(crs=crs, retrieved_at=retrieved_at)
    for w in routable:
        nodes, start, seg = w["nodes"], 0, 0
        for i in range(1, len(nodes)):
            if nodes[i] in split_nodes:
                _add_edge(G, w, nodes[start : i + 1], seg, coords, to_crs, retrieved_at)
                start, seg = i, seg + 1

    for nid in G.nodes:
        lon, lat = coords[nid]
        x, y = to_crs.transform(lon, lat)
        G.nodes[nid].update(lon=lon, lat=lat, x=x, y=y, other_highway_ways=sorted(other_at.get(nid, [])))

    return TrailGraph(G, crs, retrieved_at, excluded, overrides, other)


def oneway_sense(tags: dict) -> int:
    """1 when OpenStreetMap signs the way one-way in its drawn direction, -1 against it, 0 otherwise.
    `oneway:foot` wins over `oneway` (a road one-way for cars can be two-way on foot)."""
    value = tags.get("oneway:foot", tags.get("oneway"))
    return 1 if value in ("yes", "true", "1") else -1 if value == "-1" else 0


def _add_edge(G, way, seg_nodes, seg, coords, to_crs, retrieved_at):
    tags = way["tags"]
    lonlat = [coords[n] for n in seg_nodes]
    xs, ys = to_crs.transform([p[0] for p in lonlat], [p[1] for p in lonlat])
    geom_utm = LineString(list(zip(xs, ys)))
    G.add_edge(
        seg_nodes[0],
        seg_nodes[-1],
        key=f"{way['id']}:{seg}",
        way_id=way["id"],
        segment=seg,
        source=f"osm:way/{way['id']}",
        retrieved_at=retrieved_at,
        osm_nodes=list(seg_nodes),
        highway=tags["highway"],
        name=tags.get("name"),
        is_road=way_class(tags) == "road",
        last_resort=is_last_resort(tags),
        oneway=oneway_sense(tags),
        tags={k: tags[k] for k in KEEP_TAGS if k in tags},
        length_m=geom_utm.length,
        geom_wgs84=LineString(lonlat),
        geom_utm=geom_utm,
    )


# --- QA ---------------------------------------------------------------------


def _label(d: dict) -> str:
    name = f" '{d['name']}'" if d.get("name") else " (unnamed)"
    return f"{d['highway']}{name} ({d['source']})"


def qa_summary(tg: TrailGraph, short_edge_m: float = SHORT_EDGE_M, near_miss_m: float = NEAR_MISS_M) -> dict:
    G = tg.graph
    edges = list(G.edges(keys=True, data=True))

    # Components, largest total length first. C0 is the main network.
    comps = []
    for nodes in nx.connected_components(G):
        sub = G.subgraph(nodes)
        comp_edges = [d for _, _, d in sub.edges(data=True)]
        names = Counter()
        for d in comp_edges:
            if d["name"]:
                names[d["name"]] += d["length_m"]
        comps.append(
            {
                "nodes": sorted(nodes),
                "edge_count": len(comp_edges),
                "length_m": sum(d["length_m"] for d in comp_edges),
                "road_m": sum(d["length_m"] for d in comp_edges if d["is_road"]),
                "names": [n for n, _ in names.most_common()],
            }
        )
    comps.sort(key=lambda c: (-c["length_m"], c["nodes"][0]))
    node_comp = {}
    for i, c in enumerate(comps):
        c["id"] = f"C{i}"
        for n in c["nodes"]:
            node_comp[n] = c["id"]

    # Straight-line gap from each other component to the main one.
    main_edges = [d for u, _, _, d in edges if node_comp[u] == "C0"]
    main_tree = STRtree([d["geom_utm"] for d in main_edges]) if main_edges else None
    for c in comps:
        c["gap_to_main_m"], c["gap_to_main_way"] = None, None
        if c["id"] == "C0" or main_tree is None:
            continue
        geoms = [d["geom_utm"] for u, _, _, d in edges if node_comp[u] == c["id"]]
        (_, tree_idx), dist = main_tree.query_nearest(geoms, return_distance=True, all_matches=False)
        best = dist.argmin()
        c["gap_to_main_m"] = float(dist[best])
        c["gap_to_main_way"] = main_edges[tree_idx[best]]["source"]

    # Dead ends, each with the most likely explanation we can see in the data.
    all_tree = STRtree([d["geom_utm"] for *_, d in edges])
    excluded_at = defaultdict(list)
    for rec in tg.excluded:
        for nid in set(rec["nodes"]):
            excluded_at[nid].append(rec)
    dead_ends = []
    for n in sorted(G.nodes):
        if G.degree(n) != 1:
            continue
        attrs = G.nodes[n]
        (_, _, _, own) = next(iter(G.edges(n, keys=True, data=True)))
        notes = [f"meets {_label(tg.other_highways[w])}, not a routable type" for w in attrs["other_highway_ways"]]
        notes += [f"continues as excluded {_label(r)}: {r['reason']}" for r in excluded_at.get(n, [])]
        near = _near_miss(G, n, edges, all_tree, near_miss_m)
        if near:
            dist, d, comp = near[0], near[1], node_comp[near[2]]
            notes.append(f"possible missing junction: {dist * FT_PER_M:.0f} ft from {_label(d)} in {comp}")
        dead_ends.append(
            {
                "node": n,
                "lat": attrs["lat"],
                "lon": attrs["lon"],
                "component": node_comp[n],
                "way": own["source"],
                "name": own["name"],
                "note": "; ".join(notes),
                "near_miss_m": near[0] if near else None,
            }
        )

    short_edges = sorted(
        (
            {"way_id": d["way_id"], "source": d["source"], "name": d["name"], "u": u, "v": v, "length_m": d["length_m"],
             "component": node_comp[u], "lat": G.nodes[u]["lat"], "lon": G.nodes[u]["lon"]}
            for u, v, _, d in edges
            if d["length_m"] < short_edge_m
        ),
        key=lambda e: e["length_m"],
    )

    # How C0 holds together without roads (matters for a future --no-roads).
    main_trails = nx.MultiGraph()
    main_trails.add_edges_from(
        (u, v, k, d) for u, v, k, d in edges if node_comp[u] == "C0" and not d["is_road"]
    )
    pieces = []
    for nodes in nx.connected_components(main_trails):
        sub = main_trails.subgraph(nodes)
        names = Counter()
        for *_, d in sub.edges(data=True):
            if d["name"]:
                names[d["name"]] += d["length_m"]
        pieces.append({"nodes": sorted(nodes), "length_m": sum(d["length_m"] for *_, d in sub.edges(data=True)),
                       "names": [n for n, _ in names.most_common()]})
    pieces.sort(key=lambda p: (-p["length_m"], p["nodes"][0]))

    named_trails: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_highway = Counter()
    for u, _, _, d in edges:
        by_highway[d["highway"]] += d["length_m"]
        if d["name"]:
            named_trails[d["name"]][node_comp[u]] += d["length_m"]

    def touching(rec):
        comps_touched = sorted({node_comp[n] for n in rec["nodes"] if n in node_comp}, key=lambda c: int(c[1:]))
        return {k: v for k, v in rec.items() if k not in ("nodes", "geometry")} | {"components": comps_touched}

    return {
        "retrieved_at": tg.retrieved_at,
        "crs": tg.crs,
        "counts": {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "self_loops": nx.number_of_selfloops(G),
            "components": len(comps),
        },
        "totals": {
            "length_m": sum(d["length_m"] for *_, d in edges),
            "road_m": sum(d["length_m"] for *_, d in edges if d["is_road"]),
            "trail_m": sum(d["length_m"] for *_, d in edges if not d["is_road"]),
            "unnamed_trail_m": sum(d["length_m"] for *_, d in edges if not d["is_road"] and not d["name"]),
        },
        "by_highway_m": dict(by_highway.most_common()),
        "tag_values": _tag_value_lengths(edges, ("sac_scale", "trail_visibility", "informal", "access", "foot")),
        "components": comps,
        "node_component": node_comp,
        "main_trail_only_pieces": pieces,
        "dead_ends": dead_ends,
        "short_edges": short_edges,
        "named_trails": {k: dict(v) for k, v in sorted(named_trails.items())},
        "excluded": [touching(r) for r in tg.excluded],
        "access_overrides": [touching(r) for r in tg.access_overrides],
        "other_highways_touching": [
            touching(r) for r in tg.other_highways.values() if any(n in node_comp for n in r["nodes"])
        ],
    }


def _near_miss(G, n, edges, tree, near_miss_m):
    """Nearest edge within near_miss_m of node n that isn't already reachable nearby."""
    reachable = nx.single_source_dijkstra_path_length(G, n, cutoff=NEAR_MISS_DETOUR_M, weight="length_m")
    pt = Point(G.nodes[n]["x"], G.nodes[n]["y"])
    best = None
    for i in tree.query(pt, predicate="dwithin", distance=near_miss_m):
        u, v, _, d = edges[i]
        if u in reachable or v in reachable:
            continue
        dist = d["geom_utm"].distance(pt)
        if best is None or dist < best[0]:
            best = (dist, d, u)
    return best


def _tag_value_lengths(edges, keys) -> dict[str, dict[str, float]]:
    out = {}
    for key in keys:
        c = Counter()
        for *_, d in edges:
            if not d["is_road"]:
                c[d["tags"].get(key, "(none)")] += d["length_m"]
        out[key] = dict(c.most_common())
    return out


def bbox_coverage(raw: dict, region: Region) -> list[dict]:
    """Check each boundary feature in the raw data against the region bbox."""
    out = []
    for e in raw["response"]["elements"]:
        if e["type"] not in ("way", "relation") or "boundary" not in e.get("tags", {}):
            continue
        b = e.get("bounds")
        if not b:
            continue
        inside = region.bbox.contains(b["minlat"], b["minlon"], b["maxlat"], b["maxlon"])
        out.append(
            {
                "source": f"osm:{e['type']}/{e['id']}",
                "name": e["tags"].get("name"),
                "bounds": b,
                "inside_bbox": inside,
                "margin_deg": {
                    "south": b["minlat"] - region.bbox.south,
                    "west": b["minlon"] - region.bbox.west,
                    "north": region.bbox.north - b["maxlat"],
                    "east": region.bbox.east - b["maxlon"],
                },
            }
        )
    return out


def chain_named_trail(tg: TrailGraph, name: str) -> dict:
    """Join the trail edges with this name into one line, using only source vertices.

    Refuses anything that isn't a single unbranched line: branches, loops, or
    disconnected pieces are reported, never bridged.
    """
    G = tg.graph
    edges = [(u, v, k, d) for u, v, k, d in G.edges(keys=True, data=True) if d["name"] == name and not d["is_road"]]
    if not edges:
        raise ValueError(f"No trail edges named {name!r}")
    sub = nx.MultiGraph()
    sub.add_edges_from(edges)
    if not nx.is_connected(sub):
        raise ValueError(f"{name!r} is in {nx.number_connected_components(sub)} disconnected pieces")
    ends = sorted(n for n, deg in sub.degree() if deg == 1)
    if any(deg > 2 for _, deg in sub.degree()) or len(ends) != 2:
        raise ValueError(f"{name!r} has a branch or loop, so it isn't a single line")

    node, prev_key = ends[0], None
    coords_ll, coords_utm, sources, nodes = [], [], [], [node]
    while True:
        nxt = [(v, k, d) for _, v, k, d in sub.edges(node, keys=True, data=True) if k != prev_key]
        if not nxt:
            break
        other, k, d = nxt[0]
        ll, utm = list(d["geom_wgs84"].coords), list(d["geom_utm"].coords)
        if d["osm_nodes"][0] != node:
            ll, utm = ll[::-1], utm[::-1]
        coords_ll += ll if not coords_ll else ll[1:]
        coords_utm += utm if not coords_utm else utm[1:]
        if d["source"] not in sources:
            sources.append(d["source"])
        node, prev_key = other, k
        nodes.append(node)
    line_utm = LineString(coords_utm)
    return {"name": name, "coords_wgs84": coords_ll, "line_utm": line_utm, "length_m": line_utm.length,
            "sources": sources, "nodes": nodes}
