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


# --- pinned camps and designated-site mode -------------------------------------------


def test_pinned_camp_is_used_exactly(route):
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, pins_m=[1200.0])
    assert plan.camps[0].dist_m == pytest.approx(1200.0, abs=0.01)
    assert plan.days[0].length_m == pytest.approx(1200.0, abs=0.01)
    assert plan.days[1].length_m == pytest.approx(route.length_m - 1200.0, abs=0.01)
    assert sum(d.gain_ft for d in plan.days) == pytest.approx(route.gain_ft, abs=0.5)
    assert plan.camps[0].pinned is True


def test_partial_pins_let_the_planner_place_the_rest(route):
    plan = split_route(route, nights=2, daily_min_m=0.3 * M, daily_max_m=2.0 * M, pins_m=[600.0])
    assert [c.pinned for c in plan.camps].count(True) == 1
    assert any(c.dist_m == pytest.approx(600.0, abs=0.01) for c in plan.camps)
    assert plan.ok


def test_pinned_camp_that_breaks_a_limit_is_reported_not_moved(route):
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=1.0 * M, pins_m=[3000.0])
    assert plan.camps[0].dist_m == pytest.approx(3000.0, abs=0.01)
    assert not plan.ok and any("Day 1" in v and "over" in v for v in plan.violations)


def test_too_many_pins_is_an_error(route):
    with pytest.raises(ValueError, match="2 camps pinned but only 1 night"):
        split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, pins_m=[1000.0, 2000.0])


def test_sites_only_mode_uses_known_sites_on_the_route(route):
    lon1, lat1 = lonlat(2060, 400)  # ~60 m off the route, 2400 m in (the route runs up x=2000 there)
    lon2, lat2 = lonlat(1000, 1500)  # far from the route
    sites = [{"source": "osm:node/9", "kind": "camp_site", "name": "Near Camp", "lat": lat1, "lon": lon1, "tags": {}},
             {"source": "osm:node/10", "kind": "camp_site", "name": "Far Camp", "lat": lat2, "lon": lon2, "tags": {}}]
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, camp_sites=sites, sites_only=True)
    assert plan.camps[0].dist_m == pytest.approx(2400.0, abs=1.0)
    assert plan.camps[0].nearby[0]["source"] == "osm:node/9"
    assert plan.camps[0].site == "osm:node/9"


def test_sites_only_mode_explains_when_no_site_is_on_the_route(route):
    lon, lat = lonlat(1000, 1500)
    sites = [{"source": "osm:node/10", "kind": "camp_site", "name": "Far Camp", "lat": lat, "lon": lon, "tags": {}}]
    plan = split_route(route, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M, camp_sites=sites, sites_only=True)
    assert not plan.ok
    assert any("no known camp site" in n.lower() for n in plan.notes)


def test_resolve_pins_accepts_miles_latlon_and_site_ids(route):
    from backcountry.split import resolve_pins

    lon, lat = lonlat(2000, 700)  # on the C-E leg, 2700 m in
    sites = [{"source": "osm:node/77", "kind": "camp_site", "name": "Site", "lat": lonlat(1000, 30)[1],
              "lon": lonlat(1000, 30)[0], "tags": {}}]
    pins, problems = resolve_pins(route, ["0.5", f"{lat},{lon}", "osm:node/77"], sites)
    assert pins == pytest.approx([0.5 * M, 2700.0, 1000.0], abs=1.0)
    assert problems == []


def test_resolve_pins_reports_points_off_the_route(route):
    from backcountry.split import resolve_pins

    lon, lat = lonlat(1000, 900)  # 900 m from the route
    pins, problems = resolve_pins(route, [f"{lat},{lon}", "osm:node/404", "9.9"], [])
    assert pins == []
    assert len(problems) == 3 and "from this route" in problems[0] and "unknown camp site" in problems[1]
    assert "beyond the end" in problems[2]
