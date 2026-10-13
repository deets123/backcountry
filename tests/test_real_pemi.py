"""Second-region invariants (M8): the same checks as Dolly Sods, on the Pemigewasset cache. Skipped without it."""

import pytest

from backcountry import ingest
from backcountry.graph import build_graph, qa_summary
from backcountry.region import load_region

REGION = load_region("pemi")
try:
    RAW = ingest.load_raw(REGION)
except FileNotFoundError:
    RAW = None

pytestmark = pytest.mark.skipif(RAW is None, reason="no cached OSM data; run `backcountry ingest --region pemi`")


@pytest.fixture(scope="module")
def tg():
    return build_graph(RAW, REGION.crs)


def test_region_uses_its_own_crs_and_trailheads_are_main_component_nodes(tg):
    assert REGION.crs == "EPSG:32619"
    node_comp = qa_summary(tg)["node_component"]
    assert REGION.trailheads
    for th in REGION.trailheads:
        assert th["node"] in tg.graph and node_comp[th["node"]] == "C0", th["id"]


def test_pemi_loop_from_lincoln_woods(tg):
    from backcountry.loops import find_candidates
    from backcountry.route import resolve_point
    from backcountry.scoring import M_PER_MI

    start = resolve_point(tg, REGION, "lincoln_woods")["node"]
    cands = find_candidates(tg, start, 24 * M_PER_MI, 32 * M_PER_MI, kinds=("loop",))
    assert cands
    for c in cands:
        r = c.route
        assert r.nodes[0] == r.nodes[-1] == start and 24 * M_PER_MI <= r.length_m <= 32 * M_PER_MI
        for e, (u, v) in zip(r.edges, zip(r.nodes, r.nodes[1:])):
            assert tg.graph.has_edge(u, v, e["key"])
