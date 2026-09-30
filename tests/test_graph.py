import math

import pytest

from backcountry.graph import build_graph, qa_summary
from conftest import CRS, RETRIEVED_AT


@pytest.fixture
def built(network):
    return network, build_graph(network.doc(), crs=CRS)


def _way(network, way_id):
    return next(e for e in network.elements if e["type"] == "way" and e["id"] == way_id)


def _edges_of_way(G, way_id):
    return [d for _, _, d in G.edges(data=True) if d["way_id"] == way_id]


def _ids(network, *names):
    return {network.ids[n] for n in names}


# --- provenance / "never invent geometry" invariants -------------------------


def test_every_edge_carries_source_and_retrieved_at(built):
    network, tg = built
    included_way_ids = {1, 2, 3, 5, 7, 8, 9, 10}
    for _, _, d in tg.graph.edges(data=True):
        assert d["source"] == f"osm:way/{d['way_id']}"
        assert d["retrieved_at"] == RETRIEVED_AT
        assert d["way_id"] in included_way_ids


def test_edge_geometry_is_contiguous_slice_of_source_way(built):
    network, tg = built
    for u, v, d in tg.graph.edges(data=True):
        way_nodes = _way(network, d["way_id"])["nodes"]
        seg = d["osm_nodes"]
        starts = [i for i in range(len(way_nodes)) if way_nodes[i : i + len(seg)] == seg]
        assert starts, f"edge nodes {seg} not a contiguous slice of way {d['way_id']}"
        assert {u, v} == {seg[0], seg[-1]}
        assert list(d["geom_wgs84"].coords) == pytest.approx([network.coords[n] for n in seg])


def test_edge_lengths_sum_to_source_way_lengths(built):
    network, tg = built
    for way_id, expected_m in {1: 2000, 2: 800, 3: 1000, 5: 500, 7: 300, 8: 10, 9: 485}.items():
        total = sum(d["length_m"] for d in _edges_of_way(tg.graph, way_id))
        assert total == pytest.approx(expected_m, abs=0.01), way_id
    loop = 1000 + 2 * math.hypot(500, 500)
    assert sum(d["length_m"] for d in _edges_of_way(tg.graph, 10)) == pytest.approx(loop, abs=0.01)


# --- topology ---------------------------------------------------------------


def test_graph_nodes_are_junctions_endpoints_and_road_crossings(built):
    network, tg = built
    assert set(tg.graph.nodes) == _ids(network, "A", "m1", "B", "C", "D", "E", "F", "S", "G", "H", "I", "J")
    assert tg.graph.number_of_edges() == 10


def test_way_is_split_at_junction_and_at_excluded_road_crossing(built):
    network, tg = built
    ends = {frozenset((d["osm_nodes"][0], d["osm_nodes"][-1])) for d in _edges_of_way(tg.graph, 1)}
    A, m1, B, C = (network.ids[n] for n in ("A", "m1", "B", "C"))
    assert ends == {frozenset((A, m1)), frozenset((m1, B)), frozenset((B, C))}
    assert tg.graph.nodes[m1]["other_highway_ways"] == [11]


def test_closed_way_becomes_self_loop(built):
    network, tg = built
    A = network.ids["A"]
    assert tg.graph.number_of_edges(A, A) == 1
    assert tg.graph.degree(A) == 3


def test_is_road_flag(built):
    _, tg = built
    for _, _, d in tg.graph.edges(data=True):
        assert d["is_road"] == (d["highway"] in ("unclassified", "service"))
    assert any(d["is_road"] for _, _, d in tg.graph.edges(data=True))


# --- access handling ---------------------------------------------------------


def test_access_restricted_way_is_excluded_with_reason(built):
    _, tg = built
    assert not _edges_of_way(tg.graph, 4)
    assert [(x["way_id"], x["reason"]) for x in tg.excluded] == [(4, "access=private")]
    assert tg.excluded[0]["source"] == "osm:way/4"


def test_foot_override_is_included_and_logged(built):
    _, tg = built
    assert _edges_of_way(tg.graph, 5)
    assert [x["way_id"] for x in tg.access_overrides] == [5]


def test_other_highways_recorded_but_not_routable(built):
    _, tg = built
    assert set(tg.other_highways) == {6, 11}
    assert not _edges_of_way(tg.graph, 6)


# --- QA summary --------------------------------------------------------------


@pytest.fixture
def qa(built):
    network, tg = built
    return network, qa_summary(tg)


def test_components_sorted_by_length_main_first(qa):
    network, s = qa
    comps = s["components"]
    assert len(comps) == 3
    assert set(comps[0]["nodes"]) == _ids(network, "A", "m1", "B", "C", "D", "E", "F", "S")
    assert comps[0]["id"] == "C0"
    assert comps[0]["gap_to_main_m"] is None
    lengths = [c["length_m"] for c in comps]
    assert lengths == sorted(lengths, reverse=True)


def test_gap_to_main_reported_for_other_components(qa):
    network, s = qa
    near = next(c for c in s["components"] if network.ids["I"] in c["nodes"])
    assert near["gap_to_main_m"] == pytest.approx(15, abs=0.01)


def test_dead_ends(qa):
    network, s = qa
    assert {d["node"] for d in s["dead_ends"]} == _ids(network, "D", "F", "S", "G", "H", "I", "J")


def test_dead_end_notes_explain_likely_cause(qa):
    network, s = qa
    notes = {d["node"]: d["note"] for d in s["dead_ends"]}
    assert "State Rd" in notes[network.ids["F"]] and "residential" in notes[network.ids["F"]]
    assert "access=private" in notes[network.ids["D"]] and "osm:way/4" in notes[network.ids["D"]]
    assert "49 ft" in notes[network.ids["I"]] and "osm:way/1" in notes[network.ids["I"]]  # 15 m
    assert notes[network.ids["J"]] == ""


def test_short_edges(qa):
    network, s = qa
    assert [(e["way_id"], round(e["length_m"])) for e in s["short_edges"]] == [(8, 10)]


def test_mileage_totals(qa):
    _, s = qa
    total_m = 2000 + 800 + 1000 + 500 + 300 + 10 + 485 + 1000 + 2 * math.hypot(500, 500)
    assert s["totals"]["length_m"] == pytest.approx(total_m, abs=0.1)
    assert s["totals"]["road_m"] == pytest.approx(1000, abs=0.01)


def test_near_miss_ignores_edges_already_reachable_nearby(qa):
    network, s = qa
    notes = {d["node"]: d["note"] for d in s["dead_ends"]}
    # S is 10 m from the FR 1 road edge, but it's connected to it through C.
    assert notes[network.ids["S"]] == ""


def test_named_trails_listed_by_component(qa):
    _, s = qa
    assert set(s["named_trails"]["Main Trail"]) == {"C0"}
    assert set(s["named_trails"]["Near Miss Trail"]) != {"C0"}


def test_non_routable_highways_touching_network_are_listed(qa):
    _, s = qa
    touching = {t["way_id"]: t for t in s["other_highways_touching"]}
    assert set(touching) == {6, 11}
    assert touching[11]["components"] == ["C0"]
    assert touching[11]["highway"] == "residential"


def test_trail_only_breakdown_of_main_component(qa):
    network, s = qa
    pieces = s["main_trail_only_pieces"]
    # Dropping road FR 1 (C-E) splits C0 into the Main Trail side and the Override Trail.
    assert [set(p["nodes"]) for p in pieces] == [
        {network.ids[n] for n in ("A", "m1", "B", "C", "D", "S")},
        {network.ids[n] for n in ("E", "F")},
    ]
    assert pieces[0]["length_m"] == pytest.approx(2000 + 800 + 10 + 1000 + 2 * math.hypot(500, 500), abs=0.01)


def test_track_is_road_and_tertiary_is_last_resort_road(network):
    network.node("Q", 5000, 800)
    network.node("R", 5000, 1300)
    network.way(14, ["H", "Q"], highway="track", name="Old Grade")
    network.way(15, ["Q", "R"], highway="tertiary", name="County Rd")
    tg = build_graph(network.doc(), crs=CRS)
    track = _edges_of_way(tg.graph, 14)[0]
    tertiary = _edges_of_way(tg.graph, 15)[0]
    assert track["is_road"] and not track["last_resort"]
    assert tertiary["is_road"] and tertiary["last_resort"]
    assert not _edges_of_way(tg.graph, 1)[0]["last_resort"]


# --- chaining a named trail into one line ------------------------------------------


def test_chain_named_trail_follows_source_vertices_in_order(network):
    from backcountry.graph import chain_named_trail

    tg = build_graph(network.doc(), crs=CRS)
    chain = chain_named_trail(tg, "Main Trail")
    expected = [network.coords[network.ids[n]] for n in ("A", "m1", "B", "m2", "C")]
    assert chain["coords_wgs84"] == pytest.approx(expected)
    assert chain["length_m"] == pytest.approx(2000, abs=0.01)
    assert chain["line_utm"].length == pytest.approx(2000, abs=0.01)
    assert chain["sources"] == ["osm:way/1"]
    assert chain["nodes"] == [network.ids[n] for n in ("A", "m1", "B", "C")]


def test_chain_refuses_a_branching_trail(network):
    from backcountry.graph import chain_named_trail

    network.node("X", 1000, -300)
    network.way(30, ["B", "X"], highway="path", name="Main Trail")
    tg = build_graph(network.doc(), crs=CRS)
    with pytest.raises(ValueError, match="branch"):
        chain_named_trail(tg, "Main Trail")


def test_chain_unknown_name(network):
    from backcountry.graph import chain_named_trail

    with pytest.raises(ValueError, match="No trail edges"):
        chain_named_trail(build_graph(network.doc(), crs=CRS), "Nope Trail")
