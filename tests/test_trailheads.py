import pytest

from backcountry.graph import build_graph, qa_summary
from backcountry.trailheads import find_candidates
from conftest import CRS


@pytest.fixture
def result(network):
    network.poi(9002, 9000, 9000, highway="trailhead", name="Far TH")
    raw = network.doc()
    tg = build_graph(raw, crs=CRS)
    return network, tg, find_candidates(tg, raw, qa_summary(tg)["node_component"])


def _by_id(cands):
    return {c["id"]: c for c in cands}


def test_every_candidate_is_a_graph_node(result):
    _, tg, res = result
    assert res["candidates"]
    assert all(c["id"] in tg.graph for c in res["candidates"])


def test_osm_trailhead_snaps_to_nearest_graph_node(result):
    network, _, res = result
    d = _by_id(res["candidates"])[network.ids["D"]]
    assert "osm_trailhead" in d["kinds"]
    assert "Test TH" in d["names"]
    assert "osm:node/9001" in d["sources"]
    assert d["snap_m"] == pytest.approx(20, abs=0.01)
    assert d["component"] == "C0"


def test_trailhead_too_far_from_network_is_reported_not_snapped(result):
    _, _, res = result
    assert [u["source"] for u in res["unsnapped"]] == ["osm:node/9002"]
    assert "osm:node/9002" not in {s for c in res["candidates"] for s in c["sources"]}


def test_parking_near_trail_node(result):
    network, _, res = result
    f = _by_id(res["candidates"])[network.ids["F"]]
    assert "parking_nearby" in f["kinds"]
    assert "osm:way/12" in f["sources"]
    assert "Test Lot" in f["names"]


def test_trail_road_junctions(result):
    network, _, res = result
    junctions = {c["id"] for c in res["candidates"] if "trail_road_junction" in c["kinds"]}
    # C, E: trail meets routable road; m1, F: trail meets a non-routable (tertiary) road.
    assert junctions == {network.ids[n] for n in ("C", "E", "m1", "F")}
