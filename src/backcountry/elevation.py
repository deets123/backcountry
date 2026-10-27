"""DEM sampling and per-edge gain/loss.

Each edge is sampled every SAMPLE_M meters along its geometry (region CRS).
Elevations are bilinear-interpolated from the DEM at those points, lightly
smoothed, and gain/loss are accumulated with a hysteresis threshold so DEM
noise doesn't inflate totals.

Settings were chosen against two CalTopo references (see PROGRESS.md, M3):
CalTopo samples its profiles every 100 ft, and sampling the same way, with a
3-sample average and a 2 m threshold, reproduces its gain and loss within 4%.
Finer sampling (10 m) over-counted small bumps on a rolling descent by 50%.

"Forward" is the source way's direction: osm_nodes[0] -> osm_nodes[-1].
If any sample has no DEM value, the edge's gain/loss are None (unknown).
Gaps in the DEM are never filled in.
"""

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString

SAMPLE_M = 30.48  # 100 ft, the spacing CalTopo uses for its profiles
SMOOTH_SAMPLES = 3  # centered moving average over ~300 ft
HYSTERESIS_M = 2.0  # ignore ups/downs smaller than ~6.6 ft
DEM_CELL_DEG = 0.05  # elevation is read in grid cells this size (~5 km), only the ones trails pass through
CORRIDOR_M = 150.0  # around each trail: wide enough for the samples' neighbouring pixels
FT_PER_M = 3.28084


@dataclass
class DEM:
    array: np.ndarray  # float elevations in meters, NaN = no data
    transform: object  # affine transform: pixel corner -> CRS coordinates
    crs: str
    source: dict = field(default_factory=dict)

    def sample(self, xs, ys, from_crs: str) -> np.ndarray:
        """Bilinear elevations at points given in from_crs. NaN where the DEM has no value."""
        xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
        if from_crs != self.crs:
            xs, ys = _transformer(from_crs, self.crs).transform(xs, ys)
        inv = ~self.transform
        cols, rows = inv.a * xs + inv.b * ys + inv.c, inv.d * xs + inv.e * ys + inv.f
        c, r = cols - 0.5, rows - 0.5  # relative to pixel centers
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        h, w = self.array.shape
        ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < w) & (r0 + 1 < h)
        out = np.full(c.shape, np.nan)
        a, c0, r0, fc, fr = self.array, c0[ok], r0[ok], fc[ok], fr[ok]
        out[ok] = (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c0 + 1] * fc * (1 - fr)
                   + a[r0 + 1, c0] * (1 - fc) * fr + a[r0 + 1, c0 + 1] * fc * fr)
        return out


@lru_cache(maxsize=8)
def _transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def densify(line: LineString, step: float = SAMPLE_M) -> tuple[list[float], np.ndarray]:
    """Evenly spaced points along the line, never more than `step` apart, both ends included: distances along
    the line, and an (n, 2) array of x, y. Linear interpolation along the vertices, as shapely's `interpolate`
    does, but in one numpy call: a long route has tens of thousands of samples."""
    n = max(1, math.ceil(line.length / step - 1e-9))
    d = [line.length * i / n for i in range(n + 1)]
    xy = np.asarray(line.coords, dtype=float)[:, :2]
    seg = np.hypot(*np.diff(xy, axis=0).T)
    keep = np.concatenate([[True], seg > 0])  # a repeated vertex adds no length and would confuse interp
    xy, cum = xy[keep], np.concatenate([[0.0], np.cumsum(seg[seg > 0])])
    if len(xy) == 1:
        return d, np.repeat(xy, len(d), axis=0)
    at = np.asarray(d) * (cum[-1] / line.length if line.length else 1.0)  # the vertices' own lengths, to rounding
    return d, np.column_stack([np.interp(at, cum, xy[:, 0]), np.interp(at, cum, xy[:, 1])])


def smooth(z, n: int = SMOOTH_SAMPLES) -> np.ndarray:
    """Centered moving average. The window shrinks symmetrically at the ends, so endpoints are unchanged."""
    z = np.asarray(z, dtype=float)
    cs = np.concatenate([[0.0], np.cumsum(z)])
    out = np.empty_like(z)
    for i in range(len(z)):
        k = min(n // 2, i, len(z) - 1 - i)
        out[i] = (cs[i + k + 1] - cs[i - k]) / (2 * k + 1)
    return out


def gain_loss(z, threshold: float = HYSTERESIS_M) -> tuple[float, float]:
    """Total ascent and descent, counting a change only once it exceeds `threshold`.

    The leftover at the end is always counted, so gain - loss == z[-1] - z[0].
    """
    gain = loss = 0.0
    ref = z[0]
    for v in z[1:]:
        diff = v - ref
        if diff >= threshold:
            gain, ref = gain + diff, v
        elif diff <= -threshold:
            loss, ref = loss - diff, v
    tail = z[-1] - ref
    return (gain + tail, loss) if tail > 0 else (gain, loss - tail)


def cumulative_gain_loss(z, threshold: float = HYSTERESIS_M):
    """Running gain and loss (same rule as gain_loss), so a stretch's gain is G[j] - G[i].

    Movement still inside the threshold isn't counted until it passes it, so a stretch can
    be short by up to `threshold`; the final tail is counted, so the totals match gain_loss.
    """
    z = np.asarray(z, dtype=float)
    G, L = np.zeros(len(z)), np.zeros(len(z))
    gain = loss = 0.0
    ref = z[0]
    for i in range(1, len(z)):
        diff = z[i] - ref
        if diff >= threshold:
            gain, ref = gain + diff, z[i]
        elif diff <= -threshold:
            loss, ref = loss - diff, z[i]
        G[i], L[i] = gain, loss
    tail = z[-1] - ref
    if tail > 0:
        G[-1] += tail
    else:
        L[-1] -= tail
    return G, L


def line_profile(line: LineString, dem: DEM, crs: str) -> dict:
    """Sampled profile and gain/loss (meters) for a line in `crs`, in the line's direction."""
    d, pts = densify(line)
    z = dem.sample(pts[:, 0], pts[:, 1], crs)
    result = {"d_m": d, "z_m": [None if math.isnan(v) else float(v) for v in z], "missing": int(np.isnan(z).sum())}
    if result["missing"]:
        return result | {"gain_m": None, "loss_m": None}
    gain, loss = gain_loss(smooth(z))
    return result | {"gain_m": gain, "loss_m": loss}


def graph_bounds_ll(tg, pad_deg: float = 0.005) -> tuple[float, float, float, float]:
    """(west, south, east, north) covering every edge, padded. Ways can extend past the region bbox."""
    bounds = [d["geom_wgs84"].bounds for *_, d in tg.graph.edges(data=True)]
    return (min(b[0] for b in bounds) - pad_deg, min(b[1] for b in bounds) - pad_deg,
            max(b[2] for b in bounds) + pad_deg, max(b[3] for b in bounds) + pad_deg)


def corridor_cells(tg, cell_deg: float = DEM_CELL_DEG, buffer_m: float = CORRIDOR_M,
                   within=None) -> list[tuple[float, float, float, float]]:
    """The grid cells (west, south, east, north; edges at multiples of cell_deg) that any edge, widened by
    buffer_m, touches. Elevation is read only for these, so the empty parts of a big park cost nothing.
    `within` (a box: west, south, east, north; or a lon/lat shape): only cells touching it (a cut's long roads
    run far outside an area)."""
    from shapely.geometry import box

    limit = None if within is None else box(*within) if isinstance(within, tuple) else within
    found = set()
    for *_, d in tg.graph.edges(data=True):
        line = d["geom_wgs84"]
        lat = (line.bounds[1] + line.bounds[3]) / 2
        widened = line.buffer(buffer_m / (111_320.0 * max(0.1, math.cos(math.radians(lat)))))  # degrees, generous
        w, s, e, n = widened.bounds
        for i in range(math.floor(w / cell_deg), math.floor(e / cell_deg) + 1):
            for j in range(math.floor(s / cell_deg), math.floor(n / cell_deg) + 1):
                cell = box(i * cell_deg, j * cell_deg, (i + 1) * cell_deg, (j + 1) * cell_deg)
                if widened.intersects(cell) and (limit is None or limit.intersects(cell)):
                    found.add((i, j))
    return [(round(i * cell_deg, 9), round(j * cell_deg, 9), round((i + 1) * cell_deg, 9), round((j + 1) * cell_deg, 9))
            for i, j in sorted(found)]


def annotate(tg, dem: DEM) -> dict:
    """Add gain/loss (ft, both directions), elevation range (ft), and a profile to every edge."""
    missing = []
    for u, v, k, d in tg.graph.edges(keys=True, data=True):
        p = line_profile(d["geom_utm"], dem, tg.crs)
        d["profile"] = {"d_m": p["d_m"], "z_m": p["z_m"]}
        if p["missing"]:
            missing.append({"key": k, "source": d["source"], "name": d["name"], "samples_missing": p["missing"],
                            "samples": len(p["d_m"])})
            d.update(gain_ft_fwd=None, loss_ft_fwd=None, gain_ft_rev=None, loss_ft_rev=None,
                     elev_min_ft=None, elev_max_ft=None)
            continue
        zs = smooth(np.array(p["z_m"]))
        gain_rev, loss_rev = gain_loss(zs[::-1])
        d.update(
            gain_ft_fwd=p["gain_m"] * FT_PER_M,
            loss_ft_fwd=p["loss_m"] * FT_PER_M,
            gain_ft_rev=gain_rev * FT_PER_M,
            loss_ft_rev=loss_rev * FT_PER_M,
            elev_min_ft=min(p["z_m"]) * FT_PER_M,
            elev_max_ft=max(p["z_m"]) * FT_PER_M,
        )
    tg.graph.graph["elevation_source"] = dem.source
    return {"missing": missing, "source": dem.source}
