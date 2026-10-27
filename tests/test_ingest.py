import json

import pytest

from backcountry import ingest
from backcountry.region import BBox, Region

REGION = Region(id="testland", name="Test", crs="EPSG:32617", bbox=BBox(1.0, 2.0, 3.0, 4.0), boundary_name_regex="Test")


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"highway": "path"}, "trail"),
        ({"highway": "footway"}, "trail"),
        ({"highway": "track"}, "road"),  # user decision 2026-09-29: tracks are roads
        ({"highway": "unclassified"}, "road"),
        ({"highway": "service"}, "road"),
        ({"highway": "tertiary"}, "road"),  # routable, but last resort
        ({"highway": "residential"}, None),
        ({"building": "yes"}, None),
    ],
)
def test_way_class(tags, expected):
    assert ingest.way_class(tags) == expected


@pytest.mark.parametrize(
    "tags, reason",
    [
        ({"highway": "path"}, None),
        ({"highway": "path", "access": "no"}, "access=no"),
        ({"highway": "service", "access": "private"}, "access=private"),
        ({"highway": "path", "foot": "no"}, "foot=no"),
        ({"highway": "path", "foot": "private"}, "foot=private"),
        # OSM semantics: a specific foot=* tag overrides the general access=* tag.
        ({"highway": "path", "access": "no", "foot": "designated"}, None),
        ({"highway": "track", "access": "private", "foot": "yes"}, None),
        ({"highway": "track", "access": "private", "foot": "permissive"}, None),
    ],
)
def test_access_exclusion_reason(tags, reason):
    assert ingest.access_exclusion_reason(tags) == reason


def test_foot_overrides_access_is_detected():
    assert ingest.foot_overrides_access({"access": "no", "foot": "designated"})
    assert not ingest.foot_overrides_access({"foot": "designated"})
    assert not ingest.foot_overrides_access({"access": "no"})


def test_query_covers_bbox_and_needed_features():
    q = ingest.build_query(REGION)
    assert "[out:json]" in q
    assert "(1.0,2.0,3.0,4.0)" in q
    assert 'way["highway"]' in q
    assert 'node["highway"="trailhead"]' in q
    assert '"amenity"="parking"' in q
    assert 'tourism' in q and 'camp_site' in q and 'camp_pitch' in q  # M5 lists nearby OSM camp sites
    assert '"name"~"Test"' in q
    assert "out meta geom" in q  # meta: edit timestamp/version, used to judge how well-established a feature is


def _fake_fetcher(calls):
    def fetch(query):
        calls.append(query)
        return {"osm3s": {"timestamp_osm_base": "2026-01-01T00:00:00Z"}, "elements": []}

    return fetch


def test_fetch_caches_with_retrieved_at_and_does_not_requery(tmp_path):
    calls = []
    path = ingest.fetch(REGION, raw_dir=tmp_path, fetcher=_fake_fetcher(calls))
    doc = json.loads(path.read_text())
    assert doc["retrieved_at"].endswith("Z")
    assert doc["region"] == "testland"
    assert doc["query"] == calls[0]
    assert doc["response"]["elements"] == []

    again = ingest.fetch(REGION, raw_dir=tmp_path, fetcher=_fake_fetcher(calls))
    assert again == path
    assert len(calls) == 1


def test_fetch_refresh_requeries(tmp_path):
    calls = []
    ingest.fetch(REGION, raw_dir=tmp_path, fetcher=_fake_fetcher(calls))
    ingest.fetch(REGION, raw_dir=tmp_path, fetcher=_fake_fetcher(calls), refresh=True)
    assert len(calls) == 2


def test_load_raw_without_cache_explains_how_to_fetch(tmp_path):
    with pytest.raises(FileNotFoundError, match="backcountry ingest"):
        ingest.load_raw(REGION, raw_dir=tmp_path)


def _fake_usfs(calls, exceeded=False):
    def fetch(url):
        calls.append(url)
        fc = {"type": "FeatureCollection", "features": []}
        if exceeded:
            fc["properties"] = {"exceededTransferLimit": True}
        return fc

    return fetch


def test_usfs_query_url_uses_bbox_envelope_and_geojson():
    url = ingest.usfs_query_url("EDW_X/MapServer/0", "a,b", REGION.bbox)
    assert url.startswith(ingest.USFS_BASE + "/EDW_X/MapServer/0/query?")
    assert "geometry=2.0%2C1.0%2C4.0%2C3.0" in url  # west,south,east,north
    assert "f=geojson" in url and "outSR=4326" in url and "outFields=a%2Cb" in url


def test_fetch_usfs_caches_all_layers_once(tmp_path):
    calls = []
    path = ingest.fetch_usfs(REGION, raw_dir=tmp_path, fetcher=_fake_usfs(calls))
    doc = json.loads(path.read_text())
    assert set(doc["layers"]) == set(ingest.USFS_LAYERS)
    assert doc["retrieved_at"].endswith("Z") and "public domain" in doc["attribution"]
    assert ingest.fetch_usfs(REGION, raw_dir=tmp_path, fetcher=_fake_usfs(calls)) == path
    assert len(calls) == len(ingest.USFS_LAYERS)
    assert ingest.load_usfs(REGION, raw_dir=tmp_path)["retrieved_at"] == doc["retrieved_at"]


def test_fetch_usfs_refuses_truncated_results(tmp_path):
    with pytest.raises(RuntimeError, match="truncated"):
        ingest.fetch_usfs(REGION, raw_dir=tmp_path, fetcher=_fake_usfs([], exceeded=True))


def test_load_usfs_is_none_without_cache(tmp_path):
    assert ingest.load_usfs(REGION, raw_dir=tmp_path) is None


@pytest.mark.parametrize(
    "highway, last_resort",
    [("tertiary", True), ("unclassified", False), ("service", False), ("track", False), ("path", False)],
)
def test_last_resort_roads(highway, last_resort):
    assert ingest.is_last_resort({"highway": highway}) is last_resort


# --- DEM (USGS 3DEP) -------------------------------------------------------------


def _item(title, date, url):
    return {"title": title, "publicationDate": date, "downloadURL": url, "sourceId": title}


ITEMS_13 = [
    _item("USGS 1/3 Arc Second n39w080 20120801", "2012-08-01", "u/n39_2012.tif"),
    _item("USGS 1/3 Arc Second n39w080 20230816", "2023-08-16", "u/n39_2023.tif"),
    _item("USGS 1/3 Arc Second n40w080 20260512", "2026-05-12", "u/n40_2026.tif"),
    _item("USGS 1/3 Arc Second n40w080 20230816", "2023-08-16", "u/n40_2023.tif"),
    _item("USGS 13 arc-second n39w080 1 x 1 degree", "2018-12-19", "u/odd.tif"),
]


def test_select_13_tiles_takes_newest_per_tile():
    assert [i["downloadURL"] for i in ingest.select_dem_items("13", ITEMS_13)] == ["u/n39_2023.tif", "u/n40_2026.tif"]


def test_select_1m_orders_newest_first():
    items = [_item("USGS 1 Meter 17 x63y432 A", "2021-01-06", "a"), _item("USGS 1 Meter 17 x63y432 B", "2023-12-01", "b")]
    assert [i["downloadURL"] for i in ingest.select_dem_items("1m", items)] == ["b", "a"]


def _fake_dem_io(calls):
    import numpy as np
    from rasterio.transform import from_origin

    def searcher(dataset, bounds):
        calls.append(("search", dataset, bounds))
        return ITEMS_13

    def reader(urls, bounds):
        calls.append(("read", tuple(urls)))
        arr = np.full((4, 5), 1000.0, dtype="float32")
        arr[0, 0] = np.nan
        return arr, from_origin(-80.0, 40.0, 0.01, 0.01), "EPSG:4269"

    return searcher, reader


def test_fetch_dem_caches_with_provenance(tmp_path):
    import math

    calls = []
    searcher, reader = _fake_dem_io(calls)
    bounds = (-79.99, 39.95, -79.96, 39.97)
    path = ingest.fetch_dem(REGION, bounds, raw_dir=tmp_path, searcher=searcher, reader=reader)
    meta = json.loads(path.read_text())
    assert meta["retrieved_at"].endswith("Z") and "public domain" in meta["attribution"]
    assert [s["url"] for s in meta["sources"]] == ["u/n39_2023.tif", "u/n40_2026.tif"]
    assert ("read", ("u/n39_2023.tif", "u/n40_2026.tif")) in calls

    dem = ingest.load_dem(REGION, raw_dir=tmp_path)
    assert dem.crs == "EPSG:4269" and dem.array.shape == (4, 5)
    assert math.isnan(dem.array[0, 0]) and dem.array[1, 1] == 1000.0
    assert dem.source["sources"][0]["url"] == "u/n39_2023.tif"

    # Cached: covered bounds don't trigger another search or read.
    assert ingest.fetch_dem(REGION, bounds, raw_dir=tmp_path, searcher=searcher, reader=reader) == path
    assert sum(1 for c in calls if c[0] == "read") == 1


def test_fetch_dem_refetches_when_cache_does_not_cover_bounds(tmp_path):
    calls = []
    searcher, reader = _fake_dem_io(calls)
    ingest.fetch_dem(REGION, (-79.99, 39.95, -79.96, 39.97), raw_dir=tmp_path, searcher=searcher, reader=reader)
    ingest.fetch_dem(REGION, (-79.99, 39.95, -79.90, 39.97), raw_dir=tmp_path, searcher=searcher, reader=reader)
    assert sum(1 for c in calls if c[0] == "read") == 2


def test_load_dem_none_without_cache(tmp_path):
    assert ingest.load_dem(REGION, raw_dir=tmp_path) is None


def test_camp_sites_from_raw_use_centers_and_sources():
    raw = {"response": {"elements": [
        {"type": "node", "id": 1, "lat": 39.0, "lon": -79.0, "tags": {"tourism": "camp_site", "name": "A Camp"}},
        {"type": "way", "id": 2, "nodes": [1, 2, 3, 1], "tags": {"tourism": "camp_pitch"},
         "geometry": [{"lat": 39.0, "lon": -79.0}, {"lat": 39.0, "lon": -79.002}, {"lat": 39.002, "lon": -79.002},
                      {"lat": 39.0, "lon": -79.0}]},
        {"type": "node", "id": 3, "lat": 39.1, "lon": -79.1, "tags": {"amenity": "parking"}},
    ]}}
    raw["response"]["elements"][0].update(timestamp="2025-08-01T10:00:00Z", version=1, changeset=99)
    sites = ingest.camp_sites(raw)
    assert sites[0]["meta"] == {"timestamp": "2025-08-01T10:00:00Z", "version": 1}
    assert sites[0]["url"] == "https://www.openstreetmap.org/node/1"
    assert sites[1]["meta"] == {"timestamp": None, "version": None}
    assert [s["source"] for s in sites] == ["osm:node/1", "osm:way/2"]
    assert sites[0]["name"] == "A Camp" and sites[1]["name"] is None
    assert sites[1]["lat"] == pytest.approx(39.000667, abs=1e-5) and sites[1]["lon"] == pytest.approx(-79.001333, abs=1e-5)
    assert sites[0]["kind"] == "camp_site" and sites[1]["kind"] == "camp_pitch"


def test_query_includes_water_features():
    q = ingest.build_query(REGION)
    assert "spring" in q and "waterway" in q and "drinking_water" in q


def test_water_features_from_raw():
    raw = {"response": {"elements": [
        {"type": "node", "id": 1, "lat": 39.0, "lon": -79.0, "tags": {"natural": "spring", "name": "Cold Spring"}},
        {"type": "node", "id": 2, "lat": 39.0, "lon": -79.0, "tags": {"amenity": "drinking_water"}},
        {"type": "way", "id": 3, "nodes": [1, 2], "tags": {"waterway": "stream", "name": "Red Creek"},
         "geometry": [{"lat": 39.0, "lon": -79.0}, {"lat": 39.1, "lon": -79.1}]},
        {"type": "way", "id": 4, "nodes": [1, 2, 3, 1], "tags": {"natural": "water", "water": "pond"},
         "geometry": [{"lat": 39.0, "lon": -79.0}, {"lat": 39.0, "lon": -79.002}, {"lat": 39.002, "lon": -79.002},
                      {"lat": 39.0, "lon": -79.0}]},
        {"type": "way", "id": 5, "nodes": [1, 2], "tags": {"highway": "path"}, "geometry": []},
    ]}}
    w = ingest.water_features(raw)
    assert [p["source"] for p in w["points"]] == ["osm:node/1", "osm:node/2", "osm:way/4"]
    assert [p["kind"] for p in w["points"]] == ["spring", "drinking_water", "water"]
    assert w["points"][0]["name"] == "Cold Spring" and w["points"][2]["name"] is None
    assert [l["source"] for l in w["lines"]] == ["osm:way/3"]
    assert w["lines"][0]["kind"] == "stream" and w["lines"][0]["name"] == "Red Creek"


def test_fetch_retries_once_on_server_timeout_then_explains(tmp_path, monkeypatch):
    import urllib.error

    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    calls = []

    def flaky(query):
        calls.append(query)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://overpass", 504, "Gateway Timeout", {}, None)
        return {"osm3s": {"timestamp_osm_base": "2026-01-01T00:00:00Z"}, "elements": []}

    path = ingest.fetch(REGION, raw_dir=tmp_path, fetcher=flaky)
    assert path.exists() and len(calls) == 2

    def dead(query):
        raise urllib.error.HTTPError("https://overpass", 504, "Gateway Timeout", {}, None)

    with pytest.raises(ingest.FetchError, match="504.*Overpass"):
        ingest.fetch(REGION, raw_dir=tmp_path, fetcher=dead, refresh=True)


def test_raw_dir_can_come_from_the_environment(tmp_path):
    # scripts/heavy-tests points a git worktree (which has no cached data) at the main checkout's data this way.
    import os
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", "from backcountry import ingest; print(ingest.RAW_DIR)"],
                         env={**os.environ, "BACKCOUNTRY_RAW_DIR": str(tmp_path)},
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == str(tmp_path)


def test_fetch_dem_interrupted_write_leaves_nothing_that_looks_cached(tmp_path, monkeypatch):
    from pathlib import Path

    searcher, reader = _fake_dem_io([])
    real = Path.write_text

    def dies_half_way(self, text, *a, **kw):  # the server is killed while the metadata is being written
        real(self, text[: len(text) // 2], *a, **kw)
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "write_text", dies_half_way)
    with pytest.raises(KeyboardInterrupt):
        ingest.fetch_dem(REGION, (-79.99, 39.95, -79.96, 39.97), raw_dir=tmp_path, searcher=searcher, reader=reader)
    monkeypatch.undo()
    assert ingest.load_dem(REGION, raw_dir=tmp_path) is None  # no truncated dem13_*.json to trip over
    assert not list((tmp_path / REGION.id).glob("*.tif"))  # nor a raster without its metadata


def test_fetch_dem_reads_cells_in_parallel_and_keeps_provenance(tmp_path, monkeypatch):
    import math
    import threading
    import time as _time

    import numpy as np
    from rasterio.transform import from_origin

    calls, threads = [], set()

    def searcher(dataset, bounds):
        return ITEMS_13

    def reader(urls, bounds):
        threads.add(threading.get_ident())
        _time.sleep(0.05)  # long enough that the pool overlaps reads
        w, s, e, n = bounds
        calls.append(bounds)
        rows, cols = round((n - s) / 0.01), round((e - w) / 0.01)
        return np.full((rows, cols), 500.0, dtype="float32"), from_origin(w, n, 0.01, 0.01), "EPSG:4269"

    cells = [(-80.0, 39.0, -79.95, 39.05), (-79.95, 39.0, -79.9, 39.05), (-80.0, 39.1, -79.95, 39.15),
             (-79.9, 39.1, -79.85, 39.15)]
    path = ingest.fetch_dem(REGION, (-80.0, 39.0, -79.85, 39.15), raw_dir=tmp_path, cells=cells, searcher=searcher,
                            reader=reader)
    assert sorted(calls) == sorted(cells) and len(threads) > 1
    meta = json.loads(path.read_text())
    assert meta["cells"] == 4 and "public domain" in meta["attribution"]
    assert [s["url"] for s in meta["sources"]] == ["u/n39_2023.tif", "u/n40_2026.tif"]
    dem = ingest.load_dem(REGION, raw_dir=tmp_path)
    assert dem.array.shape == (15, 15)
    assert dem.array[0, 0] == 500.0  # top-left: inside the cell (-80.0, 39.1, -79.95, 39.15)
    assert math.isnan(dem.array[7, 7])  # (-79.93, 39.08): in no cell
    # a later fetch for the same trails is cached; one needing a new cell isn't
    assert ingest.fetch_dem(REGION, (-80.0, 39.0, -79.85, 39.15), raw_dir=tmp_path, cells=cells, searcher=searcher,
                            reader=reader) == path
    ingest.fetch_dem(REGION, (-80.0, 39.0, -79.85, 39.15), raw_dir=tmp_path, cells=cells + [(-79.9, 39.0, -79.85, 39.05)],
                     searcher=searcher, reader=reader)
    assert len(calls) == 4 + 5


def test_each_cell_reads_only_the_tiles_that_hold_it():
    items = [_item("USGS 1/3 Arc Second n39w080 20230101", "2023-01-01", "u/n39w080.tif"),
             _item("USGS 1/3 Arc Second n40w080 20230101", "2023-01-01", "u/n40w080.tif")]
    assert ingest._urls_for(items, "13", (-79.5, 38.5, -79.45, 38.55)) == ["u/n39w080.tif"]
    assert ingest._urls_for(items, "13", (-79.5, 39.5, -79.45, 39.55)) == ["u/n40w080.tif"]
    assert ingest._urls_for(items, "13", (-79.5, 38.98, -79.45, 39.03)) == ["u/n39w080.tif", "u/n40w080.tif"]
    assert ingest._urls_for([_item("odd", "2023-01-01", "u/x.tif")], "13", (0, 0, 1, 1)) == ["u/x.tif"]
