import copy

import pytest

from backcountry.chat.facts import MAX_FEATURES, MAX_NAMES, fact_sheet, settings_text, start_name
from backcountry.graph import build_graph
from backcountry.planning import GenerateRequest, plan_trip
from backcountry.region import BBox, Region
from backcountry.serialize import run_payload
from conftest import CRS

REGION = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))
STARTS = [{"id": "bear_rocks", "name": "Bear Rocks Trailhead"}, {"id": "red_creek", "name": "Red Creek Trailhead"}]


@pytest.fixture
def run(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1,
                                     "daily": "0.5-4", "type": "loop"})
    r = run_payload("r1", REGION, plan_trip(REGION, raw, tg, req), raw, tg, "2026-01-01T00:00:00Z", diff=None)
    c = r["candidates"][0]  # add a water feature and a campsite so the wording can be checked
    c["features"] = [
        {"source": "osm:node/60", "kind": "spring", "name": "Cold Spring", "dist_m": 600.0, "off_m": 12.0, "lat": 0, "lon": 0,
         "tags": {}, "meta": {}, "url": None, "day": 1},
        {"source": "osm:node/61", "kind": "camp_site", "name": None, "dist_m": 900.0, "off_m": 2.0, "lat": 0, "lon": 0,
         "tags": {}, "meta": {}, "url": None, "day": 1}]
    c["plan"]["camps"][0]["nearest_water"] = {"source": "osm:node/60", "kind": "spring", "name": "Cold Spring", "distance_m": 304.8}
    return r


def test_fact_sheet_has_settings_route_days_camps_features_and_gaps(run):
    sheet = fact_sheet(run, 1, ["Row 0", "Col 0"], STARTS)
    c = run["candidates"][0]
    assert "Current settings: start a start point from the trail data; shape loop; total 2.4-5.1 mi; nights 1; miles per day 0.5-4 mi" in sheet
    assert f"Route (candidate 1 of {len(run['candidates'])}, loop): {c['length_m'] / 1609.344:.2f} mi" in sheet
    assert "Day 1: Start to Camp 1," in sheet and "Day 2: Camp 1 to End," in sheet
    assert "gain unknown" in sheet  # no DEM in this test
    assert "Camp 1: mile" in sheet and "location unverified" in sheet
    assert "nearest water: Spring: Cold Spring, 1,000 ft away (presence unknown)" in sheet
    assert "Spring: Cold Spring, 39 ft off the route (water presence unknown)" in sheet
    assert "Campsite, 7 ft off the route (existence and status unknown)" in sheet
    assert "Not checked by this tool: water: map features only" in sheet
    assert "Trail names in this region: Col 0; Row 0" in sheet
    assert "Trailheads you can start from: Bear Rocks Trailhead; Red Creek Trailhead" in sheet
    assert "ranked by penalty" in sheet
    assert "osm:" not in sheet


def test_fact_sheet_without_a_day_plan(run):
    r = copy.deepcopy(run)
    r["candidates"][0]["plan"] = None
    assert "Days: not planned (no nights given)." in fact_sheet(r, 1, [], STARTS)


def test_start_names_never_show_raw_ids():
    assert start_name("bear_rocks", STARTS) == "Bear Rocks Trailhead"
    assert start_name("39.04,-79.38", STARTS) == "the map point 39.04,-79.38"
    assert start_name("402290253", STARTS) == "a start point from the trail data"
    text = settings_text({"trailhead": "bear_rocks", "total": [18, 26], "nights": 2, "daily": [6, 11], "max_gain_day": 2500,
                          "type": "any", "no_roads": True, "direction": "cw", "must_visit": ["Red Creek Trail", "155984740"],
                          "avoid": [], "camps": ["6.5", "13"], "camps_at_sites": False}, STARTS)
    assert "gain cap 2,500 ft per day; roads avoided; direction clockwise" in text
    assert "must visit Red Creek Trail, a point from the trail data" in text
    assert "camps pinned at mile 6.5, 13" in text and "155984740" not in text


def test_fact_sheet_caps_long_lists(run):
    r = copy.deepcopy(run)
    r["candidates"][0]["features"] = [dict(r["candidates"][0]["features"][0], dist_m=float(i)) for i in range(100)]
    names = [f"Trail {i:03d}" for i in range(500)]
    sheet = fact_sheet(r, 1, names, STARTS)
    assert sheet.count("Spring: Cold Spring,") == MAX_FEATURES + 1  # the listed features, plus the camp's nearest water
    assert f"(and {100 - MAX_FEATURES} more)" in sheet
    assert "Trail 299" in sheet and "Trail 300" not in sheet and f"(and {500 - MAX_NAMES} more)" in sheet
