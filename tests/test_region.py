from pathlib import Path

import pytest
import yaml

from backcountry.region import load_region

REGIONS_DIR = Path(__file__).resolve().parents[1] / "regions"


def test_dolly_sods_region_loads():
    region = load_region("dolly_sods")
    assert region.id == "dolly_sods"
    assert region.crs == "EPSG:32617"
    assert region.bbox.south < region.bbox.north
    assert region.bbox.west < region.bbox.east


@pytest.mark.parametrize("path", sorted(REGIONS_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_region_trailheads_with_coordinates_are_verified(path):
    """CLAUDE.md rule 5: coordinates only on entries the user marked verified."""
    data = yaml.safe_load(path.read_text())
    for th in data.get("trailheads") or []:
        if "lat" in th or "lon" in th:
            assert th.get("verified") is True, f"{path.name}: trailhead {th.get('id')} has coordinates but is not verified"


def test_unverified_trailhead_with_coordinates_is_rejected(tmp_path):
    (tmp_path / "x.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "x",
                "name": "X",
                "crs": "EPSG:32617",
                "bbox": {"south": 0.0, "west": 0.0, "north": 1.0, "east": 1.0},
                "trailheads": [{"id": "a", "name": "A", "lat": 0.5, "lon": 0.5}],
            }
        )
    )
    with pytest.raises(ValueError, match="verified"):
        load_region("x", regions_dir=tmp_path)


def test_inverted_bbox_is_rejected(tmp_path):
    (tmp_path / "x.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "x",
                "name": "X",
                "crs": "EPSG:32617",
                "bbox": {"south": 1.0, "west": 0.0, "north": 0.0, "east": 1.0},
                "trailheads": [],
            }
        )
    )
    with pytest.raises(ValueError, match="bbox"):
        load_region("x", regions_dir=tmp_path)


def test_dolly_sods_has_usfs_crosscheck():
    assert load_region("dolly_sods").usfs_crosscheck is True


def _region_with_trailheads(tmp_path, trailheads):
    (tmp_path / "x.yaml").write_text(yaml.safe_dump({
        "id": "x", "name": "X", "crs": "EPSG:32617",
        "bbox": {"south": 0.0, "west": 0.0, "north": 1.0, "east": 1.0},
        "trailheads": trailheads,
    }))
    return load_region("x", regions_dir=tmp_path)


def test_trailhead_by_graph_node_needs_no_coordinates(tmp_path):
    region = _region_with_trailheads(tmp_path, [{"id": "a", "name": "A", "node": 123, "verified": False}])
    assert region.trailheads[0]["node"] == 123


def test_trailhead_needs_a_node_or_verified_coordinates(tmp_path):
    with pytest.raises(ValueError, match="node"):
        _region_with_trailheads(tmp_path, [{"id": "a", "name": "A"}])


def test_trailhead_ids_are_unique(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        _region_with_trailheads(tmp_path, [{"id": "a", "name": "A", "node": 1}, {"id": "a", "name": "B", "node": 2}])
