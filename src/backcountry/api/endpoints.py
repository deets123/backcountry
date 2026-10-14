"""HTTP endpoints. Each one serializes engine results; none computes geometry."""

from fastapi import APIRouter, Request

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
        node = th["node"]
        in_graph = node in G
        out.append({
            "id": th["id"], "name": th.get("name"), "node": node, "source": "yaml", "verified": bool(th.get("verified")),
            "in_graph": in_graph, "usfs": th.get("usfs"), "note": th.get("note"),
            "lat": G.nodes[node]["lat"] if in_graph else None, "lon": G.nodes[node]["lon"] if in_graph else None,
            "component": loaded.node_component.get(node),
        })
    known = {th["node"] for th in loaded.region.trailheads}
    for c in find_candidates(loaded.tg, loaded.raw, loaded.node_component)["candidates"]:
        if c["id"] in known:
            continue
        out.append({
            "id": str(c["id"]), "name": " | ".join(c["names"]) or None, "node": c["id"], "source": "data",
            "verified": False, "in_graph": True, "kinds": c["kinds"], "lat": c["lat"], "lon": c["lon"],
            "component": c["component"], "snap_m": c["snap_m"],
        })
    return {"region": region_id, "trailheads": out}
