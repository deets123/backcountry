import json
import re

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
    return TestClient(create_app(env, web_dist=env.runs_dir / "no-web"))  # a built web/dist must not change test results


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
    c = TestClient(create_app(env, web_dist=env.runs_dir / "no-web"))
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
    c = TestClient(create_app(env, web_dist=env.runs_dir / "no-web"))
    run = c.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1"}).json()
    assert run["candidates"][0]["gain_ft"] is not None and run["candidates"][0]["gain_ft"] > 0
    assert run["data_sources"][1]["name"].startswith("National Elevation Dataset")


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/nope").status_code == 404


# --- downloads --------------------------------------------------------------------------

import subprocess  # noqa: E402

from backcountry import cli  # noqa: E402


def test_gpx_download_matches_cli_bytes(client, grid, tmp_path, monkeypatch):
    body = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1, "daily": "0.5-4", "type": "loop"}
    run = client.post("/api/regions/dolly_sods/generate", json=body).json()
    gpx = client.get(f"/api/runs/{run['run_id']}/candidates/1.gpx")
    assert gpx.status_code == 200 and gpx.headers["content-type"].startswith("application/gpx+xml")
    assert 'filename="' in gpx.headers["content-disposition"]

    # The CLI, on the same cache and request, writes the same bytes.
    from typer.testing import CliRunner

    monkeypatch.setattr(cli.ingest, "RAW_DIR", client.app.state.settings.raw_dir)
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(cli, "PROCESSED_DIR", tmp_path / "processed")
    result = CliRunner().invoke(cli.app, ["generate", "--region", "dolly_sods", "--trailhead", str(grid.ids["g00"]),
                                          "--total", "2.4-5.1", "--nights", "1", "--daily", "0.5-4", "--type", "loop"])
    assert result.exit_code == 0, result.output
    out = tmp_path / "out" / "loops" / f"{grid.ids['g00']}_2.4-5.1mi" / "candidate_1"
    assert gpx.text == (out / "route.gpx").read_text()
    day = client.get(f"/api/runs/{run['run_id']}/candidates/1.gpx?day=2")
    assert day.text == (out / "day_2.gpx").read_text()


def test_gpx_validates_against_schema(client, grid, tmp_path):
    import shutil

    if not shutil.which("xmllint"):
        pytest.skip("xmllint not installed")
    xsd = tmp_path.parent / "gpx.xsd"
    if not xsd.exists():
        r = subprocess.run(["curl", "-s", "--max-time", "20", "-o", str(xsd), "https://www.topografix.com/GPX/1/1/gpx.xsd"])
        if r.returncode != 0 or not xsd.exists():
            pytest.skip("could not download the GPX schema")
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1"}).json()
    path = tmp_path / "c1.gpx"
    path.write_text(client.get(f"/api/runs/{run['run_id']}/candidates/1.gpx").text)
    assert subprocess.run(["xmllint", "--noout", "--schema", str(xsd), str(path)], capture_output=True).returncode == 0


def test_geojson_download(client, grid):
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1",
                                                              "nights": 1, "daily": "0.5-4", "type": "loop"}).json()
    fc = client.get(f"/api/runs/{run['run_id']}/candidates/1.geojson").json()
    lines = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    assert [f["properties"]["name"] for f in lines] == ["Day 1", "Day 2"]
    assert any(f["properties"]["name"] == "Camp 1" for f in fc["features"] if f["geometry"]["type"] == "Point")


@pytest.mark.parametrize("path", ["/api/runs/nope/candidates/1.gpx", "/api/runs/{run}/candidates/0.gpx",
                                  "/api/runs/{run}/candidates/9.gpx", "/api/runs/{run}/candidates/1.gpx?day=7"])
def test_download_404s(client, grid, path):
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1",
                                                              "nights": 1, "daily": "0.5-4", "type": "loop"}).json()
    r = client.get(path.format(run=run["run_id"]))
    assert r.status_code == 404 and "error" in r.json()


# --- static web app ---------------------------------------------------------------------


def test_static_web_app_is_served_when_built(env, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>app</body></html>")
    c = TestClient(create_app(env, web_dist=dist))
    assert c.get("/").text.startswith("<html>") and c.get("/api/regions").status_code == 200


def test_without_a_built_web_app_root_explains(env, tmp_path):
    c = TestClient(create_app(env, web_dist=tmp_path / "missing"))
    r = c.get("/")
    assert r.status_code == 200 and "web app" in r.text.lower() and "/api/regions" in r.text


# --- review fixes -------------------------------------------------------------------------


def test_only_store_misses_become_404_other_keyerrors_are_500(env):
    app = create_app(env, web_dist=env.runs_dir / "no-web")

    @app.get("/api/boom")
    def boom():
        return {}["missing"]

    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/boom").status_code == 500
    assert c.get("/api/runs/nope").status_code == 404


def test_yaml_trailhead_with_verified_latlon_and_no_node_is_listed(tmp_path, grid):
    import yaml

    from conftest import lonlat

    regions_dir = tmp_path / "regions"
    regions_dir.mkdir()
    lon, lat = lonlat(1000, 830)
    (regions_dir / "synth.yaml").write_text(yaml.safe_dump({
        "id": "synth", "name": "Synth", "crs": "EPSG:32617",
        "bbox": {"south": 36.0, "west": -81.1, "north": 36.2, "east": -80.9},
        "trailheads": [{"id": "near_d", "name": "Near D", "lat": lat, "lon": lon, "verified": True}],
    }))
    raw_dir = tmp_path / "raw" / "synth"
    raw_dir.mkdir(parents=True)
    (raw_dir / "overpass_20260101T000000Z.json").write_text(json.dumps(grid.doc()))
    c = TestClient(create_app(Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", regions_dir=regions_dir)))
    r = c.get("/api/regions/synth/trailheads")
    assert r.status_code == 200, r.text
    th = next(t for t in r.json()["trailheads"] if t["id"] == "near_d")
    assert th["in_graph"] is True and th["node"] == grid.ids["g11"]  # snapped to the nearest graph node (170 m)


def test_concurrent_first_requests_build_the_graph_once(env):
    import threading

    LoadedRegion.builds = 0
    c = TestClient(create_app(env, web_dist=env.runs_dir / "no-web"))
    threads = [threading.Thread(target=lambda: c.get("/api/regions/dolly_sods/trailheads")) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert LoadedRegion.builds == 1


def test_generate_does_not_leak_other_caches_into_a_custom_raw_dir(client, grid):
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1"}).json()
    kinds = {f["kind"] for c in run["candidates"] for f in c["features"]}
    assert not any(k.startswith("usfs") for k in kinds)  # the repo's real USFS cache must not be read


# --- final review fixes ------------------------------------------------------------------


def test_every_error_is_json_with_an_error_key(env, grid):
    app = create_app(env, web_dist=env.runs_dir / "no-web")

    @app.get("/api/explode")
    def explode():
        raise RuntimeError("engine fell over")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/regions/dolly_sods/generate", content="hello", headers={"content-type": "application/json"})
    assert r.status_code == 400 and "error" in r.json()
    r = c.post("/api/regions/dolly_sods/generate", json=[1, 2])
    assert r.status_code == 400 and "error" in r.json()
    r = c.get("/api/regions/dolly_sods/features")
    assert r.status_code == 400 and r.json()["field"] == "bbox"
    r = c.get("/api/runs/x/candidates/abc.gpx")
    assert r.status_code == 400 and r.json()["field"] == "n"
    r = c.get("/api/explode")
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert "engine fell over" not in r.json()["error"]  # details (often paths) stay in the server log
    assert re.fullmatch(r"Something went wrong on the server \(error [0-9a-f]{8}\)\. Try again\.", r.json()["error"])


def test_a_corrupt_run_file_does_not_break_generate(client, grid):
    runs_dir = client.app.state.settings.runs_dir
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "20200101T000000Z-dead.json").write_text("{")
    body = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "type": "loop"}
    assert client.post("/api/regions/dolly_sods/generate", json=body).status_code == 200
    run = client.post("/api/regions/dolly_sods/generate", json=body | {"avoid": ["Grid Road"]}).json()
    assert run["diff"] and "Grid Road" in run["diff"]  # the previous good run was still found


def test_run_save_is_atomic_and_indexed(env, tmp_path):
    from backcountry.api.store import RunStore

    store = RunStore(tmp_path / "runs")
    run = {"run_id": "20260101T000000Z-aaaa", "region": "r", "request": {"trailhead": "t"}, "candidates": []}
    store.save(run)
    assert not list((tmp_path / "runs").glob("*.tmp"))
    assert store.latest("r", "t")["run_id"] == "20260101T000000Z-aaaa"
    index = json.loads((tmp_path / "runs" / "index.json").read_text())
    assert index["20260101T000000Z-aaaa"] == {"region": "r", "trailhead": "t", "cut": None}
    assert store.latest("r", "other") is None


# --- POST /api/difficulty: re-rate a saved trip ---------------------------------------


def _tagged_client(tmp_path, grid, with_dem=True):
    from backcountry import ingest
    from backcountry.region import load_region

    for e in grid.elements:
        if e.get("id") == 110:  # Col 0
            e["tags"]["sac_scale"] = "alpine_hiking"
    raw_dir = tmp_path / "raw" / "dolly_sods"
    raw_dir.mkdir(parents=True)
    (raw_dir / "overpass_20260101T000000Z.json").write_text(json.dumps(grid.doc()))
    settings = Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", regions_dir=None)
    if with_dem:
        dem = plane_dem(0.08, 0.05, 1000)
        ingest.fetch_dem(load_region("dolly_sods"), (-180, -90, 180, 90), raw_dir=settings.raw_dir,
                         searcher=lambda ds, b: [{"title": "USGS 1/3 Arc Second n40w080 20260101",
                                                   "publicationDate": "2026-01-01", "downloadURL": "x"}],
                         reader=lambda urls, b: (dem.array.astype("float32"), dem.transform, dem.crs))
    return TestClient(create_app(settings, web_dist=settings.runs_dir / "no-web"))


def _saved(c):
    return {"region": "dolly_sods", "nights": 1, "profile": c["profile"], "segments": c["segments"]}


def test_difficulty_rerates_a_saved_candidate(tmp_path, grid):
    import numpy as np

    from backcountry.difficulty import RULES, rate_stretch
    from backcountry.elevation import FT_PER_M, cumulative_gain_loss, smooth

    client = _tagged_client(tmp_path, grid)
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1",
                                                                 "nights": 1, "daily": "0.5-4", "type": "loop"}).json()
    c = run["candidates"][0]
    end = c["profile"]["d_m"][-1]
    camp = round(end * 0.3, 1)  # moved on the phone
    r = client.post("/api/difficulty", json=_saved(c) | {"camps_m": [camp]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["terrain"] == c["terrain"]  # rebuilt from the graph by way ids: same as the run's
    assert body["difficulty_rules"] == RULES
    d, z = c["profile"]["d_m"], c["profile"]["z_m"]
    G, _ = cumulative_gain_loss(smooth(np.array(z, dtype=float)))
    at = lambda m: float(np.interp(m, d, G)) * FT_PER_M  # noqa: E731
    for (a, b), day in zip([(0.0, camp), (camp, end)], body["days"]):
        want = rate_stretch(d, z, c["terrain"], a, b, at(b) - at(a), "backpacking")
        assert day["rating"]["level"] == want.level and day["rating"]["effort"] == pytest.approx(want.effort)
        assert (day["start_m"], day["end_m"]) == (a, b)
    order = ["easy", "moderate", "hard", "strenuous"]
    assert body["level"] == max((x["rating"]["level"] for x in body["days"]), key=order.index)
    assert body["rating"] is None


def test_difficulty_day_hike_rates_the_whole_route(tmp_path, grid):
    client = _tagged_client(tmp_path, grid)
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1",
                                                                 "type": "loop"}).json()
    c = run["candidates"][0]
    body = client.post("/api/difficulty", json=_saved(c) | {"nights": None, "camps_m": None}).json()
    assert body["days"] == [] and body["rating"] == c["rating"] and body["level"] == c["level"]


def test_difficulty_mixed_segment_adds_no_terrain(tmp_path, grid):
    client = _tagged_client(tmp_path, grid, with_dem=False)
    prof = {"d_m": [0.0, 1000.0, 2000.0], "z_m": [None, None, None]}
    seg = {"label": "x", "highway": "path", "is_road": False, "last_resort": False, "length_m": 2000.0}
    mixed = client.post("/api/difficulty", json={"region": "dolly_sods", "nights": None, "camps_m": None, "profile": prof,
                                                  "segments": [seg | {"sources": ["osm:way/110", "osm:way/100"]}]}).json()
    assert mixed["terrain"] == [] and mixed["rating"]["untagged_share"] == 1.0
    assert mixed["rating"]["level"] is None  # no elevation: unknown, never guessed
    uniform = client.post("/api/difficulty", json={"region": "dolly_sods", "nights": None, "camps_m": None, "profile": prof,
                                                    "segments": [seg | {"sources": ["osm:way/110"]}]}).json()
    assert [t["reason"] for t in uniform["terrain"]] == ["scramble (tagged)"]


@pytest.mark.parametrize("change, field", [
    ({"profile": {"d_m": [0, 1], "z_m": [1]}}, "profile"),
    ({"profile": "x"}, "profile"),
    ({"segments": [{"length_m": "far", "sources": []}]}, "segments"),
    ({"camps_m": [5000.0]}, "camps_m"),
    ({"camps_m": [100.0, 200.0]}, "camps_m"),
    ({"nights": 0}, "nights"),
])
def test_difficulty_bad_input_is_400(client, change, field):
    body = {"region": "dolly_sods", "nights": 1, "camps_m": [500.0],
            "profile": {"d_m": [0.0, 1000.0], "z_m": [1.0, 2.0]},
            "segments": [{"label": "x", "is_road": False, "length_m": 1000.0, "sources": ["osm:way/100"]}]}
    r = client.post("/api/difficulty", json=body | change)
    assert r.status_code == 400 and r.json()["field"] == field


def test_difficulty_unknown_region_is_404(client):
    r = client.post("/api/difficulty", json={"region": "mars", "nights": None, "camps_m": None,
                                             "profile": {"d_m": [0.0, 1.0], "z_m": [None, None]}, "segments": []})
    assert r.status_code == 404


def test_difficulty_places_tags_inside_a_mixed_segment_by_the_route_line(tmp_path, grid):
    # "Row 1" is two ways (g01-m, m-g11-g21); only g01-m is tagged: the saved route's own line says where it is
    for e in grid.elements:
        if e.get("id") == 101:
            e["tags"]["sac_scale"] = "alpine_hiking"
    client = _tagged_client(tmp_path, grid)
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1",
                                                                 "type": "loop"}).json()
    mixed = [c for c in run["candidates"]
             if any(len(s["sources"]) > 1 and "osm:way/101" in s["sources"] for s in c["segments"])]
    assert mixed  # a loop over the whole of Row 1: the mixed segment this test is about
    c = mixed[0]
    body = _saved(c) | {"nights": None, "camps_m": None}
    without_line = client.post("/api/difficulty", json=body).json()
    row1 = next(t for t in c["terrain"] if "osm:way/101" in t["sources"])
    assert not any("osm:way/101" in t["sources"] for t in without_line["terrain"])  # no line: the mixed segment adds nothing
    with_line = client.post("/api/difficulty", json=body | {"geometry": c["geometry"]["coordinates"]}).json()
    assert [(round(t["start_m"]), round(t["end_m"]), t["reason"]) for t in with_line["terrain"]] == \
        [(round(t["start_m"]), round(t["end_m"]), t["reason"]) for t in c["terrain"]]
    assert with_line["rating"]["level"] == c["rating"]["level"] and row1["reason"] == "scramble (tagged)"


# --- plan jobs (first trips early) -------------------------------------------------------


def test_plan_job_returns_the_final_run_and_saves_it(env, grid):
    env.area_thread = False
    client = TestClient(create_app(env, web_dist=env.runs_dir / "no-web"))
    body = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "nights": 1, "daily": "0.5-4", "type": "loop"}
    r = client.post("/api/regions/dolly_sods/plans", json=body)
    assert r.status_code == 200, r.text
    job = client.get(f"/api/plans/{r.json()['job_id']}").json()
    assert job["status"] == "done" and job["run"]["partial"] is False and len(job["run"]["candidates"]) == 3
    assert client.get(f"/api/runs/{job['run']['run_id']}").json() == job["run"]


def test_plan_job_bad_settings_are_a_400(client, grid):
    r = client.post("/api/regions/dolly_sods/plans", json={"trailhead": str(grid.ids["g00"]), "total": "26-18"})
    assert r.status_code == 400 and r.json()["field"] == "total"


def test_plan_job_id_is_validated(client):
    assert client.get("/api/plans/..%2F..%2Fetc").status_code in (400, 404)
    assert client.get("/api/plans/0123456789abcdef").status_code == 404


def test_finishes_endpoint(client, grid):
    r = client.get("/api/regions/dolly_sods/finishes", params={"trailhead": str(grid.ids["T"]), "total": "0.5-4"})
    assert r.status_code == 200, r.text
    finishes = r.json()["finishes"]
    assert 1 <= len(finishes) <= 5
    for f in finishes:
        assert {"id", "name", "length_m", "gain_ft", "loss_ft", "straight_line_m"} <= set(f) and f["node"] != grid.ids["T"]
    assert client.get("/api/regions/dolly_sods/finishes", params={"trailhead": "nowhere", "total": "1-4"}).status_code == 400
    assert client.get("/api/regions/dolly_sods/finishes", params={"trailhead": str(grid.ids["T"]), "total": "4-1"}).status_code == 400


def test_cancel_endpoint(client):
    assert client.delete("/api/plans/0123456789abcdef").status_code == 404
    assert client.delete("/api/plans/..%2Fx").status_code in (400, 404)


def test_generate_and_update_count_against_the_plan_cap(env, grid, monkeypatch):
    import threading

    from backcountry.api import endpoints

    env.plan_jobs = 1
    client = TestClient(create_app(env, web_dist=env.runs_dir / "no-web"))
    body = {"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1", "type": "loop"}
    run = client.post("/api/regions/dolly_sods/generate", json=body).json()  # fine on its own: the slot was free
    release, started = threading.Event(), threading.Event()
    real = endpoints.plan_trip

    def slow(*a, **k):
        started.set()
        release.wait(5)
        return real(*a, **k)
    monkeypatch.setattr(endpoints, "plan_trip", slow)
    job = client.post("/api/regions/dolly_sods/plans", json=body).json()["job_id"]
    started.wait(5)
    try:
        assert client.post("/api/regions/dolly_sods/generate", json=body).status_code == 429
        assert run["run_id"]  # (the saved-trip update takes the same slot; it needs newer data to run at all)
    finally:
        release.set()
    for _ in range(200):
        if client.get(f"/api/plans/{job}").json()["status"] == "done":
            break
        threading.Event().wait(0.02)
    assert client.get(f"/api/plans/{job}").json()["status"] == "done"


# --- request limits and run ids ---------------------------------------------------------------------


def test_bodies_over_a_megabyte_are_refused(client):
    big = "[" + ",".join(["1"] * 600_000) + "]"  # about 1.2 MB
    r = client.post("/api/difficulty", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and "error" in r.json()


@pytest.mark.parametrize("bad", ["index", "..%2Findex", "20261027T000000Z-zzzz", "%00x", "a" * 200])
def test_run_ids_are_validated(client, bad):
    assert client.get(f"/api/runs/{bad}").status_code == 404
    assert client.get(f"/api/runs/{bad}/candidates/1.gpx").status_code in (400, 404)


# --- work done once per loaded area, not per request --------------------------------------------------


def test_trails_are_encoded_once_per_loaded_area_and_gzipped(client, monkeypatch):
    from backcountry.api import endpoints

    calls = []
    real = endpoints.edges_features
    monkeypatch.setattr(endpoints, "edges_features", lambda *a, **k: calls.append(1) or real(*a, **k))
    a = client.get("/api/regions/dolly_sods/trails")
    b = client.get("/api/regions/dolly_sods/trails", headers={"accept-encoding": "gzip"})
    assert a.status_code == b.status_code == 200 and a.json() == b.json() and len(calls) == 1
    assert b.headers.get("content-encoding") == "gzip" and a.headers["content-type"].startswith("application/json")


def test_features_lists_are_built_once_per_loaded_area(client, monkeypatch):
    from backcountry import ingest

    calls = []
    real = ingest.camp_sites
    monkeypatch.setattr(ingest, "camp_sites", lambda raw: calls.append(1) or real(raw))
    for _ in range(3):
        assert client.get("/api/regions/dolly_sods/features", params={"bbox": "-81.1,36,-80.9,36.2"}).status_code == 200
    assert len(calls) == 1


def test_region_summaries_read_each_cache_once(client, monkeypatch):
    from backcountry.api import store as store_mod

    calls = []
    real = store_mod.json.loads
    monkeypatch.setattr(store_mod.json, "loads", lambda text, *a, **k: calls.append(text) or real(text, *a, **k))
    client.get("/api/regions"), client.get("/api/regions")
    assert sum(1 for t in calls if '"response"' in t) <= 1  # the Overpass cache (big) is parsed at most once
