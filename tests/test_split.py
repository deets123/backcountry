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


# --- per-day targets and levels ------------------------------------------------------


def long_route(length_m: float, grade_pct: float = 0.0):
    """A straight synthetic route with a uniform grade (no real place)."""
    from shapely.geometry import LineString

    from backcountry.elevation import densify
    from backcountry.route import Route
    from conftest import X0, Y0

    line = LineString([(X0, Y0), (X0 + length_m, Y0)])
    d, _ = densify(line)
    z = [x * grade_pct / 100 for x in d]
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    edge = {"source": "osm:way/1", "way_id": 1, "name": "Long Trail", "highway": "path", "is_road": False,
            "last_resort": False, "length_m": length_m, "retrieved_at": "2026-01-01T00:00:00Z", "tags": {}}
    return Route(nodes=[1, 2], edges=[edge], line_wgs84=LineString([to_ll.transform(*c) for c in line.coords]),
                 line_utm=line, length_m=length_m, road_m=0.0, last_resort_m=0.0,
                 gain_ft=z[-1] * 3.28084, loss_ft=0.0, profile={"d_m": d, "z_m": z}, crs=CRS)


def targets(*days):
    return [{"day": i, "level": lvl, "miles": miles, "max_gain_ft": None, "max_grade_pct": None,
             "targeted": lvl is not None or miles is not None, "explicit": ["miles"] if miles else []}
            for i, (lvl, miles) in enumerate(days, 1)]


def test_day_targets_shape_the_split():
    route = long_route(45_000)  # 27.96 mi, flat
    plan = split_route(route, 3, 8 * M, 12 * M, targets=targets((None, [2, 5]), (None, [8, 12]), (None, [8, 12]), (None, [8, 12])))
    miles = [d.length_m / M for d in plan.days]
    assert 2 <= miles[0] <= 5
    assert all(8 - 0.1 <= m <= 12 + 0.1 for m in miles[1:])
    assert plan.violations == []


def test_level_target_is_met_or_reported():
    route = long_route(30_000)
    plan = split_route(route, 2, 5 * M, 8 * M, targets=targets((None, None), ("hard", [7, 12]), (None, None)))
    assert "Day 2 came out easy, not hard" in plan.violations
    assert not plan.ok


def test_level_target_pulls_the_camps():
    # climbing 3% all the way: a longer day 2 is harder, so a hard target makes day 2 the long one
    route = long_route(30_000, grade_pct=3)
    t = targets((None, [3, 12]), ("hard", [3, 12]), (None, [3, 12]))
    plan = split_route(route, 2, 3 * M, 12 * M, targets=t)
    assert plan.days[1].rating["level"] == "hard"
    assert plan.days[1].length_m == max(d.length_m for d in plan.days)


def test_day_target_violations_name_the_target():
    route = long_route(30_000)
    plan = split_route(route, 1, 5 * M, 15 * M, targets=targets((None, [2, 5]), (None, [2, 5])))
    assert any(v.startswith("Day 1: ") and "over your 5.00 mi target" in v for v in plan.violations)


def test_grade_target_is_reported():
    route = long_route(10_000, grade_pct=18)
    t = [{"day": i, "level": None, "miles": [2, 4], "max_gain_ft": None, "max_grade_pct": 15.0, "targeted": True,
          "explicit": ["miles", "max_grade_pct"]} for i in (1, 2)]
    plan = split_route(route, 1, 2 * M, 4 * M, targets=t)
    import re
    assert any(re.fullmatch(r"Day 1: 18% grade for 0\.\d mi is over your 15% target", v) for v in plan.violations)


def test_days_carry_ratings(route):
    plan = split_route(route, nights=1, daily_min_m=1.0 * M, daily_max_m=1.3 * M)
    assert all(d.rating["level"] in ("easy", "moderate", "hard", "strenuous") for d in plan.days)
    assert plan.days[0].rating["effort"] > 0


def test_days_without_elevation_have_unknown_levels(network):
    tg = build_graph(network.doc(), crs=CRS)
    r = add_elevation(shortest_route(tg, network.ids["A"], network.ids["F"]), plane_dem(0.1, 0.0, 1000, xmax=1200), CRS)
    plan = split_route(r, nights=1, daily_min_m=0.5 * M, daily_max_m=2.0 * M,
                       targets=targets(("easy", [0.5, 2]), (None, None)))
    assert all(d.rating["level"] is None for d in plan.days)
    assert not any("came out" in v for v in plan.violations)


def test_a_level_only_day_reports_the_level_not_the_tables_numbers():
    # 3% all the way: a hard day 2 needs more climbing than the hard row's gain cap allows at its length
    from backcountry.difficulty import LEVEL_TARGETS

    route = long_route(30_000, grade_pct=3)
    hard = LEVEL_TARGETS["backpacking"]["hard"]
    t = targets((None, [3, 12]), ("hard", None), (None, [3, 12]))
    t[1] |= {"miles": hard["miles"], "max_gain_ft": 300.0, "max_grade_pct": hard["max_grade_pct"]}  # as resolved from the level
    plan = split_route(route, 2, 3 * M, 12 * M, targets=t)
    assert plan.days[1].rating["level"] == "hard"
    assert not any(v.startswith("Day 2") for v in plan.violations)


def test_grade_at_the_cap_after_rounding_is_not_a_miss():
    route = long_route(10_000, grade_pct=15.2)
    t = [{"day": i, "level": None, "miles": [2, 4], "max_gain_ft": None, "max_grade_pct": 15.0, "targeted": True,
          "explicit": ["miles", "max_grade_pct"]} for i in (1, 2)]
    plan = split_route(route, 1, 2 * M, 4 * M, targets=t)
    assert not any("grade" in v for v in plan.violations)


def test_explicit_numbers_never_relax_the_trip_checks_they_dont_set():
    from backcountry.planning import GenerateRequest

    route = long_route(30_000, grade_pct=3)  # 18.64 mi, ~2,950 ft
    req = GenerateRequest.from_dict({"trailhead": "x", "total": "15-25", "nights": 1, "daily": "6-11", "max_gain_day": 1000,
                                     "days": [{"day": 2, "miles": [8, 12]}]})
    plan = split_route(route, 1, 6 * M, 11 * M, 1000, targets=req.targets_for(2))
    assert any(v.startswith("Day 2: ") and "over the 1,000 ft daily gain cap" in v for v in plan.violations)
    req = GenerateRequest.from_dict({"trailhead": "x", "total": "15-25", "nights": 1, "daily": "6-8",
                                     "days": [{"day": 1, "max_gain_ft": 3000}]})
    plan = split_route(route, 1, 6 * M, 8 * M, None, targets=req.targets_for(2))
    assert any(v.startswith("Day 1: ") and "daily max" in v for v in plan.violations)
