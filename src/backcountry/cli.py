"""Command-line interface: `backcountry <command> --region <id>`."""

import typer

from backcountry import ingest
from backcountry.export import ft, mi, write_graph_geojson, write_qa_outputs, write_verify_report
from backcountry.graph import bbox_coverage, build_graph, qa_summary
from backcountry.region import REPO_ROOT, load_region
from backcountry.trailheads import find_candidates
from backcountry.verify import verify

OUT_DIR = REPO_ROOT / "out"
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


@app.command(name="ingest")
def ingest_cmd(
    region: str = RegionOpt,
    refresh: bool = typer.Option(False, help="Re-query Overpass even if a cached response exists."),
):
    """Fetch OSM trail data for a region and cache the raw response."""
    r = load_region(region)
    cached = ingest.latest_cache(r)
    path = ingest.fetch(r, refresh=refresh)
    raw = ingest.load_raw(r)
    verb = "Using cached" if cached == path else "Fetched"
    typer.echo(f"{verb} {path}")
    typer.echo(f"retrieved_at {raw['retrieved_at']}, OSM base {raw.get('osm_base')}, "
               f"{len(raw['response']['elements'])} elements. {ingest.OSM_ATTRIBUTION}")



@app.command()
def qa(region: str = RegionOpt):
    """Build the trail graph and write a QA report + component map."""
    r, raw, tg = _load(region)
    summary = qa_summary(tg)
    coverage = bbox_coverage(raw, r)
    graph_paths = write_graph_geojson(tg, summary["node_component"], PROCESSED_DIR / r.id)
    usfs = ingest.load_usfs(r) if r.usfs_crosscheck else None  # overlay only if already cached
    verification = verify(tg, raw, usfs, r, summary["node_component"]) if usfs else None
    paths = write_qa_outputs(summary, tg, raw, r, OUT_DIR / "qa" / r.id, coverage, verification)

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
    qa_paths = write_qa_outputs(summary, tg, raw, r, out, bbox_coverage(raw, r), v)

    typer.echo(f"OSM retrieved {v['osm_retrieved_at']}, USFS retrieved {v['usfs_retrieved_at']} ({usfs['cache_path']})")
    for t in v["usfs_trails"]:
        gaps = f", {len(t['gaps'])} gap(s) {ft(sum(g['length_m'] for g in t['gaps']))}" if t["gaps"] else ""
        typer.echo(f"  {t['label'][:40]:40} {mi(t['usfs_m']):>9} in bbox, {t['within_match_pct']:5.1f}% within 100 ft, "
                   f"median {ft(t['median_offset_m'])}{gaps}")
    matched = sum(1 for t in v["trailheads"] if t["osm_trailhead"])
    typer.echo(f"trailheads: {len(v['trailheads'])} USFS, {matched} with an OSM trailhead within 490 ft")
    typer.echo(f"report: {paths['report']}\nmap:    {qa_paths['map']}")
