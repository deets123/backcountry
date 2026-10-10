"""Invariants on the real cached region data. Skipped if nothing is cached."""

import pytest
from pyproj import Transformer
from shapely.geometry import LineString

from backcountry import ingest
from backcountry.graph import build_graph
from backcountry.region import load_region

REGION = load_region("dolly_sods")
try:
    RAW = ingest.load_raw(REGION)
except FileNotFoundError:
    RAW = None

pytestmark = pytest.mark.skipif(RAW is None, reason="no cached OSM data; run `backcountry ingest --region dolly_sods`")


@pytest.fixture(scope="module")
def tg():
    return build_graph(RAW, REGION.crs)


@pytest.fixture(scope="module")
def ways():
    return {e["id"]: e for e in RAW["response"]["elements"] if e["type"] == "way"}


def test_every_edge_is_a_slice_of_a_cached_osm_way(tg, ways):
    for _, _, d in tg.graph.edges(data=True):
        nodes, seg = ways[d["way_id"]]["nodes"], d["osm_nodes"]
        assert any(nodes[i : i + len(seg)] == seg for i in range(len(nodes))), d["source"]
        assert d["retrieved_at"] == RAW["retrieved_at"]


def test_no_excluded_or_non_routable_way_in_graph(tg):
    in_graph = {d["way_id"] for *_, d in tg.graph.edges(data=True)}
    assert not in_graph & {r["way_id"] for r in tg.excluded}
    assert not in_graph & set(tg.other_highways)


def test_way_length_is_preserved_across_its_edges(tg, ways):
    to_utm = Transformer.from_crs("EPSG:4326", REGION.crs, always_xy=True)
    per_way = {}
    for *_, d in tg.graph.edges(data=True):
        per_way[d["way_id"]] = per_way.get(d["way_id"], 0.0) + d["length_m"]
    for way_id, total in per_way.items():
        geom = ways[way_id]["geometry"]
        full = LineString([to_utm.transform(g["lon"], g["lat"]) for g in geom]).length
        assert total == pytest.approx(full, abs=0.01), f"osm:way/{way_id}"


def test_region_trailheads_are_main_component_graph_nodes(tg):
    from backcountry.graph import qa_summary

    node_comp = qa_summary(tg)["node_component"]
    assert REGION.trailheads, "regions/dolly_sods.yaml lists no trailheads"
    for th in REGION.trailheads:
        assert th["node"] in tg.graph, th["id"]
        assert node_comp[th["node"]] == "C0", th["id"]


def test_every_edge_has_elevation_and_consistent_net_change(tg):
    from backcountry.elevation import FT_PER_M, annotate

    dem = ingest.load_dem(REGION)
    if dem is None:
        pytest.skip("no cached DEM; run `backcountry dem --region dolly_sods`")
    result = annotate(tg, dem)
    assert result["missing"] == []
    for *_, d in tg.graph.edges(data=True):
        net_ft = (d["profile"]["z_m"][-1] - d["profile"]["z_m"][0]) * FT_PER_M
        assert d["gain_ft_fwd"] - d["loss_ft_fwd"] == pytest.approx(net_ft, abs=1e-6), d["source"]
        assert d["gain_ft_rev"] - d["loss_ft_rev"] == pytest.approx(-net_ft, abs=1e-6), d["source"]


def test_trailhead_to_trailhead_route_uses_only_source_vertices(tg, ways):
    from backcountry.route import resolve_point, shortest_route

    ids = [th["id"] for th in REGION.trailheads]
    a, b = resolve_point(tg, REGION, "fisher_spring_run"), resolve_point(tg, REGION, "red_creek")
    route = shortest_route(tg, a["node"], b["node"])
    assert route.nodes[0] == a["node"] and route.nodes[-1] == b["node"]
    assert route.line_utm.length == pytest.approx(route.length_m, abs=0.01)
    osm_vertices = {(round(g["lon"], 7), round(g["lat"], 7)) for w in ways.values() for g in w["geometry"]}
    for lon, lat in route.line_wgs84.coords:
        assert (round(lon, 7), round(lat, 7)) in osm_vertices
    for e, (u, v) in zip(route.edges, zip(route.nodes, route.nodes[1:])):
        assert tg.graph.has_edge(u, v, e["key"]), e
    assert ids  # the yaml still lists trailheads


@pytest.mark.parametrize("trailhead", ["bear_rocks", "red_creek", "blackbird_knob"])
def test_three_closed_loops_per_trailhead(tg, trailhead):
    """Roadmap M4 'done when': 3 loops each, every one closed, connected, real edges only."""
    from backcountry.loops import find_candidates
    from backcountry.route import resolve_point
    from backcountry.scoring import M_PER_MI

    start = resolve_point(tg, REGION, trailhead)["node"]
    cands = find_candidates(tg, start, 18 * M_PER_MI, 26 * M_PER_MI)
    assert len(cands) == 3, trailhead
    for c in cands:
        r = c.route
        assert r.nodes[0] == r.nodes[-1] == start
        assert 18 * M_PER_MI <= r.length_m <= 26 * M_PER_MI
        for e, (u, v) in zip(r.edges, zip(r.nodes, r.nodes[1:])):
            assert tg.graph.has_edge(u, v, e["key"])
        assert r.line_utm.length == pytest.approx(r.length_m, abs=0.01)
        keys = [e["key"] for e in r.edges]
        assert len(keys) == len(set(keys)) or c.stem_keys, "no edge walked twice unless it's a lollipop stem"
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            assert a.overlap(b) < 0.5
