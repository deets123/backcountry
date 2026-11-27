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
        apply_changes(REQ, {"nights": -1}, TRAILS, STARTS)  # 0 nights is a day hike, not an error
    with pytest.raises(ProposalError, match=r"Miles per day need a low below the high, both above 0 \(e.g. 6-11\); got 11-6"):
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


def test_a_one_day_trip_clears_nights_daily_and_camps():
    assert shape_problem({"reply": "", "kind": "change", "changes": {"nights": None, "daily": None}}, RESPONSE_SCHEMA) is None
    new, lines = apply_changes({**REQ, "camps": ["6.3", "12.6"]}, {"nights": None}, TRAILS, STARTS)
    assert (new["nights"], new["daily"], new["camps"]) == (None, None, [])
    assert "Nights: 2 → none" in lines and "Miles per day: 6-11 mi → not set" in lines


def test_a_range_without_a_low_below_the_high_says_so_plainly():
    with pytest.raises(ProposalError, match=r"Total miles need a low below the high, both above 0 \(e.g. 20-26\); got 22.97-22.97"):
        apply_changes(REQ, {"total": [22.97, 22.97]}, TRAILS, STARTS)
    with pytest.raises(ProposalError, match=r"Miles per day need a low below the high, both above 0 \(e.g. 6-11\); got 0-0"):
        apply_changes(REQ, {"daily": [0, 0]}, TRAILS, STARTS)


def test_zero_nights_means_a_one_day_hike():
    new, lines = apply_changes(REQ, {"nights": 0, "total": [8, 12]}, TRAILS, STARTS)
    assert (new["nights"], new["daily"]) == (None, None) and "Nights: 2 → none" in lines


def test_fewer_nights_with_the_old_camp_list_lets_the_engine_place_camps():
    # The model changed nights 3 → 2 but sent the old trip's three camps (seen on the phone, Pemi).
    three = {**REQ, "nights": 3}
    new, lines = apply_changes(three, {"nights": 2, "camps": [7.2, 14.5, 21.8]}, TRAILS, STARTS)
    assert new["nights"] == 2 and new["camps"] == [] and "Nights: 3 → 2" in lines
    with pytest.raises(ProposalError, match="5 camps for 2 nights"):  # without a nights change it's still refused
        apply_changes(REQ, {"camps": [1, 2, 3, 4, 5]}, TRAILS, STARTS)


# --- per-day levels and the trip level -----------------------------------------------


def test_day_levels_become_a_proposal_with_numbers():
    from backcountry.difficulty import LEVEL_TARGETS

    new, lines = apply_changes(REQ, {"days": [{"day": 1, "level": "easy"}, {"day": 2, "level": "hard"},
                                              {"day": 3, "level": "hard", "miles": [8, 10]}]}, TRAILS, STARTS)
    assert [d["level"] for d in new["days"]] == ["easy", "hard", "hard"] and new["days"][2]["miles"] == [8.0, 10.0]
    easy = LEVEL_TARGETS["backpacking"]["easy"]
    assert lines[0] == (f"Day 1: not set → Easy ({easy['miles'][0]}-{easy['miles'][1]} mi, ≤ {easy['max_gain_ft']:,} ft, "
                        f"≤ {easy['max_grade_pct']}%)")
    assert lines[2].startswith("Day 3: not set → Hard (8-10 mi, ≤ ")
    assert "Days" not in " ".join(lines)  # one line per day, not a raw list
    schema_days = RESPONSE_SCHEMA["properties"]["changes"]["properties"]["days"]
    assert schema_days["items"]["properties"]["level"]["enum"] == ["easy", "moderate", "hard", "strenuous", None]


def test_day_targets_merge_by_day_and_clear():
    first, _ = apply_changes(REQ, {"days": [{"day": 1, "level": "easy"}, {"day": 2, "level": "hard"}]}, TRAILS, STARTS)
    second, lines = apply_changes(first, {"days": [{"day": 2, "level": "moderate"}]}, TRAILS, STARTS)
    assert [(d["day"], d["level"]) for d in second["days"]] == [(1, "easy"), (2, "moderate")]
    assert lines == ["Day 2: Hard → Moderate (4-8 mi, ≤ 1,200 ft, ≤ 15%)"]
    unset, lines = apply_changes(second, {"days": [{"day": 1, "level": None}]}, TRAILS, STARTS)
    assert [d["day"] for d in unset["days"]] == [2] and lines == ["Day 1: Easy → not set"]
    cleared, _ = apply_changes(second, {"days": []}, TRAILS, STARTS)
    assert cleared["days"] == []


def test_late_start_sets_a_day_one_target():
    new, lines = apply_changes(REQ, {"days": [{"day": 1, "miles": [2, 4]}]}, TRAILS, STARTS)
    assert new["days"] == [{"day": 1, "level": None, "miles": [2.0, 4.0], "max_gain_ft": None, "max_grade_pct": None}]
    assert lines == ["Day 1: not set → 2-4 mi"]


def test_day_targets_replace_camp_pins():
    pinned = REQ | {"camps": ["7", "14"]}
    new, lines = apply_changes(pinned, {"days": [{"day": 1, "level": "easy"}]}, TRAILS, STARTS)
    assert new["camps"] == [] and "Camps: pinned at mile 7, 14 → placed by the engine" in lines


def test_bad_day_targets_are_refused_in_plain_words():
    with pytest.raises(ProposalError, match="That change doesn't work: day 4: this trip has 3 days"):
        apply_changes(REQ, {"days": [{"day": 4, "level": "easy"}]}, TRAILS, STARTS)


def test_fewer_nights_drops_targets_for_days_that_no_longer_exist():
    first, _ = apply_changes(REQ, {"days": [{"day": 1, "level": "easy"}, {"day": 3, "level": "hard"}]}, TRAILS, STARTS)
    new, _ = apply_changes(first, {"nights": 1, "total": [12, 22]}, TRAILS, STARTS)
    assert [d["day"] for d in new["days"]] == [1]
    hike, _ = apply_changes(first, {"nights": None, "total": [6, 10]}, TRAILS, STARTS)
    assert hike["days"] == []


def test_trip_level_is_a_change():
    new, lines = apply_changes(REQ, {"level": "moderate"}, TRAILS, STARTS)
    assert new["level"] == "moderate" and lines == ["Trip level: any → Moderate"]
    back, lines = apply_changes(new, {"level": None}, TRAILS, STARTS)
    assert back["level"] is None and lines == ["Trip level: Moderate → any"]


def test_prompt_maps_words_to_levels_and_days():
    for words in ("days (not daily)", "start late", "chill", "brutal", "easy, moderate, hard or strenuous unless", "hardest day"):
        assert words in " ".join(SYSTEM_PROMPT.lower().split()), words


def test_no_daily_limit_on_a_trip_with_nights_means_the_loosest_range():
    # the prompt says to widen daily from 1 up to the total's high; a model that sends null instead means the same
    new, lines = apply_changes(REQ, {"daily": None}, TRAILS, STARTS)
    assert new["daily"] == [1.0, 26.0] and new["nights"] == 2
    assert lines == ["Miles per day: 6-11 mi → 1-26 mi"]
    hike, _ = apply_changes(REQ, {"nights": None, "daily": None, "total": [6, 10]}, TRAILS, STARTS)
    assert hike["daily"] is None


def test_chat_proposal_keeps_destination_finish_and_type():
    from backcountry.planning import GenerateRequest

    req = GenerateRequest.from_dict({"trailhead": "1", "total": "8-12", "type": "one_way", "finish": "2",
                                     "destination": "node/5"})
    new, _ = apply_changes(req.to_dict(), {"total": [9, 14]}, [], [])
    out = GenerateRequest.from_dict(new)
    assert out.type == "one_way" and out.finish == "2" and out.destination == "node/5" and out.total == (9.0, 14.0)
