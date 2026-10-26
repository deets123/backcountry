"""Acceptance check 5: no user input can make the server fetch from a host of its choosing, write outside its data
folders, change the index, or start unlimited work."""

import sqlite3
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from backcountry import areas, ingest
from backcountry.api.app import create_app
from backcountry.api.store import Settings

HOSTILE = ["../../etc/passwd", "http://evil.example/x.pbf", "file:///etc/passwd", "'; DROP TABLE areas; --",
           "$(rm -rf ~)", "`id`", "Zion\x00", "%2e%2e%2f", "relation/3003/../../x", "<script>alert(1)</script>"]


def client_for(tmp_path, index_path, dem_fetch=None, **limits):
    settings = Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", areas_dir=tmp_path / "areas",
                        osm_dir=index_path.parent, area_thread=False,
                        area_limits={"max_queue": 100, "per_hour": 100, "disk_gb": 40.0, **limits})
    return TestClient(create_app(settings, web_dist=tmp_path / "no-web", dem_fetch=dem_fetch)), settings


def test_no_user_controlled_fetch_hosts(tmp_path, index_path, monkeypatch):
    """Every URL the server opens while serving hostile requests is on the fixed list."""
    opened = []
    monkeypatch.setattr(ingest, "tnm_search", lambda dataset, bounds, fetcher=None: opened.append(ingest.TNM_API) or [
        {"title": "USGS 1/3 Arc Second n11w150 20250101", "downloadURL": "https://prd-tnm.s3.amazonaws.com/x.tif",
         "publicationDate": "2025-01-01"}])

    def reader(urls, bounds):
        opened.extend(urls)
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(ingest, "_read_merged", reader)
    client, _ = client_for(tmp_path, index_path)  # the real fetch_dem, with its network calls recorded
    for text in HOSTILE:
        client.get("/api/search", params={"q": text})
        client.post("/api/areas", json={"ref": text})
        client.get(f"/api/areas/{urllib.parse.quote(text, safe='')}/status")
    client.post("/api/areas", json={"ref": "relation/3003"})
    client.app.state.areas.run_pending()
    hosts = {urllib.parse.urlparse(u).netloc for u in opened}
    assert opened and hosts <= {"tnmaccess.nationalmap.gov", "prd-tnm.s3.amazonaws.com"}


def test_dem_urls_off_the_allowlist_are_refused(tmp_path, monkeypatch, index_path):
    from backcountry.region import BBox, Region

    monkeypatch.setattr(ingest, "tnm_search", lambda dataset, bounds, fetcher=None: [
        {"title": "USGS 1/3 Arc Second n11w150 20250101", "downloadURL": "http://evil.example/x.tif", "publicationDate": "2025-01-01"}])
    opened = []
    monkeypatch.setattr(ingest, "_read_merged", lambda urls, bounds: opened.extend(urls))
    region = Region(id="x", name="x", crs="EPSG:32606", bbox=BBox(10, -150, 11, -149))
    with pytest.raises(ingest.FetchError, match="unexpected host"):
        ingest.fetch_dem(region, (-150, 10, -149, 11), raw_dir=tmp_path)
    assert opened == []


@pytest.mark.parametrize("name", HOSTILE + ["x" * 500, "", "Æ"])
def test_area_ids_never_escape_dirs(tmp_path, index, name):
    spec = areas.AreaSpec(id=areas.area_id(name, None, areas.BBox(10.1, -149.95, 10.5, -149.6)), name=name,
                          type_label="Area", state=None, bbox=areas.BBox(10.1, -149.95, 10.5, -149.6), osm=None, capped=False)
    raw_dir, areas_dir = tmp_path / "raw", tmp_path / "areas"
    try:
        areas.prepare_area(spec, index, raw_dir=raw_dir, areas_dir=areas_dir, dem_fetch=lambda *a, **k: None)
    except ValueError:
        pass
    for p in tmp_path.rglob("*"):
        r = p.resolve()
        assert r.is_relative_to(raw_dir.resolve()) or r.is_relative_to(areas_dir.resolve()), p


def test_index_opened_read_only_by_api(tmp_path, index_path):
    client, _ = client_for(tmp_path, index_path)
    con = client.app.state.areas.index()
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO meta VALUES ('x', 'y')")
    con.close()


def test_written_files_not_executable(tmp_path, index_path, index):
    spec = areas.area_from_ref(index, "relation/3003")
    areas.prepare_area(spec, index, raw_dir=tmp_path / "raw", areas_dir=tmp_path / "areas", dem_fetch=lambda *a, **k: None)
    files = [index_path, *(p for p in tmp_path.rglob("*") if p.is_file())]
    assert files and all(p.stat().st_mode & 0o111 == 0 for p in files)


def test_flood_of_boxes_capped(tmp_path, index_path):
    client, _ = client_for(tmp_path, index_path, per_hour=6, max_queue=100)
    codes = [client.post("/api/areas", json={"box": [-149.95 + i * 0.004, 10.11, -149.94 + i * 0.004, 10.12]}).status_code
             for i in range(50)]
    assert codes.count(200) == 6 and set(codes) == {200, 429}


def test_unknown_body_keys_and_huge_bodies_refused(tmp_path, index_path):
    client, _ = client_for(tmp_path, index_path)
    assert client.post("/api/areas", json={"ref": "relation/3003", "raw_dir": "/tmp"}).status_code == 400
    assert client.post("/api/areas", json={"box": list(range(10_000))}).status_code == 400
    assert client.get("/api/search", params={"q": "a" * 10_000}).status_code == 400
