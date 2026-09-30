import pytest

from backcountry.graph import build_graph
from backcountry.region import BBox, Region
from backcountry.route import LAST_RESORT_FACTOR, resolve_point, shortest_route
from conftest import CRS, lonlat

REGION = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9),
                trailheads=[{"id": "a_th", "name": "A Trailhead", "node": 1000, "verified": False}])


@pytest.fixture
def tg(network):
    return build_graph(network.doc(), crs=CRS)


# --- resolving --from / --to ------------------------------------------------------


def test_resolve_trailhead_id_node_id_and_latlon(network, tg):
    assert resolve_point(tg, REGION, "a_th")["node"] == network.ids["A"]
    assert resolve_point(tg, REGION, str(network.ids["B"]))["node"] == network.ids["B"]
    lon, lat = lonlat(1000, 830)  # 30 m from D
    r = resolve_point(tg, REGION, f"{lat},{lon}")
    assert r["node"] == network.ids["D"] and r["snap_m"] == pytest.approx(30, abs=0.01)


def test_resolve_refuses_points_far_from_the_network(tg):
    lon, lat = lonlat(3500, 1500)  # nothing within 200 m
    with pytest.raises(ValueError, match="ft from the nearest"):
        resolve_point(tg, REGION, f"{lat},{lon}")


def test_resolve_rejects_unknown_ids(tg):
    with pytest.raises(ValueError, match="not a trailhead id"):
        resolve_point(tg, REGION, "nowhere")
    with pytest.raises(ValueError, match="not a graph node"):
        resolve_point(tg, REGION, "424242")


# --- shortest path ------------------------------------------------------------------


def test_shortest_route_uses_only_graph_edges_with_exact_vertices(network, tg):
    r = shortest_route(tg, network.ids["A"], network.ids["F"])
    assert r.nodes == [network.ids[n] for n in ("A", "m1", "B", "C", "E", "F")]
    assert r.length_m == pytest.approx(3500, abs=0.01)
    assert r.line_utm.length == pytest.approx(sum(e["length_m"] for e in r.edges), abs=0.01)
    expected = [network.coords[network.ids[n]] for n in ("A", "m1", "B", "m2", "C", "c1", "E", "F")]
    assert list(r.line_wgs84.coords) == pytest.approx(expected)
    assert [e["source"] for e in r.edges] == ["osm:way/1", "osm:way/1", "osm:way/1", "osm:way/3", "osm:way/5"]
    assert r.road_m == pytest.approx(1000, abs=0.01)


def test_reverse_route_reverses_vertices(network, tg):
    fwd = shortest_route(tg, network.ids["A"], network.ids["F"])
    rev = shortest_route(tg, network.ids["F"], network.ids["A"])
    assert list(rev.line_wgs84.coords) == list(fwd.line_wgs84.coords)[::-1]
    assert [e["reversed"] for e in rev.edges] == [not e for e in [x["reversed"] for x in fwd.edges]][::-1]


def test_no_roads_refuses_when_only_a_road_connects(network, tg):
    with pytest.raises(ValueError, match="No route"):
        shortest_route(tg, network.ids["A"], network.ids["F"], no_roads=True)


def test_last_resort_road_only_when_needed(network):
    network.node("W", 1500, 1300)
    network.way(40, ["D", "E"], highway="tertiary", name="County Rd")  # 1118 m shortcut, penalized
    network.way(41, ["D", "W"], highway="tertiary", name="Only Way")  # the only way to W
    tg = build_graph(network.doc(), crs=CRS)
    via_trails = shortest_route(tg, network.ids["D"], network.ids["E"])
    assert via_trails.last_resort_m == 0 and via_trails.length_m == pytest.approx(2800, abs=0.01)
    assert LAST_RESORT_FACTOR * 1118 > 2800  # the test only means something while this holds
    needed = shortest_route(tg, network.ids["D"], network.ids["W"])
    assert needed.last_resort_m == pytest.approx(needed.length_m)


def test_unreachable_component_is_an_error(network, tg):
    with pytest.raises(ValueError, match="No route"):
        shortest_route(tg, network.ids["A"], network.ids["G"])


# --- elevation along the joined line ----------------------------------------------


def test_route_gain_from_joined_profile(network, tg):
    from backcountry.elevation import FT_PER_M
    from backcountry.route import add_elevation
    from conftest import plane_dem

    r = shortest_route(tg, network.ids["A"], network.ids["F"])
    add_elevation(r, plane_dem(0.1, 0.0, 1000), CRS)  # rises 0.1 m per m eastward; A x=0, F x=2500
    assert r.gain_ft == pytest.approx(250 * FT_PER_M, rel=1e-6)
    assert r.loss_ft == pytest.approx(0, abs=1e-6)
    assert r.profile["d_m"][-1] == pytest.approx(r.length_m)


def test_route_gain_unknown_when_dem_missing_data(network, tg):
    from backcountry.route import add_elevation
    from conftest import plane_dem

    r = shortest_route(tg, network.ids["A"], network.ids["F"])
    add_elevation(r, plane_dem(0.1, 0.0, 1000, xmax=1200), CRS)  # DEM ends before C
    assert r.gain_ft is None and r.loss_ft is None


# --- outputs -------------------------------------------------------------------------


def test_write_route_outputs(network, tg, tmp_path):
    import gpxpy

    from backcountry.export import read_gpx_line, write_route
    from backcountry.route import add_elevation
    from conftest import plane_dem

    r = add_elevation(shortest_route(tg, network.ids["A"], network.ids["F"]), plane_dem(0.1, 0.0, 1000), CRS)
    paths = write_route(r, REGION, tmp_path / "r", "trailhead a_th (A Trailhead)", "osm:node/1010", "2026-01-01T00:00:00Z")
    for key in ("gpx", "geojson", "summary"):
        assert paths[key].exists()
    gpx = gpxpy.parse(paths["gpx"].read_text())
    assert [w.name for w in gpx.waypoints] == ["Start", "End"]
    assert list(read_gpx_line(paths["gpx"]).coords) == pytest.approx(list(r.line_wgs84.coords))
    md = paths["summary"].read_text()
    for text in ("2.17 mi", "820 ft", "Main Trail", "FR 1", "Not checked", "OpenStreetMap contributors", "unknown"):
        assert text in md, text
    assert " m " not in md and "safe" not in md.lower()


def test_summary_says_unknown_when_no_elevation(network, tg, tmp_path):
    from backcountry.export import write_route

    r = shortest_route(tg, network.ids["A"], network.ids["F"])
    md = write_route(r, REGION, tmp_path / "r", "x", "y", "2026-01-01T00:00:00Z")["summary"].read_text()
    assert "gain: unknown" in md


def test_road_factor_prefers_trails_when_asked(network):
    network.node("t1b", 2300, 500)
    network.way(42, ["C", "t1b", "E"], highway="path", name="Long Way Round")  # 1166 m of trail vs 1000 m of road
    tg = build_graph(network.doc(), crs=CRS)
    by_distance = shortest_route(tg, network.ids["C"], network.ids["E"])
    assert [e["source"] for e in by_distance.edges] == ["osm:way/3"]
    prefer_trail = shortest_route(tg, network.ids["C"], network.ids["E"], road_factor=2.0)
    assert [e["source"] for e in prefer_trail.edges] == ["osm:way/42"]
    assert prefer_trail.length_m == pytest.approx(2 * (300**2 + 500**2) ** 0.5, abs=0.01)  # real distance, unweighted
