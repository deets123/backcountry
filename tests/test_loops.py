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
        assert repeated == set(c.stem_keys), "only stem edges may be walked twice"
        if c.stem_keys:  # lollipop: stem out, loop, same stem back
            n = len(c.stem_keys)
            assert [e["key"] for e in r.edges[:n]] == c.stem_keys
            assert [e["key"] for e in r.edges[-n:]] == c.stem_keys[::-1]
            assert c.repeated_m == pytest.approx(sum(e["length_m"] for e in r.edges[:n]), abs=0.01)
        else:
            assert c.repeated_m == 0


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
