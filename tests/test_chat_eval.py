from pathlib import Path

import pytest

from backcountry.chat.eval import EXPECT_KEYS, check_case, load_cases, run_eval, summary_table
from backcountry.chat.models import FakeModel, ModelBadOutput

CASES = Path(__file__).resolve().parents[1] / "evals" / "chat_cases.yaml"
M = 1609.344
RUN = {"request": {"trailhead": "bear_rocks", "total": [18.0, 26.0], "nights": 2, "daily": [6.0, 11.0], "max_gain_day": 2500.0,
                   "type": "any", "no_roads": False, "camps": [], "camps_at_sites": False, "must_visit": [], "avoid": [], "direction": None},
       "candidates": [{"length_m": 19.02 * M, "road_m": 0.81 * M, "gain_ft": 2023.0, "plan": {"days": [
           {"index": 1, "start_m": 0, "end_m": 6.34 * M, "length_m": 6.34 * M, "gain_ft": 784.0},
           {"index": 2, "start_m": 6.34 * M, "end_m": 12.68 * M, "length_m": 6.34 * M, "gain_ft": 331.0},
           {"index": 3, "start_m": 12.68 * M, "end_m": 19.02 * M, "length_m": 6.34 * M, "gain_ft": 908.0}]}}]}


def result(kind="change", reply="", request=None, withheld=None, note=None, changes=None):
    proposal = {"request": {**RUN["request"], **(request or {})}, "changes": changes or ["x"]} if request is not None else None
    return {"kind": kind, "reply": reply, "proposal": proposal, "withheld": withheld, "note": note, "model": "fake"}


def test_the_case_file_is_well_formed():
    cases = load_cases(CASES)
    assert len(cases) >= 28 and len({c["id"] for c in cases}) == len(cases)
    assert all(set(c["expect"]) <= EXPECT_KEYS for c in cases)
    assert sum(c["expect"].get("kind") == "explain" for c in cases) >= 8


def test_request_and_direction_checks():
    case = {"id": "n", "message": "3 nights", "expect": {"kind": "change", "request": {"nights": 3}}}
    assert check_case(case, result(request={"nights": 3}), None, RUN) == []
    assert check_case(case, result(request={"nights": 2}), None, RUN) == ["nights is 2, expected 3"]
    assert check_case(case, result(kind="explain"), None, RUN) == ["kind is explain, expected change", "no proposal"]
    lower = {"id": "g", "message": "less climbing", "expect": {"lower": "max_gain_day"}}
    assert check_case(lower, result(request={"max_gain_day": 2000.0}), None, RUN) == []
    assert check_case(lower, result(request={"max_gain_day": 3000.0}), None, RUN) == ["max_gain_day did not go down (2500.0 → 3000.0)"]
    contains = {"id": "a", "message": "skip the road", "expect": {"request_contains": {"avoid": "Dolly Sods Road"}}}
    assert check_case(contains, result(request={"avoid": ["Dolly Sods Road"]}), None, RUN) == []


def test_day_length_checks_use_camp_pins_or_the_daily_max():
    case = {"id": "d", "message": "shorter day 2", "expect": {"day_shorter": 2}}
    assert check_case(case, result(request={"camps": ["6.34", "11"]}), None, RUN) == []
    assert check_case(case, result(request={"daily": [6.0, 6.0]}), None, RUN) == []
    assert check_case(case, result(request={"camps": ["6.34", "14"]}), None, RUN) == ["day 2 is not shorter"]
    longer = {"id": "l", "message": "longer day 1", "expect": {"day_longer": 1}}
    assert check_case(longer, result(request={"camps": ["8", "13"]}), None, RUN) == []


def test_reply_checks_and_dynamic_facts():
    hardest = {"id": "h", "message": "hardest day?", "expect": {"kind": "explain", "grounded": True, "mentions_hardest_day": True}}
    assert check_case(hardest, result("explain", "Day 3 has the most climbing."), None, RUN) == []
    assert check_case(hardest, result("explain", "Day 1 is hardest."), None, RUN) == ["reply doesn't name day 3"]
    assert check_case(hardest, result("explain", "", withheld="unsupported number '9'"), None, RUN) == [
        "reply was withheld (unsupported number '9')", "reply doesn't name day 3"]
    road = {"id": "r", "message": "road miles?", "expect": {"mentions_number": "road_m"}}
    assert check_case(road, result("explain", "About 0.8 mi is on roads."), None, RUN) == []
    assert check_case(road, result("explain", "About 2 mi is on roads."), None, RUN) == ["reply doesn't give road_m (0.81)"]
    unknown = {"id": "w", "message": "water ok?", "expect": {"reply_includes_any": ["unknown", "not checked"], "no_proposal": True}}
    assert check_case(unknown, result("explain", "That's unknown."), None, RUN) == []
    assert check_case(unknown, result("explain", "Yes."), None, RUN) == ["reply has none of: unknown, not checked"]
    assert check_case(unknown, None, "The model's answer didn't make sense", RUN) == ["error: The model's answer didn't make sense"]


def test_run_eval_scores_a_model_and_the_table_summarizes():
    cases = [{"id": "n", "message": "3 nights", "expect": {"kind": "change", "request": {"nights": 3}}},
             {"id": "b", "message": "?", "expect": {"kind": "explain"}}]
    model = FakeModel([{"reply": "Three nights.", "kind": "change", "changes": {"nights": 3}}, ModelBadOutput("bad")])
    ticks = iter([0.0, 2.0, 2.0, 3.0])
    out = run_eval(model, _grid_run(), cases, ["Row 0"], [], clock=lambda: next(ticks))
    assert [c["ok"] for c in out["cases"]] == [True, False] and out["summary"]["passed"] == 1
    assert out["summary"]["errors"] == 1 and out["summary"]["mean_s"] == pytest.approx(1.5)
    table = summary_table([out])
    assert "fake" in table and "1/2" in table


def _grid_run():
    return {**RUN, "candidates": [{**RUN["candidates"][0], "index": 1, "kind": "loop", "loss_ft": 2023.0, "repeated_m": 0.0,
                                   "penalty": 0.81, "segments": [{"label": "Row 0"}], "features": [], "notes": [],
                                   "plan": {**RUN["candidates"][0]["plan"], "camps": [], "notes": [], "violations": [],
                                            "days": [{**d, "loss_ft": 1.0, "start_label": "Start", "end_label": "End"}
                                                     for d in RUN["candidates"][0]["plan"]["days"]]}}],
            "known_gaps": ["permits: not checked"]}
