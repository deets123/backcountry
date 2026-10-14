"""Command-line interface: `backcountry <command> --region <id>`."""

import re
from pathlib import Path

import typer
import yaml

from backcountry import ingest
from backcountry.elevation import FT_PER_M, annotate, graph_bounds_ll, line_profile
from backcountry.export import (ft, mi, route_segments, write_candidates, write_graph_geojson, write_qa_outputs,
                                write_route, write_trail_gpx, write_verify_report)
from backcountry.graph import bbox_coverage, build_graph, chain_named_trail, qa_summary
from backcountry.loops import avoided_keys
from backcountry.planning import GenerateRequest, RequestError, plan_trip
from backcountry.region import REPO_ROOT, load_region
from backcountry.route import add_elevation, resolve_point, shortest_route
from backcountry.scoring import M_PER_MI
from backcountry.trailheads import find_candidates
from backcountry.verify import verify

OUT_DIR = REPO_ROOT / "out"
REFERENCE_DIR = REPO_ROOT / "tests" / "reference"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

app = typer.Typer(help="Backcountry route engine (Phase 1: Dolly Sods, CLI only).", no_args_is_help=True)

RegionOpt = typer.Option(..., "--region", help="Region id, e.g. dolly_sods")


def _load(region_id: str):
    region = load_region(region_id)
    try:
        raw = ingest.load_raw(region)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    return region, raw, build_graph(raw, region.crs)


def _elevate(region, tg) -> dict | None:
    """Annotate edges with elevation if a DEM is cached; None means elevation is unknown."""
    dem = ingest.load_dem(region)
    return annotate(tg, dem) if dem is not None else None


@app.command(name="ingest")
def ingest_cmd(
    region: str = RegionOpt,
    refresh: bool = typer.Option(False, help="Re-query Overpass even if a cached response exists."),
):
    """Fetch OSM trail data for a region and cache the raw response."""
    r = load_region(region)
    cached = ingest.latest_cache(r)
    try:
        path = ingest.fetch(r, refresh=refresh)
    except ingest.FetchError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    raw = ingest.load_raw(r)
    verb = "Using cached" if cached == path else "Fetched"
    typer.echo(f"{verb} {path}")
    typer.echo(f"retrieved_at {raw['retrieved_at']}, OSM base {raw.get('osm_base')}, "
               f"{len(raw['response']['elements'])} elements. {ingest.OSM_ATTRIBUTION}")



@app.command()
def qa(region: str = RegionOpt):
    """Build the trail graph and write a QA report + component map."""
    r, raw, tg = _load(region)
    elevation = _elevate(r, tg)
    summary = qa_summary(tg)
    coverage = bbox_coverage(raw, r)
    graph_paths = write_graph_geojson(tg, summary["node_component"], PROCESSED_DIR / r.id)
    usfs = ingest.load_usfs(r) if r.usfs_crosscheck else None  # overlay only if already cached
    verification = verify(tg, raw, usfs, r, summary["node_component"]) if usfs else None
    paths = write_qa_outputs(summary, tg, raw, r, OUT_DIR / "qa" / r.id, coverage, verification, elevation)

    c, t = summary["counts"], summary["totals"]
    typer.echo(f"{r.name}: data retrieved {summary['retrieved_at']} ({raw['cache_path']})")
    typer.echo(f"{c['nodes']} nodes, {c['edges']} edges, {c['components']} components; "
               f"{mi(t['length_m'])} total ({mi(t['trail_m'])} trail, {mi(t['road_m'])} road)")
    for comp in summary["components"][:8]:
        gap = f", {ft(comp['gap_to_main_m'])} from C0" if comp["gap_to_main_m"] is not None else ""
        typer.echo(f"  {comp['id']:>4}: {mi(comp['length_m']):>10}, {len(comp['nodes'])} nodes{gap}  "
                   f"{', '.join(comp['names'][:4])}")
    if len(summary["components"]) > 8:
        typer.echo(f"  ... {len(summary['components']) - 8} more in the report")
    near = sum(1 for d in summary["dead_ends"] if d["near_miss_m"] is not None)
    typer.echo(f"dead ends {len(summary['dead_ends'])} (possible missing junctions {near}), "
               f"short edges {len(summary['short_edges'])}, excluded ways {len(summary['excluded'])}")
    for cov in coverage:
        typer.echo(f"bbox covers {cov['name']}: {'yes' if cov['inside_bbox'] else 'NO'}")
    typer.echo(f"report: {paths['report']}\nmap:    {paths['map']}\njson:   {paths['json']}")
    typer.echo(f"graph:  {graph_paths['edges']}")
    if elevation is None:
        typer.echo("elevation: unknown (no DEM cached; run `backcountry dem`)")
    else:
        typer.echo(f"elevation: {len(elevation['missing'])} edges without DEM coverage; profiles: {graph_paths['profiles']}")


@app.command()
def trailheads(
    region: str = RegionOpt,
    all_components: bool = typer.Option(False, "--all", help="Include candidates outside the main component C0."),
):
    """List candidate trailheads found in source data (nothing is hardcoded)."""
    r, raw, tg = _load(region)
    summary = qa_summary(tg)
    result = find_candidates(tg, raw, summary["node_component"])
    shown = [c for c in result["candidates"] if all_components or c["component"] == "C0"]
    typer.echo(f"{len(shown)} candidates" + ("" if all_components else " in C0 (use --all for other components)")
               + f". Data retrieved {tg.retrieved_at}. Access, parking, and road status: unknown.")
    typer.echo(f"{'id':>12}  {'comp':4}  {'kinds':48}  {'lat, lon':20}  {'snap':>7}  name / sources")
    for c in shown:
        typer.echo(f"{c['id']:>12}  {c['component']:4}  {','.join(c['kinds']):48}  "
                   f"{c['lat']:.5f}, {c['lon']:.5f}  {ft(c['snap_m']):>7}  "
                   f"{' | '.join(c['names']) or '(unnamed)'}  [{', '.join(c['sources'][:3])}]")
    for u in result["unsnapped"]:
        typer.echo(f"not snapped: {u['source']} {u['name'] or ''} is {ft(u['distance_m'])} from the nearest graph node")


@app.command(name="verify")
def verify_cmd(
    region: str = RegionOpt,
    refresh: bool = typer.Option(False, help="Re-query the USFS services even if a cached response exists."),
):
    """Cross-check the graph against official USFS trails, roads, and trailheads."""
    r, raw, tg = _load(region)
    if not r.usfs_crosscheck:
        typer.echo(f"Region {r.id!r} doesn't enable usfs_crosscheck in its yaml.", err=True)
        raise typer.Exit(code=1)
    ingest.fetch_usfs(r, refresh=refresh)
    usfs = ingest.load_usfs(r)
    summary = qa_summary(tg)
    v = verify(tg, raw, usfs, r, summary["node_component"])
    out = OUT_DIR / "qa" / r.id
    paths = write_verify_report(v, r, out)
    qa_paths = write_qa_outputs(summary, tg, raw, r, out, bbox_coverage(raw, r), v, _elevate(r, tg))

    typer.echo(f"OSM retrieved {v['osm_retrieved_at']}, USFS retrieved {v['usfs_retrieved_at']} ({usfs['cache_path']})")
    for t in v["usfs_trails"]:
        gaps = f", {len(t['gaps'])} gap(s) {ft(sum(g['length_m'] for g in t['gaps']))}" if t["gaps"] else ""
        typer.echo(f"  {t['label'][:40]:40} {mi(t['usfs_m']):>9} in bbox, {t['within_match_pct']:5.1f}% within 100 ft, "
                   f"median {ft(t['median_offset_m'])}{gaps}")
    matched = sum(1 for t in v["trailheads"] if t["osm_trailhead"])
    typer.echo(f"trailheads: {len(v['trailheads'])} USFS, {matched} with an OSM trailhead within 490 ft")
    typer.echo(f"report: {paths['report']}\nmap:    {qa_paths['map']}")


@app.command()
def dem(
    region: str = RegionOpt,
    refresh: bool = typer.Option(False, help="Re-download even if a cached DEM covers the graph."),
):
    """Fetch and cache the USGS 3DEP ~10 m DEM covering every edge in the graph."""
    r, _, tg = _load(region)
    bounds = graph_bounds_ll(tg)
    path = ingest.fetch_dem(r, bounds, refresh=refresh)
    d = ingest.load_dem(r)
    typer.echo(f"DEM: {path}")
    typer.echo(f"retrieved {d.source['retrieved_at']}, {d.array.shape[1]} x {d.array.shape[0]} cells, {d.crs}. "
               f"{d.source['attribution']}")
    for s in d.source["sources"]:
        typer.echo(f"  {s['title']} (published {s['publication_date']})")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@app.command()
def reference(
    region: str = RegionOpt,
    trail: str = typer.Option(..., help="OSM trail name, e.g. 'Big Stonecoal Trail'"),
    out_dir: Path = typer.Option(None, help="Where to write the GPX + YAML (default tests/reference)."),
    lidar: bool = typer.Option(True, help="Also compute gain on USGS 1 m lidar (downloads a small window once)."),
):
    """Export one trail as GPX for a CalTopo cross-check, and print our distance/gain/loss for it."""
    r, raw, tg = _load(region)
    out_dir = out_dir or REFERENCE_DIR
    try:
        chain = chain_named_trail(tg, trail)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    slug = slugify(trail)
    gpx_path = write_trail_gpx(chain, out_dir / f"{slug}.gpx", tg.retrieved_at)
    stub_path = write_reference_stub(out_dir / f"{slug}.yaml", r, trail, gpx_path.name, chain["sources"], tg.retrieved_at)
    typer.echo(f"{trail}: {mi(chain['length_m'])}, {len(chain['coords_wgs84'])} points from {', '.join(chain['sources'])}")
    typer.echo(f"gpx:  {gpx_path}\nyaml: {stub_path}")

    rows = []
    dem10 = ingest.load_dem(r)
    if dem10 is not None:
        rows.append(("USGS ~10 m DEM", line_profile(chain["line_utm"], dem10, tg.crs)))
    if lidar:
        west, south, east, north = _padded_bounds(chain["coords_wgs84"], pad=0.002)
        ingest.fetch_dem(r, (west, south, east, north), kind="1m", name=slug)
        rows.append(("USGS 1 m lidar", line_profile(chain["line_utm"], ingest.load_dem(r, kind="1m", name=slug), tg.crs)))
    for label, p in rows:
        if p["gain_m"] is None:
            typer.echo(f"  {label:16} elevation unknown ({p['missing']} of {len(p['d_m'])} samples have no DEM value)")
        else:
            typer.echo(f"  {label:16} gain {p['gain_m'] * FT_PER_M:,.0f} ft, loss {p['loss_m'] * FT_PER_M:,.0f} ft "
                       f"(in the GPX's direction)")
    if dem10 is None:
        typer.echo("  ~10 m DEM not cached; run `backcountry dem` for our main numbers.")


def write_reference_stub(path: Path, region, name: str, gpx_name: str, sources: list[str], retrieved_at: str) -> Path:
    """YAML for CalTopo's numbers. Never overwrites one that exists (it may hold numbers already copied in)."""
    if not path.exists():
        path.write_text(
            "# CalTopo cross-check. Import the .gpx into CalTopo, then copy its numbers below.\n"
            + yaml.safe_dump({
                "region": region.id,
                "name": name,
                "gpx": gpx_name,
                "exported_from": f"{', '.join(sources)} (OSM retrieved {retrieved_at})",
                "caltopo": {"distance_mi": None, "gain_ft": None, "loss_ft": None},
            }, sort_keys=False)
        )
    return path


def _padded_bounds(coords: list, pad: float) -> tuple[float, float, float, float]:
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


@app.command()
def route(
    region: str = RegionOpt,
    from_: str = typer.Option(..., "--from", help="Trailhead id from the region yaml, an OSM node id, or lat,lon"),
    to: str = typer.Option(..., "--to", help="Same forms as --from"),
    no_roads: bool = typer.Option(False, "--no-roads", help="Refuse to use any road edge."),
    road_factor: float = typer.Option(1.0, help="A road mile costs this many trail miles when choosing the path."),
    avoid: list[str] = typer.Option(None, "--avoid", help="A trail/road name or way id to stay off. Repeatable."),
    out_dir: Path = typer.Option(None, help="Output directory (default out/routes/<from>-to-<to>)."),
    reference: str = typer.Option(None, help="Also save the GPX + a CalTopo stub as tests/reference/<name>."),
):
    """Shortest point-to-point route, exported as GPX with distance and gain."""
    r, raw, tg = _load(region)
    try:
        a, b = resolve_point(tg, r, from_), resolve_point(tg, r, to)
        rt = shortest_route(tg, a["node"], b["node"], no_roads=no_roads, road_factor=road_factor,
                            exclude_keys=avoided_keys(tg, avoid) if avoid else frozenset())
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    dem = ingest.load_dem(r)
    if dem is not None:
        add_elevation(rt, dem, tg.crs)
    else:
        rt.notes.append("elevation unknown: no DEM cached (run `backcountry dem`)")
    for p in (a, b):
        if p["snap_m"]:
            rt.notes.append(f"{p['how']}, {ft(p['snap_m'])} away")
    out_dir = out_dir or OUT_DIR / "routes" / f"{slugify(from_)}-to-{slugify(to)}"
    paths = write_route(rt, r, out_dir, a["how"], b["how"], tg.retrieved_at)
    gain = f"gain {rt.gain_ft:,.0f} ft, loss {rt.loss_ft:,.0f} ft" if rt.gain_ft is not None else "elevation unknown"
    typer.echo(f"{a['how']} -> {b['how']}: {mi(rt.length_m)}, {gain}; road {mi(rt.road_m)}"
               + (f" (last resort {mi(rt.last_resort_m)})" if rt.last_resort_m else ""))
    for n in rt.notes:
        typer.echo(f"  note: {n}")
    typer.echo(f"gpx: {paths['gpx']}\nsummary: {paths['summary']}")
    if reference:
        slug = slugify(reference)
        gpx = REFERENCE_DIR / f"{slug}.gpx"
        gpx.parent.mkdir(parents=True, exist_ok=True)
        gpx.write_text(paths["gpx"].read_text())
        sources = list(dict.fromkeys(e["source"] for e in rt.edges))
        stub = write_reference_stub(REFERENCE_DIR / f"{slug}.yaml", r, f"{a['how']} to {b['how']}", gpx.name, sources,
                                    tg.retrieved_at)
        typer.echo(f"reference: {gpx}\n           {stub}")


@app.command()
def generate(
    region: str = RegionOpt,
    trailhead: str = typer.Option(..., help="Trailhead id from the region yaml, an OSM node id, or lat,lon"),
    total: str = typer.Option(..., help="Total distance range in miles, e.g. 18-26"),
    type_: str = typer.Option("any", "--type", help="loop, lollipop, or any"),
    count: int = typer.Option(3, help="How many candidates to return"),
    no_roads: bool = typer.Option(False, "--no-roads", help="Refuse to use any road edge."),
    road_weight: float = typer.Option(1.0, help="Penalty per road mile"),
    repeat_weight: float = typer.Option(0.5, help="Penalty per mile walked twice (lollipop stem)"),
    nights: int = typer.Option(None, help="Split each candidate into nights+1 days (needs --daily)"),
    daily: str = typer.Option(None, help="Daily distance range in miles, e.g. 6-11"),
    max_gain_day: float = typer.Option(None, help="Daily elevation gain cap in feet, e.g. 2500"),
    camp: list[str] = typer.Option(None, "--camp", help="Pin a camp: miles in (6.5), lat,lon, or osm:node/<id>. Repeatable."),
    camps_at_sites: bool = typer.Option(False, "--camps-at-sites",
                                        help="Designated-site mode: camps only at known camp sites on the route."),
    basemap: str = typer.Option("topo", help="Map background: topo, hybrid, or satellite"),
    must_visit: list[str] = typer.Option(None, "--must-visit",
                                         help="A place the loop must include: trailhead id, OSM node id, lat,lon, or a trail name. Repeatable."),
    avoid: list[str] = typer.Option(None, "--avoid", help="A trail/road name or way id to stay off. Repeatable."),
    direction: str = typer.Option(None, help="Walk loops clockwise (cw) or counterclockwise (ccw)."),
    out_dir: Path = typer.Option(None, help="Output directory (default out/loops/<trailhead>_<total>mi)"),
):
    """Generate candidate loops from a trailhead within a total distance range, optionally split into days."""
    try:
        req = GenerateRequest.from_dict({
            "trailhead": trailhead, "total": total, "nights": nights, "daily": daily, "max_gain_day": max_gain_day,
            "type": type_, "count": count, "no_roads": no_roads, "road_weight": road_weight,
            "repeat_weight": repeat_weight, "camps": camp or [], "camps_at_sites": camps_at_sites,
            "must_visit": must_visit or [], "avoid": avoid or [], "direction": direction,
        })
    except RequestError as e:
        typer.echo(f"--{e.field.replace('_', '-')}: {e}", err=True)
        raise typer.Exit(code=1)
    r, raw, tg = _load(region)
    try:
        result = plan_trip(r, raw, tg, req)
    except RequestError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    report, start = result.report, result.start
    lo_mi, hi_mi = req.total
    request = {"trailhead": trailhead, "total": total, "type": type_, "nights": nights, "daily": daily,
               "max_gain_day": max_gain_day, "no_roads": no_roads, "road_weight": road_weight, "camps": camp or [],
               "camps_at_sites": camps_at_sites, "must_visit": must_visit or [], "avoid": avoid or [],
               "direction": direction}  # the CLI's own record format, unchanged from before the API
    out_dir = out_dir or OUT_DIR / "loops" / f"{slugify(trailhead)}_{total}mi"
    try:
        paths = write_candidates(report, r, out_dir, start["how"], tg.retrieved_at, lo_mi * M_PER_MI, hi_mi * M_PER_MI,
                                 basemap=basemap, request=request)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    typer.echo(f"{start['how']}, {total} mi: {len(report['candidates'])} candidates from {report['scored']} loops "
               f"({report['via_pairs_tried']:,} waypoint pairs, {report['seconds']:.1f} s)"
               + (" [widened: nothing fit the range]" if report["widened"] else "")
               + (" [time budget hit]" if report["truncated"] else "")
               + (f" [avoiding {report['avoided_edges']} segment(s)]" if report.get("avoided_edges") else "")
               + (f" [direction {direction}]" if direction else ""))
    for n in report.get("notes", []) + report.get("must_visit_notes", []):
        typer.echo(f"  {n}")
    for i, c in enumerate(report["candidates"], 1):
        gain = f"+{c.route.gain_ft:,.0f}/-{c.route.loss_ft:,.0f} ft" if c.route.gain_ft is not None else "elevation unknown"
        names = [s["label"] for s in route_segments(c.route)]
        typer.echo(f"  {i}. {c.kind:8} {mi(c.length_m):>9}  {gain:>18}  road {mi(c.road_m):>8}  penalty {c.penalty:5.2f}")
        typer.echo(f"     " + " > ".join(dict.fromkeys(names)))
        if c.plan is not None:
            for d in c.plan.days:
                g = f"+{d.gain_ft:,.0f} ft" if d.gain_ft is not None else "gain unknown"
                typer.echo(f"     Day {d.index}: {d.start_label} -> {d.end_label}, {mi(d.length_m)}, {g}")
            for cp in c.plan.camps:
                near = f"{len(cp.nearby)} known camp site(s) within {ft(500)}" if cp.nearby else "no known camp site nearby"
                how = "pinned" if cp.pinned else "at a known campsite" if cp.site else "placed for mileage"
                water_txt = (f"nearest water: {cp.nearest_water['kind']} {ft(cp.nearest_water['distance_m'])} away (presence unknown)"
                             if cp.nearest_water else "no water feature listed nearby")
                typer.echo(f"     Camp {cp.index}: {cp.lat:.5f}, {cp.lon:.5f} on {cp.on_name or 'an unnamed trail'}, {how}; {near}; {water_txt}")
            for n in c.plan.notes:
                typer.echo(f"     note: {n}")
            for v in c.plan.violations:
                typer.echo(f"     NOT MET: {v}")
        if c.features:
            crossings = sum(1 for f in c.features if f["kind"].endswith("crossing"))
            typer.echo(f"     features: {len(c.features)} along the route ({crossings} stream crossings); see summary.md. "
                       "Water presence unknown.")
    typer.echo(f"summary: {paths['summary']}\nmap:     {paths['map']}")
    if "diff" in paths:
        typer.echo(f"diff:    {paths['diff']} (what changed since the previous run in this folder)")


def _run_server(host: str, port: int) -> None:
    import uvicorn

    from backcountry.api.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Use 0.0.0.0 to let your phone connect over Wi-Fi."),
    port: int = typer.Option(8000),
):
    """Run the API (and the web app once it's built) for the browser."""
    typer.echo(f"Serving on http://{host}:{port} (API at /api). Ctrl-C to stop.")
    _run_server(host, port)
