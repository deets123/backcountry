import json

import pytest
from fastapi.testclient import TestClient

from backcountry.api.app import create_app
from backcountry.api.store import Settings
from conftest import MINI_DATE


class FakeDem:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, region, bounds, **kw):
        self.calls.append(region.id)
        if self.fail:
            raise RuntimeError(f"cannot open /secret/path/{region.id}.tif")


def make_client(tmp_path, index_path, dem=None, **limits):
    settings = Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", areas_dir=tmp_path / "areas",
                        osm_dir=index_path.parent, area_thread=False,
                        area_limits={"max_queue": 3, "per_hour": 6, "disk_gb": 40.0, **limits})
    dem = dem or FakeDem()
    return TestClient(create_app(settings, web_dist=tmp_path / "no-web", dem_fetch=dem)), dem


@pytest.fixture
def api(tmp_path, index_path):
    client, dem = make_client(tmp_path, index_path)
    return client, dem


def run_worker(client):
    client.app.state.areas.run_pending()


def test_search_regions_first(api):
    client, _ = api
    body = client.get("/api/search", params={"q": "dolly"}).json()
    assert body["results"][0] == {"kind": "region", "id": "dolly_sods", "name": "Dolly Sods Wilderness, WV",
                                  "type_label": "Region", "state": None}
    assert body["index"] is True


def test_search_finds_park_and_place(api):
    client, _ = api
    r = client.get("/api/search", params={"q": "Testpark"}).json()["results"]
    assert r[0]["kind"] == "area" and r[0]["ref"] == "relation/3003" and r[0]["type_label"] == "National Park"
    assert r[0]["state"] == "Statea" and "bbox" in r[0]
    assert client.get("/api/search", params={"q": "Lone Peak"}).json()["results"][0]["kind"] == "place"


@pytest.mark.parametrize("q", ["", "   ", "x" * 101])
def test_search_validates_q(api, q):
    client, _ = api
    assert client.get("/api/search", params={"q": q}).status_code == 400


def test_search_without_index(tmp_path):
    settings = Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", areas_dir=tmp_path / "areas",
                        osm_dir=tmp_path / "no-osm", area_thread=False)
    client = TestClient(create_app(settings, web_dist=tmp_path / "no-web"))
    body = client.get("/api/search", params={"q": "dolly"}).json()
    assert body["index"] is False and [r["id"] for r in body["results"]] == ["dolly_sods"]
    r = client.post("/api/areas", json={"ref": "relation/3003"})
    assert r.status_code == 503 and "isn't set up" in r.json()["error"]


def test_prepare_then_ready_then_generate(api):
    client, dem = api
    r = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    assert r["status"] == "queued" and r["message"] == "Finding trails in Testpark National Park…"
    assert client.get(f"/api/areas/{r['area_id']}/status").json()["status"] == "queued"
    run_worker(client)
    s = client.get(f"/api/areas/{r['area_id']}/status").json()
    assert s["status"] == "ready" and s["name"] == "Testpark National Park" and s["bbox"]
    assert dem.calls == [r["area_id"]]
    ths = client.get(f"/api/regions/{r['area_id']}/trailheads").json()["trailheads"]
    th = next(t for t in ths if "osm_trailhead" in (t.get("kinds") or []))
    run = client.post(f"/api/regions/{r['area_id']}/generate",
                      json={"trailhead": th["id"], "total": "20-32", "nights": 1, "daily": "8-20", "type": "loop"}).json()
    assert run["candidates"] and run["data_cut"] == MINI_DATE
    again = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    assert again["status"] == "ready" and dem.calls == [r["area_id"]]  # ready areas aren't prepared again


def test_place_ref_opens_its_park(api):
    client, _ = api
    peak = client.get("/api/search", params={"q": "Lone Peak"}).json()["results"][0]
    r = client.post("/api/areas", json={"ref": peak["ref"]}).json()
    assert r["name"] == "Testpark National Park"


def test_same_area_twice_prepares_once(api):
    client, dem = api
    a = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    b = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    assert a["area_id"] == b["area_id"] and b["status"] == "queued"
    run_worker(client)
    assert dem.calls == [a["area_id"]]


@pytest.mark.parametrize("body", [
    {"ref": "../etc/passwd"}, {"ref": "http://evil.example/x"}, {"ref": "relation/999999"}, {"ref": 5},
    {"box": [0, 0, "1", 1]}, {"box": [0, 0, 1]}, {"box": [-149.9, 10.2, -149.8, float("nan")]},
    {"point": [200, 0]}, {"point": ["x", 1]}, {"box": [10.0, 10.0, 10.1, 10.1]},
    {}, {"ref": "relation/3003", "point": [-149.8, 10.3]}, {"url": "https://evil.example"},
])
def test_bad_inputs_refused(api, body):
    client, dem = api
    raw = json.dumps(body).replace("NaN", "NaN")  # NaN goes through as a JSON extension; it must still be refused
    r = client.post("/api/areas", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 400, r.text
    run_worker(client)
    assert dem.calls == []


def test_box_and_point_open_areas(api):
    client, _ = api
    assert client.post("/api/areas", json={"point": [-149.8, 10.3]}).json()["name"] == "Testpark National Park"
    box = client.post("/api/areas", json={"box": [-149.75, 10.75, -149.65, 10.85]}).json()
    assert box["name"] == "Area near Bear Creek" and box["status"] == "queued"


def test_rate_limit_and_queue(tmp_path, index_path):
    client, _ = make_client(tmp_path, index_path, max_queue=1, per_hour=6)
    assert client.post("/api/areas", json={"ref": "relation/3003"}).status_code == 200
    busy = client.post("/api/areas", json={"ref": "relation/3004"})
    assert busy.status_code == 429 and "busy" in busy.json()["error"]
    client2, _ = make_client(tmp_path / "b", index_path, max_queue=5, per_hour=1)
    assert client2.post("/api/areas", json={"ref": "relation/3003"}).status_code == 200
    r = client2.post("/api/areas", json={"ref": "relation/3004"})
    assert r.status_code == 429 and "try again" in r.json()["error"].lower()


def test_disk_budget_evicts_unreferenced_only(tmp_path, index_path):
    client, _ = make_client(tmp_path, index_path)
    park = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    wild = client.post("/api/areas", json={"ref": "relation/3004"}).json()["area_id"]
    run_worker(client)
    ths = client.get(f"/api/regions/{park}/trailheads").json()["trailheads"]
    th = next(t for t in ths if "osm_trailhead" in (t.get("kinds") or []))
    client.post(f"/api/regions/{park}/generate", json={"trailhead": th["id"], "total": "20-32", "nights": 1,
                                                       "daily": "8-20", "type": "loop"})  # a saved run in the park
    client.app.state.areas.limits["disk_gb"] = 0.0  # everything is over budget now
    r = client.post("/api/areas", json={"box": [-149.75, 10.75, -149.65, 10.85]})
    assert r.status_code == 507 and "out of room" in r.json()["error"]
    areas_dir = tmp_path / "areas"
    assert (areas_dir / f"{park}.yaml").exists() and (tmp_path / "raw" / park).exists()  # a saved trip uses it
    assert not (areas_dir / f"{wild}.yaml").exists() and not (tmp_path / "raw" / wild).exists()  # unreferenced: removed
    assert (tmp_path / "raw").exists() and list((tmp_path / "raw").iterdir()) == [tmp_path / "raw" / park]


def test_status_unknown_id_is_unknown_not_error(api):
    client, _ = api
    assert client.get("/api/areas/no-such-area-abc/status").json() == {"status": "unknown", "message": None}


@pytest.mark.parametrize("bad", ["..", "a.b", "UPPER", "x" * 81])
def test_status_id_validated(api, bad):
    client, _ = api
    assert client.get(f"/api/areas/{bad}/status").status_code in (400, 404)


def test_failure_message_has_no_paths(tmp_path, index_path):
    client, _ = make_client(tmp_path, index_path, dem=FakeDem(fail=True))
    a = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    run_worker(client)
    s = client.get(f"/api/areas/{a['area_id']}/status").json()
    assert s["status"] == "failed" and "/" not in s["message"] and "secret" not in s["message"]
    assert s["message"] == "Couldn't get the trail data ready for this area. Try again later."


def test_no_trails_failure_says_so(api):
    client, _ = api
    a = client.post("/api/areas", json={"box": [-149.3, 10.6, -149.2, 10.7]}).json()
    run_worker(client)
    s = client.get(f"/api/areas/{a['area_id']}/status").json()
    assert s == {"status": "failed", "message": "No trails were found there.", "name": a["name"]}
