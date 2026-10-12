import pytest
from pyproj import Transformer
from shapely.geometry import Point

from backcountry.graph import build_graph
from backcountry.route import add_elevation, shortest_route
from backcountry.split import split_route
from conftest import CRS, lonlat, plane_dem

M = 1609.344


@pytest.fixture
def route(network):
    # A -> F: 3500 m straight-ish, climbing 0.1 m per m eastward (A x=0 to F x=2500: 250 m = 820 ft).
    tg = build_graph(network.doc(), crs=CRS)
    return add_elevation(shortest_route(tg, network.ids["A"], network.ids["F"]), plane_dem(0.1, 0.0, 1000), CRS)


def test_feasible_split_respects_ranges(route):
    plan = split_route(route, nights=1, daily_min_m=1.0 * M, daily_max_m=1.3 * M)
    assert plan.ok and plan.violations == []
    assert len(plan.days) == 2 and len(plan.camps) == 1
    assert sum(d.length_m for d in plan.days) == pytest.approx(route.length_m, abs=0.01)
    assert all(1.0 * M <= d.length_m <= 1.3 * M for d in plan.days)
    assert plan.days[0].end_m == plan.camps[0].dist_m == plan.days[1].start_m
    assert (plan.days[0].start_label, plan.days[0].end_label, plan.days[1].end_label) == ("Start", "Camp 1", "End")


def test_day_gains_add_up_to_the_route(route):
    plan = split_route(route, nights=2, daily_min_m=0.5 * M, daily_max_m=1.0 * M)
    assert sum(d.gain_ft for d in plan.days) == pytest.approx(route.gain_ft, abs=0.5)
    assert sum(d.loss_ft for d in plan.days) == pytest.approx(route.loss_ft, abs=0.5)


def test_camp_lies_on_the_route_and_names_its_edge(route):
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M)
    camp = plan.camps[0]
    x, y = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(camp.lon, camp.lat)
    assert route.line_utm.distance(Point(x, y)) < 0.01
    assert camp.on_source.startswith("osm:way/") and camp.note == "camp_location: unverified"
    assert camp.nearby == []


def test_balanced_when_everything_fits(route):
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M)
    assert abs(plan.days[0].length_m - plan.days[1].length_m) <= 160  # within one candidate step


def test_infeasible_distance_is_explained(route):
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=0.9 * M)
    assert not plan.ok
    assert any("over the 0.90 mi daily max" in v for v in plan.violations)
    assert any("can't fit" in n for n in plan.notes)
    assert len(plan.days) == 2  # still returns the best split it found


def test_gain_cap_violation_is_explained(route):
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, max_gain_ft=300)
    assert not plan.ok
    assert any("over the 300 ft daily gain cap" in v for v in plan.violations)


def test_nearby_osm_camp_sites_are_listed_with_source(route):
    lon1, lat1 = lonlat(1750, 100)  # ~100 m from the midpoint camp
    lon2, lat2 = lonlat(1750, 2000)  # far away
    sites = [{"source": "osm:node/9", "kind": "camp_site", "name": "Near Camp", "lat": lat1, "lon": lon1, "tags": {}},
             {"source": "osm:node/10", "kind": "camp_pitch", "name": None, "lat": lat2, "lon": lon2, "tags": {}}]
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, camp_sites=sites)
    assert [n["source"] for n in plan.camps[0].nearby] == ["osm:node/9"]
    assert plan.camps[0].nearby[0]["distance_m"] == pytest.approx(100, abs=160)


def test_unknown_elevation_splits_by_distance_and_says_so(network):
    tg = build_graph(network.doc(), crs=CRS)
    r = shortest_route(tg, network.ids["A"], network.ids["F"])
    r = add_elevation(r, plane_dem(0.1, 0.0, 1000, xmax=1200), CRS)  # DEM ends before C: gain unknown
    plan = split_route(r, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, max_gain_ft=500)
    assert plan.days[0].gain_ft is None
    assert any("unknown" in n for n in plan.notes)
