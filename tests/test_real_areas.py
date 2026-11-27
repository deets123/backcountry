"""Real-data checks for any US area (heavy): need the local US index (`backcountry usdata download` + `build`),
network access to USGS for elevation the first time, and they keep what they prepare in data/raw and data/areas.
Skipped when no index is built (e.g. on GitHub)."""

import time

import pytest
import yaml
from pyproj import Geod

from backcountry import areas, ingest, osm_index, usdata
from backcountry.calibration import CALIBRATION_DIR, LOCAL_TRACKS, rate_reference_trip
from backcountry.graph import build_graph, qa_summary
from backcountry.region import AREAS_DIR, load_region

INDEX = usdata.current_index()
pytestmark = pytest.mark.skipif(INDEX is None, reason="no local US index (backcountry usdata download/build)")


@pytest.fixture(scope="module")
def con():
    c = osm_index.open_index(INDEX)
    yield c
    c.close()


def prepared(con, query, want=None):
    hits = osm_index.search(con, query)
    hit = next(h for h in hits if want is None or want(h))
    spec = areas.area_from_ref(con, hit["osm"])
    t0 = time.monotonic()
    out = areas.prepare_area(spec, con)
    region = load_region(spec.id)
    raw = ingest.load_raw(region)
    return spec, region, raw, build_graph(raw, region.crs), time.monotonic() - t0, out


def names(tg) -> set[str]:
    return {d["name"] for *_, d in tg.graph.edges(data=True) if d.get("name")}


def test_zion_whole_park(con):
    first = osm_index.search(con, "Zion")[0]
    assert first["name"] == "Zion National Park" and first["type_label"] == "National Park"
    spec, region, raw, tg, secs, out = prepared(con, "Zion National Park", lambda h: h["kind"] == "area")
    print(f"\nZion: {out}, {secs:.1f} s, capped={spec.capped}")
    assert not spec.capped
    found = names(tg)
    west_rim = {n for n in found if "West Rim" in n}
    angels = {n for n in found if "Angels Landing" in n}
    assert west_rim and angels, sorted(found)[:40]
    comp = qa_summary(tg)["node_component"]
    edge_comp = lambda ns: {comp[u] for u, v, d in tg.graph.edges(data=True) if d.get("name") in ns}  # noqa: E731
    assert edge_comp(west_rim) & edge_comp(angels), "the West Rim and Angels Landing should be on one trail network"


def test_zion_reference_gpx_info(con):
    path = LOCAL_TRACKS / "zion_west_rim_top_down.gpx"
    if not path.exists():
        pytest.skip("the user's Zion GPX is local only")
    spec, *_ = prepared(con, "Zion National Park", lambda h: h["kind"] == "area")
    case = next(c for c in yaml.safe_load((CALIBRATION_DIR / "cases.yaml").read_text()) if c["id"] == "zion_west_rim")
    whole = rate_reference_trip({**case, "region": spec.id}, regions_dir=AREAS_DIR)
    calib = rate_reference_trip(case)
    print(f"\nZion GPX on the whole park: {whole['got']} {whole['days']}\n  on the calibration area: {calib['got']} {calib['days']}")


def test_chesler_park(con):
    place = osm_index.search(con, "Chesler Park")[0]
    spec, region, raw, tg, secs, out = prepared(con, "Chesler Park")
    print(f"\nChesler Park ({place['type_label']}) opened {spec.name} ({spec.type_label}), capped={spec.capped}: {out}, {secs:.1f} s")
    assert "Canyonlands" in spec.name or "Needles" in spec.name
    assert any("Chesler" in n for n in names(tg)), sorted(names(tg))[:40]


def test_new_area_timing(con):
    spec, region, raw, tg, first, out = prepared(con, "Linville Gorge", lambda h: h["kind"] == "area")
    t0 = time.monotonic()
    again = areas.prepare_area(spec, con)
    build_graph(ingest.load_raw(region), region.crs)
    second = time.monotonic() - t0
    print(f"\nLinville Gorge: first {first:.1f} s ({out}), again {second:.1f} s ({again})")
    assert again["already"] is True


def test_osm_lengths_consistent(con):
    *_, tg, _, _ = prepared(con, "Zion National Park", lambda h: h["kind"] == "area")
    geod = Geod(ellps="WGS84")
    for *_, d in tg.graph.edges(data=True):
        lon, lat = zip(*d["geom_wgs84"].coords)
        true = geod.line_length(lon, lat)
        assert d["length_m"] == pytest.approx(true, rel=2e-3, abs=0.5), d["source"]
