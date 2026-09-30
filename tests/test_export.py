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


def test_map_never_requests_osm_tiles(outputs):
    """OSM's volunteer tile servers block pages opened from disk; CLAUDE.md rule 6."""
    _, _, paths = outputs
    html = paths["map"].read_text()
    assert "tile.openstreetmap.org" not in html
    assert "basemap.nationalmap.gov" in html
    assert "OpenStreetMap contributors" in html  # data attribution stays


@pytest.fixture
def elevated(network, tmp_path):
    from backcountry.elevation import annotate
    from conftest import plane_dem

    raw = network.doc()
    tg = build_graph(raw, crs=CRS)
    elevation = annotate(tg, plane_dem(0.1, 0.0, 1000))
    summary = qa_summary(tg)
    paths = write_qa_outputs(summary, tg, raw, REGION, tmp_path / "qa", coverage=[], elevation=elevation)
    return tg, summary, paths, tmp_path


def test_report_has_elevation_section_in_feet(elevated):
    _, _, paths, _ = elevated
    md = paths["report"].read_text()
    assert "## Elevation" in md and "synthetic plane" in md
    assert " m " not in md


def test_map_tooltips_show_gain_when_known(elevated):
    _, _, paths, _ = elevated
    assert "gain/loss along way" in paths["map"].read_text()


def test_geojson_carries_gain_and_profiles_written(elevated):
    tg, summary, _, tmp_path = elevated
    paths = write_graph_geojson(tg, summary["node_component"], tmp_path / "processed")
    fc = json.loads(paths["edges"].read_text())
    assert all("gain_ft_fwd" in f["properties"] and "profile" not in f["properties"] for f in fc["features"])
    profiles = json.loads(paths["profiles"].read_text())
    assert len(profiles["edges"]) == tg.graph.number_of_edges()
    first = next(iter(profiles["edges"].values()))
    assert {"from_node", "d_m", "z_m"} <= set(first)


def test_trail_gpx_round_trips_exact_osm_points_without_elevation(network, tmp_path):
    import gpxpy

    from backcountry.export import read_gpx_line, write_trail_gpx
    from backcountry.graph import chain_named_trail

    tg = build_graph(network.doc(), crs=CRS)
    chain = chain_named_trail(tg, "Main Trail")
    path = write_trail_gpx(chain, tmp_path / "main_trail.gpx", retrieved_at="2026-01-01T00:00:00Z")
    gpx = gpxpy.parse(path.read_text())
    assert len(gpx.tracks) == 1 and len(gpx.tracks[0].segments) == 1
    assert all(p.elevation is None for p in gpx.tracks[0].segments[0].points)
    assert "OpenStreetMap contributors" in gpx.description and "osm:way/1" in gpx.description
    assert list(read_gpx_line(path).coords) == pytest.approx(chain["coords_wgs84"])
