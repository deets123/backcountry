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
    assert len(gpx.tracks) == 2 and gpx.waypoints[0].name == "Start"


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
