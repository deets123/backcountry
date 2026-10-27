import pytest

from backcountry.graph import build_graph
from backcountry.loops import Weights, find_candidates, loop_graph
from conftest import CRS

KM = 1000.0


@pytest.fixture
def tg(grid):
    return build_graph(grid.doc(), crs=CRS)


def _check_invariants(tg, cands):
    """Every candidate: closed, connected, real edges only, no edge reused except on the stem."""
    G = tg.graph
    for c in cands:
        r = c.route
        assert r.nodes[0] == r.nodes[-1] == c.start, "loop must start and end at the start node"
        assert list(r.line_wgs84.coords[0]) == pytest.approx(list(r.line_wgs84.coords[-1]))
        for e, (u, v) in zip(r.edges, zip(r.nodes, r.nodes[1:])):
            assert G.has_edge(u, v, e["key"]), e
        assert r.length_m == pytest.approx(sum(e["length_m"] for e in r.edges), abs=0.01)
        counts = {}
        for e in r.edges:
            counts[e["key"]] = counts.get(e["key"], 0) + 1
        repeated = {k for k, n in counts.items() if n > 1}
        assert all(n <= 2 for n in counts.values())
        assert repeated == set(c.stem_keys) | set(c.detour_keys), "only stem or detour edges may be walked twice"
        lengths = {e["key"]: e["length_m"] for e in r.edges}
        if c.stem_keys:  # lollipop: stem out, loop, same stem back
            n = len(c.stem_keys)
            assert [e["key"] for e in r.edges[:n]] == c.stem_keys
            assert [e["key"] for e in r.edges[-n:]] == c.stem_keys[::-1]
        # repeated miles = trail walked twice, counted once (stem + detours)
        assert c.repeated_m == pytest.approx(sum(lengths[k] for k in c.stem_keys) + sum(lengths[k] for k in c.detour_keys), abs=0.01)


# --- loop graph ------------------------------------------------------------------


def test_loop_graph_prunes_spurs_and_merges_pass_through_nodes(grid, tg):
    lg = loop_graph(tg, grid.ids["g00"])
    assert grid.ids["D"] not in lg.graph and grid.ids["T"] not in lg.graph
    assert grid.ids["m"] not in lg.graph
    merged = next(d for u, v, d in lg.graph.edges(data=True) if {u, v} == {grid.ids["g01"], grid.ids["g11"]})
    assert merged["length_m"] == pytest.approx(KM, abs=0.01) and len(merged["keys"]) == 2
    # Corners g20, g02, g22 have only two edges, so they merge too; the start g00 is kept.
    assert grid.ids["g20"] not in lg.graph and grid.ids["g00"] in lg.graph
    assert lg.graph.number_of_nodes() == 6 and lg.graph.number_of_edges() == 9  # 13 real edges, 4 merges


def test_loop_graph_keeps_the_start_even_on_a_spur(grid, tg):
    lg = loop_graph(tg, grid.ids["T"])
    assert grid.ids["T"] in lg.graph and lg.graph.degree(grid.ids["T"]) == 1


# --- candidates -------------------------------------------------------------------


def test_exact_small_square_is_the_only_loop_in_a_tight_range(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 4.1 * KM, kinds=("loop",))
    _check_invariants(tg, cands)
    assert len(cands) == 1 and cands[0].length_m == pytest.approx(4 * KM, abs=0.01)
    assert set(cands[0].route.nodes) == {grid.ids[n] for n in ("g00", "g10", "g11", "m", "g01")}


def test_two_rectangles_and_road_penalty_orders_them(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 5.9 * KM, 6.1 * KM, kinds=("loop",))
    _check_invariants(tg, cands)
    assert [round(c.length_m) for c in cands] == [6000, 6000]
    assert cands[0].road_m == 0 and cands[1].road_m == pytest.approx(KM, abs=0.01)
    assert cands[0].penalty < cands[1].penalty


def test_no_roads_drops_loops_that_need_the_road(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 5.9 * KM, 6.1 * KM, kinds=("loop",), no_roads=True)
    assert len(cands) == 1 and cands[0].road_m == 0


def test_spur_trailhead_gets_lollipops_with_a_single_stem(grid, tg):
    cands = find_candidates(tg, grid.ids["T"], 4.9 * KM, 5.1 * KM)
    _check_invariants(tg, cands)
    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "lollipop" and c.length_m == pytest.approx(5 * KM, abs=0.01)
    assert c.repeated_m == pytest.approx(500, abs=0.01)
    assert c.route.nodes[0] == c.route.nodes[-1] == grid.ids["T"]


def test_lollipop_stem_can_be_longer_than_one_edge(grid, tg):
    # From T, a lollipop around the far square (g11 g21 g22 g12) needs stem T-g00-g10-g11 or similar.
    cands = find_candidates(tg, grid.ids["T"], 8.9 * KM, 9.1 * KM, kinds=("lollipop",))
    _check_invariants(tg, cands)
    assert cands and all(c.repeated_m > 500 for c in cands)


def test_returned_candidates_are_diverse_and_sorted(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, count=3)
    _check_invariants(tg, cands)
    assert len(cands) == 3
    assert [c.penalty for c in cands] == sorted(c.penalty for c in cands)
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            assert a.overlap(b) < 0.5


def test_out_of_range_is_penalized_not_hidden_when_nothing_fits(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 4.5 * KM, 5.5 * KM, kinds=("loop",))
    assert cands == [] or all(c.penalty > 0 for c in cands)


def test_weights_are_configurable(grid, tg):
    heavy = find_candidates(tg, grid.ids["g00"], 5.9 * KM, 6.1 * KM, kinds=("loop",), weights=Weights(road=100.0))
    assert heavy[1].penalty - heavy[0].penalty == pytest.approx(100.0 * KM / 1609.344, rel=1e-6)


def test_search_report_counts_what_was_explored(grid, tg):
    from backcountry.loops import search

    result = search(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM)
    assert result["loops_found"] >= 3 and result["truncated"] is False
    assert result["via_pairs_tried"] > 0
    assert result["candidates"]


# --- outputs ------------------------------------------------------------------------


def test_write_candidates_outputs(grid, tg, tmp_path):
    from backcountry.export import write_candidates
    from backcountry.loops import search
    from backcountry.region import BBox, Region

    region = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))
    report = search(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM)
    paths = write_candidates(report, region, tmp_path / "loops", "osm:node/1000", "2026-01-01T00:00:00Z",
                             3.9 * KM, 8.1 * KM)
    assert paths["summary"].exists() and paths["map"].exists() and paths["search"].exists()
    md = paths["summary"].read_text()
    assert "Candidate 1" in md and "Candidate 3" in md and " mi" in md and "Not checked" in md
    assert " m " not in md and "safe" not in md.lower()
    for i in (1, 2, 3):
        assert (tmp_path / "loops" / f"candidate_{i}" / "route.gpx").exists()
        assert (tmp_path / "loops" / f"candidate_{i}" / "summary.md").exists()
    html = paths["map"].read_text()
    assert "Candidate 1" in html and "basemap.nationalmap.gov" in html and "OpenStreetMap contributors" in html


# --- day plans in the ranking and outputs -----------------------------------------


def test_evaluate_hook_adds_penalty_and_reorders(grid, tg):
    from backcountry.loops import search

    plain = search(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM)["candidates"]
    assert plain[0].length_m == pytest.approx(6 * KM, abs=0.01)  # penalty 0 and nearest the middle of the range

    def dislike_long(c):  # pretend the longer loops split badly
        c.plan = f"plan for {round(c.length_m)}"
        return 50.0 if c.length_m > 5 * KM else 0.0

    ranked = search(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, evaluate=dislike_long)["candidates"]
    assert ranked[0].length_m == pytest.approx(4 * KM, abs=0.01) and ranked[0].plan.startswith("plan for")
    assert [c.penalty for c in ranked] == sorted(c.penalty for c in ranked)


def test_candidate_outputs_include_days_and_camps(grid, tg, tmp_path):
    import gpxpy

    from backcountry.export import write_candidates
    from backcountry.loops import search
    from backcountry.region import BBox, Region
    from backcountry.route import add_elevation
    from backcountry.split import split_route
    from conftest import plane_dem

    region = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))
    dem = plane_dem(0.0, 0.05, 1000)

    def evaluate(c):
        add_elevation(c.route, dem, CRS)
        c.plan = split_route(c.route, nights=1, daily_min_m=1.0 * KM, daily_max_m=4.0 * KM, max_gain_ft=10_000)
        return c.plan.penalty

    report = search(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, evaluate=evaluate)
    paths = write_candidates(report, region, tmp_path / "loops", "osm:node/1000", "2026-01-01T00:00:00Z",
                             3.9 * KM, 8.1 * KM)
    md = paths["summary"].read_text()
    for text in ("Day 1", "Day 2", "Camp 1", "Location unverified", "no known camp site within"):
        assert text in md, text
    gpx = gpxpy.parse((tmp_path / "loops" / "candidate_1" / "route.gpx").read_text())
    assert "Camp 1" in [w.name for w in gpx.waypoints]
    assert "Day 1" in (tmp_path / "loops" / "candidate_1" / "summary.md").read_text()


# --- M7 constraints -----------------------------------------------------------------


def test_avoid_by_name_and_by_way_id(grid, tg):
    from backcountry.loops import avoided_keys

    keys = avoided_keys(tg, ["Grid Road"])
    assert keys and all(tg.graph.edges[u, v, k]["way_id"] == 112 for u, v, k in
                        [(u, v, k) for u, v, k in tg.graph.edges(keys=True) if k in keys])
    assert avoided_keys(tg, ["osm:way/112"]) == keys and avoided_keys(tg, ["112"]) == keys
    assert avoided_keys(tg, ["grid road"]) == keys  # case-insensitive
    with pytest.raises(ValueError, match="No trail or road"):
        avoided_keys(tg, ["Nope Trail"])


def test_avoided_trail_never_appears(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, avoid=["Grid Road"], count=5)
    assert cands
    assert all(e["way_id"] != 112 for c in cands for e in c.route.edges)


def test_must_visit_node_on_the_network(grid, tg):
    g22 = grid.ids["g22"]
    cands = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, must_visit=[{"node": g22}], count=5)
    _check_invariants(tg, cands)
    assert cands and all(g22 in c.route.nodes for c in cands)
    # Loops that don't pass the corner naturally reach it by an out-and-back, which counts as repeated miles.
    assert all(c.length_m >= 8 * KM - 1 for c in cands if not c.detour_keys)
    assert all(c.repeated_m > 0 for c in cands if c.detour_keys)


def test_must_visit_spur_gets_an_out_and_back_detour(grid, tg):
    D = grid.ids["D"]
    cands = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, must_visit=[{"node": D}], count=3)
    assert cands
    for c in cands:
        assert D in c.route.nodes
        assert c.detour_keys and c.repeated_m == pytest.approx(500, abs=0.01)  # 500 m of trail walked twice
        keys = [e["key"] for e in c.route.edges]
        assert all(keys.count(k) == 2 for k in c.detour_keys)
        assert c.route.length_m == pytest.approx(sum(e["length_m"] for e in c.route.edges), abs=0.01)


def test_must_visit_trail_by_name(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, must_visit=[{"name": "Row 2"}], count=5)
    assert cands and all(any(e["name"] == "Row 2" for e in c.route.edges) for c in cands)


def test_impossible_must_visit_is_explained(grid, tg):
    from backcountry.loops import search

    grid.node("Z1", 9000, 9000)
    grid.node("Z2", 9000, 9500)
    grid.way(200, ["Z1", "Z2"], highway="path", name="Island")
    tg2 = build_graph(grid.doc(), crs=CRS)
    report = search(tg2, grid.ids["g00"], 3.9 * KM, 8.1 * KM, must_visit=[{"node": grid.ids["Z1"]}])
    assert report["candidates"] == []
    assert any("not reachable" in n for n in report["must_visit_notes"])


def test_direction_can_be_forced(grid, tg):
    from backcountry.loops import orientation

    cw = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 4.1 * KM, direction="cw")
    ccw = find_candidates(tg, grid.ids["g00"], 3.9 * KM, 4.1 * KM, direction="ccw")
    assert orientation(cw[0].route) == "cw" and orientation(ccw[0].route) == "ccw"
    assert cw[0].route.nodes == ccw[0].route.nodes[::-1]
    assert set(e["key"] for e in cw[0].route.edges) == set(e["key"] for e in ccw[0].route.edges)
    _check_invariants(tg, cw + ccw)


# --- diff against the previous run -------------------------------------------------


def test_second_run_into_the_same_folder_writes_a_diff(grid, tg, tmp_path):
    from backcountry.export import write_candidates
    from backcountry.loops import search
    from backcountry.region import BBox, Region

    region = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))
    out = tmp_path / "loops"
    first = search(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM)
    paths = write_candidates(first, region, out, "osm:node/1000", "2026-01-01T00:00:00Z", 3.9 * KM, 8.1 * KM,
                             request={"total": "2.4-5.0"})
    assert "diff" not in paths and (out / "candidates.json").exists()

    second = search(tg, grid.ids["g00"], 3.9 * KM, 8.1 * KM, avoid=["Grid Road"])
    paths = write_candidates(second, region, out, "osm:node/1000", "2026-01-01T00:00:00Z", 3.9 * KM, 8.1 * KM,
                             request={"total": "2.4-5.0", "avoid": ["Grid Road"]})
    md = paths["diff"].read_text()
    assert "## Request" in md and "avoid" in md and "Grid Road" in md
    assert "Candidate 1" in md and ("unchanged" in md or "changed" in md or "new" in md)
    assert (out / "candidates.previous.json").exists()


def test_no_loops_from_a_spur_explains_and_suggests_lollipops(grid, tg):
    from backcountry.loops import search

    report = search(tg, grid.ids["T"], 3.9 * KM, 4.1 * KM, kinds=("loop",))
    assert report["candidates"] == []
    assert any("dead-end" in n and "--type any" in n for n in report["notes"])


def test_search_does_not_depend_on_sub_millimetre_length_noise(grid):
    # Each platform's math library converts coordinates slightly differently (~1e-9 m). Equal-length paths must
    # stay equal everywhere, or the search picks different routes on Linux and macOS.
    found = []
    for noise in (-1e-6, 0.0, 1e-6):  # far below a millimetre, enough to flip a tie either way
        tg = build_graph(grid.doc(), crs=CRS)
        for u, v, d in tg.graph.edges(data=True):
            if {u, v} == {grid.ids["g01"], grid.ids["m"]}:  # g00 to g11 is 2,000 m two ways; this breaks the tie
                d["length_m"] += noise
        cands = find_candidates(tg, grid.ids["g00"], 5.9 * KM, 6.1 * KM, kinds=("loop",), no_roads=True)
        found.append([round(c.length_m) for c in cands])
    assert found[0] == found[1] == found[2] == [6000]


# --- a single ring trail (no junction on it but the start) ---------------------------------------


def _ring(stem: bool):
    from conftest import RawBuilder

    b = RawBuilder()
    for name, x, y in [("A", 0, 0), ("B", 1000, 0), ("C", 1000, 1000), ("D", 0, 1000), ("T", -500, 0)]:
        b.node(name, x, y)
    b.way(1, ["A", "B", "C"], highway="path", name="Ring East")
    b.way(2, ["C", "D", "A"], highway="path", name="Ring West")
    if stem:
        b.way(3, ["T", "A"], highway="path", name="Stem")
    return b, build_graph(b.doc(), crs=CRS)


def test_ring_trail_from_a_start_on_it_is_a_loop():
    b, tg = _ring(stem=False)
    cands = find_candidates(tg, b.ids["A"], 3.5 * KM, 4.5 * KM, kinds=("loop",))
    assert len(cands) == 1 and cands[0].kind == "loop" and cands[0].length_m == pytest.approx(4000, abs=1)
    _check_invariants(tg, cands)


def test_ring_trail_behind_a_stem_is_a_lollipop():
    b, tg = _ring(stem=True)
    cands = find_candidates(tg, b.ids["T"], 4.5 * KM, 5.5 * KM, kinds=("loop", "lollipop"))
    assert [c.kind for c in cands] == ["lollipop"] and cands[0].length_m == pytest.approx(5000, abs=1)
    _check_invariants(tg, cands)


# --- reach limit (speed) ----------------------------------------------------------------


def test_loop_graph_drops_nodes_out_of_reach(grid, tg):
    ids = grid.ids
    H = loop_graph(tg, ids["T"], reach_m=2600).graph
    for far in ("g12", "g21", "g22", "g02"):
        assert ids[far] not in H, far
    assert ids["g11"] in H and ids["g00"] in H


@pytest.mark.parametrize("lo, hi", [(2 * KM, 5 * KM), (4 * KM, 7 * KM), (5 * KM, 9 * KM)])
def test_reach_limit_finds_the_same_loops(grid, tg, lo, hi):
    from backcountry.loops import _expand, waypoint_loops

    def found(reach):
        H = loop_graph(tg, grid.ids["g00"], reach_m=reach).graph
        return {frozenset(_expand(H, steps)[0]) for steps, _ in waypoint_loops(H, grid.ids["g00"], lo, hi)}

    assert found(hi / 2) == found(None)
    assert found(None)


def test_search_budget_is_60_seconds():
    from backcountry import loops

    assert loops.MAX_SECONDS == 60.0


def test_search_reports_the_reach(grid, tg):
    from backcountry.loops import WIDEN, search

    report = search(tg, grid.ids["g00"], 2 * KM, 5 * KM)
    assert report["loop_graph"]["reach_m"] == pytest.approx(5 * KM * (1 + WIDEN) / 2)


# --- first results early (speed) ---------------------------------------------------------


def _ticking_clock(step):
    t = [0.0]

    def clock():
        t[0] += step
        return t[0]
    return clock


def test_snapshot_comes_after_first_s_with_scored_candidates(grid, tg):
    from backcountry.loops import search

    snaps = []
    report = search(tg, grid.ids["g00"], 2 * KM, 9 * KM, on_snapshot=snaps.append, first_s=8, clock=_ticking_clock(5))
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap["partial"] is True and report["partial"] is False
    assert snap["candidates"]
    assert [c.penalty for c in snap["candidates"]] == sorted(c.penalty for c in snap["candidates"])
    _check_invariants(tg, snap["candidates"])


def test_no_snapshot_when_search_finishes_first(grid, tg):
    from backcountry.loops import search

    snaps = []
    report = search(tg, grid.ids["g00"], 2 * KM, 9 * KM, on_snapshot=snaps.append, first_s=999)
    assert snaps == [] and report["partial"] is False


def test_only_the_best_loops_get_day_plans(grid, tg, monkeypatch):
    from backcountry import loops

    monkeypatch.setattr(loops, "MAX_EVALUATED", 1)
    seen = []
    report = loops.search(tg, grid.ids["g00"], 2 * KM, 12 * KM, count=1, evaluate=lambda c: seen.append(c) or 0.0)
    total = report["loops_found"] + report["lollipops_found"]
    assert report["evaluated"] == len(seen) <= 1 + 3 * 1 and len(seen) < total
    assert all(c in seen for c in report["candidates"]) and len(report["candidates"]) == 1


# --- out-and-back -------------------------------------------------------------------------


def test_out_and_back_to_the_dead_spur(grid, tg):
    from backcountry.loops import out_and_backs

    cands = out_and_backs(tg, grid.ids["g00"], 2.5 * KM, 3.5 * KM)
    to_d = [c for c in cands if c.turnaround == grid.ids["D"]]
    assert to_d, [c.turnaround for c in cands]
    c = to_d[0]
    assert c.kind == "out_and_back" and c.nodes == c.nodes[::-1] and c.nodes[len(c.nodes) // 2] == grid.ids["D"]
    assert c.repeated_m == pytest.approx(c.length_m / 2)
    assert c.length_m == pytest.approx(3 * KM, abs=1)
    assert any("Out and back, 0.9 mi each way." == n for n in c.route.notes)
    _check_invariants(tg, cands)


def test_out_and_back_turnarounds_fit_the_range(grid, tg):
    from backcountry.loops import out_and_backs

    for lo, hi in ((1 * KM, 2.2 * KM), (3 * KM, 6 * KM)):
        cands = out_and_backs(tg, grid.ids["T"], lo, hi)
        assert cands
        assert all(lo <= c.length_m <= hi for c in cands)
        G = tg.graph
        assert all(G.degree(c.turnaround) == 1 or G.degree(c.turnaround) >= 3 for c in cands)


def test_out_and_back_respects_avoid(grid, tg):
    from backcountry.loops import avoided_keys, out_and_backs

    excluded = avoided_keys(tg, ["Dead Spur"])
    assert not any(c.turnaround == grid.ids["D"] for c in out_and_backs(tg, grid.ids["g00"], 2.5 * KM, 3.5 * KM,
                                                                       exclude_keys=excluded))


def test_search_kinds_out_and_back(grid, tg):
    cands = find_candidates(tg, grid.ids["g00"], 2.5 * KM, 3.5 * KM, kinds=("out_and_back",))
    assert cands and all(c.kind == "out_and_back" for c in cands)


# --- one way ------------------------------------------------------------------------------


def test_one_way_from_T_to_g22(grid, tg):
    from backcountry.loops import one_ways

    ids = grid.ids
    cands = one_ways(tg, ids["T"], ids["g22"], 4 * KM, 7 * KM)
    assert cands
    G = tg.graph
    for c in cands:
        assert c.kind == "one_way" and c.finish == ids["g22"]
        assert c.route.nodes[0] == ids["T"] and c.route.nodes[-1] == ids["g22"]
        assert 4 * KM <= c.length_m <= 7 * KM
        for e, (u, v) in zip(c.route.edges, zip(c.route.nodes, c.route.nodes[1:])):
            assert G.has_edge(u, v, e["key"])
        assert any(n.startswith("One way: start and finish are ") and n.endswith("Shuttle drive: unknown.")
                   for n in c.route.notes)
    assert min(c.length_m for c in cands) == pytest.approx(4.5 * KM, abs=1)


def test_one_way_through_a_spur_walks_it_out_and_back(grid, tg):
    from backcountry.loops import one_ways

    ids = grid.ids
    cands = one_ways(tg, ids["T"], ids["g20"], 2 * KM, 5 * KM, through=ids["D"])
    assert cands and all(ids["D"] in c.route.nodes for c in cands)
    c = cands[0]
    assert c.detour_keys and c.repeated_m == pytest.approx(500, abs=1)  # D's spur, walked twice
