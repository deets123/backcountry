import json

import gpxpy
import pytest

from backcountry.export import write_route
from backcountry.graph import build_graph
from backcountry.planning import GenerateRequest, plan_trip
from backcountry.region import BBox, Region
from backcountry.serialize import KNOWN_GAPS, build_gpx, candidate_payload, day_gpx, run_payload
from conftest import CRS

REGION = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))


@pytest.fixture
def planned(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1,
                                     "daily": "0.5-4", "type": "loop"})
    return raw, tg, plan_trip(REGION, raw, tg, req)


def test_candidate_payload_is_json_and_matches_spec(planned):
    raw, tg, result = planned
    c = result.report["candidates"][0]
    p = candidate_payload(1, c, "osm:node/1000", tg.retrieved_at)
    json.dumps(p)  # JSON-able, no shapely or dataclass objects left
    assert p["index"] == 1 and p["kind"] == "loop"
    assert p["geometry"]["type"] == "LineString" and len(p["geometry"]["coordinates"]) == len(c.route.line_wgs84.coords)
    assert [piece["name"] for piece in p["pieces"]] == ["Day 1: Start to Camp 1", "Day 2: Camp 1 to End"]
    assert all(pt[2] is None for piece in p["pieces"] for pt in piece["points"])  # no DEM in this test
    assert [w["name"] for w in p["waypoints"]][:3] == ["Start", "End", "Camp 1"]
    assert p["plan"]["days"][0]["start_label"] == "Start" and len(p["plan"]["camps"]) == 1
    assert p["plan"]["camps"][0]["note"] == "camp_location: unverified"
    assert isinstance(p["features"], list) and "d_m" in p["profile"]
    assert [s["label"] for s in p["segments"]][0]


def test_gpx_from_payload_matches_write_route_bytes(planned, tmp_path):
    raw, tg, result = planned
    c = result.report["candidates"][0]
    paths = write_route(c.route, REGION, tmp_path / "cli", "osm:node/1000", "osm:node/1000 (loop)", tg.retrieved_at,
                        plan=c.plan, features=c.features)
    p = candidate_payload(1, c, "osm:node/1000", tg.retrieved_at)
    assert build_gpx(p["title"], p["description"], p["pieces"], p["waypoints"]) == paths["gpx"].read_text()
    assert day_gpx(p["title"], p["description"], p["pieces"][1], p["waypoints"]) == (tmp_path / "cli" / "day_2.gpx").read_text()


def test_gpx_survives_json_round_trip(planned):
    raw, tg, result = planned
    p = json.loads(json.dumps(candidate_payload(1, result.report["candidates"][0], "osm:node/1000", tg.retrieved_at)))
    gpx = gpxpy.parse(build_gpx(p["title"], p["description"], p["pieces"], p["waypoints"]))
    assert len(gpx.tracks) == 1 and gpx.waypoints[0].name == "Start"  # one track: Garmin imports only the first
    n_points = sum(len(piece["points"]) for piece in p["pieces"]) - (len(p["pieces"]) - 1)  # day pieces share boundaries
    assert len(gpx.tracks[0].segments[0].points) == n_points


def test_run_payload_has_provenance_and_gaps(planned):
    raw, tg, result = planned
    run = run_payload("20260101T000000Z-abcd", REGION, result, raw, tg, "2026-01-01T00:00:00Z", diff=None)
    json.dumps(run)
    assert run["run_id"] == "20260101T000000Z-abcd" and run["region"] == "synthetic"
    assert run["request"]["total"] == [2.4, 5.1]
    assert "candidates" not in run["search"] and run["search"]["scored"] >= 3
    assert len(run["candidates"]) == 3
    assert run["data_sources"][0]["name"].startswith("OpenStreetMap") and run["data_sources"][0]["retrieved_at"]
    assert run["known_gaps"] == KNOWN_GAPS and "water" in KNOWN_GAPS[0]
    assert run["diff"] is None and run["start"]["node"] == result.start["node"]


# --- difficulty levels in the payload ------------------------------------------------


def _plan(grid, dem, **extra):
    from conftest import plane_dem  # noqa: F401  (dem built by callers)

    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "type": "loop"} | extra)
    return raw, tg, plan_trip(REGION, raw, tg, req, dem=dem)


def test_payload_has_levels_terrain_and_rules(grid):
    from conftest import plane_dem

    from backcountry.difficulty import RULES

    raw, tg, result = _plan(grid, plane_dem(0.05, 0.02, 500), nights=1, daily="0.5-4")
    run = json.loads(json.dumps(run_payload("r", REGION, result, raw, tg, "2026-01-01T00:00:00Z", diff=None)))
    c = run["candidates"][0]
    days = c["plan"]["days"]
    assert all(d["rating"]["level"] in ("easy", "moderate", "hard", "strenuous") for d in days)
    order = ["easy", "moderate", "hard", "strenuous"]
    assert c["level"] == max((d["rating"]["level"] for d in days), key=order.index)
    assert c["terrain"] == [] and c["rating"] is None  # the grid has no terrain tags; backpacking trips rate per day
    assert run["difficulty_rules"] == RULES and run["difficulty_rules"]["version"] == RULES["version"]


def test_plan_without_dem_has_unknown_levels(grid):
    raw, tg, result = _plan(grid, None, nights=1, daily="0.5-4")
    c = candidate_payload(1, result.report["candidates"][0], "x", tg.retrieved_at)
    assert all(d["rating"]["level"] is None for d in c["plan"]["days"])
    assert c["level"] is None


def test_day_hike_is_rated_on_the_day_hike_scale(grid):
    from conftest import plane_dem

    from backcountry.difficulty import rate_stretch

    raw, tg, result = _plan(grid, plane_dem(0.05, 0.02, 500))
    cand = result.report["candidates"][0]
    c = candidate_payload(1, cand, "x", tg.retrieved_at)
    assert c["plan"] is None and c["rating"]["level"] is not None and c["level"] == c["rating"]["level"]
    prof = cand.route.profile
    want = rate_stretch(prof["d_m"], prof["z_m"], [], 0.0, prof["d_m"][-1], cand.route.gain_ft, "day_hike")
    assert c["rating"]["effort"] == pytest.approx(want.effort)


def test_terrain_tags_reach_the_payload(grid):
    from conftest import plane_dem

    for e in grid.elements:
        if e.get("id") == 110:  # Col 0
            e["tags"]["sac_scale"] = "alpine_hiking"
    raw, tg, result = _plan(grid, plane_dem(0.0, 0.0, 500), nights=1, daily="0.5-4")
    cands = [candidate_payload(i, c, "x", tg.retrieved_at) for i, c in enumerate(result.report["candidates"], 1)]
    tagged = [c for c in cands if any(t["reason"] == "scramble (tagged)" for t in c["terrain"])]
    assert tagged and all(c["level"] == "hard" for c in tagged)
    day = next(d for d in tagged[0]["plan"]["days"] if d["rating"]["tagged"])
    assert "scramble (tagged)" in day["rating"]["reasons"]


def test_day_targets_reach_the_split(grid):
    from conftest import plane_dem

    raw, tg, result = _plan(grid, plane_dem(0.0, 0.0, 500), nights=1, daily="0.5-4",
                            days=[{"day": 1, "miles": [0.5, 1.0]}])
    plan = result.report["candidates"][0].plan
    assert plan.targets[0]["miles"] == [0.5, 1.0] and plan.targets[1]["miles"] == [0.5, 4.0]
    assert plan.days[0].length_m <= 1.0 * 1609.344 + 1 or any("Day 1:" in v for v in plan.violations)


def test_one_way_gpx_has_start_and_finish(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["T"]), "total": "2.5-4.4", "type": "one_way",
                                     "finish": str(grid.ids["g22"])})
    result = plan_trip(REGION, raw, tg, req)
    run = run_payload("r1", REGION, result, raw, tg, "2026-10-27T00:00:00Z", None)
    p = run["candidates"][0]
    names = [w["name"] for w in p["waypoints"]]
    assert names[:2] == ["Start", "Finish"] and "End" not in names
    assert p["waypoints"][1]["desc"].startswith("Finish: ") and p["waypoints"][0]["desc"].startswith("Start: ")
    assert p["finish"]["node"] == grid.ids["g22"] and p["kind"] == "one_way"
    assert "Shuttle drive: unknown." in p["description"]
    assert "(loop)" not in p["title"] and "one way" in p["title"]
    gpx = gpxpy.parse(build_gpx(p["title"], p["description"], p["pieces"], p["waypoints"]))
    assert {w.name for w in gpx.waypoints} >= {"Start", "Finish"}


def test_loop_gpx_unchanged(planned):
    raw, tg, result = planned
    p = candidate_payload(1, result.report["candidates"][0], "osm:node/1000", tg.retrieved_at)
    assert [w["name"] for w in p["waypoints"]][:2] == ["Start", "End"] and p["finish"] is None
    assert p["title"].endswith("(loop)") and "Shuttle" not in p["description"]


def test_out_and_back_payload_names_its_turnaround(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "1.5-2.2", "type": "out_and_back"})
    c = plan_trip(REGION, raw, tg, req).report["candidates"][0]
    p = candidate_payload(1, c, "osm:node/1000", tg.retrieved_at)
    assert p["kind"] == "out_and_back" and p["turnaround"] == c.turnaround and "out and back" in p["title"]


# --- no raw OSM ids in anything a GPX or a phone shows ------------------------------------------------------


def _no_osm_ids(run: dict) -> None:
    for c in run["candidates"]:
        assert "osm:" not in c["title"], c["title"]
        for w in c["waypoints"]:
            assert "osm:" not in w["name"] and "osm:" not in w["desc"], w
        if c.get("finish"):
            assert "osm:" not in (c["finish"]["name"] or "")


def test_titles_and_waypoints_name_the_start_not_its_node_id(grid):
    from backcountry.trailheads import find_candidates

    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    from backcountry.graph import qa_summary

    names = {c["id"]: " | ".join(c["names"]) for c in find_candidates(tg, raw, qa_summary(tg)["node_component"])["candidates"]}
    g20 = grid.ids["g20"]  # Row 0 meets Grid Road: a start point from the data, named after both
    req = GenerateRequest.from_dict({"trailhead": str(g20), "total": "2.4-5.1", "type": "loop"})
    result = plan_trip(REGION, raw, tg, req, names=names)
    assert result.start["name"] == names[g20] and "osm:" not in result.start["name"]
    run = run_payload("r1", REGION, result, raw, tg, "2026-10-27T00:00:00Z", None)
    _no_osm_ids(run)
    assert run["candidates"][0]["title"] == f"{names[g20]} to {names[g20]} (loop)"
    assert run["candidates"][0]["waypoints"][0]["desc"] == names[g20]
    assert result.start["how"].startswith("osm:node/")  # provenance keeps the id


def test_unnamed_starts_and_finishes_get_plain_words(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    ids = grid.ids
    lat, lon = tg.graph.nodes[ids["g11"]]["lat"], tg.graph.nodes[ids["g11"]]["lon"]
    req = GenerateRequest.from_dict({"trailhead": f"{lat:.5f},{lon:.5f}", "total": "2.4-5.1", "type": "one_way",
                                     "finish": str(ids["g22"])})
    result = plan_trip(REGION, raw, tg, req)
    run = run_payload("r1", REGION, result, raw, tg, "2026-10-27T00:00:00Z", None)
    _no_osm_ids(run)
    c = run["candidates"][0]
    assert c["waypoints"][0]["desc"].startswith("Start: ") and "," in c["waypoints"][0]["desc"]  # the tapped point
    assert c["finish"]["name"] and "osm:" not in c["finish"]["name"]
