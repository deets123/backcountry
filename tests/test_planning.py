import pytest

from backcountry.graph import build_graph
from backcountry.planning import GenerateRequest, RequestError, parse_range, plan_trip
from backcountry.region import BBox, Region
from conftest import CRS

KM = 1000.0
REGION = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))


def test_parse_range_accepts_text_and_pairs():
    assert parse_range("18-26", "total") == (18.0, 26.0)
    assert parse_range([6, 11], "daily") == (6.0, 11.0)


@pytest.mark.parametrize("bad", ["26-18", "18", "abc", [11, 6], [0, 5], None])
def test_parse_range_rejects_bad_values(bad):
    with pytest.raises(RequestError) as e:
        parse_range(bad, "total")
    assert e.value.field == "total" and "18-26" in str(e.value)


def test_from_dict_defaults_and_round_trip():
    req = GenerateRequest.from_dict({"trailhead": "1000", "total": "2.4-5.1"})
    assert req.count == 3 and req.type == "any" and req.kinds == ("loop", "lollipop", "out_and_back")
    assert req.total_m == pytest.approx((2.4 * 1609.344, 5.1 * 1609.344))
    assert req.daily is None and req.daily_m is None
    assert GenerateRequest.from_dict(req.to_dict()) == req
    assert req.to_dict()["total"] == [2.4, 5.1]


@pytest.mark.parametrize("data, field", [
    ({"total": "2-5"}, "trailhead"),
    ({"trailhead": "1000", "total": "2-5", "nights": 1}, "daily"),
    ({"trailhead": "1000", "total": "2-5", "direction": "sideways"}, "direction"),
    ({"trailhead": "1000", "total": "2-5", "type": "circle"}, "type"),
    ({"trailhead": "1000", "total": "2-5", "nights": -1, "daily": "1-2"}, "nights"),
    ({"trailhead": "1000", "total": "2-5", "count": 0}, "count"),
])
def test_from_dict_names_the_bad_field(data, field):
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict(data)
    assert e.value.field == field


def test_plan_trip_runs_the_search_with_days_and_features(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1,
                                     "daily": "0.5-4", "type": "loop"})
    result = plan_trip(REGION, raw, tg, req)
    assert result.start["node"] == grid.ids["g00"]
    cands = result.report["candidates"]
    assert len(cands) == 3
    assert all(c.plan is not None and c.features is not None for c in cands)
    assert all(c.route.profile is not None for c in cands)  # distance-only profile without a DEM


def test_plan_trip_without_nights_still_attaches_features(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    result = plan_trip(REGION, raw, tg, GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1"}))
    assert all(c.plan is None and c.features is not None for c in result.report["candidates"])


@pytest.mark.parametrize("data, field", [
    ({"trailhead": "nowhere", "total": "2-5"}, "trailhead"),
    ({"trailhead": "1000", "total": "2-5", "avoid": ["Nope Trail"]}, "avoid"),
    ({"trailhead": "1000", "total": "2-5", "must_visit": ["Nope Trail"]}, "must_visit"),
])
def test_plan_trip_reports_bad_places_by_field(grid, data, field):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    with pytest.raises(RequestError) as e:
        plan_trip(REGION, raw, tg, GenerateRequest.from_dict(data))
    assert e.value.field == field


@pytest.mark.parametrize("data, field", [
    ({"trailhead": "1000", "total": "2-5", "max_gain_day": "500"}, "max_gain_day"),
    ({"trailhead": "1000", "total": "2-5", "road_weight": None}, "road_weight"),
    ({"trailhead": "1000", "total": "2-5", "repeat_weight": "x"}, "repeat_weight"),
    ({"trailhead": "1000", "total": "2-5", "camps": "1.2"}, "camps"),
    ({"trailhead": "1000", "total": "2-5", "must_visit": "Row 2"}, "must_visit"),
    ({"trailhead": "1000", "total": "2-5", "avoid": "Grid Road"}, "avoid"),
    ({"trailhead": "1000", "total": "2-5", "no_roads": "false"}, "no_roads"),
    ({"trailhead": "1000", "total": "2-5", "camps_at_sites": 1}, "camps_at_sites"),
    ({"trailhead": "1000", "total": "2-5", "nights": True, "daily": "1-2"}, "nights"),
    ({"trailhead": "1000", "total": "2-5", "count": 2.5}, "count"),
])
def test_from_dict_rejects_json_typed_mistakes_by_field(data, field):
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict(data)
    assert e.value.field == field


def test_too_many_pinned_camps_is_a_request_error(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1,
                                     "daily": "0.5-4", "camps": ["0.5", "1.0"]})
    with pytest.raises(RequestError) as e:
        plan_trip(REGION, raw, tg, req)
    assert e.value.field == "camps" and "pinned" in str(e.value)


# --- per-day targets and the trip level ---------------------------------------------

TRIP = {"trailhead": "1000", "total": "18-26", "nights": 3, "daily": "6-11"}


def test_day_targets_round_trip():
    req = GenerateRequest.from_dict(TRIP | {"days": [{"day": 1, "level": "easy"},
                                                     {"day": 2, "miles": "8-12", "max_gain_ft": 3000}],
                                            "level": "hard"})
    out = req.to_dict()
    assert out["level"] == "hard"
    assert out["days"] == [
        {"day": 1, "level": "easy", "miles": None, "max_gain_ft": None, "max_grade_pct": None},
        {"day": 2, "level": None, "miles": [8.0, 12.0], "max_gain_ft": 3000.0, "max_grade_pct": None},
    ]
    assert GenerateRequest.from_dict(out) == req
    assert GenerateRequest.from_dict(TRIP).to_dict()["days"] == [] and GenerateRequest.from_dict(TRIP).level is None


def test_unknown_level_is_refused():
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict(TRIP | {"days": [{"day": 1, "level": "chill"}]})
    assert e.value.field == "days" and str(e.value) == "day 1: level must be easy, moderate, hard or strenuous"
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict(TRIP | {"level": "brutal"})
    assert e.value.field == "level" and str(e.value) == "level must be easy, moderate, hard or strenuous"


@pytest.mark.parametrize("days, message", [
    ([{"day": 0}], "day 0: days are numbered from 1"),
    ([{"day": "1"}], "each day target needs a day number; got {'day': '1'}"),
    ([{"day": 1, "miles": "9-3"}], "day 1: miles must look like 8-12 (low-high); got '9-3'"),
    ([{"day": 1, "max_gain_ft": -5}], "day 1: max_gain_ft must be a number above 0; got -5"),
    ([{"day": 1}, {"day": 1, "level": "easy"}], "day 1 is set twice"),
    ("easy", "days must be a list of day targets; got 'easy'"),
])
def test_bad_day_targets_are_refused(days, message):
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict(TRIP | {"days": days})
    assert e.value.field == "days" and str(e.value) == message


def test_day_targets_beyond_the_trip_are_refused():
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict(TRIP | {"nights": 2, "days": [{"day": 4, "level": "easy"}]})
    assert e.value.field == "days" and str(e.value) == "day 4: this trip has 3 days"
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict({"trailhead": "1000", "total": "4-6", "days": [{"day": 1, "level": "easy"}]})
    assert e.value.field == "days" and str(e.value) == "per-day targets need nights"


def test_day_targets_that_cannot_fit_the_total_are_refused():
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict({"trailhead": "1000", "total": "10-12", "nights": 1, "daily": "4-8",
                                   "days": [{"day": 1, "miles": [8, 9]}, {"day": 2, "miles": [8, 9]}]})
    assert e.value.field == "days"
    assert str(e.value) == "the day targets add up to 16-18 mi, outside the total 10-12 mi"


def test_targets_for_fills_untargeted_days_from_the_trip():
    from backcountry.difficulty import LEVEL_TARGETS

    req = GenerateRequest.from_dict(TRIP | {"max_gain_day": 2500, "days": [{"day": 1, "level": "easy"},
                                                                          {"day": 3, "level": "hard", "miles": "9-10"}]})
    t = req.targets_for(4)
    easy, hard = LEVEL_TARGETS["backpacking"]["easy"], LEVEL_TARGETS["backpacking"]["hard"]
    assert t[0] == {"day": 1, "level": "easy", "miles": [float(m) for m in easy["miles"]],
                    "max_gain_ft": easy["max_gain_ft"], "max_grade_pct": easy["max_grade_pct"], "targeted": True,
                    "explicit": [], "from_trip": []}
    assert t[1] == {"day": 2, "level": None, "miles": [6.0, 11.0], "max_gain_ft": 2500.0, "max_grade_pct": None,
                    "targeted": False, "explicit": [], "from_trip": ["miles", "max_gain_ft"]}
    assert t[2]["explicit"] == ["miles"]
    assert t[2]["miles"] == [9.0, 10.0] and t[2]["max_gain_ft"] == hard["max_gain_ft"]  # numbers win over the level
    assert len(t) == 4 and t[3]["level"] is None


# --- the trip-level filter and the closest-fit suggestion ---------------------------


def _tagged_grid(grid):
    for e in grid.elements:
        if e.get("id") == 111:  # Col 1: a tagged scramble makes every loop over it at least hard
            e["tags"]["sac_scale"] = "alpine_hiking"
    raw = grid.doc()
    return raw, build_graph(raw, crs=CRS)


def _levels(result):
    from backcountry.planning import candidate_level

    return [candidate_level(c) for c in result.report["candidates"]]


@pytest.mark.parametrize("level", ["easy", "hard"])
def test_level_filter_keeps_only_matching_trips(grid, level):
    from conftest import plane_dem

    raw, tg = _tagged_grid(grid)
    base = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "count": 2}
    result = plan_trip(REGION, raw, tg, GenerateRequest.from_dict(base | {"level": level}), dem=plane_dem(0, 0, 500))
    assert _levels(result) and set(_levels(result)) == {level}
    assert not any("comes out" in n for n in result.report["notes"])


def test_level_filter_falls_back_to_the_closest_with_a_note(grid):
    from conftest import plane_dem

    raw, tg = _tagged_grid(grid)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "level": "strenuous"})
    result = plan_trip(REGION, raw, tg, req, dem=plane_dem(0, 0, 500))
    assert _levels(result) and set(_levels(result)) == {"hard"}  # hard is one step from strenuous, easy three
    assert "No loop here comes out strenuous; showing the closest (hard)." in result.report["notes"]


def _flat_trip(grid, days):
    from conftest import plane_dem

    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    data = {"trailhead": str(grid.ids["g00"]), "total": "1.0-2.6", "nights": 1, "daily": "0.5-4", "type": "loop", "days": days}
    return raw, tg, data, plan_trip(REGION, raw, tg, GenerateRequest.from_dict(data), dem=plane_dem(0, 0, 500))


def test_suggestion_is_rechecked(grid):
    from conftest import plane_dem

    raw, tg, data, result = _flat_trip(grid, [{"day": 1, "miles": [0.5, 1.0]}, {"day": 2, "miles": [1.0, 1.3]}])
    c = result.report["candidates"][0]
    assert c.plan.violations and c.suggestion
    assert c.suggestion["text"] == "Allowing day 1 up to 2 mi would fix it."
    fixed = plan_trip(REGION, raw, tg, GenerateRequest.from_dict(data | {"days": c.suggestion["days"]}),
                      dem=plane_dem(0, 0, 500))
    same = next(f for f in fixed.report["candidates"] if f.keys == c.keys)
    assert same.plan.violations == []


def test_no_suggestion_when_nothing_single_step_works(grid):
    _, _, _, result = _flat_trip(grid, [{"day": 1, "miles": [0.5, 0.6]}, {"day": 2, "miles": [0.5, 0.6]}])
    c = result.report["candidates"][0]
    assert c.plan.violations and c.suggestion is None


def test_level_suggestion_names_the_level(grid):
    raw, tg, data, result = _flat_trip(grid, [{"day": 1, "level": "hard", "miles": [0.5, 2]}])
    c = result.report["candidates"][0]
    assert "Day 1 came out easy, not hard" in c.plan.violations
    assert c.suggestion["text"] == "Setting day 1 to easy would fix it."  # moderate (one step) still misses on the flat


@pytest.mark.parametrize("nights", [None, 1])
def test_plan_trip_reports_timings(grid, nights):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    body = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1"} | ({"nights": 1, "daily": "0.5-4"} if nights else {})
    timings = plan_trip(REGION, raw, tg, GenerateRequest.from_dict(body)).report["timings"]
    assert set(timings) == {"resolve", "search", "day_split", "finish"}
    assert all(isinstance(v, float) and v >= 0 for v in timings.values())


def test_plan_trip_snapshot_has_day_plans(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    t = [0.0]

    def clock():
        t[0] += 5
        return t[0]

    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "1.2-5.6", "nights": 1, "daily": "0.5-4"})
    snaps = []
    final = plan_trip(REGION, raw, tg, req, on_snapshot=snaps.append, first_s=8, clock=clock)
    assert len(snaps) == 1 and snaps[0].report["partial"] is True and final.report["partial"] is False
    assert snaps[0].report["candidates"] and all(c.plan is not None for c in snaps[0].report["candidates"])


def test_any_includes_out_and_back_but_not_one_way():
    req = GenerateRequest.from_dict({"trailhead": "1000", "total": "2-5"})
    assert req.kinds == ("loop", "lollipop", "out_and_back")
    assert GenerateRequest.from_dict({"trailhead": "1000", "total": "2-5", "type": "out_and_back"}).kinds == ("out_and_back",)


# --- one way --------------------------------------------------------------------------------------


def test_one_way_needs_a_finish(grid):
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict({"trailhead": "1000", "total": "2-5", "type": "one_way"})
    assert e.value.field == "finish"


def test_one_way_bad_finish_is_a_request_error(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    ids = grid.ids
    for finish in (str(ids["T"]), "0.0,0.0"):
        req = GenerateRequest.from_dict({"trailhead": str(ids["T"]), "total": "2-5", "type": "one_way", "finish": finish})
        with pytest.raises(RequestError) as e:
            plan_trip(REGION, raw, tg, req)
        assert e.value.field == "finish"


def test_one_way_plans_to_the_finish(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    ids = grid.ids
    req = GenerateRequest.from_dict({"trailhead": str(ids["T"]), "total": "2.5-4.4", "type": "one_way",
                                     "finish": str(ids["g22"])})
    assert GenerateRequest.from_dict(req.to_dict()) == req
    cands = plan_trip(REGION, raw, tg, req).report["candidates"]
    assert cands and all(c.route.nodes[-1] == ids["g22"] for c in cands)


def test_suggest_finishes_fit_the_range_and_skip_the_start(grid):
    from backcountry.planning import suggest_finishes

    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    ids = grid.ids
    ths = [{"id": n, "name": f"TH {n}", "node": ids[n], "in_graph": True, "source": "data", "verified": False,
            "lat": tg.graph.nodes[ids[n]]["lat"], "lon": tg.graph.nodes[ids[n]]["lon"]} for n in ("T", "g22", "g02", "g20", "D")]
    got = suggest_finishes(tg, ths, ids["T"], 2.0 * KM, 4.6 * KM)
    # trail distances from T: D 2.0 km, g02 and g20 2.5 km, g22 4.5 km; closest to the range middle (3.3 km) first
    assert [f["id"] for f in got] == ["g02", "g20", "g22", "D"]
    assert len(suggest_finishes(tg, ths * 3, ids["T"], 2.0 * KM, 4.6 * KM)) <= 5
    for f in got:
        assert 2.0 * KM <= f["length_m"] <= 4.6 * KM and f["straight_line_m"] > 0 and f["gain_ft"] is None


def test_suggested_finishes_prefer_trailheads_and_spread_out(grid):
    from backcountry.planning import suggest_finishes

    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    ids, G = grid.ids, tg.graph

    def th(name, node, kinds):
        return {"id": name, "name": name, "node": ids[node], "in_graph": True, "source": "data", "verified": False,
                "lat": G.nodes[ids[node]]["lat"], "lon": G.nodes[ids[node]]["lon"], "kinds": kinds}

    ths = [th("junction m", "m", ["trail_road_junction"]), th("junction g11", "g11", ["trail_road_junction"]),
           th("Tagged g12", "g12", ["osm_trailhead"]), th("junction g01", "g01", ["trail_road_junction"])]
    got = [f["id"] for f in suggest_finishes(tg, ths, ids["T"], 1.0 * KM, 6.0 * KM)]
    assert got[0] == "Tagged g12"  # a tagged trailhead before road crossings
    assert not ({"junction m", "junction g11"} <= set(got))  # 500 m apart: one of them is enough


def test_one_way_offers_variants_that_share_most_of_their_trail():
    from conftest import RawBuilder

    # A to D along a long shared trail, with two ways round the middle: a short cut and a longer bend
    b = RawBuilder()
    for name, x, y in [("A", 0, 0), ("B", 4000, 0), ("C", 5000, 0), ("D", 9000, 0), ("E", 4500, 600)]:
        b.node(name, x, y)
    b.way(1, ["A", "B"], highway="path", name="Long Trail")
    b.way(2, ["B", "C"], highway="path", name="Short Cut")
    b.way(3, ["B", "E", "C"], highway="path", name="Rim Bend")
    b.way(4, ["C", "D"], highway="path", name="Long Trail")
    raw = b.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(b.ids["A"]), "total": "5-7", "type": "one_way",
                                     "finish": str(b.ids["D"]), "count": 3})
    cands = plan_trip(REGION, raw, tg, req).report["candidates"]
    names = [{e["name"] for e in c.route.edges} for c in cands]
    assert any("Short Cut" in n for n in names) and any("Rim Bend" in n for n in names)


# --- review fixes ------------------------------------------------------------------------------------------


def test_one_way_ignores_direction_and_keeps_its_ends(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    ids = grid.ids
    for extra in ({"direction": "ccw"}, {"must_visit": [str(ids["D"])]}):
        req = GenerateRequest.from_dict({"trailhead": str(ids["T"]), "total": "2.5-4.4", "type": "one_way",
                                         "finish": str(ids["g22"])} | extra)
        cands = plan_trip(REGION, raw, tg, req).report["candidates"]
        assert cands, extra
        for c in cands:
            assert c.route.nodes[0] == ids["T"] and c.route.nodes[-1] == ids["g22"] and c.finish == ids["g22"], extra


def test_out_and_back_with_a_detour_keeps_its_turnaround(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    ids = grid.ids
    req = GenerateRequest.from_dict({"trailhead": str(ids["T"]), "total": "1-6", "type": "out_and_back",
                                     "must_visit": [str(ids["D"])], "direction": "cw"})
    cands = plan_trip(REGION, raw, tg, req).report["candidates"]
    assert cands and all(c.turnaround is not None and c.turnaround in c.route.nodes for c in cands)
    assert all(c.route.nodes[0] == ids["T"] for c in cands)


def test_a_route_too_short_to_split_gets_a_plan_with_a_violation_not_an_error(grid):
    from conftest import RawBuilder

    from backcountry.planning import plan_route
    from backcountry.route import shortest_route

    b = RawBuilder()
    b.node("A", 0, 0), b.node("B", 50, 0)
    b.way(1, ["A", "B"], highway="path", name="Short Trail")
    raw = b.doc()
    tg = build_graph(raw, crs=CRS)
    route = shortest_route(tg, b.ids["A"], b.ids["B"])
    req = GenerateRequest.from_dict({"trailhead": str(b.ids["A"]), "total": "0.01-0.1", "nights": 1, "daily": "0.5-4"})
    result = plan_route(REGION, raw, tg, req, route, "one_way")
    c = result.report["candidates"][0]
    assert c.plan is not None and len(c.plan.days) == 2 and not c.plan.ok
    assert any("under" in v or "short" in v.lower() for v in c.plan.violations + c.plan.notes)


def test_too_many_pinned_camps_is_still_a_camps_error(grid):
    raw = grid.doc()
    tg = build_graph(raw, crs=CRS)
    req = GenerateRequest.from_dict({"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1, "daily": "0.5-4",
                                     "camps": ["1.0", "1.5"]})
    with pytest.raises(RequestError) as e:
        plan_trip(REGION, raw, tg, req)
    assert e.value.field == "camps"
