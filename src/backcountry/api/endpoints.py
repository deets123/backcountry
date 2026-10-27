"""HTTP endpoints. Each one serializes engine results; none computes geometry."""

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from backcountry import ingest
from backcountry import osm_index
from backcountry.api.area_worker import NoIndex
from backcountry.api.store import NotFound
from backcountry.chat.service import chat_turn, start_names, trail_names
from backcountry.destination import check_ref, trail_distances
from backcountry.destination import resolve as resolve_destination
from backcountry.difficulty import rate_saved, segments_terrain
from backcountry.export import diff_markdown, edges_features
from backcountry.ingest import OSM_ATTRIBUTION
from backcountry.planning import GenerateRequest, RequestError, parse_range, plan_route, plan_trip, suggest_finishes
from backcountry.scoring import M_PER_MI
from backcountry.retrace import changes as trip_changes
from backcountry.retrace import retrace
from backcountry.region import load_region
from backcountry.route import resolve_point
from backcountry.serialize import build_gpx, day_gpx, run_payload
from backcountry.trailheads import find_candidates

router = APIRouter(prefix="/api")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _regions(request: Request):
    return request.app.state.regions


@router.get("/regions")
def regions(request: Request):
    return {"regions": _regions(request).summaries(), "recent_areas": _regions(request).recent_areas()}


@contextmanager
def _index_for(app, req_destination):
    """An index connection when a destination needs looking up, else None."""
    if req_destination is None:
        yield None
        return
    try:
        con = app.state.areas.index()
    except NoIndex as e:
        raise RequestError("destination", "Destinations need the trail index on this server.") from e
    try:
        yield con
    finally:
        con.close()


def _trailhead_list(loaded) -> list[dict]:
    """The region's trailheads (yaml entries first), then start points found in the data."""
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
    return out


@router.get("/regions/{region_id}/trailheads")
def trailheads(region_id: str, request: Request, destination: str | None = None):
    """Trailheads; with `destination` (a search result's ref), each gets its trail distance to it, nearest first."""
    if destination is not None:
        check_ref(destination)
    loaded = _regions(request).get(region_id)
    out = _trailhead_list(loaded)
    if destination is not None:
        with _index_for(request.app, destination) as con:
            dist = trail_distances(loaded.tg, resolve_destination(con, loaded.tg, destination))
        for t in out:
            d = dist.get(t["node"]) if t["in_graph"] else None
            t["to_destination_m"] = round(d, 1) if d is not None else None
        out.sort(key=lambda t: (t["to_destination_m"] is None, t["to_destination_m"] or 0.0))
    return {"region": region_id, "trailheads": out}


@router.get("/regions/{region_id}/finishes")
def finishes(region_id: str, request: Request, trailhead: str, total: str, destination: str | None = None):
    """Trailheads a one-way trip from `trailhead` can finish at within `total` miles (via `destination`)."""
    lo, hi = parse_range(total, "total")
    if destination is not None:
        check_ref(destination)
    loaded = _regions(request).get(region_id)
    try:
        start = resolve_point(loaded.tg, loaded.region, trailhead)["node"]
    except ValueError as e:
        raise RequestError("trailhead", str(e)) from e
    through = None
    if destination is not None:
        with _index_for(request.app, destination) as con:
            through = resolve_destination(con, loaded.tg, destination).node
    return {"finishes": suggest_finishes(loaded.tg, _trailhead_list(loaded), start, lo * M_PER_MI, hi * M_PER_MI,
                                         through=through, dem=loaded.dem)}


# --- search and areas ------------------------------------------------------------------------


_AREA_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


@router.get("/search")
def search(request: Request, q: str = ""):
    """Hand-made regions first, then any US area or place in the local index."""
    text = q.strip()
    if not 1 <= len(text) <= 100:
        raise RequestError("q", "Type 1 to 100 characters to search.")
    store, worker = _regions(request), request.app.state.areas
    regions = [load_region(rid, store.settings.regions_dir) for rid in store.ids()]
    out = [{"kind": "region", "id": r.id, "name": r.name, "type_label": "Region", "state": None}
           for r in regions if text.lower() in r.name.lower()]
    try:
        con = worker.index()
    except NoIndex:
        return {"results": out, "index": False}
    try:
        found = osm_index.search(con, text)
        group = osm_index.name_group(con, text)
    finally:
        con.close()
    curated = [re.compile(r.boundary_name_regex, re.I) for r in regions if r.boundary_name_regex]
    if group and not any(c.search(a["name"]) for c in curated for a in group["areas"]):
        g = group["areas"]
        bbox = [min(a["bbox"][0] for a in g), min(a["bbox"][1] for a in g), max(a["bbox"][2] for a in g),
                max(a["bbox"][3] for a in g)]
        out.append({"kind": "group", "refs": [a["osm"] for a in g], "name": group["name"], "type_label": "Areas",
                    "state": g[0]["state"], "bbox": bbox, "center": g[0]["center"]})
    for f in found:
        if f["kind"] == "area" and any(c.search(f["name"]) for c in curated):
            continue  # a hand-made region covers it (with its curated trailheads)
        out.append({"kind": f["kind"], "ref": f["osm"], "name": f["name"], "type_label": f["type_label"],
                    "state": f["state"], "bbox": f["bbox"], "center": f["center"]})
    return {"results": out, "index": True}


@router.post("/areas")
def open_area(request: Request, body: dict):
    """Start preparing an area (or find it ready). Returns its id and status; poll /areas/{id}/status."""
    worker = request.app.state.areas
    try:
        spec = worker.spec_for(body)
    except ValueError as e:
        raise RequestError("area", str(e)) from e
    st = worker.request(spec)
    return {k: st[k] for k in ("area_id", "name", "status", "message", "bbox")}


@router.get("/areas/{area_id}/status")
def area_status(area_id: str, request: Request):
    if not _AREA_ID.match(area_id):
        raise RequestError("area_id", "Not an area id.")
    return request.app.state.areas.status_of(area_id)


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


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan(app, region_id: str, req: GenerateRequest, on_snapshot=None) -> dict:
    """Plan and save a run. `on_snapshot(run)` gets the first trips found (not saved) while the search goes on."""
    loaded = app.state.regions.get(region_id)

    def snapshot(result):
        run = run_payload("partial", loaded.region, result, loaded.raw, loaded.tg, _now(), diff=None)
        on_snapshot(run | {"partial": True})

    with _index_for(app, req.destination) as con:
        result = plan_trip(loaded.region, loaded.raw, loaded.tg, req, dem=loaded.dem, raw_dir=app.state.settings.raw_dir,
                           on_snapshot=snapshot if on_snapshot else None, index=con,
                           feature_index=loaded.feature_index(app.state.settings.raw_dir))
    runs = app.state.runs
    previous = runs.latest(region_id, req.trailhead) if runs else None
    run = run_payload(runs.new_id() if runs else "unsaved", loaded.region, result, loaded.raw, loaded.tg, _now(),
                      diff=None) | {"partial": False}
    if previous:
        run["diff"] = diff_markdown(_record(previous), _record(run))
    if runs:
        runs.save(run)
    return run


@router.post("/regions/{region_id}/generate")
def generate(region_id: str, request: Request, body: dict):
    return _plan(request.app, region_id, GenerateRequest.from_dict(body))


@router.post("/regions/{region_id}/plans")
def start_plan(region_id: str, request: Request, body: dict):
    """Start planning in the background (settings are checked first); poll /plans/{job_id}."""
    req = GenerateRequest.from_dict(body)
    app = request.app
    return {"job_id": app.state.plans.start(lambda on_snapshot: _plan(app, region_id, req, on_snapshot))}


@router.get("/plans/{job_id}")
def plan_status(job_id: str, request: Request):
    return request.app.state.plans.get(job_id)


@router.delete("/plans/{job_id}")
def cancel_plan(job_id: str, request: Request):
    request.app.state.plans.cancel(job_id)
    return {"ok": True}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@router.post("/difficulty")
def difficulty(request: Request, body: dict):
    """Re-rate a saved trip's days from its stored route (camps moved on the phone included), with the
    terrain rebuilt from this region's graph. For trips saved before levels existed, and after rule changes."""
    cut = body.get("cut")
    if cut is not None and (not isinstance(cut, str) or not _DATE.match(cut)):
        raise RequestError("cut", "cut must be an extract date like 2026-10-20, or null")
    loaded = _regions(request).get(str(body.get("region")), cut)
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
    geometry = body.get("geometry")
    if geometry is not None and (not isinstance(geometry, list) or not all(
            isinstance(c, list) and len(c) >= 2 and all(_is_number(x) for x in c[:2]) for c in geometry)):
        raise RequestError("geometry", "geometry must be the route's [lon, lat] points")
    terrain = segments_terrain(loaded.tg, segs, geometry)
    return rate_saved(prof, terrain, nights, [float(m) for m in camps] if nights else None)


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return request.app.state.runs.load(run_id)


# --- saved trips and newer data ---------------------------------------------------------------


def _latest_of(loaded) -> str:
    """The data version a region would plan on now: an area's extract date, or a hand-made region's retrieval time."""
    return loaded.cut or loaded.raw["retrieved_at"]


def _version_of(run: dict) -> str | None:
    return run.get("data_cut") or (run.get("data_sources") or [{}])[0].get("retrieved_at")


def _candidate_n(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RequestError("candidate", "candidate must be a whole number")
    return value


@router.get("/runs/{run_id}/update")
def update_check(run_id: str, request: Request, candidate: int = 1):
    """Is there newer trail data for this saved trip's area? Changes nothing."""
    run, c = _candidate(request, run_id, candidate)
    loaded = _regions(request).get(run["region"])
    have, latest = _version_of(run), _latest_of(loaded)
    pending = request.app.state.areas.newer_data(run["region"]) if loaded.region.generated else None
    latest = max(latest, pending or latest)  # a refreshed index: its cut is being made now
    return {"newer": bool(have and latest > have), "cut": have, "latest": latest,
            "approx_bytes": len(json.dumps(c))}


@router.post("/runs/{run_id}/update")
def update_trip(run_id: str, request: Request, body: dict):
    """Follow the saved route on the newest data and plan it the same way; returns the new run and what changed.
    The old run is never modified; the phone keeps the old trip unless the user accepts."""
    run, c = _candidate(request, run_id, _candidate_n(body.get("candidate")))
    loaded = _regions(request).get(run["region"])
    pending = request.app.state.areas.newer_data(run["region"]) if loaded.region.generated else None
    if pending and pending > _latest_of(loaded):
        return {"ok": False, "message": "The newer trail data for this area is still being set up. Try again in a minute. "
                                        "Your trip is unchanged."}
    if not (_version_of(run) and _latest_of(loaded) > _version_of(run)):
        raise RequestError("update", "This trip already uses the newest trail data.")
    followed = retrace(loaded.tg, c)
    if not followed["ok"]:
        if followed.get("reason"):
            return {"ok": False, "message": "This trip was saved before routes were recorded, so it can't be followed "
                                            "on newer data. Plan it again to use the newer data. Your trip is unchanged."}
        names = ", ".join(m["name"] or "an unnamed trail" for m in followed["missing"])
        return {"ok": False, "message": f"The route no longer follows the same trails in the newer data (missing: {names}). "
                                        "Your trip is unchanged."}
    req = GenerateRequest.from_dict(run["request"])
    result = plan_route(loaded.region, loaded.raw, loaded.tg, req, followed["route"], c["kind"],
                        (c.get("route") or {}).get("stem_keys") or (), dem=loaded.dem,
                        raw_dir=request.app.state.settings.raw_dir,
                        feature_index=loaded.feature_index(request.app.state.settings.raw_dir))
    result.start = run["start"]
    if (run.get("search") or {}).get("finish") and "finish" in result.report:  # keep how the finish was named
        result.report["finish"] = run["search"]["finish"] | {"node": result.report["finish"]["node"]}
    runs = request.app.state.runs
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = run_payload(runs.new_id(), loaded.region, result, loaded.raw, loaded.tg, now, diff=None)
    runs.save(new)
    return {"ok": True, "run": new, "changes": trip_changes(c, new["candidates"][0])}


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
    loaded = _regions(request).get(run["region"], run.get("data_cut"))
    return chat_turn(request.app.state.chat_model(), run, n, message, history, trail_names(loaded.tg), start_names(loaded.region))
