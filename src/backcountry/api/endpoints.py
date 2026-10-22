"""HTTP endpoints. Each one serializes engine results; none computes geometry."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from backcountry import ingest
from backcountry.api.store import NotFound
from backcountry.chat.service import chat_turn, start_names, trail_names
from backcountry.difficulty import rate_saved, segments_terrain
from backcountry.export import diff_markdown, edges_features
from backcountry.ingest import OSM_ATTRIBUTION
from backcountry.planning import GenerateRequest, RequestError, plan_trip
from backcountry.route import resolve_point
from backcountry.serialize import build_gpx, day_gpx, run_payload
from backcountry.trailheads import find_candidates

router = APIRouter(prefix="/api")


def _regions(request: Request):
    return request.app.state.regions


@router.get("/regions")
def regions(request: Request):
    return {"regions": _regions(request).summaries()}


@router.get("/regions/{region_id}/trailheads")
def trailheads(region_id: str, request: Request):
    loaded = _regions(request).get(region_id)
    G = loaded.tg.graph
    out = []
    for th in loaded.region.trailheads:
        node = th.get("node")
        if node is None and th.get("lat") is not None:  # verified lat/lon entry: snap to the nearest graph node
            try:
                node = resolve_point(loaded.tg, loaded.region, f"{th['lat']},{th['lon']}")["node"]
            except ValueError:
                node = None
        in_graph = node in G
        out.append({
            "id": th["id"], "name": th.get("name"), "node": node, "source": "yaml", "verified": bool(th.get("verified")),
            "in_graph": in_graph, "usfs": th.get("usfs"), "note": th.get("note"),
            "lat": G.nodes[node]["lat"] if in_graph else None, "lon": G.nodes[node]["lon"] if in_graph else None,
            "component": loaded.node_component.get(node),
        })
    known = {t["node"] for t in out if t["node"] is not None}
    for c in find_candidates(loaded.tg, loaded.raw, loaded.node_component)["candidates"]:
        if c["id"] in known:
            continue
        out.append({
            "id": str(c["id"]), "name": " | ".join(c["names"]) or None, "node": c["id"], "source": "data",
            "verified": False, "in_graph": True, "kinds": c["kinds"], "lat": c["lat"], "lon": c["lon"],
            "component": c["component"], "snap_m": c["snap_m"],
        })
    return {"region": region_id, "trailheads": out}


# --- trails and features ------------------------------------------------------------


@router.get("/regions/{region_id}/trails")
def trails(region_id: str, request: Request):
    loaded = _regions(request).get(region_id)
    return {"type": "FeatureCollection", "attribution": OSM_ATTRIBUTION,
            "features": edges_features(loaded.tg, loaded.node_component)}


def _parse_bbox(text: str) -> tuple[float, float, float, float]:
    try:
        w, s, e, n = (float(x) for x in text.split(","))
        if not (w < e and s < n):
            raise ValueError
    except (ValueError, AttributeError):
        raise RequestError("bbox", "bbox must be west,south,east,north in degrees")
    return w, s, e, n


@router.get("/regions/{region_id}/features")
def features(region_id: str, request: Request, bbox: str):
    loaded = _regions(request).get(region_id)
    w, s, e, n = _parse_bbox(bbox)

    def inside(lat, lon):
        return s <= lat <= n and w <= lon <= e

    water = ingest.water_features(loaded.raw)
    return {
        "camp_sites": [c for c in ingest.camp_sites(loaded.raw) if inside(c["lat"], c["lon"])],
        "water_points": [p for p in water["points"] if inside(p["lat"], p["lon"])],
        "streams": [l for l in water["lines"] if any(inside(lat, lon) for lon, lat in l["coords"])],
    }


# --- generate and runs ---------------------------------------------------------------


def _record(run: dict) -> dict:
    return {"generated": run["generated_at"], "request": run["request"],
            "candidates": [c["record"] for c in run["candidates"]]}


@router.post("/regions/{region_id}/generate")
def generate(region_id: str, request: Request, body: dict):
    loaded = _regions(request).get(region_id)
    req = GenerateRequest.from_dict(body)
    result = plan_trip(loaded.region, loaded.raw, loaded.tg, req, dem=loaded.dem,
                       raw_dir=request.app.state.settings.raw_dir)
    runs = request.app.state.runs
    previous = runs.latest(region_id, req.trailhead) if runs else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run = run_payload(runs.new_id() if runs else "unsaved", loaded.region, result, loaded.raw, loaded.tg, now, diff=None)
    if previous:
        run["diff"] = diff_markdown(_record(previous), _record(run))
    if runs:
        runs.save(run)
    return run


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@router.post("/difficulty")
def difficulty(request: Request, body: dict):
    """Re-rate a saved trip's days from its stored route (camps moved on the phone included), with the
    terrain rebuilt from this region's graph. For trips saved before levels existed, and after rule changes."""
    loaded = _regions(request).get(str(body.get("region")))
    prof = body.get("profile")
    if (not isinstance(prof, dict) or not isinstance(prof.get("d_m"), list) or not isinstance(prof.get("z_m"), list)
            or len(prof["d_m"]) != len(prof["z_m"]) or len(prof["d_m"]) < 2
            or not all(_is_number(x) for x in prof["d_m"]) or any(b <= a for a, b in zip(prof["d_m"], prof["d_m"][1:]))
            or not all(v is None or _is_number(v) for v in prof["z_m"])):
        raise RequestError("profile", "profile must be {d_m, z_m}: equal-length lists, d_m increasing")
    segs = body.get("segments")
    if not isinstance(segs, list) or not all(
            isinstance(s, dict) and _is_number(s.get("length_m")) and isinstance(s.get("sources"), list) for s in segs):
        raise RequestError("segments", "segments must be a list of {length_m, sources}")
    nights, camps = body.get("nights"), body.get("camps_m")
    if nights is not None and (not isinstance(nights, int) or isinstance(nights, bool) or nights < 1):
        raise RequestError("nights", "nights must be a whole number of at least 1, or null for a day hike")
    if nights:
        end = prof["d_m"][-1]
        if (not isinstance(camps, list) or len(camps) != nights or not all(_is_number(m) and 0 < m < end for m in camps)
                or camps != sorted(camps)):
            raise RequestError("camps_m", f"camps_m must be {nights} distance(s) in order along the route (0 to {end:.0f} m)")
    terrain = segments_terrain(loaded.tg, segs)
    return rate_saved(prof, terrain, nights, [float(m) for m in camps] if nights else None)


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return request.app.state.runs.load(run_id)


# --- downloads --------------------------------------------------------------------------


def _candidate(request: Request, run_id: str, n: int) -> tuple[dict, dict]:
    run = request.app.state.runs.load(run_id)
    if not 1 <= n <= len(run["candidates"]):
        raise NotFound(f"Run {run_id} has {len(run['candidates'])} candidates; there is no candidate {n}")
    return run, run["candidates"][n - 1]


@router.get("/runs/{run_id}/candidates/{n}.gpx")
def candidate_gpx(run_id: str, n: int, request: Request, day: int | None = None):
    run, c = _candidate(request, run_id, n)
    pieces = [{**p, "points": [tuple(pt) for pt in p["points"]]} for p in c["pieces"]]
    if day is None:
        text, name = build_gpx(c["title"], c["description"], pieces, c["waypoints"]), f"{run_id}_candidate_{n}.gpx"
    else:
        piece = next((p for p in pieces if p["day"] == day), None)
        if piece is None:
            raise NotFound(f"Candidate {n} has no day {day}")
        text, name = day_gpx(c["title"], c["description"], piece, c["waypoints"]), f"{run_id}_candidate_{n}_day_{day}.gpx"
    return Response(content=text, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/runs/{run_id}/candidates/{n}.geojson")
def candidate_geojson(run_id: str, n: int, request: Request):
    run, c = _candidate(request, run_id, n)
    feats = [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lon, lat, _ in p["points"]]},
              "properties": {"name": p["name"].split(":")[0], "day": p["day"], "length_m": p["length_m"],
                             "gain_ft": p["gain_ft"], "loss_ft": p["loss_ft"]}} for p in c["pieces"]]
    feats += [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [w["lon"], w["lat"]]},
               "properties": {"name": w["name"], "kind": w["kind"], "description": w["desc"], "dist_m": w["dist_m"]}}
              for w in c["waypoints"]]
    return {"type": "FeatureCollection", "attribution": run["data_sources"][0]["attribution"], "features": feats}


# --- chat ---------------------------------------------------------------------------------


@router.post("/chat")
def chat(request: Request, body: dict):
    run_id, n = body.get("run_id"), body.get("candidate")
    message = body.get("message") if isinstance(body.get("message"), str) else ""
    message = message.strip()
    history = body.get("history") or []
    if not isinstance(run_id, str) or not run_id:
        raise RequestError("run_id", "run_id is required")
    if not isinstance(n, int) or isinstance(n, bool):
        raise RequestError("candidate", "candidate must be a whole number")
    if not message:
        raise RequestError("message", "Type a message first.")
    if len(message) > 1000:
        raise RequestError("message", "Keep messages under 1,000 characters.")
    if not isinstance(history, list):
        raise RequestError("history", "history must be a list of messages")
    run, _ = _candidate(request, run_id, n)
    loaded = _regions(request).get(run["region"])
    return chat_turn(request.app.state.chat_model(), run, n, message, history, trail_names(loaded.tg), start_names(loaded.region))
