"""Cross-check the OSM trail graph against official USFS data.

USFS geometry is never used for routing. It answers three questions:
  - Is every official (USFS) trail in the OSM graph, and where isn't it?
  - What do USFS records say about each OSM way (designated trail, road, closed road)?
  - Do OSM trailheads line up with USFS trailhead sites?

USFS records describe the Forest Service inventory. They don't describe current
conditions, and nothing here should be read as saying a trail is open or passable.
"""

import statistics
from collections import Counter, defaultdict

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import substring, transform, unary_union

MATCH_M = 30.0  # within ~100 ft: the same line
OFFSET_M = 75.0  # beyond ~250 ft: OSM has no routable line here
SAMPLE_M = 10.0
MIN_GAP_M = 100.0  # shorter gaps are junction/endpoint geometry, not missing trail
EDGE_MATCH_FRACTION = 0.8
TRAILHEAD_MATCH_M = 150.0


def _features(usfs: dict, layer: str) -> list[dict]:
    return usfs["layers"].get(layer, {}).get("geojson", {}).get("features", [])


def _lines(geom) -> list[LineString]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        return [line for part in geom.geoms for line in _lines(part)]
    return []


def _sample(line: LineString, step: float = SAMPLE_M) -> tuple[list[float], list[Point]]:
    positions = [i * step for i in range(int(line.length // step) + 1)]
    if positions[-1] < line.length:
        positions.append(line.length)
    return positions, [line.interpolate(p) for p in positions]


def trail_label(props: dict) -> str:
    return f"TR {props.get('trail_no')} {props.get('trail_name')}"


def road_label(props: dict) -> str:
    return f"FR {props.get('id')} {props.get('name')}"


def verify(tg, raw: dict, usfs: dict, region, node_component: dict) -> dict:
    G = tg.graph
    to_utm = Transformer.from_crs("EPSG:4326", tg.crs, always_xy=True).transform
    to_wgs = Transformer.from_crs(tg.crs, "EPSG:4326", always_xy=True).transform
    b = region.bbox
    bbox_ll = box(b.west, b.south, b.east, b.north)

    edges = list(G.edges(keys=True, data=True))
    edge_tree = STRtree([d["geom_utm"] for *_, d in edges])

    # Non-routable OSM ways, to explain gaps that OSM does have but we don't route on.
    nonroutable = [(r, f"{r['highway']} '{r['name'] or 'unnamed'}' ({r['source']}) excluded: {r['reason']}")
                   for r in tg.excluded]
    nonroutable += [(r, f"{r['highway']} '{r['name'] or 'unnamed'}' ({r['source']}) is not a routable type")
                    for r in tg.other_highways.values()]
    nonroutable = [(LineString([to_utm(*p) for p in r["geometry"]]), label)
                   for r, label in nonroutable if len(r["geometry"]) >= 2]
    nonroutable_tree = STRtree([g for g, _ in nonroutable]) if nonroutable else None

    # --- USFS trails -> OSM coverage -------------------------------------------
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in _features(usfs, "trails"):
        groups[(f["properties"].get("trail_no"), f["properties"].get("trail_name"))].append(f)

    usfs_trails = []
    for (trail_no, name), feats in sorted(groups.items(), key=lambda kv: str(kv[0])):
        full_m, parts = 0.0, []
        for f in feats:
            geom_ll = shape(f["geometry"])
            full_m += sum(line.length for line in _lines(transform(to_utm, geom_ll)))
            parts += [transform(to_utm, line) for line in _lines(geom_ll.intersection(bbox_ll))]
        usfs_m = sum(p.length for p in parts)
        offsets, names, comps, gaps = [], Counter(), set(), []
        for part in parts:
            positions, pts = _sample(part)
            (_, idx), dist = edge_tree.query_nearest(pts, return_distance=True, all_matches=False)
            offsets += list(dist)
            for i, d in zip(idx, dist):
                if d <= MATCH_M:
                    u, _, _, e = edges[i]
                    names[e["name"] or "(unnamed)"] += 1
                    comps.add(node_component[u])
            for gap in _gaps(positions, pts, dist, nonroutable, nonroutable_tree):
                seg = transform(to_wgs, substring(part, gap.pop("from_pos"), gap.pop("to_pos")))
                gap["coords"] = [list(c) for c in seg.coords] if seg.geom_type == "LineString" else []
                gaps.append(gap)
        offsets = [float(o) for o in offsets] or [float("inf")]
        usfs_trails.append({
            "trail_no": trail_no,
            "name": name,
            "label": f"TR {trail_no} {name}",
            "usfs_m": usfs_m,
            "outside_bbox_m": full_m - usfs_m,
            "within_match_pct": 100 * sum(o <= MATCH_M for o in offsets) / len(offsets),
            "within_offset_pct": 100 * sum(o <= OFFSET_M for o in offsets) / len(offsets),
            "median_offset_m": statistics.median(offsets),
            "max_offset_m": max(offsets),
            "osm_names": [n for n, _ in names.most_common()],
            "components": sorted(comps, key=lambda c: int(c[1:])),
            "gaps": gaps,
            "osm_matched_m": 0.0,  # filled in below from the per-edge classification
        })

    # --- OSM edges -> USFS classification ---------------------------------------
    trail_feats = [(line, f["properties"]) for f in _features(usfs, "trails")
                   for line in _lines(transform(to_utm, shape(f["geometry"])))]
    road_feats = [(line, f["properties"], closed) for closed, layer in ((False, "roads_open"), (True, "roads_closed"))
                  for f in _features(usfs, layer) for line in _lines(transform(to_utm, shape(f["geometry"])))]
    wild = [transform(to_utm, shape(f["geometry"])) for f in _features(usfs, "wilderness")]
    wild_union = unary_union(wild) if wild else None

    def best_match(pts, feats):
        if not feats:
            return None
        tree = STRtree([f[0] for f in feats])
        (_, idx), dist = tree.query_nearest(pts, return_distance=True, all_matches=False)
        within = [i for i, d in zip(idx, dist) if d <= MATCH_M]
        if len(within) < EDGE_MATCH_FRACTION * len(pts):
            return None
        return feats[Counter(within).most_common(1)[0][0]]

    matched_by_label = Counter()
    groups_osm: dict[tuple, dict] = {}
    for u, _, _, d in edges:
        _, pts = _sample(d["geom_utm"])
        kind, label, closed, level = None, None, None, None
        if (t := best_match(pts, trail_feats)) is not None:
            kind, label = "trail", trail_label(t[1])
        elif (r := best_match(pts, road_feats)) is not None:
            kind, label, closed, level = "road", road_label(r[1]), r[2], r[1].get("oper_maint_level")
        if kind == "trail":
            matched_by_label[label] += d["length_m"]
        in_wild = d["geom_utm"].intersection(wild_union).length if wild_union is not None else 0.0
        key = (d["name"], d["highway"], label)
        g = groups_osm.setdefault(key, {
            "name": d["name"], "highway": d["highway"], "is_road": d["is_road"], "usfs_match": label,
            "usfs_kind": kind, "usfs_closed_to_motor": closed, "usfs_maint_level": level,
            "length_m": 0.0, "wilderness_m": 0.0, "components": set(), "sources": set()})
        g["length_m"] += d["length_m"]
        g["wilderness_m"] += in_wild
        g["components"].add(node_component[u])
        g["sources"].add(d["source"])
    osm_ways = []
    for g in groups_osm.values():
        g["inside_wilderness"] = g["wilderness_m"] > 0.5 * g["length_m"]
        g["components"] = sorted(g["components"], key=lambda c: int(c[1:]))
        g["sources"] = sorted(g["sources"])
        osm_ways.append(g)
    osm_ways.sort(key=lambda g: (g["name"] or "~", g["highway"]))
    for t in usfs_trails:
        t["osm_matched_m"] = matched_by_label[t["label"]]

    # --- trailheads ---------------------------------------------------------------
    osm_ths = [(Point(to_utm(e["lon"], e["lat"])), e) for e in raw["response"]["elements"]
               if e["type"] == "node" and e.get("tags", {}).get("highway") == "trailhead"]
    nodes = sorted(G.nodes)
    node_tree = STRtree([Point(G.nodes[n]["x"], G.nodes[n]["y"]) for n in nodes])
    trailheads, matched_osm = [], set()
    for f in _features(usfs, "rec_sites"):
        p = f["properties"]
        if p.get("site_subtype") != "TRAILHEAD":
            continue
        pt = Point(to_utm(*f["geometry"]["coordinates"][:2]))
        osm = None
        if osm_ths:
            near = min(osm_ths, key=lambda o: o[0].distance(pt))
            if near[0].distance(pt) <= TRAILHEAD_MATCH_M:
                osm = {"source": f"osm:node/{near[1]['id']}", "name": near[1].get("tags", {}).get("name"),
                       "distance_m": near[0].distance(pt)}
                matched_osm.add(near[1]["id"])
        n = nodes[node_tree.query_nearest(pt, all_matches=False)[0]]
        lon, lat = f["geometry"]["coordinates"][:2]
        trailheads.append({
            "site_id": p.get("site_id"), "name": p.get("public_site_name") or p.get("site_name"),
            "site_name": p.get("site_name"), "lat": lat, "lon": lon, "osm_trailhead": osm,
            "nearest_node": {"id": n, "distance_m": Point(G.nodes[n]["x"], G.nodes[n]["y"]).distance(pt),
                             "component": node_component[n]},
        })
    trailheads.sort(key=lambda t: t["name"] or "")
    osm_unmatched = [{"source": f"osm:node/{e['id']}", "name": e.get("tags", {}).get("name"),
                      "lat": e["lat"], "lon": e["lon"]} for _, e in osm_ths if e["id"] not in matched_osm]

    return {
        "osm_retrieved_at": raw["retrieved_at"],
        "usfs_retrieved_at": usfs["retrieved_at"],
        "thresholds_m": {"match": MATCH_M, "offset": OFFSET_M, "min_gap": MIN_GAP_M, "trailhead": TRAILHEAD_MATCH_M},
        "usfs_trails": usfs_trails,
        "osm_ways": osm_ways,
        "trailheads": trailheads,
        "osm_trailheads_unmatched": osm_unmatched,
        "wilderness": [f["properties"] for f in _features(usfs, "wilderness")],
        "display": _display(usfs),
    }


def _display(usfs: dict) -> dict:
    """USFS geometry in WGS84 for the map (display only)."""
    def coords(feature):
        return [[list(c) for c in line.coords] for line in _lines(shape(feature["geometry"]))]

    out = {"trails": [], "roads": [], "wilderness": []}
    for f in _features(usfs, "trails"):
        out["trails"] += [{"label": trail_label(f["properties"]), "coords": c} for c in coords(f)]
    for closed, layer in ((False, "roads_open"), (True, "roads_closed")):
        for f in _features(usfs, layer):
            out["roads"] += [{"label": road_label(f["properties"]), "closed": closed,
                              "maint": f["properties"].get("oper_maint_level"), "coords": c} for c in coords(f)]
    for f in _features(usfs, "wilderness"):
        geom = shape(f["geometry"])
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        out["wilderness"] += [{"label": f["properties"].get("wildernessname"),
                               "coords": [list(c) for c in p.exterior.coords]} for p in polys]
    return out


def _gaps(positions, pts, dist, nonroutable, nonroutable_tree) -> list[dict]:
    """Runs of USFS samples with no routable OSM line within OFFSET_M."""
    gaps, start = [], None
    for i, d in enumerate(list(dist) + [0.0]):  # sentinel closes a trailing run
        if d > OFFSET_M and start is None:
            start = i
        elif d <= OFFSET_M and start is not None:
            end = i - 1
            length = positions[end] - positions[start]
            if length >= MIN_GAP_M:
                gaps.append({
                    "length_m": length,
                    "from_pos": positions[start],
                    "to_pos": positions[end],
                    "in_osm": _explain(pts[start : end + 1], nonroutable, nonroutable_tree),
                })
            start = None
    return gaps


def _explain(pts, nonroutable, tree) -> str | None:
    """If OSM has a non-routable way along most of this gap, say which."""
    if tree is None:
        return None
    (_, idx), dist = tree.query_nearest(pts, return_distance=True, all_matches=False)
    hits = [nonroutable[i][1] for i, d in zip(idx, dist) if d <= MATCH_M]
    if len(hits) < 0.5 * len(pts):
        return None
    return Counter(hits).most_common(1)[0][0]
