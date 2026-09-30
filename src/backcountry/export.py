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
from shapely.geometry import mapping

from backcountry.ingest import LAST_RESORT_HIGHWAYS, OSM_ATTRIBUTION, ROAD_HIGHWAYS, TRAIL_HIGHWAYS, USFS_ATTRIBUTION

M_PER_MI = 1609.344
FT_PER_M = 3.28084
# Base maps: USGS National Map only (public domain). OSM's volunteer tile servers
# block pages opened from disk, and CLAUDE.md rule 6 says to respect tile terms.
USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
USGS_IMAGERY = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"
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


def write_graph_geojson(tg, node_component: dict, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    G = tg.graph
    edges, profiles = [], {}
    for u, v, k, d in G.edges(keys=True, data=True):
        props = {key: d[key] for key in ("source", "way_id", "segment", "retrieved_at", "name", "highway", "is_road",
                                          "last_resort", "length_m", "tags")}
        props.update({key: d[key] for key in ELEVATION_KEYS if key in d})
        props.update(key=k, u=u, v=v, from_node=d["osm_nodes"][0], component=node_component[u])
        edges.append({"type": "Feature", "geometry": mapping(d["geom_wgs84"]), "properties": props})
        if "profile" in d:
            profiles[k] = {"from_node": d["osm_nodes"][0], "source": d["source"]} | d["profile"]
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
    trails = v["usfs_trails"]
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
        f"- USFS lists {len(trails)} official trails with {mi(usfs_total)} inside the region bbox. Routable OSM lines "
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
