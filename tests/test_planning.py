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
