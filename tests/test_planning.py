import pytest

from backcountry.graph import build_graph
from backcountry.planning import GenerateRequest, RequestError, parse_range, plan_trip
from backcountry.region import BBox, Region
from conftest import CRS

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
    assert req.count == 3 and req.type == "any" and req.kinds == ("loop", "lollipop")
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
                    "max_gain_ft": easy["max_gain_ft"], "max_grade_pct": easy["max_grade_pct"], "targeted": True}
    assert t[1] == {"day": 2, "level": None, "miles": [6.0, 11.0], "max_gain_ft": 2500.0, "max_grade_pct": None,
                    "targeted": False}
    assert t[2]["miles"] == [9.0, 10.0] and t[2]["max_gain_ft"] == hard["max_gain_ft"]  # numbers win over the level
    assert len(t) == 4 and t[3]["level"] is None
