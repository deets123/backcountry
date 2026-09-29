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
