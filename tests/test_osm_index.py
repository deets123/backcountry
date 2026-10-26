from backcountry import ingest, osm_index
from backcountry.graph import build_graph, qa_summary
from backcountry.trailheads import find_candidates
from conftest import LONE_PEAK, MINI_DATE, PARK_BBOX

CRS = "EPSG:32605"  # UTM 5N covers the fixture's lon -150..-149


def test_build_counts(index_path, index):
    meta = osm_index.index_meta(index)
    assert meta["extract_date"] == MINI_DATE and meta["built_at"]


def test_cut_matches_overpass_shape(index):
    raw = osm_index.cut(index, PARK_BBOX, MINI_DATE)
    tg = build_graph(raw, CRS)  # graph builds unchanged
    assert tg.graph.number_of_edges() > 0
    assert raw["source"].startswith("OpenStreetMap via Geofabrik")
    assert raw["attribution"] == ingest.OSM_ATTRIBUTION and raw["osm_base"] == MINI_DATE
    way = next(e for e in raw["response"]["elements"] if e["type"] == "way" and "highway" in e["tags"])
    assert len(way["geometry"]) == len(way["nodes"]) and {"timestamp", "version", "bounds"} <= way.keys()
    assert way["version"] == 3 and way["timestamp"] == "2026-01-15T00:00:00Z"


def test_cut_geometry_is_exact(index):
    raw = osm_index.cut(index, PARK_BBOX, MINI_DATE)
    rim = next(e for e in raw["response"]["elements"] if e["type"] == "way" and e["tags"].get("name") == "Rim Trail")
    assert rim["geometry"][0] == {"lat": 10.25, "lon": -149.85}  # the fixture's vertex, to OSM's 7 decimals


def test_cut_keeps_trailhead_spring_and_parking(index):
    raw = osm_index.cut(index, PARK_BBOX, MINI_DATE)
    tg = build_graph(raw, CRS)
    cands = find_candidates(tg, raw, qa_summary(tg)["node_component"])["candidates"]
    kinds = {k for c in cands for k in c["kinds"]}
    assert {"osm_trailhead", "trail_road_junction", "parking_nearby"} <= kinds
    assert [p["name"] for p in ingest.water_features(raw)["points"]] == ["Hidden Spring"]


def test_cut_includes_boundaries_with_bounds(index):
    raw = osm_index.cut(index, PARK_BBOX, MINI_DATE)
    names = {e["tags"].get("name") for e in raw["response"]["elements"] if "boundary" in e.get("tags", {}) and e.get("bounds")}
    assert {"Testpark National Park", "Testpark Wilderness"} <= names


def test_cut_outside_has_no_ways(index):
    raw = osm_index.cut(index, (-149.4, 10.6, -149.3, 10.7), MINI_DATE)
    assert not [e for e in raw["response"]["elements"] if e["type"] == "way" and "highway" in e.get("tags", {})]


def test_search_park_ranks_first(index):
    r = osm_index.search(index, "Testpark")
    assert [x["type_label"] for x in r[:2]] == ["National Park", "Protected Area"]
    assert r[0]["state"] == "Statea" and r[0]["kind"] == "area" and r[0]["osm"] == "relation/3003"
    w, s, e, n = r[0]["bbox"]
    assert (round(w, 2), round(s, 2), round(e, 2), round(n, 2)) == PARK_BBOX


def test_search_place_and_trail(index):
    peak = osm_index.search(index, "Lone Peak")[0]
    assert peak["kind"] == "place" and peak["type_label"] == "Peak" and peak["center"] == list(LONE_PEAK)
    trail = osm_index.search(index, "Rim Trail")
    assert len(trail) == 1 and trail[0]["type_label"] == "Trail"  # two ways, one result


def test_search_prefix(index):
    assert osm_index.search(index, "lone pe")[0]["name"] == "Lone Peak"


def test_search_same_name_two_states(index):
    r = osm_index.search(index, "Bear Creek")
    assert sorted(x["state"] for x in r) == ["Statea", "Stateb"]


def test_states_are_not_search_results(index):
    assert osm_index.search(index, "Statea") == []


def test_containing_areas_smallest_first(index):
    areas = osm_index.containing_areas(index, *LONE_PEAK)
    assert [a["name"] for a in areas] == ["Testpark Wilderness", "Testpark National Park"]  # states excluded
    assert [a["park"] for a in areas] == [False, True] and areas[0]["area_km2"] < areas[1]["area_km2"]


def test_state_at(index):
    assert osm_index.state_at(index, *LONE_PEAK) == "Statea"
    assert osm_index.state_at(index, -149.2, 10.5) == "Stateb"
    assert osm_index.state_at(index, 0.0, 0.0) is None


def test_search_text_is_data(index):
    for q in ['" OR 1=1 --', "'; DROP TABLE elements; --", "name:*", "NEAR(a b)", "\x00", "a" * 5000, "*", "   ", "(("]:
        assert isinstance(osm_index.search(index, q), list)  # no exception, no SQL error
    assert osm_index.search(index, "Testpark")  # tables still there


def test_index_is_read_only(index):
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.OperationalError):
        index.execute("DELETE FROM areas")
