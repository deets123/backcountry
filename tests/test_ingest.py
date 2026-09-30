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
    assert '"name"~"Test"' in q
    assert "out body geom" in q


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
