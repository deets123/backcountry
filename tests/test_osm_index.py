import re

from backcountry import ingest, osm_index
from backcountry.graph import build_graph, qa_summary
from backcountry.trailheads import find_candidates
from conftest import LONE_PEAK, MINI_DATE, PARK_BBOX

CRS = "EPSG:32606"  # UTM 6N covers the fixture's lon -150..-149


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
    r = osm_index.search(index, "Testpark")  # a hamlet is named exactly "Testpark": the park still comes first
    assert [x["type_label"] for x in r[:2]] == ["National Park", "Protected Area"]
    assert any(x["kind"] == "place" and x["name"] == "Testpark" for x in r[2:])
    assert r[0]["state"] == "Statea" and r[0]["kind"] == "area" and r[0]["osm"] == "relation/3003"
    w, s, e, n = r[0]["bbox"]
    assert (round(w, 2), round(s, 2), round(e, 2), round(n, 2)) == PARK_BBOX


def test_search_place_and_trail(index):
    peak = osm_index.search(index, "Lone Peak")[0]
    assert peak["kind"] == "place" and peak["type_label"] == "Peak" and peak["center"] == list(LONE_PEAK)
    trail = osm_index.search(index, "Rim Trail")
    assert len(trail) == 1 and trail[0]["type_label"] == "Trail"  # two ways, one result


def test_search_finds_areas_among_many_place_matches(index, monkeypatch):
    monkeypatch.setattr(osm_index, "PLACE_CANDIDATES", 1)  # the US has hundreds of places named "Zion..."
    r = osm_index.search(index, "Testpark")
    assert [x["osm"] for x in r[:2]] == ["relation/3003", "relation/3004"]


def test_search_leaves_out_places_outside_the_us(index):
    assert not [x for x in osm_index.search(index, "Testpark") if x["name"] == "Testpark Bay"]


def test_area_labels_follow_protection_title_not_the_boundary_tag(index):
    # OSM uses boundary=national_park loosely (monuments, forests, recreation areas): never label those parks
    monument = osm_index.search(index, "Testrock")[0]
    assert (monument["type_label"], monument["park"]) == ("National Monument", False)
    forest = osm_index.search(index, "Testwood")[0]
    assert (forest["type_label"], forest["park"]) == ("Protected Area", False)
    assert osm_index.search(index, "Testpark National Park")[0]["type_label"] == "National Park"


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


# --- build progress ----------------------------------------------------------------------------------------


def _build(tmp_path, name, **kw):
    from conftest import FIXTURES

    lines = []
    osm_index.build_index(FIXTURES / "mini.osm", tmp_path / name, MINI_DATE, progress=lines.append, every_s=0, **kw)
    return lines


def test_first_build_reports_stages_without_a_guessed_total(tmp_path):
    lines = _build(tmp_path, "a.sqlite")
    for stage in ("Reading nodes", "Reading ways", "Building areas", "Saving"):
        assert any(line.startswith(stage) for line in lines), stage
    assert not any("%" in line for line in lines)  # no earlier build to compare with: counts only


def test_build_records_totals_for_the_next_build(tmp_path):
    _build(tmp_path, "a.sqlite")
    con = osm_index.open_index(tmp_path / "a.sqlite")
    seen = osm_index.index_meta(con)["progress"]["seen"]
    con.close()
    assert seen["node"] > 0 and seen["way"] > 0 and seen["area"] > 0


def test_later_build_shows_percent_from_the_previous_build(tmp_path):
    _build(tmp_path, "a.sqlite")
    con = osm_index.open_index(tmp_path / "a.sqlite")
    previous = osm_index.index_meta(con)["progress"]
    con.close()
    lines = _build(tmp_path, "b.sqlite", previous=previous)
    assert any(re.search(r"\d+% overall", line) for line in lines)


def test_progress_percent_weights_stages_by_previous_time():
    t = [0.0]
    lines = []
    p = osm_index.Progress(lines.append, previous={"seen": {"node": 100, "way": 100, "area": 0},
                                                   "stage_s": {"node": 10.0, "way": 30.0, "area": 0.0}},
                           every_s=0, clock=lambda: t[0])
    for _ in range(50):
        p.tick("node")
    t[0] = 5.0
    p.tick("node")
    assert "13% overall" in lines[-1]  # 51 of 100 nodes = 5.1 of 40 previous seconds
    assert "of about 100" in lines[-1]


# --- short names and abbreviations ------------------------------------------------------------


def test_abbreviations_expand_whole_words():
    from backcountry.osm_index import expand_abbreviations

    assert expand_abbreviations("zion np") == "zion National Park"
    assert expand_abbreviations("Pisgah NF") == "Pisgah National Forest"
    assert expand_abbreviations("Lake Mead NRA") == "Lake Mead National Recreation Area"
    assert expand_abbreviations("Bears Ears NM") == "Bears Ears National Monument"
    assert expand_abbreviations("Custer SP") == "Custer State Park"
    assert expand_abbreviations("Linville Gorge WA") == "Linville Gorge Wilderness"
    assert expand_abbreviations("snp") == "snp" and expand_abbreviations("NPS trail") == "NPS trail"


def test_abbreviated_search_finds_the_park(index):
    assert osm_index.search(index, "testpark np")[0]["name"] == "Testpark National Park"


def test_short_name_groups_touching_areas(index):
    group = osm_index.name_group(index, "Testpark")
    assert group is not None
    assert [a["osm"] for a in group["areas"]] == ["relation/3003", "relation/3004"]
    assert group["name"] == "Testpark (park and nearby wilderness)"


def test_full_name_opens_the_park_alone(index):
    assert osm_index.name_group(index, "Testpark National Park") is None
    assert osm_index.name_group(index, "testpark np") is None
    assert osm_index.name_group(index, "Testrock") is None  # one area only


def test_state_at_decodes_each_state_outline_once(index, monkeypatch):
    from backcountry import osm_index as oi

    calls = []
    real = oi.wkb.loads
    monkeypatch.setattr(oi.wkb, "loads", lambda b, *a, **k: calls.append(1) or real(b, *a, **k))
    assert osm_index.state_at(index, -149.8, 10.3) == "Statea"
    n = len(calls)
    for _ in range(5):
        osm_index.state_at(index, -149.8, 10.3)
    assert len(calls) == n  # the outlines are kept, not decoded again per point
