import json

import pytest
from fastapi.testclient import TestClient

from backcountry.api.app import create_app
from backcountry.api.store import LoadedRegion, Settings


@pytest.fixture
def env(tmp_path, grid):
    """Synthetic grid cached as region 'dolly_sods' (the yaml is real; its trailheads aren't in this graph)."""
    raw_dir = tmp_path / "raw" / "dolly_sods"
    raw_dir.mkdir(parents=True)
    (raw_dir / "overpass_20260101T000000Z.json").write_text(json.dumps(grid.doc()))
    settings = Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", regions_dir=None)
    return settings


@pytest.fixture
def client(env):
    return TestClient(create_app(env))


def test_regions_lists_what_is_cached(client):
    body = client.get("/api/regions").json()
    ids = {r["id"]: r for r in body["regions"]}
    assert ids["dolly_sods"]["osm_retrieved_at"] == "2026-01-01T00:00:00Z"
    assert ids["dolly_sods"]["dem"] is False and ids["dolly_sods"]["name"]
    assert ids["pemi"]["osm_retrieved_at"] is None  # yaml exists, nothing cached in this temp dir


def test_trailheads_include_yaml_entries_even_when_not_in_graph(client, grid):
    body = client.get("/api/regions/dolly_sods/trailheads").json()
    yaml_entries = [t for t in body["trailheads"] if t["source"] == "yaml"]
    assert yaml_entries and all(t["in_graph"] is False for t in yaml_entries)  # grid graph != real Dolly Sods
    from_data = [t for t in body["trailheads"] if t["source"] == "data"]
    assert any(t["node"] == grid.ids["g20"] for t in from_data)  # Row 0 meets Grid Road: a trail/road junction
    assert all("lat" in t and "lon" in t and t["verified"] in (True, False) for t in body["trailheads"])


def test_unknown_region_and_uncached_region_are_404(client):
    assert client.get("/api/regions/mars/trailheads").status_code == 404
    r = client.get("/api/regions/pemi/trailheads")
    assert r.status_code == 404 and "backcountry ingest" in r.json()["error"]


def test_region_graph_is_built_once(env):
    LoadedRegion.builds = 0
    c = TestClient(create_app(env))
    c.get("/api/regions/dolly_sods/trailheads")
    c.get("/api/regions/dolly_sods/trailheads")
    assert LoadedRegion.builds == 1


def grid_edge_count(grid):
    from backcountry.graph import build_graph
    from conftest import CRS

    return build_graph(grid.doc(), crs=CRS).graph.number_of_edges()


def test_trails_geojson_has_provenance(client, grid):
    fc = client.get("/api/regions/dolly_sods/trails").json()
    assert fc["type"] == "FeatureCollection" and "OpenStreetMap" in fc["attribution"]
    f = fc["features"][0]
    assert f["properties"]["source"].startswith("osm:way/") and "component" in f["properties"]
    assert "profile" not in f["properties"] and len(fc["features"]) == grid_edge_count(grid)


def test_features_in_bbox_with_evidence(client, grid):
    from conftest import lonlat

    lon, lat = lonlat(1200, 80)
    grid.poi(9100, 1200, 80, tourism="camp_site", backcountry="yes")
    grid.elements[-1].update(timestamp="2024-05-02T00:00:00Z", version=3)
    raw_dir = client.app.state.settings.raw_dir / "dolly_sods"
    (raw_dir / "overpass_20260101T000000Z.json").write_text(json.dumps(grid.doc()))
    w, s, e, n = lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01
    body = client.get(f"/api/regions/dolly_sods/features?bbox={w},{s},{e},{n}").json()
    site = next(c for c in body["camp_sites"] if c["source"] == "osm:node/9100")
    assert site["tags"]["backcountry"] == "yes" and site["meta"]["version"] == 3
    assert site["url"] == "https://www.openstreetmap.org/node/9100"
    assert body["water_points"] == [] and body["streams"] == []


def test_features_rejects_bad_bbox(client):
    r = client.get("/api/regions/dolly_sods/features?bbox=1,2,3")
    assert r.status_code == 400 and r.json()["field"] == "bbox"


# --- generate and runs ---------------------------------------------------------------

from conftest import plane_dem  # noqa: E402


def test_generate_returns_a_run_and_saves_it(client, grid):
    body = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1, "daily": "0.5-4", "type": "loop"}
    r = client.post("/api/regions/dolly_sods/generate", json=body)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["region"] == "dolly_sods" and len(run["candidates"]) == 3
    assert run["candidates"][0]["plan"]["days"] and run["candidates"][0]["gain_ft"] is None
    assert any("elevation unknown" in n for n in run["candidates"][0]["notes"])
    assert run["known_gaps"] and run["data_sources"][0]["retrieved_at"] == "2026-01-01T00:00:00Z"
    assert run["diff"] is None
    again = client.get(f"/api/runs/{run['run_id']}").json()
    assert again == run


def test_second_generate_for_same_trailhead_carries_a_diff(client, grid):
    body = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "type": "loop"}
    client.post("/api/regions/dolly_sods/generate", json=body)
    run = client.post("/api/regions/dolly_sods/generate", json=body | {"avoid": ["Grid Road"]}).json()
    assert run["diff"] and "Grid Road" in run["diff"] and "## Candidates" in run["diff"]


@pytest.mark.parametrize("body, field, status", [
    ({"trailhead": "1000", "total": "5-2"}, "total", 400),
    ({"trailhead": "nowhere", "total": "2-5"}, "trailhead", 400),
    ({"trailhead": "1000", "total": "2-5", "avoid": ["Nope"]}, "avoid", 400),
    ({"trailhead": "1000", "total": "2-5", "nights": 1}, "daily", 400),
])
def test_generate_bad_requests_name_the_field(client, body, field, status):
    r = client.post("/api/regions/dolly_sods/generate", json=body)
    assert r.status_code == status and r.json()["field"] == field


def test_generate_uses_the_cached_dem(env, grid, tmp_path):
    from backcountry import ingest
    from backcountry.region import load_region

    dem = plane_dem(0.0, 0.05, 1000)
    ingest.fetch_dem(load_region("dolly_sods"), (-180, -90, 180, 90), raw_dir=env.raw_dir,
                     searcher=lambda ds, b: [{"title": "USGS 1/3 Arc Second n40w080 20260101", "publicationDate": "2026-01-01",
                                               "downloadURL": "x"}],
                     reader=lambda urls, b: (dem.array.astype("float32"), dem.transform, dem.crs))
    c = TestClient(create_app(env))
    run = c.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1"}).json()
    assert run["candidates"][0]["gain_ft"] is not None and run["candidates"][0]["gain_ft"] > 0
    assert run["data_sources"][1]["name"].startswith("National Elevation Dataset")


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/nope").status_code == 404
