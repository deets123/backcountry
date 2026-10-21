import pytest

from backcountry.chat.models import shape_problem
from backcountry.chat.proposal import RESPONSE_SCHEMA, SYSTEM_PROMPT, ProposalError, apply_changes

REQ = {"trailhead": "bear_rocks", "total": [18.0, 26.0], "nights": 2, "daily": [6.0, 11.0], "max_gain_day": 2500.0,
       "type": "any", "count": 3, "no_roads": False, "road_weight": 1.0, "repeat_weight": 0.5, "camps": [],
       "camps_at_sites": False, "must_visit": [], "avoid": [], "direction": None}
TRAILS = ["Red Creek Trail", "Dolly Sods Road", "Rocky Ridge Trail"]
STARTS = [{"id": "bear_rocks", "name": "Bear Rocks Trailhead"}, {"id": "red_creek", "name": "Red Creek Trailhead"}]


def test_schema_accepts_a_change_and_rejects_unknown_fields():
    good = {"reply": "Moving camp 2 earlier.", "kind": "change", "changes": {"camps": [6.3, 11.0], "nights": 2}}
    assert shape_problem(good, RESPONSE_SCHEMA) is None
    assert shape_problem({**good, "changes": {"geometry": []}}, RESPONSE_SCHEMA) == "answer.changes.geometry is not allowed"
    assert "never say a route is safe" in SYSTEM_PROMPT and "FACTS" in SYSTEM_PROMPT


def test_each_change_field_is_applied_and_described():
    new, lines = apply_changes(REQ, {"nights": 3, "daily": [5, 9], "max_gain_day": 2000, "no_roads": True, "direction": "cw",
                                     "type": "loop", "camps_at_sites": True, "total": [20, 30]}, TRAILS, STARTS)
    assert (new["nights"], new["daily"], new["max_gain_day"], new["no_roads"]) == (3, [5.0, 9.0], 2000.0, True)
    assert lines == ["Shape: any → loop", "Total miles: 18-26 mi → 20-30 mi", "Nights: 2 → 3", "Miles per day: 6-11 mi → 5-9 mi",
                     "Gain cap per day: 2,500 ft → 2,000 ft", "Roads: allowed → avoided", "Direction: either → clockwise",
                     "Camps at known sites only: no → yes"]


def test_names_lists_camps_and_trailhead():
    new, lines = apply_changes(REQ, {"must_visit_add": ["red creek trail"], "avoid_add": ["Dolly Sods Road"],
                                     "camps": [6.3, 11], "trailhead": "Red Creek Trailhead"}, TRAILS, STARTS)
    assert new["must_visit"] == ["Red Creek Trail"] and new["avoid"] == ["Dolly Sods Road"]
    assert new["camps"] == ["6.3", "11"] and new["trailhead"] == "red_creek"
    assert "Start: Bear Rocks Trailhead → Red Creek Trailhead" in lines
    assert "Camps: placed by the engine → pinned at mile 6.3, 11" in lines
    removed, _ = apply_changes(new, {"avoid_remove": ["dolly sods road"], "camps": []}, TRAILS, STARTS)
    assert removed["avoid"] == [] and removed["camps"] == []


def test_unknown_names_are_refused_with_a_suggestion():
    with pytest.raises(ProposalError, match="There's no trail named 'Red Creek Trial' in this region; did you mean Red Creek Trail?"):
        apply_changes(REQ, {"must_visit_add": ["Red Creek Trial"]}, TRAILS, STARTS)
    with pytest.raises(ProposalError, match="No trailhead named 'Moon Base'.*Bear Rocks Trailhead, Red Creek Trailhead"):
        apply_changes(REQ, {"trailhead": "Moon Base"}, TRAILS, STARTS)


def test_impossible_values_are_refused_with_the_engines_reason():
    with pytest.raises(ProposalError, match="nights must be a whole number of at least 1"):
        apply_changes(REQ, {"nights": 0}, TRAILS, STARTS)
    with pytest.raises(ProposalError, match="daily must look like 18-26"):
        apply_changes(REQ, {"daily": [11, 6]}, TRAILS, STARTS)
    with pytest.raises(ProposalError, match="gain cap must be more than 0"):
        apply_changes(REQ, {"max_gain_day": -100}, TRAILS, STARTS)
    with pytest.raises(ProposalError, match="5 camps for 2 nights"):
        apply_changes(REQ, {"camps": [1, 2, 3, 4, 5]}, TRAILS, STARTS)
    with pytest.raises(ProposalError, match="Camp miles must go up"):
        apply_changes(REQ, {"camps": [11, 6]}, TRAILS, STARTS)


def test_fewer_nights_clear_pins_that_no_longer_fit():
    pinned = {**REQ, "camps": ["6.3", "12.6"]}
    new, lines = apply_changes(pinned, {"nights": 1}, TRAILS, STARTS)
    assert new["camps"] == [] and "Camps: pinned at mile 6.3, 12.6 → placed by the engine" in lines


def test_a_change_that_changes_nothing_has_no_lines():
    new, lines = apply_changes(REQ, {"nights": 2, "no_roads": False}, TRAILS, STARTS)
    assert lines == [] and new["nights"] == 2


def test_the_prompts_camp_example_does_not_reuse_the_eval_routes_miles():
    # The eval's Bear Rocks route camps at miles 6.34 and 12.68; an example built on those numbers is an answer key.
    for leaked in ("6.34", "12.68", "10.5", "14.5"):
        assert leaked not in SYSTEM_PROMPT


def test_pins_at_the_current_camps_change_nothing():
    new, lines = apply_changes(REQ, {"camps": [7.48, 14.95]}, TRAILS, STARTS, current_camps=[7.481, 14.952])
    assert lines == [] and new["camps"] == []
    _, moved = apply_changes(REQ, {"camps": [7.48, 13.0]}, TRAILS, STARTS, current_camps=[7.481, 14.952])
    assert moved == ["Camps: placed by the engine → pinned at mile 7.48, 13"]


def test_point_must_visits_never_show_raw_ids_in_the_card():
    _, lines = apply_changes({**REQ, "must_visit": ["155984740"]}, {"must_visit_add": ["Red Creek Trail"]}, TRAILS, STARTS)
    assert lines == ["Must visit: a point from the trail data → a point from the trail data, Red Creek Trail"]
