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


def make_client(tmp_path, index_path, dem=None, osm_dir=None, **limits):
    settings = Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", areas_dir=tmp_path / "areas",
                        osm_dir=osm_dir or index_path.parent, area_thread=False,
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


# --- saved trips and newer data ---------------------------------------------------------------------


@pytest.fixture
def planned(api):
    client, _ = api
    a = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    ths = client.get(f"/api/regions/{a}/trailheads").json()["trailheads"]
    th = next(t for t in ths if "osm_trailhead" in (t.get("kinds") or []))
    run = client.post(f"/api/regions/{a}/generate", json={"trailhead": th["id"], "total": "20-32", "nights": 1,
                                                          "daily": "8-20", "type": "loop"}).json()
    return client, a, run


def _rename(old, new):
    def mutate(elements):
        for e in elements:
            if e.get("tags", {}).get("name") == old:
                e["tags"]["name"] = new
    return mutate


def test_update_check_newer_only_after_new_cut(planned):
    from conftest import add_cut

    client, a, run = planned
    check = client.get(f"/api/runs/{run['run_id']}/update", params={"candidate": 1}).json()
    assert check["newer"] is False and check["cut"] == MINI_DATE
    add_cut(client.app.state.settings, a, "2026-11-20")
    check = client.get(f"/api/runs/{run['run_id']}/update", params={"candidate": 1}).json()
    assert check == {"newer": True, "cut": MINI_DATE, "latest": "2026-11-20", "approx_bytes": check["approx_bytes"]}
    assert check["approx_bytes"] > 1000


def test_update_never_runs_on_get(planned):
    from conftest import add_cut

    client, a, run = planned
    add_cut(client.app.state.settings, a, "2026-11-20")
    before = sorted((client.app.state.settings.runs_dir).glob("*.json"))
    client.get(f"/api/runs/{run['run_id']}/update", params={"candidate": 1})
    assert sorted((client.app.state.settings.runs_dir).glob("*.json")) == before


def test_update_without_newer_data_refused(planned):
    client, _, run = planned
    r = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1})
    assert r.status_code == 400 and "newest trail data" in r.json()["error"]


def test_update_same_trails_has_no_changes(planned):
    from conftest import add_cut

    client, a, run = planned
    add_cut(client.app.state.settings, a, "2026-11-20")
    old_file = (client.app.state.settings.runs_dir / f"{run['run_id']}.json").read_text()
    body = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1}).json()
    assert body["ok"] is True and body["changes"] == ["No changes to this trip."]
    new = body["run"]
    assert new["data_cut"] == "2026-11-20" and new["run_id"] != run["run_id"] and len(new["candidates"]) == 1
    assert new["candidates"][0]["length_m"] == pytest.approx(run["candidates"][0]["length_m"], abs=0.01)
    assert new["start"] == run["start"] and new["request"] == run["request"]
    assert (client.app.state.settings.runs_dir / f"{run['run_id']}.json").read_text() == old_file  # old trip untouched
    assert client.get(f"/api/runs/{new['run_id']}").status_code == 200  # the new one is saved


def test_update_lists_renamed_trail(planned):
    from conftest import add_cut

    client, a, run = planned
    add_cut(client.app.state.settings, a, "2026-11-20", _rename("Canyon Trail", "Canyon Rim Trail"))
    body = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1}).json()
    assert "Trail renamed: Canyon Trail → Canyon Rim Trail" in body["changes"]
    assert not any("osm:" in line or "way/" in line for line in body["changes"])  # no raw OSM ids in user text


def test_update_missing_way_message(planned):
    from conftest import add_cut

    client, a, run = planned
    add_cut(client.app.state.settings, a, "2026-11-20",
            lambda els: els.__setitem__(slice(None), [e for e in els if e.get("tags", {}).get("name") != "Canyon Trail"]))
    runs_before = len(list(client.app.state.settings.runs_dir.glob("*.json")))
    body = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1}).json()
    assert body["ok"] is False
    assert body["message"] == ("The route no longer follows the same trails in the newer data (missing: Canyon Trail). "
                               "Your trip is unchanged.")
    assert len(list(client.app.state.settings.runs_dir.glob("*.json"))) == runs_before


def test_update_bad_candidate(planned):
    client, _, run = planned
    assert client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 9}).status_code == 404
    assert client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": "1"}).status_code == 400
    assert client.get("/api/runs/../update", params={"candidate": 1}).status_code == 404


# --- a refreshed index: areas opened before it get a new cut ------------------------------------------------

NEWER = "2026-11-20"


def own_osm_dir(tmp_path, index_path):
    """An index folder of the test's own (the shared fixture index stays untouched), holding the first index."""
    import shutil

    d = tmp_path / "osm"
    d.mkdir()
    shutil.copy(index_path, d / index_path.name)
    return d


def refresh_index(osm_dir):
    from backcountry import osm_index
    from conftest import FIXTURES

    osm_index.build_index(FIXTURES / "mini.osm", osm_dir / f"us-{NEWER}.sqlite", NEWER)


def area_cuts(client, area_id):
    from backcountry import ingest
    from backcountry.region import load_region

    s = client.app.state.settings
    return ingest.cuts(load_region(area_id, s.regions_dir, s.areas), s.raw_dir)


def test_refresh_gives_an_opened_area_a_new_cut_on_next_open(tmp_path, index_path):
    d = own_osm_dir(tmp_path, index_path)
    client, _ = make_client(tmp_path, index_path, osm_dir=d, per_hour=1)
    a = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    assert area_cuts(client, a) == [MINI_DATE]
    refresh_index(d)
    r = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    assert r["status"] == "queued"  # not counted against the hourly cap (per_hour=1 is used up)
    run_worker(client)
    assert area_cuts(client, a) == [MINI_DATE, NEWER]
    assert client.post("/api/areas", json={"ref": "relation/3003"}).json()["status"] == "ready"


def test_refresh_with_a_full_queue_opens_on_the_older_data(tmp_path, index_path):
    d = own_osm_dir(tmp_path, index_path)
    client, _ = make_client(tmp_path, index_path, osm_dir=d, max_queue=1)
    client.post("/api/areas", json={"ref": "relation/3003"})
    run_worker(client)
    refresh_index(d)
    assert client.post("/api/areas", json={"box": [-149.95, 10.6, -149.9, 10.65]}).json()["status"] == "queued"
    assert client.post("/api/areas", json={"ref": "relation/3003"}).json()["status"] == "ready"


def test_update_offer_after_refresh_without_reopening_the_area(tmp_path, index_path):
    d = own_osm_dir(tmp_path, index_path)
    client, _ = make_client(tmp_path, index_path, osm_dir=d)
    a = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    ths = client.get(f"/api/regions/{a}/trailheads").json()["trailheads"]
    th = next(t for t in ths if "osm_trailhead" in (t.get("kinds") or []))
    run = client.post(f"/api/regions/{a}/generate", json={"trailhead": th["id"], "total": "20-32", "nights": 1,
                                                          "daily": "8-20", "type": "loop"}).json()
    refresh_index(d)
    check = client.get(f"/api/runs/{run['run_id']}/update", params={"candidate": 1}).json()
    assert check["newer"] is True and check["latest"] == NEWER
    early = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1}).json()
    assert early["ok"] is False and "still being set up" in early["message"] and "unchanged" in early["message"]
    run_worker(client)  # the check queued the new cut quietly
    done = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1}).json()
    assert done["ok"] is True and done["run"]["data_cut"] == NEWER


def test_capped_park_says_it_is_only_part(tmp_path, index_path, monkeypatch):
    from backcountry import areas

    monkeypatch.setattr(areas, "MAX_AREA_KM2", 10.0)
    client, _ = make_client(tmp_path, index_path)
    r = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    assert r["name"] == "Part of Testpark National Park" and r["message"] == "Finding trails in Part of Testpark National Park…"
    run_worker(client)
    assert client.get(f"/api/areas/{r['area_id']}/status").json()["name"] == "Part of Testpark National Park"
    assert any(a["name"] == "Part of Testpark National Park" for a in client.get("/api/regions").json()["recent_areas"])


# --- two steps: trails first, elevation behind them --------------------------------------------


def test_area_shows_trails_before_elevation(tmp_path, index_path):
    seen = {}

    def dem(region, bounds, **kw):
        st = client.get(f"/api/areas/{region.id}/status").json()
        seen["status"], seen["message"] = st["status"], st["message"]
        seen["trails"] = client.get(f"/api/regions/{region.id}/trails").status_code
        seen["trailheads"] = client.get(f"/api/regions/{region.id}/trailheads").status_code

    client, _ = make_client(tmp_path, index_path, dem=dem)
    area_id = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    assert seen == {"status": "trails", "message": "Adding elevation…", "trails": 200, "trailheads": 200}
    assert client.get(f"/api/areas/{area_id}/status").json()["status"] == "ready"


def test_cut_without_elevation_is_not_ready(tmp_path, index_path):
    fail = FakeDem(fail=True)
    client, _ = make_client(tmp_path, index_path, dem=fail)
    area_id = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    assert client.get(f"/api/areas/{area_id}/status").json()["status"] == "failed"
    cut_files = sorted((tmp_path / "raw" / area_id).glob("extract_*.json"))
    assert cut_files  # the trails were kept
    before = {p.name: p.stat().st_mtime_ns for p in cut_files}
    fail.fail = False
    again = client.post("/api/areas", json={"ref": "relation/3003"}).json()
    assert again["status"] == "trails" and again["message"] == "Adding elevation…"
    run_worker(client)
    assert client.get(f"/api/areas/{area_id}/status").json()["status"] == "ready"
    assert {p.name: p.stat().st_mtime_ns for p in cut_files} == before  # elevation only: the cut wasn't redone
    assert fail.calls == [area_id, area_id]


def test_ready_area_is_reloaded_with_its_elevation(tmp_path, index_path):
    client, _ = make_client(tmp_path, index_path)
    area_id = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    forgotten = []
    store = client.app.state.regions
    orig = store.forget
    store.forget = lambda rid: (forgotten.append(rid), orig(rid))
    run_worker(client)
    assert forgotten == [area_id]


def test_user_areas_never_evict_prepared_ones(tmp_path, index_path):
    from backcountry import areas as areas_mod

    client, _ = make_client(tmp_path, index_path)
    park = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    wild = client.post("/api/areas", json={"ref": "relation/3004"}).json()["area_id"]
    run_worker(client)
    areas_mod.write_prepared(tmp_path / "areas", [park])
    client.app.state.areas.limits["disk_gb"] = 0.0
    client.post("/api/areas", json={"box": [-149.75, 10.75, -149.65, 10.85]})
    assert (tmp_path / "areas" / f"{park}.yaml").exists() and (tmp_path / "raw" / park).exists()
    assert not (tmp_path / "raw" / wild).exists()


# --- destinations -------------------------------------------------------------------------


def test_trailheads_sorted_by_distance_to_destination(api):
    client, _ = api
    area_id = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    ths = client.get(f"/api/regions/{area_id}/trailheads", params={"destination": "node/1060"}).json()["trailheads"]
    reach = [t["to_destination_m"] for t in ths]
    known = [r for r in reach if r is not None]
    assert known and known == sorted(known) and reach[:len(known)] == known  # reachable first, nearest first
    plain = client.get(f"/api/regions/{area_id}/trailheads").json()["trailheads"]
    assert all("to_destination_m" not in t for t in plain)


def test_generate_with_a_destination(api):
    client, _ = api
    area_id = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    body = {"trailhead": "10.2495,-149.8505", "total": "18-24", "type": "out_and_back", "destination": "node/1060"}
    run = client.post(f"/api/regions/{area_id}/generate", json=body).json()
    assert run["search"]["destination"]["name"] == "Spur Top"
    assert run["request"]["destination"] == "node/1060" and run["candidates"]


@pytest.mark.parametrize("bad", ["../x", "node/1;ls", "http://x"])
def test_trailheads_destination_is_validated(api, bad):
    client, _ = api
    area_id = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    assert client.get(f"/api/regions/{area_id}/trailheads", params={"destination": bad}).status_code == 400


def test_old_saved_run_without_new_fields_opens_and_updates(planned):
    from conftest import add_cut

    client, a, run = planned
    path = client.app.state.settings.runs_dir / f"{run['run_id']}.json"
    old = json.loads(path.read_text())
    for key in ("destination", "finish"):
        old["request"].pop(key, None)
    for c in old["candidates"]:
        c.pop("finish", None), c.pop("turnaround", None)
    path.write_text(json.dumps(old))
    assert client.get(f"/api/runs/{run['run_id']}").status_code == 200
    assert client.get(f"/api/runs/{run['run_id']}/candidates/1.gpx").status_code == 200
    add_cut(client.app.state.settings, a, "2026-11-20")
    body = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1}).json()
    assert body["ok"] is True


def test_one_way_trip_updates_to_newer_data(api):
    from conftest import add_cut

    client, _ = api
    a = client.post("/api/areas", json={"ref": "relation/3003"}).json()["area_id"]
    run_worker(client)
    run = client.post(f"/api/regions/{a}/generate", json={"trailhead": "10.2495,-149.8505", "total": "5-12",
                                                          "type": "one_way", "finish": "1029"}).json()
    assert run["candidates"] and run["candidates"][0]["kind"] == "one_way"
    add_cut(client.app.state.settings, a, "2026-11-20")
    body = client.post(f"/api/runs/{run['run_id']}/update", json={"candidate": 1}).json()
    assert body["ok"] is True and body["run"]["candidates"][0]["kind"] == "one_way"
    assert body["run"]["candidates"][0]["finish"]["node"] == 1029
