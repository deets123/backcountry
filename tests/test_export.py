import json

import pytest

from backcountry.export import write_graph_geojson, write_qa_outputs
from backcountry.graph import build_graph, qa_summary
from backcountry.region import BBox, Region
from conftest import CRS

REGION = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))


@pytest.fixture
def outputs(network, tmp_path):
    raw = network.doc()
    tg = build_graph(raw, crs=CRS)
    summary = qa_summary(tg)
    paths = write_qa_outputs(summary, tg, raw, REGION, tmp_path / "qa", coverage=[])
    return tg, summary, paths


def test_qa_outputs_written(outputs):
    _, _, paths = outputs
    for key in ("report", "json", "map"):
        assert paths[key].exists() and paths[key].stat().st_size > 0


def test_report_has_required_sections_and_attribution(outputs):
    _, _, paths = outputs
    md = paths["report"].read_text()
    for heading in ("Connected components", "Dead ends", "Very short edges", "Possible missing junctions",
                    "Excluded ways", "OpenStreetMap contributors"):
        assert heading in md


def test_report_uses_miles_and_feet_not_meters(outputs):
    _, _, paths = outputs
    md = paths["report"].read_text()
    assert " mi" in md and " ft" in md
    assert " m " not in md and " km" not in md


def test_map_colors_components_and_shows_attribution(outputs):
    _, summary, paths = outputs
    html = paths["map"].read_text()
    assert "OpenStreetMap contributors" in html
    for comp in summary["components"]:
        assert comp["id"] in html


def test_qa_json_is_valid(outputs):
    _, summary, paths = outputs
    data = json.loads(paths["json"].read_text())
    assert data["counts"] == summary["counts"]


def test_edges_geojson_keeps_provenance(network, tmp_path):
    tg = build_graph(network.doc(), crs=CRS)
    summary = qa_summary(tg)
    paths = write_graph_geojson(tg, summary["node_component"], tmp_path)
    fc = json.loads(paths["edges"].read_text())
    assert len(fc["features"]) == tg.graph.number_of_edges()
    for f in fc["features"]:
        assert f["properties"]["source"].startswith("osm:way/")
        assert f["properties"]["retrieved_at"]
