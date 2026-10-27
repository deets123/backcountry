import math

import numpy as np
import pytest
from shapely.geometry import LineString

from backcountry.elevation import FT_PER_M, annotate, densify, gain_loss, smooth
from backcountry.graph import build_graph
from conftest import CRS, X0, Y0, plane_dem


# --- densify -------------------------------------------------------------------


def test_densify_even_spacing_includes_endpoints():
    d, pts = densify(LineString([(0, 0), (100, 0)]), 10)
    assert d == pytest.approx([i * 10 for i in range(11)])
    assert (pts[0].x, pts[-1].x) == (0, 100)


def test_densify_never_exceeds_step():
    d, _ = densify(LineString([(0, 0), (95, 0)]), 10)
    assert len(d) == 11 and max(np.diff(d)) <= 10 and d[-1] == pytest.approx(95)


def test_densify_short_line_keeps_both_ends():
    d, _ = densify(LineString([(0, 0), (3, 0)]), 10)
    assert d == pytest.approx([0, 3])


# --- DEM sampling --------------------------------------------------------------


def test_bilinear_sampling_is_exact_on_a_plane():
    dem = plane_dem(0.05, -0.02, 900)
    xs, ys = np.array([X0 + 123.4, X0 + 4567.8]), np.array([Y0 + 55.5, Y0 + 1999.1])
    z = dem.sample(xs, ys, CRS)
    assert z == pytest.approx([0.05 * 123.4 - 0.02 * 55.5 + 900, 0.05 * 4567.8 - 0.02 * 1999.1 + 900])


def test_sampling_reprojects_points_into_dem_crs():
    # Same plane, but the DEM grid is laid out in geographic coordinates (like USGS 1/3").
    from pyproj import Transformer
    from rasterio.transform import from_origin

    from backcountry.elevation import DEM

    to_ll = Transformer.from_crs(CRS, "EPSG:4269", always_xy=True)
    lon0, lat0 = to_ll.transform(X0 - 500, Y0 + 500)
    cell = 1 / 10800
    cols = rows = 400
    lons = lon0 + cell * (np.arange(cols) + 0.5)
    lats = lat0 - cell * (np.arange(rows) + 0.5)
    z = 1000 * lons[None, :] + 2000 * lats[:, None]
    dem = DEM(array=z, transform=from_origin(lon0, lat0, cell, cell), crs="EPSG:4269", source={})
    x, y = X0 + 100.0, Y0 + 50.0
    lon, lat = to_ll.transform(x, y)
    assert dem.sample(np.array([x]), np.array([y]), CRS)[0] == pytest.approx(1000 * lon + 2000 * lat, abs=1e-6)


def test_nodata_and_outside_points_are_nan_not_guessed():
    dem = plane_dem(0, 0, 500)
    dem.array[100, 100] = np.nan
    x_nan = X0 - 1500 + 10 * 100 + 5
    y_nan = Y0 + 2500 - 10 * 100 - 5
    z = dem.sample(np.array([x_nan, X0 + 99_999]), np.array([y_nan, Y0]), CRS)
    assert np.isnan(z).all()


# --- smoothing and gain/loss ----------------------------------------------------


def test_smooth_keeps_constants_and_lines():
    assert smooth(np.full(9, 7.0), 5) == pytest.approx(np.full(9, 7.0))
    ramp = np.arange(12, dtype=float) * 3
    assert smooth(ramp, 5) == pytest.approx(ramp)


def test_smooth_damps_a_spike():
    z = np.zeros(9)
    z[4] = 10
    assert smooth(z, 5).max() == pytest.approx(2.0)


def test_gain_loss_on_a_climb():
    assert gain_loss(np.linspace(0, 100, 21), 1.0) == pytest.approx((100, 0))


def test_noise_below_threshold_adds_nothing():
    z = 500 + 0.4 * np.sin(np.arange(200))
    gain, loss = gain_loss(z, 1.0)
    assert gain + loss <= 0.8 + 1e-9  # only the net end difference can remain


def test_real_bumps_above_threshold_count():
    z = np.array([0, 5, 0, 5, 0], dtype=float)
    assert gain_loss(z, 1.0) == pytest.approx((10, 10))


@pytest.mark.parametrize("seed", range(5))
def test_gain_minus_loss_equals_net_change(seed):
    z = np.cumsum(np.random.default_rng(seed).normal(0, 2, 300))
    gain, loss = gain_loss(z, 1.5)
    assert gain - loss == pytest.approx(z[-1] - z[0])


# --- graph annotation ----------------------------------------------------------


@pytest.fixture
def annotated(network):
    network.node("Z", 9000, 0)  # far outside the synthetic DEM
    network.way(20, ["H", "Z"], highway="path", name="Off Map")
    tg = build_graph(network.doc(), crs=CRS)
    result = annotate(tg, plane_dem(0.1, 0.0, 1000))  # climbs 0.1 m per m eastward
    return network, tg, result


def _edge(tg, way_id, seg=0):
    return next(d for *_, d in tg.graph.edges(data=True) if d["way_id"] == way_id and d["segment"] == seg)


def test_edge_gain_in_both_directions(annotated):
    _, tg, _ = annotated
    bc = _edge(tg, 1, 2)  # Main Trail B -> C, x 1000 -> 2000, forward is the way's direction
    assert bc["gain_ft_fwd"] == pytest.approx(100 * FT_PER_M, rel=1e-6)
    assert bc["loss_ft_fwd"] == pytest.approx(0, abs=1e-6)
    assert bc["gain_ft_rev"] == pytest.approx(0, abs=1e-6)
    assert bc["loss_ft_rev"] == pytest.approx(100 * FT_PER_M, rel=1e-6)


def test_every_edge_net_change_matches_endpoint_elevations(annotated):
    network, tg, _ = annotated
    for *_, d in tg.graph.edges(data=True):
        if d["gain_ft_fwd"] is None:
            continue
        x0 = network.xy[[k for k, v in network.ids.items() if v == d["osm_nodes"][0]][0]][0]
        x1 = network.xy[[k for k, v in network.ids.items() if v == d["osm_nodes"][-1]][0]][0]
        assert d["gain_ft_fwd"] - d["loss_ft_fwd"] == pytest.approx(0.1 * (x1 - x0) * FT_PER_M, abs=1e-6)
        assert d["gain_ft_rev"] - d["loss_ft_rev"] == pytest.approx(-0.1 * (x1 - x0) * FT_PER_M, abs=1e-6)


def test_profile_stored_from_start_to_end(annotated):
    _, tg, _ = annotated
    d = _edge(tg, 1, 2)
    assert d["profile"]["d_m"][0] == 0 and d["profile"]["d_m"][-1] == pytest.approx(d["length_m"])
    assert len(d["profile"]["d_m"]) == len(d["profile"]["z_m"])
    assert d["profile"]["z_m"][0] == pytest.approx(1000 + 0.1 * 1000)


def test_edge_off_the_dem_is_unknown_and_reported(annotated):
    _, tg, result = annotated
    off = _edge(tg, 20)
    assert off["gain_ft_fwd"] is None and off["loss_ft_rev"] is None
    assert [m["source"] for m in result["missing"]] == ["osm:way/20"]


def test_graph_bounds_cover_every_edge(network):
    from backcountry.elevation import graph_bounds_ll

    tg = build_graph(network.doc(), crs=CRS)
    w, s, e, n = graph_bounds_ll(tg, pad_deg=0.0)
    for *_, d in tg.graph.edges(data=True):
        bw, bs, be, bn = d["geom_wgs84"].bounds
        assert w <= bw and s <= bs and e >= be and n >= bn


def test_cumulative_gain_matches_total_and_never_decreases():
    from backcountry.elevation import cumulative_gain_loss

    z = np.cumsum(np.random.default_rng(1).normal(0, 2, 400))
    G, L = cumulative_gain_loss(z, 1.5)
    assert (G[-1], L[-1]) == pytest.approx(gain_loss(z, 1.5))
    assert np.all(np.diff(G) >= 0) and np.all(np.diff(L) >= 0)
    assert G[0] == 0 and L[0] == 0


# --- elevation only where the trails are (speed) -----------------------------------------------

LL_RES, LL_W, LL_N = 0.0005, -81.3, 36.4  # a made-up lon/lat elevation grid around the fixture origin
LL_COLS, LL_ROWS = 1200, 800


def _ll_plane():
    """z = 1000 + 2000 * (lon + 81) + 3000 * (lat - 36) on a lon/lat grid (bilinear of a plane is exact)."""
    import numpy as np
    from rasterio.transform import from_origin

    lons = LL_W + LL_RES * (np.arange(LL_COLS) + 0.5)
    lats = LL_N - LL_RES * (np.arange(LL_ROWS) + 0.5)
    z = 1000 + 2000 * (lons[None, :] + 81) + 3000 * (lats[:, None] - 36)
    return z.astype("float32"), from_origin(LL_W, LL_N, LL_RES, LL_RES)


def _window_reader(calls):
    """Reads the window of the made-up grid covering bounds, on the grid's own pixels (like a merge would)."""
    import threading

    from rasterio.transform import from_origin

    full, _ = _ll_plane()

    def reader(urls, bounds):
        w, s, e, n = bounds
        c0, c1 = round((w - LL_W) / LL_RES), round((e - LL_W) / LL_RES)
        r0, r1 = round((LL_N - n) / LL_RES), round((LL_N - s) / LL_RES)
        calls.append((bounds, threading.get_ident()))
        return full[r0:r1, c0:c1].copy(), from_origin(LL_W + c0 * LL_RES, LL_N - r0 * LL_RES, LL_RES, LL_RES), "EPSG:4326"
    return reader


def _corridor_graph():
    from conftest import RawBuilder

    b = RawBuilder()
    b.node("a", 0, 0), b.node("b", 0, 3000)          # along lon -81.0 exactly: a cell border
    b.node("c", 2000, 1000), b.node("d", 8000, 1000)  # east-west across a cell border
    b.node("f", 25000, 20000), b.node("g", 26000, 20000)  # far away: its own cells
    b.way(1, ["a", "b"], highway="path", name="Border Trail")
    b.way(2, ["c", "d"], highway="path", name="Crossing Trail")
    b.way(3, ["f", "g"], highway="path", name="Far Trail")
    return build_graph(b.doc(), crs=CRS)


def test_corridor_cells_cover_only_trail_cells():
    from shapely.geometry import box

    from backcountry.elevation import DEM_CELL_DEG, corridor_cells

    tg = _corridor_graph()
    cells = corridor_cells(tg)
    assert cells == sorted(cells) and len(cells) == len(set(cells))
    for w, s, e, n in cells:
        assert e - w == pytest.approx(DEM_CELL_DEG) and n - s == pytest.approx(DEM_CELL_DEG)
    boxes = [box(*c) for c in cells]
    for *_, d in tg.graph.edges(data=True):
        for pt in d["geom_wgs84"].coords:
            assert any(bx.covers(__import__("shapely").geometry.Point(pt)) for bx in boxes)
    # nothing between the near trails and the far one (which is ~0.2 degrees away)
    gap = box(-80.85, 36.1, -80.75, 36.2)
    assert not any(bx.intersects(gap) for bx in boxes)


def test_corridor_samples_match_a_full_read_along_trails(tmp_path):
    from backcountry import ingest
    from backcountry.elevation import DEM, annotate, corridor_cells
    from backcountry.region import BBox, Region

    region = Region(id="corridor", name="Corridor", crs=CRS, bbox=BBox(36.0, -81.3, 36.4, -80.7))
    full, transform = _ll_plane()
    tg_full, tg_cells = _corridor_graph(), _corridor_graph()
    want = annotate(tg_full, DEM(array=full.astype("float64"), transform=transform, crs="EPSG:4326"))
    assert not want["missing"]
    calls = []
    ingest.fetch_dem(region, (-81.05, 36.0, -80.65, 36.35), raw_dir=tmp_path, cells=corridor_cells(tg_cells),
                     searcher=lambda dataset, bounds: [
                         {"title": f"USGS 1/3 Arc Second {t} 20240101", "downloadURL": f"u/{t}.tif",
                          "publicationDate": "2024-01-01", "sourceId": t} for t in ("n37w082", "n37w081")],
                     reader=_window_reader(calls))
    got = annotate(tg_cells, ingest.load_dem(region, raw_dir=tmp_path))
    assert len(calls) == len(corridor_cells(tg_cells))
    assert not got["missing"]
    for (u, v, k, a), (_, _, _, b) in zip(sorted(tg_full.graph.edges(keys=True, data=True)),
                                          sorted(tg_cells.graph.edges(keys=True, data=True))):
        assert b["gain_ft_fwd"] == pytest.approx(a["gain_ft_fwd"], abs=0.1)
        assert b["loss_ft_fwd"] == pytest.approx(a["loss_ft_fwd"], abs=0.1)


def test_corridor_cells_stay_near_the_area():
    from shapely.geometry import box

    from backcountry.elevation import corridor_cells

    tg = _corridor_graph()
    near = (-81.01, 36.1, -80.89, 36.2)  # around the two near trails; the far one is outside
    cells = corridor_cells(tg, within=near)
    assert cells and all(box(*c).intersects(box(*near)) for c in cells)
    assert len(cells) < len(corridor_cells(tg))
