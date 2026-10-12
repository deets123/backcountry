import json

from typer.testing import CliRunner

from backcountry import cli, ingest
from backcountry.cli import app

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("ingest", "qa", "trailheads"):
        assert command in result.output



def _cache(tmp_path, monkeypatch, doc=None):
    monkeypatch.setattr(ingest, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(cli, "PROCESSED_DIR", tmp_path / "processed")
    if doc is not None:
        d = tmp_path / "raw" / "dolly_sods"
        d.mkdir(parents=True)
        (d / "overpass_20260101T000000Z.json").write_text(json.dumps(doc))


def test_qa_command_writes_report_and_map(tmp_path, monkeypatch, network):
    _cache(tmp_path, monkeypatch, network.doc())
    result = runner.invoke(app, ["qa", "--region", "dolly_sods"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "qa" / "dolly_sods" / "qa_map.html").exists()
    assert (tmp_path / "out" / "qa" / "dolly_sods" / "qa_report.md").exists()
    assert (tmp_path / "processed" / "dolly_sods" / "edges.geojson").exists()
    assert "C0" in result.output


def test_trailheads_command_lists_source_candidates(tmp_path, monkeypatch, network):
    _cache(tmp_path, monkeypatch, network.doc())
    result = runner.invoke(app, ["trailheads", "--region", "dolly_sods"])
    assert result.exit_code == 0, result.output
    assert "Test TH" in result.output
    assert str(network.ids["D"]) in result.output


def test_qa_without_cache_says_to_ingest(tmp_path, monkeypatch):
    _cache(tmp_path, monkeypatch)
    result = runner.invoke(app, ["qa", "--region", "dolly_sods"])
    assert result.exit_code != 0
    assert "backcountry ingest" in result.output


def test_verify_command_uses_cached_usfs(tmp_path, monkeypatch, network):
    from conftest import usfs_doc, usfs_line

    _cache(tmp_path, monkeypatch, network.doc())
    (tmp_path / "raw" / "dolly_sods" / "usfs_20260101T000000Z.json").write_text(json.dumps(usfs_doc({
        "trails": [usfs_line([(0, 5), (2000, 5)], trail_no="1", trail_name="MAIN TRAIL")],
    })))
    result = runner.invoke(app, ["verify", "--region", "dolly_sods"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "qa" / "dolly_sods" / "verify_report.md").exists()
    assert (tmp_path / "out" / "qa" / "dolly_sods" / "qa_map.html").exists()


def test_qa_uses_cached_dem(tmp_path, monkeypatch, network):
    from conftest import plane_dem

    _cache(tmp_path, monkeypatch, network.doc())
    dem = plane_dem(0.1, 0.0, 1000)
    region = cli.load_region("dolly_sods")
    ingest.fetch_dem(region, (-180, -90, 180, 90), raw_dir=tmp_path / "raw",
                     searcher=lambda ds, b: [{"title": "USGS 1/3 Arc Second n40w080 20260101", "publicationDate": "2026-01-01",
                                               "downloadURL": "x"}],
                     reader=lambda urls, b: (dem.array.astype("float32"), dem.transform, dem.crs))
    result = runner.invoke(app, ["qa", "--region", "dolly_sods"])
    assert result.exit_code == 0, result.output
    assert "## Elevation" in (tmp_path / "out" / "qa" / "dolly_sods" / "qa_report.md").read_text()
    assert (tmp_path / "processed" / "dolly_sods" / "profiles.json").exists()


def test_reference_command_writes_gpx_and_caltopo_stub(tmp_path, monkeypatch, network):
    import yaml

    _cache(tmp_path, monkeypatch, network.doc())
    out = tmp_path / "reference"
    result = runner.invoke(app, ["reference", "--region", "dolly_sods", "--trail", "Main Trail",
                                 "--out-dir", str(out), "--no-lidar"])
    assert result.exit_code == 0, result.output
    assert (out / "main_trail.gpx").exists()
    stub = yaml.safe_load((out / "main_trail.yaml").read_text())
    assert stub["region"] == "dolly_sods" and stub["gpx"] == "main_trail.gpx"
    assert stub["caltopo"] == {"distance_mi": None, "gain_ft": None, "loss_ft": None}
    assert "1.24 mi" in result.output  # 2000 m

    # Re-running must not wipe numbers already copied from CalTopo.
    stub["caltopo"]["gain_ft"] = 123
    (out / "main_trail.yaml").write_text(yaml.safe_dump(stub))
    runner.invoke(app, ["reference", "--region", "dolly_sods", "--trail", "Main Trail", "--out-dir", str(out), "--no-lidar"])
    assert yaml.safe_load((out / "main_trail.yaml").read_text())["caltopo"]["gain_ft"] == 123


def test_verify_keeps_elevation_in_rewritten_qa_outputs(tmp_path, monkeypatch, network):
    from conftest import plane_dem, usfs_doc, usfs_line

    _cache(tmp_path, monkeypatch, network.doc())
    dem = plane_dem(0.1, 0.0, 1000)
    region = cli.load_region("dolly_sods")
    ingest.fetch_dem(region, (-180, -90, 180, 90), raw_dir=tmp_path / "raw",
                     searcher=lambda ds, b: [{"title": "USGS 1/3 Arc Second n40w080 20260101", "publicationDate": "2026-01-01",
                                               "downloadURL": "x"}],
                     reader=lambda urls, b: (dem.array.astype("float32"), dem.transform, dem.crs))
    (tmp_path / "raw" / "dolly_sods" / "usfs_20260101T000000Z.json").write_text(json.dumps(usfs_doc({
        "trails": [usfs_line([(0, 5), (2000, 5)], trail_no="1", trail_name="MAIN TRAIL")],
    })))
    result = runner.invoke(app, ["verify", "--region", "dolly_sods"])
    assert result.exit_code == 0, result.output
    report = (tmp_path / "out" / "qa" / "dolly_sods" / "qa_report.md").read_text()
    assert "## Elevation" in report and "No DEM cached" not in report


def test_route_command(tmp_path, monkeypatch, network):
    _cache(tmp_path, monkeypatch, network.doc())
    a, f = network.ids["A"], network.ids["F"]
    result = runner.invoke(app, ["route", "--region", "dolly_sods", "--from", str(a), "--to", str(f)])
    assert result.exit_code == 0, result.output
    assert "2.17 mi" in result.output and "elevation unknown" in result.output.lower()
    out = tmp_path / "out" / "routes" / f"{a}-to-{f}"
    assert (out / "route.gpx").exists() and (out / "summary.md").exists()


def test_route_command_explains_bad_endpoints(tmp_path, monkeypatch, network):
    _cache(tmp_path, monkeypatch, network.doc())
    result = runner.invoke(app, ["route", "--region", "dolly_sods", "--from", "nowhere", "--to", "1000"])
    assert result.exit_code == 1 and "not a trailhead id" in result.output


def test_route_reference_flag_writes_caltopo_stub(tmp_path, monkeypatch, network):
    import yaml

    _cache(tmp_path, monkeypatch, network.doc())
    monkeypatch.setattr(cli, "REFERENCE_DIR", tmp_path / "reference")
    a, f = network.ids["A"], network.ids["F"]
    result = runner.invoke(app, ["route", "--region", "dolly_sods", "--from", str(a), "--to", str(f),
                                 "--reference", "a_to_f"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "reference" / "a_to_f.gpx").exists()
    stub = yaml.safe_load((tmp_path / "reference" / "a_to_f.yaml").read_text())
    assert stub["gpx"] == "a_to_f.gpx" and stub["caltopo"]["distance_mi"] is None


def test_generate_command(tmp_path, monkeypatch, grid):
    _cache(tmp_path, monkeypatch, grid.doc())
    g00 = grid.ids["g00"]
    result = runner.invoke(app, ["generate", "--region", "dolly_sods", "--trailhead", str(g00),
                                 "--total", "2.4-5.1", "--type", "loop"])
    assert result.exit_code == 0, result.output
    assert "3 candidates" in result.output
    out = tmp_path / "out" / "loops" / f"{g00}_2.4-5.1mi"
    assert (out / "candidates.md").exists() and (out / "candidate_1" / "route.gpx").exists()


def test_generate_rejects_bad_range(tmp_path, monkeypatch, grid):
    _cache(tmp_path, monkeypatch, grid.doc())
    result = runner.invoke(app, ["generate", "--region", "dolly_sods", "--trailhead", str(grid.ids["g00"]),
                                 "--total", "26-18"])
    assert result.exit_code == 1 and "--total" in result.output


def test_generate_with_nights_splits_days(tmp_path, monkeypatch, grid):
    _cache(tmp_path, monkeypatch, grid.doc())
    g00 = grid.ids["g00"]
    result = runner.invoke(app, ["generate", "--region", "dolly_sods", "--trailhead", str(g00), "--total", "2.4-5.1",
                                 "--nights", "1", "--daily", "1-2.6", "--max-gain-day", "2500"])
    assert result.exit_code == 0, result.output
    assert "Day 1" in result.output and "Camp 1" in result.output
    assert "elevation unknown" in result.output.lower()


def test_generate_nights_needs_daily(tmp_path, monkeypatch, grid):
    _cache(tmp_path, monkeypatch, grid.doc())
    result = runner.invoke(app, ["generate", "--region", "dolly_sods", "--trailhead", str(grid.ids["g00"]),
                                 "--total", "2.4-5.1", "--nights", "1"])
    assert result.exit_code == 1 and "--daily" in result.output
