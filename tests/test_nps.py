"""The National Park Service client (nps.py): a per-park disk cache with TTLs; the key only ever travels in a header."""

import json
import logging
import urllib.error
from pathlib import Path

import pytest

from backcountry import nps
from backcountry.region import BBox, load_region

FIX = Path(__file__).parent / "fixtures" / "nps"


class FakeFetch:
    def __init__(self, pages: dict[str, bytes], fail: dict[str, Exception] | None = None):
        self.pages, self.fail, self.calls = pages, fail or {}, []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        for key, exc in self.fail.items():
            if key in url:
                raise exc
        for key, body in self.pages.items():
            if key in url:
                return body
        raise AssertionError(f"unexpected url {url}")


def _pages():
    return {"/parks?": (FIX / "parks_small.json").read_bytes(), "/alerts?": (FIX / "shen_alerts.json").read_bytes(),
            "/thingstodo?": (FIX / "shen_things.json").read_bytes()}


def _client(tmp_path, fetch, clock, key="k-secret"):
    return nps.NpsClient(tmp_path / "nps", key, fetch=fetch, clock=clock, thread=False)


def test_key_travels_in_the_header_never_in_the_url_or_the_log(tmp_path, caplog):
    fetch = FakeFetch(_pages())
    c = _client(tmp_path, fetch, lambda: 1_000.0)
    with caplog.at_level(logging.DEBUG, logger="backcountry.nps"):
        data = c.refresh("shen")
    assert fetch.calls and all("k-secret" not in url for url, _ in fetch.calls)
    assert all(h.get("X-Api-Key") == "k-secret" for _, h in fetch.calls)
    assert "k-secret" not in caplog.text and "k-secret" not in json.dumps(data)
    assert data["alerts"] and data["things"] and data["fetched_at"] and data["stale"] is False
    assert data["park"]["parkCode"] == "shen"


def test_cache_is_reused_within_ttl_and_refetched_after(tmp_path):
    now = [1_000.0]
    fetch = FakeFetch(_pages())
    c = _client(tmp_path, fetch, lambda: now[0])
    c.refresh("shen")
    n = len(fetch.calls)
    assert c.due("shen") is False and c.refresh("shen") is not None and len(fetch.calls) == n
    now[0] += nps.ALERTS_TTL_S + 1
    assert c.due("shen") is True
    c.refresh("shen")
    new = [u for u, _ in fetch.calls[n:]]
    assert new and all("/alerts?" in u for u in new)  # only the alerts were due


def test_failed_refresh_keeps_the_cache_and_marks_it_stale(tmp_path):
    now = [1_000.0]
    c = _client(tmp_path, FakeFetch(_pages()), lambda: now[0])
    first = c.refresh("shen")
    now[0] += nps.ALERTS_TTL_S + 1
    c.fetch = FakeFetch({}, fail={"/alerts?": urllib.error.HTTPError("u", 429, "Too Many", {}, None)})
    data = c.refresh("shen")
    assert data["alerts"] == first["alerts"] and data["stale"] is True and "429" in data["error"]
    c.fetch = FakeFetch({"/alerts?": b"<html>not json</html>"})
    assert c.refresh("shen")["stale"] is True


def test_oversize_responses_and_bad_park_codes_are_refused(tmp_path):
    def big(url, headers):
        if "/alerts?" in url:
            raise ValueError("response too big")  # what the default fetch raises past MAX_BYTES
        pages = {"/parks?": (FIX / "parks_small.json").read_bytes(), "/thingstodo?": (FIX / "shen_things.json").read_bytes()}
        return pages[next(k for k in pages if k in url)]
    c = _client(tmp_path, big, lambda: 1.0)
    with pytest.raises(ValueError):
        c.cached("../etc")
    d = c.refresh("shen")
    assert d["stale"] is True and d["alerts"] == [] and "too big" in d["error"] and d["things"]
    assert sorted(p.name for p in (tmp_path / "nps").iterdir()) == ["shen"]  # nothing for "../etc"; the park entry is per park


def test_unconfigured_client_reads_the_cache_and_never_fetches(tmp_path):
    fetch = FakeFetch({})
    c = nps.NpsClient(tmp_path / "nps", None, fetch=fetch, clock=lambda: 1.0, thread=False)
    assert c.configured is False and c.cached("shen") is None and c.refresh("shen") is None and fetch.calls == []


def test_pagination_follows_total(tmp_path):
    page1 = json.dumps({"total": "60", "limit": "50", "start": "0", "data": [{"id": str(i)} for i in range(50)]}).encode()
    page2 = json.dumps({"total": "60", "limit": "50", "start": "50", "data": [{"id": str(i)} for i in range(50, 60)]}).encode()
    fetch = FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes(), "start=50": page2, "/thingstodo?": page1,
                       "/alerts?": b'{"total":"0","data":[]}'})
    c = _client(tmp_path, fetch, lambda: 1.0)
    assert len(c.refresh("shen")["things"]) == 60


def _region(name, area, bbox=None):
    return type("R", (), {"name": name, "area": area, "bbox": bbox})()


def test_park_for_matches_by_name_and_group_members(tmp_path):
    c = _client(tmp_path, FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes()}), lambda: 1.0, key="k")
    assert c.park_for(_region("Zion National Park", {"name": "Zion National Park", "osm": None})) == "zion"
    group = _region("Zion (park and nearby wilderness)",
                    {"name": "Zion", "osm": None, "members": [["relation/1", "Zion Wilderness"], ["relation/2", "Zion National Park"]]})
    assert c.park_for(group) == "zion"
    assert c.park_for(_region("Pine Hill Wilderness", {"name": "Pine Hill Wilderness", "osm": None})) is None


def test_park_for_a_hand_made_region_uses_its_bbox(tmp_path):
    c = _client(tmp_path, FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes()}), lambda: 1.0)
    assert c.park_for(load_region("dolly_sods")) is None  # no NPS unit point inside Dolly Sods' box
    fake = _region("Somewhere", None, BBox(south=37.0, west=-113.3, north=37.6, east=-112.8))
    assert c.park_for(fake) == "zion"  # Zion's point is inside


def test_park_name_and_park_of_never_wait_on_the_network(tmp_path):
    """The parks list is fetched in the background, never in a request: with no cache and a slow network, park_name is
    None at once and park_of says pending at once (the fetch it kicks off runs on its own thread)."""
    import threading
    import time as _time

    started = threading.Event()

    def slow_fetch(url, headers):
        started.set()
        _time.sleep(1.5)
        raise ValueError("slow network")
    c = nps.NpsClient(tmp_path / "nps", "k", fetch=slow_fetch, clock=_time.time, thread=True)
    loaded = type("L", (), {"region": _region("Zion National Park", {"name": "Zion National Park", "osm": None}), "park_code": None,
                            "park_mapped": False})()
    t0 = _time.perf_counter()
    assert c.park_name("zion") is None
    assert c.park_of(loaded) == {"code": None, "configured": True, "data": None, "pending": True}
    assert _time.perf_counter() - t0 < 0.5
    assert started.wait(2.0)  # the fetch did start, off the request thread


def test_a_failed_parks_fetch_leaves_the_area_unmapped_until_it_succeeds(tmp_path):
    """The first attach fails to get the parks list (a rate limit): the area stays pending, not 'no feed', and a
    later attach maps it once the list arrives."""
    import urllib.error

    now = [1_000.0]
    fetch = FakeFetch({}, fail={"/parks?": urllib.error.HTTPError("u", 429, "Too Many", {}, None)})
    c = _client(tmp_path, fetch, lambda: now[0])
    loaded = type("L", (), {"region": _region("Zion National Park", {"name": "Zion National Park", "osm": None}), "park_code": None,
                            "park_mapped": False})()
    c.attach(loaded)
    park = c.park_of(loaded)
    assert loaded.park_mapped is False and park["pending"] is False and "429" in park["error"]  # unreachable, said so, not "no feed"
    now[0] += nps.PARKS_RETRY_S + 1
    c.fetch = FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes(), "/alerts?": (FIX / "zion_alerts.json").read_bytes(),
                         "/thingstodo?": (FIX / "zion_things.json").read_bytes()})
    c.attach(loaded)
    assert loaded.park_mapped is True and loaded.park_code == "zion"
    assert c.park_of(loaded)["pending"] is False and c.park_of(loaded)["data"]["alerts"]


def test_a_failed_refresh_is_visible_from_the_cache_and_not_retried_at_once(tmp_path):
    now = [1_000.0]
    c = _client(tmp_path, FakeFetch(_pages()), lambda: now[0])
    c.refresh("shen")
    now[0] += nps.ALERTS_TTL_S + 1
    c.fetch = FakeFetch({}, fail={"/alerts?": urllib.error.HTTPError("u", 429, "Too Many", {}, None)})
    c.refresh("shen")
    cached = c.cached("shen")
    assert cached["stale"] is True and "429" in cached["error"] and cached["alerts"]  # the old alerts stay, dated
    assert c.due("shen") is False  # a failure a moment ago: no retry on the next request
    now[0] += nps.PARKS_RETRY_S + 1
    assert c.due("shen") is True
    c.fetch = FakeFetch(_pages())
    c.refresh("shen")
    assert c.cached("shen")["stale"] is False and c.cached("shen")["error"] is None


def test_park_of_reports_an_unreachable_service_instead_of_pending_forever(tmp_path):
    now = [1_000.0]
    c = _client(tmp_path, FakeFetch({}, fail={"/parks?": urllib.error.HTTPError("u", 429, "Too Many", {}, None)}), lambda: now[0])
    loaded = type("L", (), {"region": _region("Zion National Park", {"name": "Zion National Park", "osm": None}), "park_code": None,
                            "park_mapped": False})()
    c.attach(loaded)
    park = c.park_of(loaded)
    assert park["pending"] is False and park["code"] is None and "429" in park["error"] and park["tried_at"]
    block, pending = nps_notices_block(c, loaded)
    assert pending is False and block[0]["text"].startswith("Couldn't reach the National Park Service")


def nps_notices_block(client, loaded):
    from backcountry.notices import assemble

    return assemble([], client.park_of(loaded), [])


def test_default_fetch_does_not_follow_redirects(tmp_path):
    """A redirect off developer.nps.gov would carry the key to another host: 3xx is a failure, not a follow."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/go"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/leak")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"leaked": true}')

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError):
            nps.default_fetch(f"http://127.0.0.1:{srv.server_port}/go", {"X-Api-Key": "k"})
    finally:
        srv.shutdown()
