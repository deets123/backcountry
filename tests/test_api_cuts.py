import json

import pytest
from fastapi.testclient import TestClient

from backcountry.api.app import create_app
from backcountry.api.store import LoadedRegion, RegionStore, Settings
from conftest import MINI_DATE, add_cut

LOOP = {"total": "20-32", "nights": 1, "daily": "8-20", "type": "loop"}


def rim_trailhead(client, area_id):
    ths = client.get(f"/api/regions/{area_id}/trailheads").json()["trailheads"]
    return next(t for t in ths if "osm_trailhead" in (t.get("kinds") or []))


@pytest.fixture
def area_client(area_env, tmp_path):
    settings, spec = area_env
    return TestClient(create_app(settings, web_dist=tmp_path / "no-web")), settings, spec


def test_run_records_cut_and_route_nodes(area_client):
    client, _, spec = area_client
    th = rim_trailhead(client, spec.id)
    assert th["verified"] is False and th["source"] == "data"
    run = client.post(f"/api/regions/{spec.id}/generate", json={"trailhead": th["id"], **LOOP}).json()
    assert run["data_cut"] == MINI_DATE and run["candidates"]
    route = run["candidates"][0]["route"]
    assert len(route["keys"]) == len(route["nodes"]) - 1 and all(":" in k for k in route["keys"])
    src = run["data_sources"][0]
    assert src["name"] == "OpenStreetMap via Geofabrik US extract" and src["retrieved_at"] == MINI_DATE
    assert "OpenStreetMap contributors" in src["attribution"]


def test_old_cut_still_loads_after_new_cut(area_env):
    settings, spec = area_env
    add_cut(settings, spec.id, "2026-11-20")
    store = RegionStore(settings)
    assert store.get(spec.id).cut == "2026-11-20"
    assert store.get(spec.id, MINI_DATE).cut == MINI_DATE


def test_unknown_cut_is_not_found(area_env):
    from backcountry.api.store import NotFound

    settings, spec = area_env
    with pytest.raises(NotFound):
        RegionStore(settings).get(spec.id, "2020-01-01")


def test_store_evicts_least_recently_used(area_env, tmp_path, grid):
    settings, spec = area_env
    (settings.raw_dir / "dolly_sods").mkdir(parents=True)
    (settings.raw_dir / "dolly_sods" / "overpass_20260101T000000Z.json").write_text(json.dumps(grid.doc()))
    settings.max_loaded = 1
    store = RegionStore(settings)
    LoadedRegion.builds = 0
    store.get(spec.id), store.get("dolly_sods"), store.get(spec.id)
    assert LoadedRegion.builds == 3
    store.get(spec.id)
    assert LoadedRegion.builds == 3


def test_regions_list_excludes_generated_areas(area_client):
    client, _, spec = area_client
    body = client.get("/api/regions").json()
    assert spec.id not in {r["id"] for r in body["regions"]}
    recent = body["recent_areas"]
    assert recent[0]["id"] == spec.id and recent[0]["name"] == "Testpark National Park"
    assert recent[0]["type_label"] == "National Park" and recent[0]["state"] == "Statea" and recent[0]["bbox"]


def test_chat_and_difficulty_use_the_runs_cut(area_client, monkeypatch):
    client, settings, spec = area_client
    th = rim_trailhead(client, spec.id)
    run = client.post(f"/api/regions/{spec.id}/generate", json={"trailhead": th["id"], **LOOP}).json()
    add_cut(settings, spec.id, "2026-11-20")
    store = client.app.state.regions
    seen = []
    real = store.get
    monkeypatch.setattr(store, "get", lambda rid, cut=None: seen.append(cut) or real(rid, cut))
    c = run["candidates"][0]
    body = {"region": spec.id, "cut": run["data_cut"], "profile": c["profile"] or {"d_m": [0, 1], "z_m": [None, None]},
            "segments": c["segments"], "nights": None, "camps_m": None}
    assert client.post("/api/difficulty", json=body).status_code == 200
    assert seen == [MINI_DATE]
    bad = client.post("/api/difficulty", json={**body, "cut": "../../x"})
    assert bad.status_code == 400


def test_hand_made_runs_unchanged(tmp_path, grid):
    raw_dir = tmp_path / "raw" / "dolly_sods"
    raw_dir.mkdir(parents=True)
    (raw_dir / "overpass_20260101T000000Z.json").write_text(json.dumps(grid.doc()))
    client = TestClient(create_app(Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs"), web_dist=tmp_path / "no"))
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2.4-5.1",
                                                                "nights": 1, "daily": "0.5-4", "type": "loop"}).json()
    assert run["data_cut"] is None and run["data_sources"][0]["name"] == "OpenStreetMap via Overpass API"
    index = json.loads((tmp_path / "runs" / "index.json").read_text())
    assert index[run["run_id"]]["cut"] is None


def test_chat_uses_the_runs_cut(area_env, tmp_path, monkeypatch):
    from backcountry.chat.models import FakeModel

    settings, spec = area_env
    model = FakeModel([{"reply": "Unknown.", "kind": "answer", "changes": {}}])
    client = TestClient(create_app(settings, web_dist=tmp_path / "no-web", chat_model=model))
    th = rim_trailhead(client, spec.id)
    run = client.post(f"/api/regions/{spec.id}/generate", json={"trailhead": th["id"], **LOOP}).json()
    add_cut(settings, spec.id, "2026-11-20")
    store, seen, real = client.app.state.regions, [], client.app.state.regions.get
    monkeypatch.setattr(store, "get", lambda rid, cut=None: seen.append(cut) or real(rid, cut))
    r = client.post("/api/chat", json={"run_id": run["run_id"], "candidate": 1, "message": "how far is it?", "history": []})
    assert r.status_code == 200 and seen == [MINI_DATE]


def test_loaded_region_reports_load_timings(area_env):
    settings, spec = area_env
    timings = RegionStore(settings).get(spec.id).timings
    assert set(timings) == {"graph", "qa", "dem_load"}
    assert all(isinstance(v, float) and v >= 0 for v in timings.values())
