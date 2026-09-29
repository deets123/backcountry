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
        ({"highway": "track"}, "trail"),
        ({"highway": "unclassified"}, "road"),
        ({"highway": "service"}, "road"),
        ({"highway": "tertiary"}, None),
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
