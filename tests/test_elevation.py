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
