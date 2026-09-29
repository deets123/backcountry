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
