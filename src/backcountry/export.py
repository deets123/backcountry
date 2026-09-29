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

from backcountry.ingest import OSM_ATTRIBUTION, ROAD_HIGHWAYS, TRAIL_HIGHWAYS

M_PER_MI = 1609.344
FT_PER_M = 3.28084
USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
MAIN_COLOR = "#1f4fd1"
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


def write_graph_geojson(tg, node_component: dict, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    G = tg.graph
    edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        props = {key: d[key] for key in ("source", "way_id", "segment", "retrieved_at", "name", "highway", "is_road",
                                          "length_m", "tags")}
        props.update(key=k, u=u, v=v, component=node_component[u])
        edges.append({"type": "Feature", "geometry": mapping(d["geom_wgs84"]), "properties": props})
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
    return paths


# --- QA outputs ---------------------------------------------------------------


def write_qa_outputs(summary: dict, tg, raw: dict, region, out_dir: Path, coverage: list[dict]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"report": out_dir / "qa_report.md", "json": out_dir / "qa_report.json", "map": out_dir / "qa_map.html"}
    paths["report"].write_text(qa_markdown(summary, tg, raw, region, coverage))
    paths["json"].write_text(json.dumps(summary | {"bbox_coverage": coverage}, indent=1, default=str))
    qa_map(summary, tg, raw, region).save(str(paths["map"]))
    return paths


def qa_markdown(summary: dict, tg, raw: dict, region, coverage: list[dict]) -> str:
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
        f"Routable types: trails = {', '.join(TRAIL_HIGHWAYS)}; roads (`is_road`) = {', '.join(ROAD_HIGHWAYS)}. "
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

    L += ["", "## Trail tag values (trail edges only)", ""]
    for key, values in summary["tag_values"].items():
        L.append(f"- `{key}`: " + ", ".join(f"{v} {mi(m)}" for v, m in values.items()))
    L.append("")
    return "\n".join(L)


def qa_map(summary: dict, tg, raw: dict, region) -> folium.Map:
    G = tg.graph
    node_comp = summary["node_component"]
    comps = summary["components"]
    colors = {x["id"]: MAIN_COLOR if i == 0 else PALETTE[(i - 1) % len(PALETTE)] for i, x in enumerate(comps)}
    b = region.bbox
    m = folium.Map(location=[(b.south + b.north) / 2, (b.west + b.east) / 2], zoom_start=12, tiles=None,
                   control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(tiles=USGS_TOPO, attr="USGS The National Map", name="USGS Topo").add_to(m)

    layers = {"main": [], "other": []}
    for u, _, d in G.edges(data=True):
        comp = node_comp[u]
        layers["main" if comp == "C0" else "other"].append({
            "type": "Feature",
            "geometry": mapping(d["geom_wgs84"]),
            "properties": {"component": comp, "name": d["name"] or "(unnamed)", "highway": d["highway"],
                           "source": d["source"], "length": mi(d["length_m"]), "color": colors[comp],
                           "dash": "8 6" if d["is_road"] else None},
        })
    main_len = mi(comps[0]["length_m"]) if comps else "0 mi"
    for key, title in (("main", f"C0 main network ({main_len})"), ("other", f"Other components ({len(comps) - 1})")):
        folium.GeoJson(
            {"type": "FeatureCollection", "features": layers[key]},
            name=title,
            style_function=lambda f, w=5 if key == "main" else 4: {
                "color": f["properties"]["color"], "weight": w, "opacity": 0.85, "dashArray": f["properties"]["dash"]},
            tooltip=folium.GeoJsonTooltip(fields=["component", "name", "highway", "source", "length"]),
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

    dead = folium.FeatureGroup(name=f"Dead ends ({len(summary['dead_ends'])})")
    near = folium.FeatureGroup(name="Possible missing junctions")
    for d in summary["dead_ends"]:
        text = f"dead end osm:node/{d['node']} ({d['component']}) on {d['name'] or '(unnamed)'} {d['way']}"
        if d["note"]:
            text += f"<br>{d['note']}"
        if d["near_miss_m"] is not None:
            folium.CircleMarker((d["lat"], d["lon"]), radius=9, color="#d00", weight=3, fill=False,
                                tooltip=text).add_to(near)
        folium.CircleMarker((d["lat"], d["lon"]), radius=4, color="#e08000", fill=True, fill_opacity=0.9,
                            tooltip=text).add_to(dead)
    dead.add_to(m)
    near.add_to(m)

    short = folium.FeatureGroup(name=f"Very short edges ({len(summary['short_edges'])})", show=False)
    for e in summary["short_edges"]:
        folium.CircleMarker((e["lat"], e["lon"]), radius=5, color="#7b1fa2", fill=True,
                            tooltip=f"short edge {e['source']} {e['name'] or ''}: {ft(e['length_m'])}").add_to(short)
    short.add_to(m)

    boundary = folium.FeatureGroup(name="OSM boundary")
    for e in raw["response"]["elements"]:
        if "boundary" not in e.get("tags", {}):
            continue
        parts = [mem.get("geometry") for mem in e.get("members", []) if mem.get("type") == "way"]
        parts += [e["geometry"]] if e["type"] == "way" else []
        for geom in parts:
            if geom:
                folium.PolyLine([(g["lat"], g["lon"]) for g in geom if g], color="#2e7d32", weight=2,
                                dash_array="6 4", tooltip=f"{e['tags'].get('name')} (osm:{e['type']}/{e['id']})").add_to(boundary)
    boundary.add_to(m)
    folium.Rectangle([(b.south, b.west), (b.north, b.east)], color="#000", weight=1, dash_array="2 4", fill=False,
                     tooltip=f"region bbox ({region.id})").add_to(m)

    legend = f"""
    <div style="position: fixed; bottom: 24px; left: 12px; z-index: 9999; background: rgba(255,255,255,0.92);
                padding: 8px 10px; border: 1px solid #999; border-radius: 4px; font: 12px/1.5 sans-serif; max-width: 320px;">
      <b>QA component map: {region.name}</b><br>
      <span style="color:{MAIN_COLOR}">&#9644;&#9644;</span> C0 main network ({main_len})<br>
      other colors: {len(comps) - 1} smaller components (hover for id)<br>
      dashed line = road (<code>is_road</code>)<br>
      <span style="color:#e08000">&#9679;</span> dead end &nbsp;
      <span style="color:#d00">&#9711;</span> possible missing junction<br>
      <span style="color:#2e7d32">- - -</span> OSM boundary &nbsp; <span>&middot;&middot;&middot;</span> region bbox<br>
      <span style="color:#555">Data {OSM_ATTRIBUTION}; retrieved {summary['retrieved_at']}.</span>
    </div>"""
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=False).add_to(m)
    return m
