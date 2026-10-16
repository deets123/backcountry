"""JSON shapes for the API (Run, Candidate) and GPX built from those shapes.

A GPX built from a stored run is byte-identical to the CLI's route.gpx for the same
request because write_route uses the same builders.
"""

from dataclasses import asdict

from shapely.geometry import mapping

from backcountry.export import candidate_record, route_pieces, route_segments, route_waypoints
from backcountry.ingest import DEM_ATTRIBUTION, OSM_ATTRIBUTION

KNOWN_GAPS = [
    "water: map features only; whether water is present, flowing, or drinkable is unknown",
    "campsites: map features only; existence, status, and legality are unknown",
    "permits: not checked",
    "closures: not checked",
    "trail conditions: not checked",
    "trailhead access: not checked",
]


def gpx_description(route, osm_retrieved_at: str) -> str:
    sources = ", ".join(dict.fromkeys(e["source"] for e in route.edges))
    text = f"Trail data {OSM_ATTRIBUTION}, retrieved {osm_retrieved_at}. Geometry is exact OSM vertices from {sources}."
    if route.elevation_source:
        src = route.elevation_source
        text += f" Elevations from {src.get('dataset', 'DEM')} ({src.get('attribution', '')})."
    else:
        text += " No elevations."
    return text


def _gpx_doc(name: str, description: str):
    import gpxpy.gpx

    g = gpxpy.gpx.GPX()
    g.creator = "backcountry route engine"
    g.name, g.description = name, description
    return g


def _track(piece: dict):
    import gpxpy.gpx

    track = gpxpy.gpx.GPXTrack(name=piece["name"])
    track.segments.append(gpxpy.gpx.GPXTrackSegment(
        [gpxpy.gpx.GPXTrackPoint(lat, lon, elevation=ele) for lon, lat, ele in piece["points"]]))
    return track


def _waypoint(w: dict):
    import gpxpy.gpx

    return gpxpy.gpx.GPXWaypoint(w["lat"], w["lon"], elevation=w["ele"], name=w["name"], description=w["desc"])


def build_gpx(title: str, description: str, pieces: list[dict], waypoints: list[dict]) -> str:
    """The whole trip as ONE track: Garmin Connect imports only a file's first track. Day pieces share
    their boundary point, so each later piece drops its first point. Days live in the per-day files
    (day_gpx) and as camp waypoints."""
    g = _gpx_doc(title, description)
    points = []
    for p in pieces:
        points += p["points"] if not points else p["points"][1:]
    g.tracks.append(_track({"name": title, "points": points}))
    g.waypoints += [_waypoint(w) for w in waypoints]
    return g.to_xml()


def day_gpx(title: str, description: str, piece: dict, waypoints: list[dict]) -> str:
    g = _gpx_doc(f"{title}, {piece['name']}", description)
    g.tracks.append(_track(piece))
    g.waypoints += [_waypoint(w) for w in waypoints if piece["start_m"] - 1 <= w["dist_m"] <= piece["end_m"] + 1]
    return g.to_xml()


def camp_payload(camp) -> dict:
    return asdict(camp)


def plan_payload(plan) -> dict | None:
    if plan is None:
        return None
    return {
        "nights": plan.nights, "daily_min_m": plan.daily_min_m, "daily_max_m": plan.daily_max_m,
        "max_gain_ft": plan.max_gain_ft, "days": [asdict(d) for d in plan.days],
        "camps": [camp_payload(c) for c in plan.camps], "ok": plan.ok, "violations": list(plan.violations),
        "notes": list(plan.notes), "penalty": plan.penalty,
    }


def candidate_payload(i: int, c, start_text: str, osm_retrieved_at: str) -> dict:
    title = f"{start_text} to {start_text} (loop)"
    pieces = route_pieces(c.route, c.plan)
    for p in pieces:
        p["points"] = [list(pt) for pt in p["points"]]
    return {
        "index": i, "kind": c.kind, "length_m": c.length_m, "gain_ft": c.route.gain_ft, "loss_ft": c.route.loss_ft,
        "road_m": c.road_m, "last_resort_m": c.last_resort_m, "repeated_m": c.repeated_m, "penalty": c.penalty,
        "segments": route_segments(c.route),
        "geometry": mapping(c.route.line_wgs84),
        "pieces": pieces,
        "waypoints": route_waypoints(c.route, start_text, f"{start_text} (loop)", c.plan),
        "plan": plan_payload(c.plan),
        "features": c.features or [],
        "profile": c.route.profile,
        "title": title,
        "description": gpx_description(c.route, osm_retrieved_at),
        "notes": list(c.route.notes),
        "record": candidate_record(i, c),
    }


def data_sources(region, raw, result) -> list[dict]:
    out = [{"name": "OpenStreetMap via Overpass API", "retrieved_at": raw["retrieved_at"], "attribution": OSM_ATTRIBUTION}]
    src = next((c.route.elevation_source for c in result.report["candidates"] if c.route.elevation_source), None)
    if src:
        out.append({"name": src.get("dataset", "DEM"), "retrieved_at": src.get("retrieved_at"), "attribution": DEM_ATTRIBUTION})
    return out


def run_payload(run_id: str, region, result, raw, tg, generated_at: str, diff: str | None) -> dict:
    report = result.report
    return {
        "run_id": run_id, "region": region.id, "request": result.request.to_dict(), "generated_at": generated_at,
        "start": result.start,
        "search": {k: v for k, v in report.items() if k != "candidates"},
        "candidates": [candidate_payload(i, c, result.start["how"], tg.retrieved_at)
                       for i, c in enumerate(report["candidates"], 1)],
        "data_sources": data_sources(region, raw, result),
        "known_gaps": KNOWN_GAPS,
        "diff": diff,
    }
