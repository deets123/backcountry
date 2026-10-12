import pytest

from backcountry.features import along_route
from backcountry.graph import build_graph
from backcountry.route import add_elevation, shortest_route
from backcountry.split import split_route
from conftest import CRS, lonlat, plane_dem

M = 1609.344


@pytest.fixture
def route(network):
    tg = build_graph(network.doc(), crs=CRS)
    return add_elevation(shortest_route(tg, network.ids["A"], network.ids["F"]), plane_dem(0.1, 0.0, 1000), CRS)


def _pt(x, y, **kw):
    lon, lat = lonlat(x, y)
    return {"lat": lat, "lon": lon, "tags": {}, **kw}


def _line(coords, **kw):
    return {"coords": [list(lonlat(x, y)) for x, y in coords], "tags": {}, **kw}


@pytest.fixture
def data():
    # Route: A(0,0) -> B(1000,0) -> C(2000,0) -> E(2000,1000) -> F(2500,1000)
    camp_sites = [_pt(1200, 80, source="osm:node/50", kind="camp_site", name="Camp Near"),
                  _pt(1200, 900, source="osm:node/51", kind="camp_site", name="Camp Far")]
    water = {
        "points": [_pt(600, -40, source="osm:node/60", kind="spring", name="Cold Spring"),
                   _pt(2100, 300, source="osm:node/61", kind="drinking_water", name=None),
                   _pt(0, 3000, source="osm:way/62", kind="water", name="Far Pond")],
        "lines": [_line([(1500, -300), (1500, 300)], source="osm:way/70", kind="stream", name="Test Run"),
                  _line([(2200, 1100), (2400, 1100)], source="osm:way/71", kind="stream", name="Misses Run")],
    }
    return camp_sites, water


def test_features_listed_with_mile_markers_and_sources(route, data):
    camp_sites, water = data
    feats = along_route(route, camp_sites=camp_sites, water=water)
    by_source = {f["source"]: f for f in feats}
    assert set(by_source) == {"osm:node/50", "osm:node/60", "osm:node/61", "osm:way/70"}
    assert by_source["osm:node/60"]["kind"] == "spring" and by_source["osm:node/60"]["dist_m"] == pytest.approx(600, abs=1)
    assert by_source["osm:node/60"]["off_m"] == pytest.approx(40, abs=1)
    assert by_source["osm:way/70"]["kind"] == "stream crossing" and by_source["osm:way/70"]["dist_m"] == pytest.approx(1500, abs=1)
    assert by_source["osm:way/70"]["off_m"] == 0
    assert by_source["osm:node/50"]["kind"] == "camp_site"
    assert [f["dist_m"] for f in feats] == sorted(f["dist_m"] for f in feats)


def test_features_get_day_numbers_from_a_plan(route, data):
    camp_sites, water = data
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, pins_m=[1400.0])
    feats = along_route(route, camp_sites=camp_sites, water=water, plan=plan)
    days = {f["source"]: f["day"] for f in feats}
    assert days["osm:node/60"] == 1 and days["osm:way/70"] == 2 and days["osm:node/61"] == 2


def test_nearest_water_to_each_camp(route, data):
    camp_sites, water = data
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, pins_m=[1400.0])
    along_route(route, camp_sites=camp_sites, water=water, plan=plan)
    camp = plan.camps[0]
    assert camp.nearest_water["source"] == "osm:way/70" and camp.nearest_water["distance_m"] == pytest.approx(100, abs=1)


def test_route_summary_lists_features_with_water_warning(route, data, tmp_path):
    from backcountry.export import write_route
    from backcountry.features import WATER_WARNING
    from backcountry.region import BBox, Region

    camp_sites, water = data
    region = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, pins_m=[1400.0])
    feats = along_route(route, camp_sites=camp_sites, water=water, plan=plan)
    md = write_route(route, region, tmp_path / "r", "a", "b", "2026-01-01T00:00:00Z", plan=plan,
                     features=feats)["summary"].read_text()
    assert "## Camp sites and water along the route" in md and WATER_WARNING in md
    assert "Cold Spring" in md and "stream crossing" in md and "osm:way/70" in md
    assert "nearest water" in md.lower() and "pinned" in md.lower()
    assert " m " not in md
