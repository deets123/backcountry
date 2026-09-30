"""M2 acceptance: our elevation numbers on each tests/reference/*.yaml track.

Compared against CalTopo (numbers copied into the YAML by hand) and against
USGS 1 m lidar. Each check skips until its data exists.
"""

from pathlib import Path

import pytest
import yaml
from pyproj import Transformer
from shapely.ops import transform

from backcountry import ingest
from backcountry.elevation import FT_PER_M, line_profile
from backcountry.export import M_PER_MI, read_gpx_line
from backcountry.region import load_region

REF_DIR = Path(__file__).parent / "reference"
REFS = sorted(REF_DIR.glob("*.yaml"))
GAIN_TOL = 0.15  # roadmap M2: within ~15%
DIST_TOL = 0.05


def _profile(ref: dict, dem):
    region = load_region(ref["region"])
    to_utm = Transformer.from_crs("EPSG:4326", region.crs, always_xy=True).transform
    line = transform(to_utm, read_gpx_line(REF_DIR / ref["gpx"]))
    return line.length, line_profile(line, dem, region.crs)


def _dem_or_skip(ref, **kw):
    dem = ingest.load_dem(load_region(ref["region"]), **kw)
    if dem is None:
        pytest.skip(f"no cached DEM {kw or ''}")
    return dem


@pytest.mark.parametrize("path", REFS, ids=lambda p: p.stem)
def test_matches_caltopo(path):
    ref = yaml.safe_load(path.read_text())
    ct = ref["caltopo"]
    if any(ct.get(k) is None for k in ("distance_mi", "gain_ft", "loss_ft")):
        pytest.skip(f"{path.name}: CalTopo numbers not filled in yet")
    length_m, p = _profile(ref, _dem_or_skip(ref))
    assert length_m / M_PER_MI == pytest.approx(ct["distance_mi"], rel=DIST_TOL)
    assert p["gain_m"] * FT_PER_M == pytest.approx(ct["gain_ft"], rel=GAIN_TOL)
    assert p["loss_m"] * FT_PER_M == pytest.approx(ct["loss_ft"], rel=GAIN_TOL)


@pytest.mark.parametrize("path", REFS, ids=lambda p: p.stem)
def test_matches_lidar(path):
    ref = yaml.safe_load(path.read_text())
    _, ours = _profile(ref, _dem_or_skip(ref))
    _, lidar = _profile(ref, _dem_or_skip(ref, kind="1m", name=path.stem))
    assert ours["gain_m"] == pytest.approx(lidar["gain_m"], rel=GAIN_TOL)
    assert ours["loss_m"] == pytest.approx(lidar["loss_m"], rel=GAIN_TOL)
