import json
import re

import pytest

from backcountry import areas, ingest, osm_index
from backcountry.graph import build_graph, qa_summary
from backcountry.region import load_region
from backcountry.trailheads import find_candidates
from conftest import LONE_PEAK, MINI_DATE, PARK_BBOX

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


@pytest.fixture
def dirs(tmp_path):
    return {"raw_dir": tmp_path / "raw", "areas_dir": tmp_path / "areas"}


def no_dem(calls=None):
    def fetch(region, bounds, **kw):
        if calls is not None:
            calls.append((region.id, bounds))
        return None
    return fetch


def test_place_opens_park_not_wilderness(index):
    peak = osm_index.search(index, "Lone Peak")[0]
    spec = areas.area_from_ref(index, peak["osm"])
    assert spec.name == "Testpark National Park" and spec.osm == "relation/3003" and not spec.capped
    assert spec.type_label == "National Park" and spec.state == "Statea"


def test_park_search_opens_whole_park(index):
    spec = areas.area_from_ref(index, "relation/3003")
    b = spec.bbox
    assert b.west <= PARK_BBOX[0] and b.south <= PARK_BBOX[1] and b.east >= PARK_BBOX[2] and b.north >= PARK_BBOX[3]


def test_wilderness_search_opens_wilderness(index):
    assert areas.area_from_ref(index, "relation/3004").name == "Testpark Wilderness"


def test_oversized_area_is_capped(index, monkeypatch):
    monkeypatch.setattr(areas, "MAX_AREA_KM2", 10.0)
    spec = areas.area_from_ref(index, "relation/3003")
    assert spec.capped and spec.name == "Testpark National Park"
    assert spec.display_name == "Part of Testpark National Park"  # the user is told only part of it is loaded
    km = (spec.bbox.north - spec.bbox.south) * 110.574
    assert abs(km - areas.CAPPED_BOX_KM) < 0.5


def test_place_in_oversized_park_centres_on_place(index, monkeypatch):
    monkeypatch.setattr(areas, "MAX_AREA_KM2", 10.0)
    spec = areas.area_from_ref(index, osm_index.search(index, "Lone Peak")[0]["osm"])
    assert spec.capped and spec.name == "Testpark National Park"
    assert abs((spec.bbox.west + spec.bbox.east) / 2 - LONE_PEAK[0]) < 1e-6


def test_place_outside_any_area_opens_capped_box(index):
    far = osm_index.search(index, "Far Corner")[0]
    spec = areas.area_from_ref(index, far["osm"])
    assert spec.capped and spec.osm is None and spec.name == "Area near Far Corner" and spec.state == "Statea"
    assert spec.display_name == "Area near Far Corner"  # a box was never a whole park


def test_box_outside_us_refused(index):
    with pytest.raises(ValueError, match="isn't in the US"):
        areas.area_from_box(index, (10.0, 10.0, 10.1, 10.1))


def test_box_names_nearest_place_and_is_capped(index, monkeypatch):
    spec = areas.area_from_box(index, (-149.75, 10.75, -149.65, 10.85))
    assert spec.name == "Area near Bear Creek" and spec.osm is None
    monkeypatch.setattr(areas, "MAX_AREA_KM2", 10.0)
    big = areas.area_from_box(index, (-149.99, 10.01, -149.01, 10.99))
    assert big.capped


def test_box_and_point_validated(index):
    for bad in [(1, 2, 3), ("a", 0, 1, 1), (0, 0, float("nan"), 1), (0, 0, -1, 1), (-200, 0, -199, 1)]:
        with pytest.raises(ValueError):
            areas.area_from_box(index, bad)
    for bad in [(200, 0), (0, 95), (float("inf"), 0), ("x", 1)]:
        with pytest.raises(ValueError):
            areas.area_from_point(index, *bad)


def test_point_opens_containing_park(index):
    assert areas.area_from_point(index, *LONE_PEAK).name == "Testpark National Park"


def test_unknown_ref_refused(index):
    for ref in ("relation/999999", "node/1", "../etc/passwd", "http://x/y", "relation/3001"):  # 3001 is a state
        with pytest.raises(ValueError):
            areas.area_from_ref(index, ref)


@pytest.mark.parametrize("name", ["../../etc", "Zion's \"Best\"", "Öster Park ☃", "x" * 300, "---", ""])
def test_area_id_safe(name):
    from backcountry.region import BBox

    assert ID_RE.match(areas.area_id(name, None, BBox(10.0, -150.0, 10.1, -149.9)))
    assert ID_RE.match(areas.area_id(name, "relation/123", BBox(10.0, -150.0, 10.1, -149.9)))


def test_area_id_stable_and_distinct():
    from backcountry.region import BBox

    a = areas.area_id("Bear Creek", None, BBox(10.0, -150.0, 10.1, -149.9))
    assert a == areas.area_id("Bear Creek", None, BBox(10.0, -150.0, 10.1, -149.9))
    assert a != areas.area_id("Bear Creek", None, BBox(10.5, -149.5, 10.6, -149.4))
    assert areas.area_id("Zion National Park", "relation/123", BBox(0, 0, 1, 1)) == "zion-national-park-r123"


def test_utm_zone():
    assert areas.utm_crs(-113.0, 37.3) == "EPSG:32612"  # Zion
    assert areas.utm_crs(-79.3, 39.0) == "EPSG:32617"   # Dolly Sods


def test_prepare_area_writes_loadable_region(index, dirs):
    calls = []
    spec = areas.area_from_ref(index, "relation/3003")
    out = areas.prepare_area(spec, index, dem_fetch=no_dem(calls), **dirs)
    assert out["area_id"] == spec.id and out["cut"] == MINI_DATE and out["edges"] > 0
    region = load_region(spec.id, regions_dir=dirs["areas_dir"].parent / "none", areas_dir=dirs["areas_dir"])
    assert region.generated and region.trailheads == [] and region.crs == "EPSG:32606"
    assert region.area["type_label"] == "National Park" and region.area["osm"] == "relation/3003"
    raw = ingest.load_raw(region, dirs["raw_dir"])
    assert raw["cut"] == MINI_DATE and raw["source"] == osm_index.SOURCE
    tg = build_graph(raw, region.crs)
    cands = find_candidates(tg, raw, qa_summary(tg)["node_component"])["candidates"]
    assert any("osm_trailhead" in c["kinds"] and "Rim Trailhead" in c["names"] for c in cands)
    assert calls and calls[0][0] == spec.id  # elevation fetched for the area's trails


def test_prepare_twice_is_quick_and_same(index, dirs):
    spec = areas.area_from_ref(index, "relation/3003")
    areas.prepare_area(spec, index, dem_fetch=no_dem(), **dirs)
    calls = []
    out = areas.prepare_area(spec, index, dem_fetch=no_dem(calls), **dirs)
    assert out["already"] is True and calls == []
    assert len(list((dirs["raw_dir"] / spec.id).glob("extract_*.json"))) == 1


def test_interrupted_elevation_keeps_the_trails_but_is_not_ready(index, dirs):
    def broken(region, bounds, **kw):
        raise RuntimeError("USGS unreachable")

    spec = areas.area_from_ref(index, "relation/3003")
    with pytest.raises(RuntimeError):
        areas.prepare_area(spec, index, dem_fetch=broken, **dirs)
    assert len(list((dirs["raw_dir"] / spec.id).glob("extract_*.json"))) == 1  # the trails can be shown
    region = load_region(spec.id, areas_dir=dirs["areas_dir"])
    assert not areas.elevation_done(region, dirs["raw_dir"], MINI_DATE)
    out = areas.prepare_area(spec, index, dem_fetch=no_dem(), **dirs)  # the next try fetches only elevation
    assert out["elevation_only"] is True and areas.elevation_done(region, dirs["raw_dir"], MINI_DATE)


def test_prepare_clears_leftovers_of_an_interrupted_run(index, dirs):
    spec = areas.area_from_ref(index, "relation/3003")
    folder = dirs["raw_dir"] / spec.id
    folder.mkdir(parents=True)
    (folder / "extract_20261020T000000Z.json.tmp").write_text('{"half": ')
    (folder / "dem13_20261020T000000Z.tif.tmp").write_bytes(b"\0" * 100)
    areas.prepare_area(spec, index, dem_fetch=no_dem(), **dirs)
    assert not list(folder.glob("*.tmp"))


def test_prepare_without_trails_refused(index, dirs):
    spec = areas.area_from_box(index, (-149.3, 10.6, -149.2, 10.7))  # Stateb: no trails
    with pytest.raises(ValueError, match="No trails were found there"):
        areas.prepare_area(spec, index, dem_fetch=no_dem(), **dirs)


def test_load_raw_by_cut(index, dirs, tmp_path):
    spec = areas.area_from_ref(index, "relation/3003")
    areas.prepare_area(spec, index, dem_fetch=no_dem(), **dirs)
    region = load_region(spec.id, areas_dir=dirs["areas_dir"])
    newer = json.loads(next((dirs["raw_dir"] / spec.id).glob("extract_*.json")).read_text())
    newer["retrieved_at"], newer["osm_base"] = "2026-11-20T00:00:00Z", "2026-11-20"
    (dirs["raw_dir"] / spec.id / "extract_20261120T000000Z.json").write_text(json.dumps(newer))
    assert ingest.load_raw(region, dirs["raw_dir"])["cut"] == "2026-11-20"
    assert ingest.load_raw(region, dirs["raw_dir"], cut=MINI_DATE)["cut"] == MINI_DATE
    with pytest.raises(FileNotFoundError):
        ingest.load_raw(region, dirs["raw_dir"], cut="2025-01-01")
    assert ingest.cuts(region, dirs["raw_dir"]) == [MINI_DATE, "2026-11-20"]


def test_prepare_area_asks_for_elevation_only_along_trails(index, dirs):
    from backcountry.elevation import corridor_cells

    got = []

    def fetch(region, bounds, **kw):
        got.append(kw.get("cells"))
    spec = areas.area_from_ref(index, "relation/3003")
    areas.prepare_area(spec, index, dem_fetch=fetch, **dirs)
    region = load_region(spec.id, regions_dir=dirs["areas_dir"].parent / "none", areas_dir=dirs["areas_dir"])
    tg = build_graph(ingest.load_raw(region, dirs["raw_dir"]), region.crs)
    b, p = spec.bbox, areas.ELEVATION_PAD_DEG
    assert got == [corridor_cells(tg, within=(b.west - p, b.south - p, b.east + p, b.north + p))] and got[0]


# --- prepared ahead (popular areas) -------------------------------------------------------------


def test_popular_areas_order(index):
    names = [a["name"] for a in osm_index.popular_areas(index)]
    assert names.index("Testpark National Park") < names.index("Testrock National Monument") < names.index("Testpark Wilderness")
    assert "Testwood National Forest" not in names and "Statea" not in names


def sized_dem(nbytes):
    def fetch(region, bounds, raw_dir=None, **kw):
        d = ingest.region_raw_dir(region, raw_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "dem13_fake.bin").write_bytes(b"\0" * nbytes)
    return fetch


def test_prepare_popular_stops_at_the_budget(index, dirs, monkeypatch):
    monkeypatch.setattr(osm_index, "popular_areas", lambda con, *a: [osm_index.area_by_osm(con, r) for r in
                                                                     ("relation/3003", "relation/3004")])
    size = 1_000_000
    # area files are small next to the fake elevation: one area fits in 1.5 of them, two don't
    out = areas.prepare_popular(index, budget_gb=1.5 * size / 1e9, dem_fetch=sized_dem(size), log=lambda *a: None, **dirs)
    park, wild = (areas.area_from_ref(index, r).id for r in ("relation/3003", "relation/3004"))
    assert out["prepared"] == [park] and out["used_gb"] <= 1.5 * size / 1e9
    assert areas.prepared_ids(dirs["areas_dir"]) == {park}
    assert not (dirs["raw_dir"] / wild).exists() and not (dirs["areas_dir"] / f"{wild}.yaml").exists()


def test_prepare_popular_skips_areas_without_trails(index, dirs, monkeypatch):
    monkeypatch.setattr(osm_index, "popular_areas", lambda con, *a: [osm_index.area_by_osm(con, "relation/3006")])
    out = areas.prepare_popular(index, budget_gb=1.0, dem_fetch=no_dem(), log=lambda *a: None, **dirs)
    assert out["prepared"] == [] and len(out["skipped"]) == 1


def test_prepare_area_limits_elevation_to_the_outline(index, dirs):
    from shapely.geometry import box

    got = []

    def fetch(region, bounds, **kw):
        got.append(kw["cells"])
    spec = areas.area_from_ref(index, "relation/3003")
    areas.prepare_area(spec, index, dem_fetch=fetch, **dirs)
    shape = osm_index.area_shape(index, "relation/3003").buffer(areas.ELEVATION_PAD_DEG)
    assert got[0] and all(box(*c).intersects(shape) for c in got[0])


def test_group_of_areas_that_dont_touch_is_refused(index):
    with pytest.raises(ValueError):
        areas.area_from_refs(index, ["relation/3003", "relation/3005"])  # Testrock is ~20 km from the park
    assert areas.area_from_refs(index, ["relation/3003", "relation/3004"]).members  # touching: fine


def test_group_box_is_capped(index, monkeypatch):
    monkeypatch.setattr(areas, "MAX_AREA_KM2", 10.0)  # the park's box is far bigger than this
    with pytest.raises(ValueError):
        areas.area_from_refs(index, ["relation/3003", "relation/3004"])


def test_prepared_area_tops_up_elevation_when_the_cell_rule_changes(index, dirs):
    from backcountry.elevation import corridor_cells

    got = []

    def fetch(region, bounds, **kw):
        got.append(kw["cells"])
    spec = areas.area_from_ref(index, "relation/3003")
    areas.prepare_area(spec, index, dem_fetch=fetch, **dirs)
    region = load_region(spec.id, regions_dir=dirs["areas_dir"].parent / "none", areas_dir=dirs["areas_dir"])
    tg = build_graph(ingest.load_raw(region, dirs["raw_dir"]), region.crs)
    # trails everywhere in the cut, roads only near the park
    shape = osm_index.area_shape(index, "relation/3003").simplify(0.005).buffer(areas.ELEVATION_PAD_DEG)
    assert got == [corridor_cells(tg, within=shape, limit_roads_only=True)]
    assert areas.prepare_area(spec, index, dem_fetch=fetch, **dirs)["already"] is True and len(got) == 1
    # an area prepared under an older rule (its marker holds the date, not the cells) gets only its elevation again
    marker = ingest.region_raw_dir(region, dirs["raw_dir"]) / areas.ELEVATION_DONE.format(date=MINI_DATE)
    marker.write_text(MINI_DATE)
    out = areas.prepare_area(spec, index, dem_fetch=fetch, **dirs)
    assert out["elevation_only"] is True and len(got) == 2
    assert areas.prepare_area(spec, index, dem_fetch=fetch, **dirs)["already"] is True and len(got) == 2
