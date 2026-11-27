import copy

import pytest

from backcountry.export import route_segments
from backcountry.graph import build_graph
from backcountry.loops import find_candidates
from backcountry.retrace import retrace
from conftest import CRS, RawBuilder


def square() -> RawBuilder:
    b = RawBuilder()
    for name, x, y in [("A", 0, 0), ("M", 500, 0), ("B", 1000, 0), ("C", 1000, 1000), ("D", 0, 1000), ("F", 500, -500)]:
        b.node(name, x, y)
    b.way(1, ["A", "M", "B"], highway="path", name="South Side")
    b.way(2, ["B", "C"], highway="path", name="East Side")
    b.way(3, ["C", "D", "A"], highway="path", name="North and West")
    return b


def saved(b: RawBuilder) -> dict:
    tg = build_graph(b.doc(), crs=CRS)
    c = find_candidates(tg, b.ids["A"], 3500, 4500, kinds=("loop",))[0]
    return {"route": {"nodes": list(c.route.nodes), "keys": [e["key"] for e in c.route.edges]},
            "segments": route_segments(c.route), "length_m": c.route.length_m}


def test_retrace_same_cut_identical():
    b = square()
    s = saved(b)
    out = retrace(build_graph(b.doc(), crs=CRS), s)
    assert out["ok"] and out["route"].length_m == pytest.approx(s["length_m"], abs=0.01)
    assert out["keys"] == s["route"]["keys"] and out["nodes"] == s["route"]["nodes"]


def test_retrace_way_split_by_new_junction():
    b = square()
    s = saved(b)
    b.way(5, ["M", "F"], highway="path", name="New Side Trail")  # M becomes a junction: way 1 is now two edges
    tg = build_graph(b.doc(), crs=CRS)
    out = retrace(tg, s)
    assert out["ok"] and out["route"].length_m == pytest.approx(s["length_m"], abs=0.01)
    assert b.ids["M"] in out["nodes"] and len(out["keys"]) == len(s["route"]["keys"]) + 1
    assert {e["way_id"] for e in out["route"].edges} == {1, 2, 3}  # never the new side trail


def test_retrace_through_a_junction_that_went_away():
    b = square()
    b.way(5, ["M", "F"], highway="path", name="Side")  # M is a junction when the trip is saved
    s = saved(b)
    assert b.ids["M"] in s["route"]["nodes"]
    b.elements = [e for e in b.elements if e.get("id") != 5]  # the side trail is deleted: M is just a vertex now
    out = retrace(build_graph(b.doc(), crs=CRS), s)
    assert out["ok"], out  # South Side is unchanged, so it isn't reported missing
    assert out["route"].length_m == pytest.approx(s["length_m"], abs=0.01)
    assert {e["way_id"] for e in out["route"].edges} == {1, 2, 3}


def test_retrace_reports_missing_way():
    b = square()
    s = saved(b)
    b.elements = [e for e in b.elements if e.get("id") != 2]  # East Side deleted in the newer data
    out = retrace(build_graph(b.doc(), crs=CRS), s)
    assert out == {"ok": False, "missing": [{"source": "osm:way/2", "name": "East Side"}]}


def test_retrace_reports_missing_node():
    b = square()
    s = saved(b)
    doc = b.doc()
    for e in doc["response"]["elements"]:
        if e.get("id") == 2:  # East Side now ends at a different node: B is gone from it
            e["nodes"] = [b.ids["M"], b.ids["C"]]
            e["geometry"] = [copy.deepcopy(e["geometry"][0]), e["geometry"][1]]
    out = retrace(build_graph(doc, crs=CRS), s)
    assert out["ok"] is False and {"source": "osm:way/2", "name": "East Side"} in out["missing"]


def test_retrace_old_run_without_route():
    b = square()
    out = retrace(build_graph(b.doc(), crs=CRS), {"segments": []})
    assert out == {"ok": False, "missing": [], "reason": "saved before routes were recorded"}


def test_changes_describe_miles_levels_camps_and_water():
    from backcountry.retrace import changes

    MI = 1609.344
    day = lambda a, b, lvl: {"start_m": a * MI, "end_m": b * MI, "rating": {"level": lvl}}  # noqa: E731
    spring = {"source": "osm:node/1", "kind": "spring", "name": "Hidden Spring"}
    old = {"length_m": 20 * MI, "gain_ft": 3000, "level": "moderate", "segments": [],
           "plan": {"days": [day(0, 10, "moderate"), day(10, 20, "moderate")], "camps": [{"dist_m": 10 * MI}]},
           "features": [spring]}
    new = {"length_m": 20.4 * MI, "gain_ft": 3200, "level": "hard", "segments": [],
           "plan": {"days": [day(0, 10.4, "hard"), day(10.4, 20.4, "moderate")], "camps": [{"dist_m": 10.4 * MI}]},
           "features": [{"source": "osm:node/2", "kind": "drinking_water", "name": None}]}
    assert changes(old, new) == [
        "Total: 20.0 mi → 20.4 mi", "Climbing: 3,000 ft → 3,200 ft", "Trip level: Moderate → Hard",
        "Day 1: 10.0 mi, Moderate → 10.4 mi, Hard", "Camp 1 moves 0.4 mi later along the route",
        "Water on the map, now along the route: drinking water",
        "Water on the map, no longer along the route: Hidden Spring"]
