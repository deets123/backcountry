"""GPX, GeoJSON, HTML map, and text summary outputs.

M1: QA report (Markdown + JSON), QA component map (folium), and the built
graph as GeoJSON for inspection. User-facing distances are miles, or feet
for short distances.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import folium
from pyproj import Transformer
from shapely.geometry import mapping

from backcountry.ingest import LAST_RESORT_HIGHWAYS, OSM_ATTRIBUTION, ROAD_HIGHWAYS, TRAIL_HIGHWAYS, USFS_ATTRIBUTION

M_PER_MI = 1609.344
FT_PER_M = 3.28084
# Base maps: USGS National Map only (public domain). OSM's volunteer tile servers
# block pages opened from disk, and CLAUDE.md rule 6 says to respect tile terms.
USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
USGS_IMAGERY = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"
USGS_IMAGERY_ONLY = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
BASEMAPS = {"topo": ("USGS Topo", USGS_TOPO), "hybrid": ("USGS Imagery + Topo", USGS_IMAGERY),
            "satellite": ("USGS Imagery (satellite)", USGS_IMAGERY_ONLY)}


def add_basemaps(m: folium.Map, default: str = "topo") -> None:
    """USGS National Map base layers (public domain); `default` is shown first."""
    if default not in BASEMAPS:
        raise ValueError(f"basemap must be one of {', '.join(BASEMAPS)}; got {default!r}")
    credit = f"Base map: USGS The National Map | Trail data {OSM_ATTRIBUTION}"
    for key in [default] + [k for k in BASEMAPS if k != default]:
        name, url = BASEMAPS[key]
        folium.TileLayer(tiles=url, attr=credit, name=name, max_native_zoom=16, max_zoom=19, show=key == default).add_to(m)


KIND_LABELS = {"camp_site": "Campsite", "camp_pitch": "Tent pitch", "spring": "Spring", "drinking_water": "Drinking water",
               "water": "Lake/pond", "stream crossing": "Stream crossing", "river crossing": "River crossing"}
HIDDEN_TAGS = ("name", "tourism", "natural", "waterway", "amenity")


def kind_label(kind: str) -> str:
    """Human label for a feature kind ('camp_site' -> 'Campsite', 'usfs campground' -> 'USFS campground')."""
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    return kind.replace("usfs", "USFS").replace("_", " ").capitalize() if not kind.startswith("usfs") else \
        "USFS " + kind[5:].strip().replace("_", " ").lower()


def feature_title(f: dict) -> str:
    return f"{kind_label(f['kind'])}" + (f": {f['name']}" if f.get("name") else "")


def osm_url(f: dict) -> str | None:
    if f.get("url"):
        return f["url"]
    src = str(f.get("source", ""))
    return f"https://www.openstreetmap.org/{src[4:]}" if src.startswith("osm:") else None


def source_link_md(f: dict) -> str:
    """Provenance as a short markdown link, never a raw id in prose."""
    url = osm_url(f)
    if url:
        return f"[OSM]({url})"
    return "USFS" if str(f.get("source", "")).startswith("usfs") else "source on file"


def evidence_html(f: dict) -> str:
    """What the data says about a feature, plus links to look at the spot yourself."""
    meta = f.get("meta") or {}
    when = (meta.get("timestamp") or "")[:10]
    tags = "; ".join(f"{k}={v}" for k, v in sorted((f.get("tags") or {}).items()) if k not in HIDDEN_TAGS) or "no other tags"
    edit = f"last edited {when}, version {meta.get('version')}" if when else "edit history unknown"
    links = [f'<a href="https://www.google.com/maps/@{f["lat"]:.6f},{f["lon"]:.6f},300m/data=!3m1!1e3" target="_blank">satellite view</a>']
    url = osm_url(f)
    if url:
        links.append(f'<a href="{url}" target="_blank">OpenStreetMap entry</a>')
        links.append(f'<a href="{url}/history" target="_blank">edit history</a>')
    src = "OpenStreetMap" if url else "USFS" if str(f.get("source", "")).startswith("usfs") else "source on file"
    return (f"<b>{feature_title(f)}</b><br>mile {f['dist_m'] / M_PER_MI:.1f}, {ft(f['off_m'])} off route<br>"
            f"details: {tags}<br>{src}: {edit}<br>" + " · ".join(links)
            + "<br><i>Existence, status, and (for water) presence are unknown. Check before relying on it.</i>")
MAIN_COLOR = "#1f4fd1"
USFS_TRAIL_COLOR = "#ffc400"
USFS_ROAD_COLOR = "#8d5524"
USFS_TH_COLOR = "#00695c"
GAP_COLOR = "#ff00c8"
DEAD_END_COLOR = "#e08000"
MISSING_JUNCTION_COLOR = "#d00000"
OSM_BOUNDARY_COLOR = "#2e7d32"
USFS_BOUNDARY_COLOR = "#00897b"
PALETTE = ["#e6194b", "#2e9e44", "#f58231", "#911eb4", "#0fa3a3", "#d6249f", "#8a9a00", "#9a6324", "#800000",
           "#4b6b00", "#000075", "#c47a00"]


def mi(m: float) -> str:
    return f"{m / M_PER_MI:.2f} mi"


def ft(m: float) -> str:
    return f"{m * FT_PER_M:,.0f} ft"


def _cell(value) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|")


def _table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines


def _loc(lat: float, lon: float) -> str:
    return f"{lat:.5f}, {lon:.5f}"


# --- graph GeoJSON ------------------------------------------------------------


ELEVATION_KEYS = ("gain_ft_fwd", "loss_ft_fwd", "gain_ft_rev", "loss_ft_rev", "elev_min_ft", "elev_max_ft")


def edges_features(tg, node_component: dict) -> list[dict]:
    """GeoJSON features for every edge, with provenance; no profiles."""
    G = tg.graph
    edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        props = {key: d[key] for key in ("source", "way_id", "segment", "retrieved_at", "name", "highway", "is_road",
                                          "last_resort", "length_m", "tags")}
        props.update({key: d[key] for key in ELEVATION_KEYS if key in d})
        props.update(key=k, u=u, v=v, from_node=d["osm_nodes"][0], component=node_component[u])
        edges.append({"type": "Feature", "geometry": mapping(d["geom_wgs84"]), "properties": props})
    return edges


def write_graph_geojson(tg, node_component: dict, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    G = tg.graph
    edges = edges_features(tg, node_component)
    profiles = {k: {"from_node": d["osm_nodes"][0], "source": d["source"]} | d["profile"]
                for _, _, k, d in G.edges(keys=True, data=True) if "profile" in d}
    nodes = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
            "properties": {"id": n, "source": f"osm:node/{n}", "degree": G.degree(n), "component": node_component[n],
                           "other_highway_ways": a["other_highway_ways"]},
        }
        for n, a in G.nodes(data=True)
    ]
    paths = {"edges": out_dir / "edges.geojson", "nodes": out_dir / "nodes.geojson"}
    for key, feats in (("edges", edges), ("nodes", nodes)):
        fc = {"type": "FeatureCollection", "attribution": OSM_ATTRIBUTION, "features": feats}
        paths[key].write_text(json.dumps(fc))
    if profiles:
        # Raw sampled elevations (meters) along each edge, forward direction = from_node onward.
        paths["profiles"] = out_dir / "profiles.json"
        paths["profiles"].write_text(json.dumps({"units": "meters", "elevation_source": G.graph.get("elevation_source"),
                                                 "edges": profiles}))
    return paths


# --- QA outputs ---------------------------------------------------------------


def write_qa_outputs(summary: dict, tg, raw: dict, region, out_dir: Path, coverage: list[dict],
                     verification: dict | None = None, elevation: dict | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"report": out_dir / "qa_report.md", "json": out_dir / "qa_report.json", "map": out_dir / "qa_map.html"}
    paths["report"].write_text(qa_markdown(summary, tg, raw, region, coverage, elevation))
    paths["json"].write_text(json.dumps(summary | {"bbox_coverage": coverage}, indent=1, default=str))
    qa_map(summary, tg, raw, region, verification).save(str(paths["map"]))
    return paths


def qa_markdown(summary: dict, tg, raw: dict, region, coverage: list[dict], elevation: dict | None = None) -> str:
    G = tg.graph
    t, c = summary["totals"], summary["counts"]
    comps = summary["components"]
    main = comps[0] if comps else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    L = [
        f"# QA report: {region.name}",
        "",
        f"Generated {now}. Data: {raw.get('source', 'unknown source')}, retrieved {summary['retrieved_at']}"
        + (f" (OSM database as of {raw['osm_base']})" if raw.get("osm_base") else "")
        + f". {OSM_ATTRIBUTION}.",
        "",
        f"Routable types: trails = {', '.join(TRAIL_HIGHWAYS)}; roads (`is_road`) = {', '.join(ROAD_HIGHWAYS)}, "
        f"of which {', '.join(LAST_RESORT_HIGHWAYS)} is last resort only. "
        "Everything else is left out of the graph. Nothing has been patched or joined: every gap below is a gap in the source data "
        "or in the type filter.",
        "",
        "## Summary",
        "",
        *_table(
            ["nodes", "edges", "self-loops", "components", "total", "trail", "road", "unnamed trail"],
            [[c["nodes"], c["edges"], c["self_loops"], c["components"], mi(t["length_m"]), mi(t["trail_m"]),
              mi(t["road_m"]), mi(t["unnamed_trail_m"])]],
        ),
        "",
        "Miles by highway type: " + ", ".join(f"{k} {mi(v)}" for k, v in summary["by_highway_m"].items()) + ".",
        "",
        "## Region bbox coverage",
        "",
    ]
    if not coverage:
        L.append("No boundary feature matching the region's `boundary_name_regex` was in the data, so coverage is **unchecked**.")
    m_per_deg_lat = 111_320
    m_per_deg_lon = 111_320 * math.cos(math.radians((region.bbox.south + region.bbox.north) / 2))
    for cov in coverage:
        margins = ", ".join(
            f"{side} {mi(abs(deg) * (m_per_deg_lat if side in ('south', 'north') else m_per_deg_lon))}"
            + (" OUTSIDE" if deg < 0 else "")
            for side, deg in cov["margin_deg"].items()
        )
        verdict = "inside the bbox" if cov["inside_bbox"] else "**NOT fully inside the bbox**"
        L.append(f"- {cov['name']} ({cov['source']}) is {verdict}. Approximate margins: {margins}.")

    L += ["", "## Connected components", ""]
    if main:
        L.append(
            f"Main network **C0**: {mi(main['length_m'])} ({mi(main['length_m'] - main['road_m'])} trail, "
            f"{mi(main['road_m'])} road), {len(main['nodes'])} nodes, {main['edge_count']} edges. "
            f"The other {len(comps) - 1} components are listed with the straight-line gap to C0."
        )
        L.append("")
    L += _table(
        ["id", "total", "trail", "road", "nodes", "edges", "gap to C0", "nearest C0 way", "names (longest first)"],
        [[x["id"], mi(x["length_m"]), mi(x["length_m"] - x["road_m"]), mi(x["road_m"]), len(x["nodes"]),
          x["edge_count"], ft(x["gap_to_main_m"]) if x["gap_to_main_m"] is not None else None,
          x["gap_to_main_way"], ", ".join(x["names"][:5])] for x in comps],
    )

    pieces = summary["main_trail_only_pieces"]
    L += ["", "### C0 without roads", "",
          f"Dropping `is_road` edges splits C0 into {len(pieces)} trail-only pieces. Largest first (top 15):", ""]
    L += _table(["piece", "trail length", "nodes", "names (longest first)"],
                [[f"T{i}", mi(p["length_m"]), len(p["nodes"]), ", ".join(p["names"][:8])]
                 for i, p in enumerate(pieces[:15])])

    split = {n: v for n, v in summary["named_trails"].items() if len(v) > 1}
    outside = {n: v for n, v in summary["named_trails"].items() if "C0" not in v}
    L += ["", "## Named ways by component", ""]
    L.append(f"{len(summary['named_trails'])} names. Split across components: {len(split)}. Not in C0: {len(outside)}.")
    L.append("")
    L += _table(
        ["name", "components", "total"],
        [[n, ", ".join(f"{k} ({mi(m)})" for k, m in v.items()), mi(sum(v.values()))]
         for n, v in summary["named_trails"].items()],
    )

    near = [d for d in summary["dead_ends"] if d["near_miss_m"] is not None]
    L += ["", f"## Possible missing junctions ({len(near)})", "",
          "Dead ends within 82 ft of another edge they can't reach within 656 ft along the network. "
          "Often an OSM digitizing gap (lines touch but don't share a node). Check each against imagery/CalTopo; "
          "the fix belongs in OSM, not here.", ""]
    L += _table(["node", "comp", "dead-end way", "location", "note"],
                [[f"osm:node/{d['node']}", d["component"], f"{d['name'] or '(unnamed)'} {d['way']}",
                  _loc(d["lat"], d["lon"]), d["note"]] for d in near])

    def on_road(d):
        return any(e["is_road"] for *_, e in G.edges(d["node"], data=True))

    dead = sorted(summary["dead_ends"], key=lambda d: (int(d["component"][1:]), on_road(d), d["name"] or "~", d["node"]))
    n_main_trail = sum(1 for d in dead if d["component"] == "C0" and not on_road(d))
    L += ["", f"## Dead ends ({len(dead)})", "",
          f"Degree-1 nodes. {n_main_trail} are trail ends in C0. Many are real (overlooks, trails ending at roads "
          "outside the routable types); the note says what the data shows at that point.", ""]
    L += _table(["node", "comp", "on", "type", "location", "note"],
                [[f"osm:node/{d['node']}", d["component"], f"{d['name'] or '(unnamed)'} {d['way']}",
                  "road" if on_road(d) else "trail", _loc(d["lat"], d["lon"]), d["note"]] for d in dead])

    L += ["", f"## Very short edges (< 66 ft): {len(summary['short_edges'])}", "",
          "Usually two junctions digitized almost on top of each other. Harmless for routing but worth a look.", ""]
    L += _table(["way", "name", "length", "comp", "location"],
                [[e["source"], e["name"], ft(e["length_m"]), e["component"], _loc(e["lat"], e["lon"])]
                 for e in summary["short_edges"]])

    touching = sorted(summary["other_highways_touching"], key=lambda r: (-len(r["components"]), r["name"] or "~"))
    L += ["", f"## Non-routable highways touching the network ({len(touching)})", "",
          "Roads whose `highway` type is outside the routable set but share a node with it. "
          "Rows touching 2+ components would join them if that type were allowed.", ""]
    L += _table(["way", "highway", "name", "touches"],
                [[r["source"], r["highway"], r["name"], ", ".join(r["components"])] for r in touching])

    L += ["", f"## Excluded ways (access): {len(summary['excluded'])}", ""]
    L += _table(["way", "highway", "name", "reason", "touches"],
                [[r["source"], r["highway"], r["name"], r["reason"], ", ".join(r["components"])]
                 for r in sorted(summary["excluded"], key=lambda r: (not r["components"], r["name"] or "~"))])

    L += ["", f"## Kept despite access tag (foot=* overrides access=*): {len(summary['access_overrides'])}", ""]
    L += _table(["way", "highway", "name", "touches"],
                [[r["source"], r["highway"], r["name"], ", ".join(r["components"])] for r in summary["access_overrides"]])

    L += _elevation_section(tg, elevation)

    L += ["", "## Trail tag values (trail edges only)", ""]
    for key, values in summary["tag_values"].items():
        L.append(f"- `{key}`: " + ", ".join(f"{v} {mi(m)}" for v, m in values.items()))
    L.append("")
    return "\n".join(L)


def qa_map(summary: dict, tg, raw: dict, region, verification: dict | None = None) -> folium.Map:
    G = tg.graph
    node_comp = summary["node_component"]
    comps = summary["components"]
    colors = {x["id"]: MAIN_COLOR if i == 0 else PALETTE[(i - 1) % len(PALETTE)] for i, x in enumerate(comps)}
    b = region.bbox
    m = folium.Map(location=[(b.south + b.north) / 2, (b.west + b.east) / 2], zoom_start=12, tiles=None,
                   control_scale=True)
    credit = f"Base map: USGS The National Map | Trail data {OSM_ATTRIBUTION}"
    folium.TileLayer(tiles=USGS_TOPO, attr=credit, name="USGS Topo", max_native_zoom=16, max_zoom=19).add_to(m)
    folium.TileLayer(tiles=USGS_IMAGERY, attr=credit, name="USGS Imagery + Topo", max_native_zoom=16, max_zoom=19, show=False).add_to(m)

    layers = {"main": [], "other": []}
    for u, _, d in G.edges(data=True):
        comp = node_comp[u]
        layers["main" if comp == "C0" else "other"].append({
            "type": "Feature",
            "geometry": mapping(d["geom_wgs84"]),
            "properties": {"component": comp, "name": d["name"] or "(unnamed)", "highway": d["highway"],
                           "source": d["source"], "length": mi(d["length_m"]), "color": colors[comp],
                           "elevation": (f"{d['gain_ft_fwd']:,.0f} ft up, {d['loss_ft_fwd']:,.0f} ft down"
                                         if d.get("gain_ft_fwd") is not None else "unknown"),
                           "dash": "2 7" if d["last_resort"] else "8 6" if d["is_road"] else None},
        })
    main_len = mi(comps[0]["length_m"]) if comps else "0 mi"
    for key, title in (("main", f"C0 main network ({main_len})"), ("other", f"Other components ({len(comps) - 1})")):
        folium.GeoJson(
            {"type": "FeatureCollection", "features": layers[key]},
            name=title,
            style_function=lambda f, w=5 if key == "main" else 4: {
                "color": f["properties"]["color"], "weight": w, "opacity": 0.85, "dashArray": f["properties"]["dash"]},
            tooltip=folium.GeoJsonTooltip(
                fields=["component", "name", "highway", "source", "length", "elevation"],
                aliases=["component", "name", "highway", "source", "length", "gain/loss along way"]),
        ).add_to(m)

    excluded = folium.FeatureGroup(name=f"Excluded ways, access ({len(tg.excluded)})", show=False)
    for r in tg.excluded:
        folium.PolyLine([(lat, lon) for lon, lat in r["geometry"]], color="#777", weight=2, dash_array="2 6",
                        tooltip=f"EXCLUDED {r['source']} {r['name'] or ''}: {r['reason']}").add_to(excluded)
    excluded.add_to(m)

    other = folium.FeatureGroup(name="Non-routable roads touching network", show=False)
    for r in summary["other_highways_touching"]:
        geom = tg.other_highways[r["way_id"]]["geometry"]
        folium.PolyLine([(lat, lon) for lon, lat in geom], color="#444", weight=2, opacity=0.6,
                        tooltip=f"not routable: {r['highway']} {r['name'] or ''} {r['source']}").add_to(other)
    other.add_to(m)

    dead = folium.FeatureGroup(name=f"Dead ends ({len(summary['dead_ends'])})", show=False)
    near = folium.FeatureGroup(name="Possible missing junctions")
    for d in summary["dead_ends"]:
        text = f"dead end osm:node/{d['node']} ({d['component']}) on {d['name'] or '(unnamed)'} {d['way']}"
        if d["note"]:
            text += f"<br>{d['note']}"
        if d["near_miss_m"] is not None:
            folium.CircleMarker((d["lat"], d["lon"]), radius=9, color=MISSING_JUNCTION_COLOR, weight=3, fill=False,
                                tooltip=text).add_to(near)
        folium.CircleMarker((d["lat"], d["lon"]), radius=4, color=DEAD_END_COLOR, fill=True, fill_opacity=0.9,
                            tooltip=text).add_to(dead)
    dead.add_to(m)
    near.add_to(m)

    short = folium.FeatureGroup(name=f"Very short edges ({len(summary['short_edges'])})", show=False)
    for e in summary["short_edges"]:
        folium.CircleMarker((e["lat"], e["lon"]), radius=5, color="#7b1fa2", fill=True,
                            tooltip=f"short edge {e['source']} {e['name'] or ''}: {ft(e['length_m'])}").add_to(short)
    short.add_to(m)

    boundary = folium.FeatureGroup(name="OSM wilderness boundary")
    for e in raw["response"]["elements"]:
        if "boundary" not in e.get("tags", {}):
            continue
        parts = [mem.get("geometry") for mem in e.get("members", []) if mem.get("type") == "way"]
        parts += [e["geometry"]] if e["type"] == "way" else []
        for geom in parts:
            if geom:
                folium.PolyLine([(g["lat"], g["lon"]) for g in geom if g], color=OSM_BOUNDARY_COLOR, weight=2,
                                dash_array="6 4", tooltip=f"{e['tags'].get('name')} (osm:{e['type']}/{e['id']})").add_to(boundary)
    boundary.add_to(m)
    folium.Rectangle([(b.south, b.west), (b.north, b.east)], color="#000", weight=1, dash_array="2 4", fill=False,
                     tooltip=f"region bbox ({region.id})").add_to(m)

    if verification:
        _usfs_layers(m, verification)
    m.get_root().html.add_child(folium.Element(_legend(summary, region, verification, main_len, len(comps) - 1)))
    folium.LayerControl(collapsed=False).add_to(m)
    return m


def _usfs_layers(m: folium.Map, v: dict) -> None:
    disp = v["display"]
    trails = folium.FeatureGroup(name=f"USFS official trails ({len(v['usfs_trails'])})")
    for t in disp["trails"]:
        folium.PolyLine([(lat, lon) for lon, lat in t["coords"]], color=USFS_TRAIL_COLOR, weight=2.5, opacity=0.95,
                        tooltip=f"USFS {t['label']}").add_to(trails)
    trails.add_to(m)

    roads = folium.FeatureGroup(name="USFS roads", show=False)
    for r in disp["roads"]:
        status = "closed to motorized use" if r["closed"] else "open"
        folium.PolyLine([(lat, lon) for lon, lat in r["coords"]], color=USFS_ROAD_COLOR, weight=2.5,
                        dash_array="1 5" if r["closed"] else "5 4",
                        tooltip=f"USFS {r['label']}: {r['maint']} ({status} per USFS layer)").add_to(roads)
    roads.add_to(m)

    wild = folium.FeatureGroup(name="USFS wilderness boundary", show=False)
    for w in disp["wilderness"]:
        folium.PolyLine([(lat, lon) for lon, lat in w["coords"]], color=USFS_BOUNDARY_COLOR, weight=2,
                        dash_array="2 4", tooltip=f"USFS: {w['label']}").add_to(wild)
    wild.add_to(m)

    ths = folium.FeatureGroup(name=f"USFS trailheads ({len(v['trailheads'])})")
    for t in v["trailheads"]:
        osm = t["osm_trailhead"]
        match = f"OSM {osm['source']} at {ft(osm['distance_m'])}" if osm else "no OSM trailhead within 490 ft"
        folium.Marker(
            (t["lat"], t["lon"]),
            icon=folium.DivIcon(html=f'<div class="bc-th">TH</div>', icon_size=(24, 16), icon_anchor=(12, 8)),
            tooltip=f"USFS trailhead: {t['name']} (site {t['site_id']})<br>{match}<br>"
                    f"nearest graph node {t['nearest_node']['id']} at {ft(t['nearest_node']['distance_m'])}",
        ).add_to(ths)
    ths.add_to(m)

    gaps = folium.FeatureGroup(name="Gaps vs USFS")
    for t in v["usfs_trails"]:
        for g in t["gaps"]:
            if g["coords"]:
                folium.PolyLine([(lat, lon) for lon, lat in g["coords"]], color=GAP_COLOR, weight=9, opacity=0.5,
                                tooltip=f"{t['label']}: {ft(g['length_m'])} with no routable OSM line within 250 ft"
                                        + (f"<br>OSM has: {g['in_osm']}" if g["in_osm"] else "")).add_to(gaps)
    gaps.add_to(m)


def _swatch_line(color: str, width: float = 4, dash: str | None = None, opacity: float = 1.0) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<svg width="30" height="12" aria-hidden="true"><line x1="2" y1="6" x2="28" y2="6" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-opacity="{opacity}"{dash_attr}/></svg>')


def _swatch_multi() -> str:
    segs = "".join(f'<line x1="{2 + i * 9}" y1="6" x2="{8 + i * 9}" y2="6" stroke="{c}" stroke-width="4" '
                   f'stroke-linecap="round"/>' for i, c in enumerate(PALETTE[:3]))
    return f'<svg width="30" height="12" aria-hidden="true">{segs}</svg>'


def _swatch_dot(fill: str | None, stroke: str, r: float) -> str:
    return (f'<svg width="30" height="16" aria-hidden="true"><circle cx="15" cy="8" r="{r}" '
            f'fill="{fill or "none"}" stroke="{stroke}" stroke-width="{2.5 if fill is None else 1}"/></svg>')


def _legend(summary: dict, region, verification: dict | None, main_len: str, n_other: int) -> str:
    def row(swatch: str, label: str) -> str:
        return f'<div class="bc-row">{swatch}<span>{label}</span></div>'

    rows = ['<div class="bc-sec">OSM trail graph</div>',
            row(_swatch_line(MAIN_COLOR, 5), f"Main network (C0), {main_len}"),
            row(_swatch_multi(), f"Other components ({n_other}), hover for id"),
            row(_swatch_line("#555", 3, "6 4"), "Road or track (dashed), usable when needed"),
            row(_swatch_line("#555", 3, "1 5"), "Paved county road (dotted), last resort"),
            row(_swatch_dot(DEAD_END_COLOR, DEAD_END_COLOR, 4), "Dead end (layer off by default)"),
            row(_swatch_dot(None, MISSING_JUNCTION_COLOR, 6), "Possible missing junction")]
    sources = f"OSM {OSM_ATTRIBUTION}, retrieved {summary['retrieved_at'][:10]}"
    if verification:
        rows += ['<div class="bc-sec">USFS official data</div>',
                 row(_swatch_line(USFS_TRAIL_COLOR, 3), "USFS official trail (drawn on top)"),
                 row(_swatch_line(GAP_COLOR, 8, opacity=0.5), "Gap vs USFS: no OSM line within 250 ft"),
                 row('<svg width="30" height="16" aria-hidden="true"><rect x="5" y="2" width="20" height="12" rx="3" '
                     f'fill="{USFS_TH_COLOR}"/></svg>', "USFS trailhead"),
                 row(_swatch_line(USFS_ROAD_COLOR, 2.5, "5 4"), "USFS road (layer off by default)")]
        sources += f" · {USFS_ATTRIBUTION}, retrieved {verification['usfs_retrieved_at'][:10]}"
    rows += ['<div class="bc-sec">Boundaries</div>',
             row(_swatch_line(OSM_BOUNDARY_COLOR, 2, "6 4"), "Wilderness boundary (OSM)"),
             row(_swatch_line("#000", 1.5, "2 4"), "Region bbox")]
    return f"""
<style>
  #bc-legend {{ position: fixed; bottom: 28px; left: 12px; z-index: 9999; max-width: 290px;
    background: #fff; color: #1b1b1b; border-radius: 8px; box-shadow: 0 1px 8px rgba(0,0,0,.35);
    font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  #bc-legend summary {{ padding: 8px 12px; font-weight: 600; cursor: pointer; }}
  #bc-legend .bc-body {{ padding: 0 12px 10px; }}
  #bc-legend .bc-sec {{ margin: 8px 0 2px; font-size: 11px; font-weight: 600; letter-spacing: .05em;
    text-transform: uppercase; color: #666; }}
  #bc-legend .bc-row {{ display: flex; align-items: center; gap: 8px; min-height: 20px; }}
  #bc-legend .bc-row svg {{ flex: 0 0 30px; }}
  #bc-legend .bc-src {{ margin-top: 8px; font-size: 11px; color: #666; }}
  .bc-th {{ background: {USFS_TH_COLOR}; color: #fff; font: 700 10px/16px sans-serif; text-align: center;
    border-radius: 3px; border: 1px solid #fff; box-shadow: 0 0 2px rgba(0,0,0,.6); }}
</style>
<div id="bc-legend"><details open><summary>Legend: {region.name}</summary><div class="bc-body">
  {''.join(rows)}
  <div class="bc-src">{sources}</div>
</div></details></div>"""


# --- verification report --------------------------------------------------------


def write_verify_report(v: dict, region, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"report": out_dir / "verify_report.md", "json": out_dir / "verify_report.json"}
    paths["report"].write_text(verify_markdown(v, region))
    paths["json"].write_text(json.dumps(v, indent=1, default=str))
    return paths


def verify_markdown(v: dict, region) -> str:
    winter = [t for t in v["usfs_trails"] if t.get("trail_type") == "SNOW"]
    trails = [t for t in v["usfs_trails"] if t.get("trail_type") != "SNOW"]
    usfs_total = sum(t["usfs_m"] for t in trails)

    def weighted(key):
        return sum(t[key] * t["usfs_m"] for t in trails) / usfs_total if usfs_total else 0.0

    gaps = [(t, g) for t in trails for g in t["gaps"]]
    not_in_usfs = [w for w in v["osm_ways"] if w["usfs_match"] is None and not w["is_road"]]
    wild_unlisted = [w for w in not_in_usfs if w["inside_wilderness"]]
    ths = v["trailheads"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    L = [
        f"# Verification vs USFS: {region.name}",
        "",
        f"Generated {now}. OSM data retrieved {v['osm_retrieved_at']} ({OSM_ATTRIBUTION}). "
        f"USFS data retrieved {v['usfs_retrieved_at']} ({USFS_ATTRIBUTION}).",
        "",
        "USFS records describe the Forest Service inventory. They say nothing about current trail conditions, "
        "closures, or whether a trail is passable. \"Matched\" means within 100 ft; a gap is a stretch of at least "
        "330 ft with no routable OSM line within 250 ft. USFS geometry is used only for this comparison, never for routing.",
        "",
        "## Summary",
        "",
        f"- USFS lists {len(trails)} ground trail{'s' if len(trails) != 1 else ''} with {mi(usfs_total)} inside the region bbox"
        + (f" (plus {len(winter)} winter-only trails, listed at the end)" if winter else "") + ". Routable OSM lines "
        f"are within 100 ft of {weighted('within_match_pct'):.1f}% of that mileage and within 250 ft of "
        f"{weighted('within_offset_pct'):.1f}%.",
        f"- {len(gaps)} gap(s) totaling {mi(sum(g['length_m'] for _, g in gaps))}.",
        f"- {len(not_in_usfs)} OSM trail name groups ({mi(sum(w['length_m'] for w in not_in_usfs))}) have no USFS "
        f"trail or road record; {len(wild_unlisted)} of them ({mi(sum(w['length_m'] for w in wild_unlisted))}) "
        "lie mostly inside the USFS wilderness boundary.",
        f"- {len(ths)} USFS trailheads; {sum(1 for t in ths if t['osm_trailhead'])} have an OSM trailhead within 490 ft.",
        "",
        "## Official USFS trails",
        "",
    ]
    L += _table(
        ["USFS trail", "USFS (in bbox)", "OSM matched", "diff", "within 100 ft", "median offset", "max offset",
         "gaps", "OSM names", "comp"],
        [[t["label"], mi(t["usfs_m"]) + (f" (+{mi(t['outside_bbox_m'])} outside bbox)" if t["outside_bbox_m"] > 1 else ""),
          mi(t["osm_matched_m"]),
          f"{(t['osm_matched_m'] - t['usfs_m']) / t['usfs_m'] * 100:+.0f}%" if t["usfs_m"] else None,
          f"{t['within_match_pct']:.0f}%", ft(t["median_offset_m"]), ft(t["max_offset_m"]), len(t["gaps"]),
          ", ".join(t["osm_names"][:3]), ", ".join(t["components"])] for t in trails],
    )
    L += ["", "\"OSM matched\" is the length of OSM edges that run along this USFS trail for at least 80% of their "
          "length, so differences also come from where each dataset splits trails at junctions.", "",
          f"## USFS trail sections missing from the graph ({len(gaps)})", ""]
    if gaps:
        L += _table(["USFS trail", "length", "from", "to", "what OSM has there"],
                    [[t["label"], ft(g["length_m"]), _loc(g["coords"][0][1], g["coords"][0][0]) if g["coords"] else None,
                      _loc(g["coords"][-1][1], g["coords"][-1][0]) if g["coords"] else None,
                      g["in_osm"] or "nothing (a real OSM data gap)"] for t, g in gaps])
    else:
        L.append("None.")

    def way_rows(rows):
        return [[w["name"] or "(unnamed)", w["highway"], mi(w["length_m"]), w["usfs_match"] or "no USFS record",
                 w["usfs_maint_level"],
                 None if w["usfs_closed_to_motor"] is None else ("yes" if w["usfs_closed_to_motor"] else "no"),
                 ", ".join(w["components"]), ", ".join(w["sources"][:3])] for w in rows]

    headers = ["OSM name", "highway", "length", "USFS record", "USFS maintenance level", "USFS: closed to motorized",
               "comp", "OSM ways"]
    tracks = [w for w in v["osm_ways"] if w["highway"] == "track"]
    L += ["", f"## OSM tracks vs USFS records ({len(tracks)})", "",
          "The graph counts `highway=track` as trail. This is what USFS records say about each one.", ""]
    L += _table(headers, way_rows(tracks))
    roads = [w for w in v["osm_ways"] if w["is_road"] and w["usfs_match"]]
    L += ["", f"## OSM roads with a USFS record ({len(roads)})", ""]
    L += _table(headers, way_rows(roads))

    L += ["", f"## OSM trails not in USFS data ({len(not_in_usfs)})", "",
          "USFS only inventories National Forest System trails. Trails on other land (state park, wildlife refuge, "
          "private) are expected here. Rows mostly inside the wilderness boundary deserve a look.", "",
          f"### Mostly inside the USFS wilderness boundary ({len(wild_unlisted)})", ""]
    L += _table(["OSM name", "highway", "length", "comp", "OSM ways"],
                [[w["name"] or "(unnamed)", w["highway"], mi(w["length_m"]), ", ".join(w["components"]),
                  ", ".join(w["sources"][:4])] for w in sorted(wild_unlisted, key=lambda w: -w["length_m"])])
    outside = [w for w in not_in_usfs if not w["inside_wilderness"]]
    L += ["", f"### Outside the wilderness boundary ({len(outside)}, {mi(sum(w['length_m'] for w in outside))})", ""]
    L += _table(["OSM name", "highway", "length", "comp"],
                [[w["name"] or "(unnamed)", w["highway"], mi(w["length_m"]), ", ".join(w["components"])]
                 for w in sorted(outside, key=lambda w: -w["length_m"])])

    L += ["", f"## Trailheads ({len(ths)} USFS)", ""]
    L += _table(["USFS trailhead", "site", "USFS location", "OSM trailhead within 490 ft", "nearest graph node", "comp"],
                [[t["name"], t["site_id"], _loc(t["lat"], t["lon"]),
                  f"{t['osm_trailhead']['name']} {t['osm_trailhead']['source']} ({ft(t['osm_trailhead']['distance_m'])})"
                  if t["osm_trailhead"] else "none",
                  f"{t['nearest_node']['id']} ({ft(t['nearest_node']['distance_m'])})", t["nearest_node"]["component"]]
                 for t in ths])
    if v["osm_trailheads_unmatched"]:
        L += ["", "OSM trailheads with no USFS trailhead within 490 ft:", ""]
        L += [f"- {u['name'] or '(unnamed)'} {u['source']} at {_loc(u['lat'], u['lon'])}" for u in v["osm_trailheads_unmatched"]]

    L += ["", f"## Winter-only USFS trails ({len(winter)})", "",
          "Snowmobile and ski trails (USFS trail_type SNOW). A hiking graph isn't expected to cover them; OSM often maps "
          "ski trails with foot=no, which this tool excludes.", ""]
    L += _table(["USFS trail", "USFS (in bbox)", "within 100 ft", "gaps"],
                [[t["label"], mi(t["usfs_m"]), f"{t['within_match_pct']:.0f}%", len(t["gaps"])] for t in winter])
    L += ["", "## Wilderness areas in the bbox (USFS)", ""]
    L += [f"- {w.get('wildernessname')}: {w.get('gis_acres', 0):,.0f} acres, boundary status "
          f"\"{w.get('boundarystatus')}\"" for w in v["wilderness"]] or ["None returned."]
    L.append("")
    return "\n".join(L)


def _elevation_section(tg, elevation: dict | None) -> list[str]:
    from backcountry.elevation import HYSTERESIS_M, SAMPLE_M, SMOOTH_SAMPLES

    L = ["", "## Elevation", ""]
    if elevation is None:
        return L + ["No DEM cached, so elevation is **unknown**. Run `backcountry dem --region <id>`."]
    src = elevation["source"]
    tiles = "; ".join(f"{s['title']} (published {s['publication_date']})" for s in src.get("sources", []))
    L += [f"DEM: {src.get('dataset', src.get('title', 'unknown'))}, {src.get('attribution', '')}. "
          f"Tiles: {tiles or 'n/a'}. Retrieved {src.get('retrieved_at', 'unknown')}.", "",
          f"Method: sample every {ft(SAMPLE_M)} along each edge, bilinear interpolation, a {SMOOTH_SAMPLES}-sample "
          f"(~{ft(SAMPLE_M * SMOOTH_SAMPLES)}) moving average, and changes counted once they pass {HYSTERESIS_M * FT_PER_M:.1f} ft. "
          "Edge totals don't add up exactly to route totals; routes are recomputed from joined profiles.", ""]
    edges = [d for *_, d in tg.graph.edges(data=True)]
    known = [d for d in edges if d.get("gain_ft_fwd") is not None]
    if known:
        L.append(f"Network elevation range: {min(d['elev_min_ft'] for d in known):,.0f} to "
                 f"{max(d['elev_max_ft'] for d in known):,.0f} ft.")
    missing = elevation["missing"]
    L += ["", f"Edges with no DEM value somewhere along them: {len(missing)} (their gain/loss is unknown)."]
    if missing:
        L += [""] + _table(["way", "name", "samples missing"],
                           [[m["source"], m["name"], f"{m['samples_missing']} of {m['samples']}"] for m in missing])
    steep = sorted((d for d in known if d["length_m"] >= 100),
                   key=lambda d: -max(d["gain_ft_fwd"], d["gain_ft_rev"]) / d["length_m"])[:10]
    L += ["", "Steepest edges (at least 330 ft long), by climb rate in the steeper direction:", ""]
    L += _table(["way", "name", "length", "gain fwd / rev", "climb rate"],
                [[d["source"], d["name"], mi(d["length_m"]), f"{d['gain_ft_fwd']:,.0f} / {d['gain_ft_rev']:,.0f} ft",
                  f"{max(d['gain_ft_fwd'], d['gain_ft_rev']) / (d['length_m'] / M_PER_MI):,.0f} ft/mi"] for d in steep])
    return L


# --- GPX ------------------------------------------------------------------------


def write_trail_gpx(chain: dict, path: Path, retrieved_at: str) -> Path:
    """One track with the chain's exact OSM vertices. No elevations, so tools use their own terrain data."""
    import gpxpy.gpx

    gpx = gpxpy.gpx.GPX()
    gpx.creator = "backcountry route engine"
    gpx.name = chain["name"]
    gpx.description = (f"Exact OpenStreetMap geometry from {', '.join(chain['sources'])}. "
                       f"Trail data {OSM_ATTRIBUTION}, retrieved {retrieved_at}. No elevations included.")
    track = gpxpy.gpx.GPXTrack(name=chain["name"])
    segment = gpxpy.gpx.GPXTrackSegment([gpxpy.gpx.GPXTrackPoint(lat, lon) for lon, lat in chain["coords_wgs84"]])
    track.segments.append(segment)
    gpx.tracks.append(track)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gpx.to_xml())
    return path


def read_gpx_line(path: Path):
    """All track points of a GPX file, in order, as a lon/lat LineString."""
    import gpxpy
    from shapely.geometry import LineString

    gpx = gpxpy.parse(Path(path).read_text())
    return LineString([(p.longitude, p.latitude) for t in gpx.tracks for s in t.segments for p in s.points])


# --- routes (M3) -------------------------------------------------------------------


def route_pieces(route, plan=None) -> list[dict]:
    """The route cut into days (or one piece), each as exact source vertices plus the day's boundary points.

    Points are (lon, lat, ele_m or None); elevations are interpolated from the route profile (DEM-based).
    """
    import numpy as np

    coords_utm, coords_ll = list(route.line_utm.coords), list(route.line_wgs84.coords)
    cum = np.concatenate([[0.0], np.cumsum([math.dist(a, b) for a, b in zip(coords_utm, coords_utm[1:])])])
    to_ll = Transformer.from_crs(route.crs, "EPSG:4326", always_xy=True)
    prof = route.profile if route.profile and None not in route.profile["z_m"] else None

    def ele(dist):
        return float(np.interp(dist, prof["d_m"], prof["z_m"])) if prof else None

    def point_at(dist):
        i = int(np.searchsorted(cum, dist))
        if i < len(cum) and abs(cum[i] - dist) < 1e-6:
            return (*coords_ll[i], ele(dist))
        pt = route.line_utm.interpolate(dist)
        lon, lat = to_ll.transform(pt.x, pt.y)
        return (lon, lat, ele(dist))

    spans = ([(d.start_m, d.end_m, f"Day {d.index}: {d.start_label} to {d.end_label}", d) for d in plan.days]
             if plan else [(0.0, cum[-1], "Route", None)])
    pieces = []
    for start_m, end_m, name, day in spans:
        pts = [point_at(start_m)]
        pts += [(*coords_ll[i], ele(cum[i])) for i in range(len(cum)) if start_m + 1e-6 < cum[i] < end_m - 1e-6]
        pts.append(point_at(end_m))
        pieces.append({"name": name, "points": pts, "start_m": start_m, "end_m": end_m, "length_m": end_m - start_m,
                       "gain_ft": day.gain_ft if day else route.gain_ft, "loss_ft": day.loss_ft if day else route.loss_ft,
                       "day": day.index if day else None})
    return pieces


def route_waypoints(route, from_text: str, to_text: str, plan=None) -> list[dict]:
    """Start, End, camps, and a waypoint at each trail-name change (junction), with mile markers."""
    import numpy as np

    prof = route.profile if route.profile and None not in route.profile["z_m"] else None

    def ele(dist):
        return float(np.interp(dist, prof["d_m"], prof["z_m"])) if prof else None

    (lon0, lat0), (lon1, lat1) = route.line_wgs84.coords[0], route.line_wgs84.coords[-1]
    x0, y0 = route.line_utm.coords[0][:2]
    x1, y1 = route.line_utm.coords[-1][:2]
    apart = math.hypot(x1 - x0, y1 - y0) > 1.0  # one way: start and finish are different places
    wps = [{"name": "Start", "desc": f"Start: {from_text}" if apart else from_text, "lat": lat0, "lon": lon0, "dist_m": 0.0,
            "ele": ele(0.0), "kind": "start"},
           {"name": "Finish" if apart else "End", "desc": f"Finish: {to_text}" if apart else to_text, "lat": lat1,
            "lon": lon1, "dist_m": route.length_m, "ele": ele(route.length_m), "kind": "end"}]
    for c in (plan.camps if plan else []):
        wps.append({"name": f"Camp {c.index}", "kind": "camp", "lat": c.lat, "lon": c.lon, "dist_m": c.dist_m,
                    "ele": ele(c.dist_m),
                    "desc": f"Camp {c.index}, mile {c.dist_m / M_PER_MI:.1f}, on {c.on_name or 'an unnamed trail'}. Location unverified."})
    to_ll = Transformer.from_crs(route.crs, "EPSG:4326", always_xy=True)
    cum, n = 0.0, 0
    for prev, e in zip(route.edges, route.edges[1:]):
        cum += prev["length_m"]
        a, b = prev["name"] or f"unnamed {prev['highway']}", e["name"] or f"unnamed {e['highway']}"
        if a != b:
            n += 1
            pt = route.line_utm.interpolate(cum)
            lon, lat = to_ll.transform(pt.x, pt.y)
            wps.append({"name": f"Jct {n}: {b}"[:40], "kind": "junction", "lat": lat, "lon": lon, "dist_m": cum,
                        "ele": ele(cum), "desc": f"mile {cum / M_PER_MI:.1f}: {a} to {b}"})
    return wps


def write_route(route, region, out_dir: Path, from_text: str, to_text: str, osm_retrieved_at: str,
                plan=None, features=None, basemap: str = "topo") -> dict[str, Path]:
    """route.gpx (one track, waypoints), day_<n>.gpx (one per day), route.geojson, map.html, summary.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"gpx": out_dir / "route.gpx", "geojson": out_dir / "route.geojson", "summary": out_dir / "summary.md",
             "map": out_dir / "map.html"}
    title = f"{from_text} to {to_text}"
    pieces = route_pieces(route, plan)
    waypoints = route_waypoints(route, from_text, to_text, plan)
    from backcountry.serialize import build_gpx, day_gpx, gpx_description  # local: serialize imports export

    desc = gpx_description(route, osm_retrieved_at)
    paths["gpx"].write_text(build_gpx(title, desc, pieces, waypoints))
    if plan:
        for piece in pieces:
            paths[f"day_{piece['day']}"] = out_dir / f"day_{piece['day']}.gpx"
            paths[f"day_{piece['day']}"].write_text(day_gpx(title, desc, piece, waypoints))

    geo = [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lon, lat, _ in p["points"]]},
            "properties": {"name": p["name"].split(":")[0], "day": p["day"], "length_m": p["length_m"],
                           "gain_ft": p["gain_ft"], "loss_ft": p["loss_ft"]}} for p in pieces]
    geo += [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [w["lon"], w["lat"]]},
             "properties": {"name": w["name"], "kind": w["kind"], "description": w["desc"], "dist_m": w["dist_m"]}}
            for w in waypoints]
    paths["geojson"].write_text(json.dumps({"type": "FeatureCollection", "attribution": OSM_ATTRIBUTION,
                                            "edges": [e["key"] for e in route.edges], "features": geo}))
    route_map(route, region, title, pieces, waypoints, features, osm_retrieved_at, basemap).save(str(paths["map"]))
    paths["summary"].write_text(route_markdown(route, region, from_text, to_text, osm_retrieved_at, plan, features))
    return paths


FEATURE_COLORS = {"camp": "#8d5524", "water": "#1565c0"}


def route_map(route, region, title: str, pieces, waypoints, features, osm_retrieved_at: str,
              basemap: str = "topo") -> folium.Map:
    lats = [lat for _, lat in route.line_wgs84.coords]
    lons = [lon for lon, _ in route.line_wgs84.coords]
    m = folium.Map(location=[(min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2], zoom_start=13, tiles=None,
                   control_scale=True)
    add_basemaps(m, basemap)
    for i, p in enumerate(pieces):
        color = CANDIDATE_COLORS[i % len(CANDIDATE_COLORS)]
        gain = f", +{p['gain_ft']:,.0f} / -{p['loss_ft']:,.0f} ft" if p["gain_ft"] is not None else ""
        label = f"{p['name']} ({mi(p['length_m'])}{gain})"
        group = folium.FeatureGroup(name=label)
        folium.PolyLine([(lat, lon) for lon, lat, _ in p["points"]], color=color, weight=5, opacity=0.85,
                        tooltip=label).add_to(group)
        group.add_to(m)
    marks = folium.FeatureGroup(name="Start, end, camps")
    jcts = folium.FeatureGroup(name="Trail junctions", show=False)
    is_loop = waypoints[0]["lat"] == waypoints[1]["lat"] and waypoints[0]["lon"] == waypoints[1]["lon"]
    for w in waypoints:
        if is_loop and w["kind"] == "end":
            continue  # same spot as the start on a loop; one badge says both
        if w["kind"] == "junction":
            folium.CircleMarker((w["lat"], w["lon"]), radius=4, color="#333", fill=True, fill_opacity=0.9,
                                tooltip=f"{w['name']}: {w['desc']}").add_to(jcts)
        else:
            text = ("S/E" if is_loop else "S") if w["kind"] == "start" else "E" if w["kind"] == "end" else f"C{w['name'].split()[-1]}"
            folium.Marker((w["lat"], w["lon"]), tooltip=f"{w['name']}: {w['desc']}",
                          icon=folium.DivIcon(html=f'<div class="bc-wp">{text}</div>', icon_size=(26, 18),
                                              icon_anchor=(13, 9))).add_to(marks)
    marks.add_to(m)
    jcts.add_to(m)
    if features:
        camps = folium.FeatureGroup(name="Camp sites (OSM/USFS; status unknown)")
        water = folium.FeatureGroup(name="Water features (OSM; presence unknown)")
        for f in features:
            is_camp = f["kind"] in ("camp_site", "camp_pitch") or f["kind"].startswith("usfs")
            color = FEATURE_COLORS["camp" if is_camp else "water"]
            tip = (f"{feature_title(f)}, mile {f['dist_m'] / M_PER_MI:.1f}, {ft(f['off_m'])} off route. "
                   + ("Status unknown." if is_camp else "Water presence unknown.") + " Click for details and a satellite link.")
            folium.CircleMarker((f["lat"], f["lon"]), radius=5 if is_camp else 4, color=color, fill=True,
                                fill_opacity=0.8, tooltip=tip,
                                popup=folium.Popup(evidence_html(f), max_width=360)).add_to(camps if is_camp else water)
        camps.add_to(m)
        water.add_to(m)
    rows = "".join(f'<div class="bc-row">{_swatch_line(CANDIDATE_COLORS[i % len(CANDIDATE_COLORS)], 5)}<span>{p["name"]}, '
                   f'{mi(p["length_m"])}</span></div>' for i, p in enumerate(pieces))
    legend = f"""
<style>
  #bc-legend {{ position: fixed; bottom: 28px; left: 12px; z-index: 9999; max-width: 300px; background: #fff; color: #1b1b1b;
    border-radius: 8px; box-shadow: 0 1px 8px rgba(0,0,0,.35); font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  #bc-legend summary {{ padding: 8px 12px; font-weight: 600; cursor: pointer; }}
  #bc-legend .bc-body {{ padding: 0 12px 10px; }}
  #bc-legend .bc-row {{ display: flex; align-items: center; gap: 8px; min-height: 20px; }}
  #bc-legend .bc-row svg {{ flex: 0 0 30px; }}
  #bc-legend .bc-src {{ margin-top: 8px; font-size: 11px; color: #666; }}
  .bc-wp {{ background: #1b5e20; color: #fff; font: 700 11px/18px sans-serif; text-align: center; border-radius: 4px;
    border: 1px solid #fff; box-shadow: 0 0 2px rgba(0,0,0,.6); }}
</style>
<div id="bc-legend"><details open><summary>{title}</summary><div class="bc-body">
  {rows}
  <div class="bc-row"><span class="bc-wp" style="width:26px">S/C1/E</span><span>start, camps, end</span></div>
  <div class="bc-row">{_swatch_dot(FEATURE_COLORS["camp"], FEATURE_COLORS["camp"], 5)}<span>OSM/USFS camp site (status unknown)</span></div>
  <div class="bc-row">{_swatch_dot(FEATURE_COLORS["water"], FEATURE_COLORS["water"], 4)}<span>water feature (presence unknown)</span></div>
  <div class="bc-src">Trail data {OSM_ATTRIBUTION}, retrieved {osm_retrieved_at[:10]}. Water, camp sites, permits, closures, conditions: unknown.</div>
</div></details></div>"""
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=False).add_to(m)
    return m


def route_segments(route) -> list[dict]:
    """Consecutive edges with the same name and type, merged, in walking order."""
    segments = []
    for e in route.edges:
        label = e["name"] or f"unnamed {e['highway']}"
        if segments and segments[-1]["label"] == label and segments[-1]["highway"] == e["highway"]:
            segments[-1]["length_m"] += e["length_m"]
            if e["source"] not in segments[-1]["sources"]:
                segments[-1]["sources"].append(e["source"])
        else:
            segments.append({"label": label, "highway": e["highway"], "is_road": e["is_road"],
                             "last_resort": e["last_resort"], "length_m": e["length_m"], "sources": [e["source"]]})
    return segments


def plan_markdown(plan) -> list[str]:
    """Days, camps, and any violated constraints for a split.DayPlan."""
    L = [f"## Days ({plan.nights} night{'s' if plan.nights != 1 else ''})", ""]
    for n in plan.notes:
        L.append(f"- {n}")
    for v in plan.violations:
        L.append(f"- **Constraint not met:** {v}")
    if plan.notes or plan.violations:
        L.append("")
    L += _table(["day", "from", "to", "distance", "gain", "loss"],
                [[f"Day {d.index}", d.start_label, d.end_label, mi(d.length_m),
                  f"{d.gain_ft:,.0f} ft" if d.gain_ft is not None else "unknown",
                  f"{d.loss_ft:,.0f} ft" if d.loss_ft is not None else "unknown"] for d in plan.days])
    L += ["", "Camps are points on the route chosen for mileage, or, in designated-site mode, known campsites from the "
          "map data (marked). Legality and water: unknown.", ""]
    for c in plan.camps:
        near = ("; ".join(f"{feature_title(n).lower()} {ft(n['distance_m'])} away ({source_link_md(n)})" for n in c.nearby)
                or f"no known camp site within {ft(NEARBY_CAMP_FT)}")
        how = "pinned by you" if c.pinned else "at a known campsite" if c.site else "placed for mileage"
        water = (f"nearest water feature: {feature_title(c.nearest_water).lower()}, {ft(c.nearest_water['distance_m'])} away "
                 f"(presence unknown)" if c.nearest_water else "nearest water feature: none listed")
        L.append(f"- **Camp {c.index}** at {_loc(c.lat, c.lon)}, {mi(c.dist_m)} in, on {c.on_name or 'an unnamed trail'}, "
                 f"{how}. Location unverified. Nearby camp sites: {near}. {water}.")
    L.append("")
    return L


def features_markdown(features) -> list[str]:
    from backcountry.features import NEAR_ROUTE_M, WATER_WARNING

    L = ["## Camp sites and water along the route", "",
         f"Map features within {ft(NEAR_ROUTE_M)} of the route, plus every stream the route crosses, from OpenStreetMap "
         f"(and USFS where noted). {WATER_WARNING}", ""]
    if not features:
        return L + ["None found in the source data.", ""]
    def tags(f):
        return "; ".join(f"{k}={v}" for k, v in sorted((f.get("tags") or {}).items()) if k not in HIDDEN_TAGS)

    def edited(f):
        meta = f.get("meta") or {}
        return f"{(meta.get('timestamp') or '')[:10]}, {meta.get('version')} edit(s)" if meta.get("timestamp") else None

    L += _table(["mile", "day", "feature", "off route", "last edited", "details", "source"],
                [[f"{f['dist_m'] / M_PER_MI:.2f}", f.get("day") or "—", feature_title(f), ft(f["off_m"]),
                  edited(f), tags(f), source_link_md(f)] for f in features])
    L += ["", "To check a site yourself: click its dot on the map for a satellite view and its OpenStreetMap entry. "
          "A feature mapped once, recently, with no details is weaker evidence than one with several edits and "
          "descriptive tags. USFS campgrounds are official; everything else is a volunteer map entry."]
    return L + [""]


NEARBY_CAMP_FT = 500.0  # meters; formatted in feet by ft()


def route_markdown(route, region, from_text: str, to_text: str, osm_retrieved_at: str, plan=None, features=None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gain = f"gain {route.gain_ft:,.0f} ft, loss {route.loss_ft:,.0f} ft" if route.gain_ft is not None else "gain: unknown, loss: unknown"
    L = [f"# Route: {from_text} to {to_text}", "", f"Region: {region.name}. Generated {now}.", "",
         f"**{mi(route.length_m)}**, {gain}. Road walking {mi(route.road_m)}"
         + (f", of which {mi(route.last_resort_m)} on last-resort paved roads" if route.last_resort_m else "") + ".", ""]
    L += [f"- {n}" for n in route.notes]
    if route.profile and route.gain_ft is not None:
        z = [v for v in route.profile["z_m"] if v is not None]
        L.append(f"- Elevation range along the route: {min(z) * FT_PER_M:,.0f} to {max(z) * FT_PER_M:,.0f} ft.")
    L += ["", "## Segments in order", ""]
    cumulative, rows = 0.0, []
    for i, s in enumerate(route_segments(route), 1):
        cumulative += s["length_m"]
        kind = ("last-resort road" if s["last_resort"] else "road" if s["is_road"] else "trail") + f" ({s['highway']})"
        rows.append([i, s["label"], kind, mi(s["length_m"]), mi(cumulative), ", ".join(s["sources"][:4])])
    L += _table(["#", "name", "type", "length", "cumulative", "OSM ways"], rows)
    if plan is not None:
        L += [""] + plan_markdown(plan)
    if features is not None:
        L += [""] + features_markdown(features)
    L += ["", "## Data sources", "", f"- Trail geometry: {OSM_ATTRIBUTION}, retrieved {osm_retrieved_at}."]
    if route.elevation_source:
        src = route.elevation_source
        L.append(f"- Elevation: {src.get('dataset') or src.get('title') or 'DEM'}, {src.get('attribution', '')}, "
                 f"retrieved {src.get('retrieved_at', 'unknown')}.")
    else:
        L.append("- Elevation: none (no DEM cached).")
    L += ["", "## Known gaps", "",
          "These are not checked by this tool: water, campsites, permits, closures, trail conditions, trailhead access. "
          "Camp sites and water listed above are map features only; whether they exist today, are open or legal to use, "
          "or hold water is **unknown**. This summary describes distance, elevation, map features, and data gaps only.", "",
          "## Files", "",
          "- `route.gpx`: the whole trip as one track (Garmin Connect imports only a file's first track) plus "
          "Start/End/camp/junction waypoints, with DEM elevations when available.",
          "- `day_<n>.gpx`: one file per day, one track each, for importing a single day as a course.",
          "- `map.html`: each day in its own color, with camps, junctions, camp sites and water features.",
          "- `route.geojson`: the same days and points as GeoJSON.", ""]
    return "\n".join(L)


# --- loop candidates (M4) --------------------------------------------------------


CANDIDATE_COLORS = ["#d32f2f", "#1976d2", "#388e3c", "#f57c00", "#7b1fa2"]


def candidate_record(i: int, c) -> dict:
    """What a diff needs to know about a candidate."""
    return {
        "index": i, "kind": c.kind, "length_m": c.length_m, "gain_ft": c.route.gain_ft, "loss_ft": c.route.loss_ft,
        "road_m": c.road_m, "repeated_m": c.repeated_m, "penalty": c.penalty, "keys": c.keys,
        "segments": [s["label"] for s in route_segments(c.route)],
        "days": [{"length_m": d.length_m, "gain_ft": d.gain_ft} for d in c.plan.days] if c.plan else None,
        "camps": [{"dist_m": cp.dist_m, "lat": cp.lat, "lon": cp.lon, "on": cp.on_name} for cp in c.plan.camps] if c.plan else None,
        "days_ok": c.plan.ok if c.plan else None,
    }


def diff_markdown(prev: dict, cur: dict) -> str:
    """How this run differs from the previous one written to the same folder."""
    L = [f"# What changed since the previous run", "",
         f"Previous run: {prev.get('generated')}. This run: {cur.get('generated')}.", "", "## Request", ""]
    keys = sorted(set(prev.get("request", {})) | set(cur.get("request", {})))
    changed = [(k, prev.get("request", {}).get(k), cur.get("request", {}).get(k)) for k in keys
               if prev.get("request", {}).get(k) != cur.get("request", {}).get(k)]
    L += ([f"- `{k}`: {a!r} -> {b!r}" for k, a, b in changed] or ["Same request as before."])
    L += ["", "## Candidates", ""]
    prev_c = prev.get("candidates", [])
    matched_prev = set()
    for c in cur.get("candidates", []):
        best, best_overlap = None, 0.0
        for p in prev_c:
            a, b = set(c["keys"]), set(p["keys"])
            ov = len(a & b) / len(a | b) if a | b else 0.0
            if ov > best_overlap:
                best, best_overlap = p, ov
        if best is None or best_overlap < 0.5:
            L.append(f"- **Candidate {c['index']}: new** ({mi(c['length_m'])}, "
                     f"{'+%s ft' % format(c['gain_ft'], ',.0f') if c['gain_ft'] is not None else 'gain unknown'}). "
                     f"Trails: {' > '.join(dict.fromkeys(c['segments']))}.")
            continue
        matched_prev.add(best["index"])
        if set(c["keys"]) == set(best["keys"]):
            line = f"- **Candidate {c['index']}: unchanged route** (was candidate {best['index']})"
        else:
            added = [s for s in dict.fromkeys(c["segments"]) if s not in best["segments"]]
            removed = [s for s in dict.fromkeys(best["segments"]) if s not in c["segments"]]
            line = (f"- **Candidate {c['index']}: changed** (was candidate {best['index']}, {best_overlap:.0%} of edges shared): "
                    f"{mi(best['length_m'])} -> {mi(c['length_m'])}")
            if c["gain_ft"] is not None and best["gain_ft"] is not None:
                line += f", gain {best['gain_ft']:,.0f} -> {c['gain_ft']:,.0f} ft"
            if added:
                line += f". Added: {', '.join(added)}"
            if removed:
                line += f". Dropped: {', '.join(removed)}"
        if c.get("camps") and best.get("camps"):
            moves = [f"Camp {i + 1} moved {abs(a['dist_m'] - b['dist_m']) / M_PER_MI:.1f} mi"
                     for i, (a, b) in enumerate(zip(c["camps"], best["camps"])) if abs(a["dist_m"] - b["dist_m"]) > 50]
            if moves:
                line += ". " + "; ".join(moves)
        elif c.get("camps") and not best.get("camps"):
            line += ". Days are now planned."
        L.append(line + ".")
    for p in prev_c:
        if p["index"] not in matched_prev:
            L.append(f"- Previous candidate {p['index']} ({mi(p['length_m'])}) is gone: "
                     f"{' > '.join(dict.fromkeys(p['segments']))}.")
    return "\n".join(L) + "\n"


def write_candidates(report: dict, region, out_dir: Path, start_text: str, osm_retrieved_at: str,
                     min_m: float, max_m: float, basemap: str = "topo", request: dict | None = None) -> dict[str, Path]:
    """candidates.md, candidates_map.html, search.json, candidates.json, candidate_<i>/ ..., and diff.md on a re-run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cands = report["candidates"]
    paths = {"summary": out_dir / "candidates.md", "map": out_dir / "candidates_map.html",
             "search": out_dir / "search.json", "records": out_dir / "candidates.json"}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    current = {"generated": now, "request": request or {}, "candidates": [candidate_record(i, c) for i, c in enumerate(cands, 1)]}
    if paths["records"].exists():
        previous = json.loads(paths["records"].read_text())
        (out_dir / "candidates.previous.json").write_text(json.dumps(previous, indent=1))
        paths["diff"] = out_dir / "diff.md"
        paths["diff"].write_text(diff_markdown(previous, current))
    paths["records"].write_text(json.dumps(current, indent=1))
    for i, c in enumerate(cands, 1):
        from backcountry.serialize import trip_title  # local: serialize imports export

        write_route(c.route, region, out_dir / f"candidate_{i}", start_text, trip_title(c.kind, start_text)[1],
                    osm_retrieved_at, plan=c.plan, features=getattr(c, "features", None), basemap=basemap)

    L = [f"# Loop candidates from {start_text}", "",
         f"Region: {region.name}. Requested {mi(min_m)} to {mi(max_m)}. Generated {now}.", ""]
    if request:
        L += ["Request: " + ", ".join(f"{k}={v}" for k, v in request.items() if v not in (None, [], False)), ""]
    for n in report.get("notes", []) + report.get("must_visit_notes", []):
        L += [f"- {n}"]
    if report.get("avoided_edges"):
        L += [f"- Avoided {report['avoided_edges']} trail segment(s) as requested."]
    if report.get("must_visit_notes") or report.get("avoided_edges"):
        L.append("")
    if report["widened"]:
        L += [f"**Nothing fit the requested range**, so the search was widened by {WIDEN_PCT}% and the "
              "distance deviation is included in each penalty.", ""]
    if report["truncated"]:
        L += ["**The search hit its time budget**, so some loops were never examined.", ""]
    L += [f"Searched {report['via_pairs_tried']:,} waypoint pairs and {report['stems_tried']} lollipop stems in "
          f"{report['seconds']:.1f} s; {report['scored']} distinct loops scored. "
          f"The {len(cands)} below are the lowest-penalty ones that overlap each other by less than 50%.", ""]
    L += _table(["#", "shape", "distance", "gain", "loss", "road", "walked twice", "days ok", "penalty"],
                [[i, c.kind, mi(c.length_m), f"{c.route.gain_ft:,.0f} ft" if c.route.gain_ft is not None else "unknown",
                  f"{c.route.loss_ft:,.0f} ft" if c.route.loss_ft is not None else "unknown", mi(c.road_m),
                  mi(c.repeated_m), ("yes" if c.plan.ok else "NO") if c.plan else "n/a", f"{c.penalty:.2f}"]
                 for i, c in enumerate(cands, 1)])
    L += ["", "Penalty = miles outside the range x10 + road miles x1 + last-resort road miles x5 + miles walked twice x0.5"
          + (" + the day plan's cost (miles outside the daily range x10, thousands of ft over the gain cap x5, "
             "imbalance x0.5)" if any(c.plan for c in cands) else "") + ". Lower is better.", ""]
    for i, c in enumerate(cands, 1):
        L += [f"## Candidate {i}: {c.kind}, {mi(c.length_m)}", ""]
        L += [f"- {n}" for n in c.route.notes]
        L += _table(["#", "name", "type", "length", "cumulative"],
                    [[j, s["label"], ("last-resort road" if s["last_resort"] else "road" if s["is_road"] else "trail"),
                      mi(s["length_m"]), mi(cum)]
                     for j, (s, cum) in enumerate(_cumulative(route_segments(c.route)), 1)])
        if c.plan is not None:
            L += [""] + ["#" + line if line.startswith("## ") else line for line in plan_markdown(c.plan)]
        if getattr(c, "features", None) is not None:
            L += ["#" + line if line.startswith("## ") else line for line in features_markdown(c.features)]
        L += [f"", f"Files: `candidate_{i}/route.gpx`, `candidate_{i}/summary.md`.", ""]
    L += ["## Not checked by this tool", "",
          "Water, campsites, permits, closures, trail conditions, and trailhead access: **unknown**.", "",
          f"Data: {OSM_ATTRIBUTION}, retrieved {osm_retrieved_at}.", ""]
    paths["summary"].write_text("\n".join(L))
    paths["search"].write_text(json.dumps({k: v for k, v in report.items() if k != "candidates"}, indent=1, default=str))
    candidates_map(cands, region, start_text, basemap).save(str(paths["map"]))
    return paths


WIDEN_PCT = 25


def _cumulative(segments):
    total = 0.0
    for s in segments:
        total += s["length_m"]
        yield s, total


def candidates_map(cands, region, start_text: str, basemap: str = "topo") -> folium.Map:
    if cands:
        lats = [lat for c in cands for _, lat in c.route.line_wgs84.coords]
        lons = [lon for c in cands for lon, _ in c.route.line_wgs84.coords]
        center = [(min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2]
    else:
        b = region.bbox
        center = [(b.south + b.north) / 2, (b.west + b.east) / 2]
    m = folium.Map(location=center, zoom_start=13, tiles=None, control_scale=True)
    add_basemaps(m, basemap)
    for i, c in enumerate(cands, 1):
        color = CANDIDATE_COLORS[(i - 1) % len(CANDIDATE_COLORS)]
        gain = f", +{c.route.gain_ft:,.0f} / -{c.route.loss_ft:,.0f} ft" if c.route.gain_ft is not None else ""
        label = f"Candidate {i}: {c.kind}, {mi(c.length_m)}{gain}, road {mi(c.road_m)}"
        group = folium.FeatureGroup(name=label)
        folium.PolyLine([(lat, lon) for lon, lat in c.route.line_wgs84.coords], color=color, weight=5, opacity=0.8,
                        tooltip=label).add_to(group)
        group.add_to(m)
    if cands:
        lon, lat = cands[0].route.line_wgs84.coords[0]
        folium.Marker((lat, lon), tooltip=f"Start: {start_text}").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    return m
