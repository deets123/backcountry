"""Real-data checks for any US area (heavy): need the local US index (`backcountry usdata download` + `build`),
network access to USGS for elevation the first time, and they keep what they prepare in data/raw and data/areas.
Skipped when no index is built (e.g. on GitHub)."""

import math
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


# --- destinations, trip shapes and speed (acceptance, 2026-10-27) -------------------------------------------

M_PER_MI = 1609.344


def start_node(tg, raw, name: str) -> int:
    from backcountry.trailheads import find_candidates

    comp = qa_summary(tg)["node_component"]
    hits = [c for c in find_candidates(tg, raw, comp)["candidates"] if any(name.lower() in n.lower() for n in c["names"])]
    assert hits, f"no start named like {name!r}"
    return hits[0]["id"]


def local_track(name: str):
    """The user's own track (an AllTrails export, git-ignored): read in place, never copied."""
    import subprocess
    from pathlib import Path

    here = LOCAL_TRACKS / name
    if here.exists():
        return here
    top = subprocess.run(["git", "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout.split()
    main = Path(top[1]) / "tests" / "reference" / "local" / name if len(top) > 1 else None
    return main if main and main.exists() else None


def zion(con):
    return prepared(con, "Zion National Park", lambda h: h["kind"] == "area" and h["name"] == "Zion National Park")


def test_angels_landing_day_hike(con):
    from backcountry.planning import GenerateRequest, plan_trip

    spec, region, raw, tg, *_ = zion(con)
    grotto = start_node(tg, raw, "Grotto Trailhead")
    req = GenerateRequest.from_dict({"trailhead": str(grotto), "total": "3-8", "type": "out_and_back",
                                     "destination": "node/356671932"})
    t0 = time.monotonic()
    r = plan_trip(region, raw, tg, req, dem=ingest.load_dem(region), index=con)
    secs = time.monotonic() - t0
    dest = r.report["destination"]
    cands = r.report["candidates"]
    print(f"\nAngels Landing day hike: {secs:.1f} s; {[(round(c.length_m / M_PER_MI, 2), round(c.route.gain_ft or 0)) for c in cands]}"
          f"; offset {dest['offset_m']:.0f} m; notes {r.report['notes']}")
    assert cands and all(c.kind == "out_and_back" for c in cands)
    from backcountry.destination import resolve

    assert all(c.turnaround == resolve(con, tg, "node/356671932").node for c in cands)
    assert (dest["note"] is not None) == (dest["offset_m"] > 400)


def test_angels_landing_two_nights(con):
    from backcountry.planning import GenerateRequest, plan_trip

    spec, region, raw, tg, *_ = zion(con)
    grotto = start_node(tg, raw, "Grotto Trailhead")
    req = GenerateRequest.from_dict({"trailhead": str(grotto), "total": "18-26", "nights": 2, "daily": "6-11",
                                     "destination": "node/356671932"})
    t0 = time.monotonic()
    r = plan_trip(region, raw, tg, req, dem=ingest.load_dem(region), index=con)
    cands = r.report["candidates"]
    print(f"\nAngels Landing, 2 nights from the Grotto: {time.monotonic() - t0:.1f} s; "
          f"{[(c.kind, round(c.length_m / M_PER_MI, 2), [n for n in c.route.notes if 'Side trip' in n]) for c in cands]}")
    assert cands and all(18 * M_PER_MI <= c.length_m <= 26 * M_PER_MI for c in cands)
    assert any(any(n.startswith("Side trip to Angels Landing") for n in c.route.notes) for c in cands)


def test_chesler_park_starts(con):
    from backcountry.destination import resolve, trail_distances
    from backcountry.planning import GenerateRequest, plan_trip

    spec, region, raw, tg, *_ = prepared(con, "Chesler Park")
    dest = resolve(con, tg, "node/2315407768")
    dist = trail_distances(tg, dest)
    from backcountry.trailheads import find_candidates

    starts = find_candidates(tg, raw, qa_summary(tg)["node_component"])["candidates"]
    named = {" | ".join(c["names"]): round(dist[c["id"]] / M_PER_MI, 1) for c in starts if c["id"] in dist and c["names"]}
    near = dict(sorted(named.items(), key=lambda kv: kv[1])[:8])
    print(f"\nChesler Park ({dest.kind}): nearest starts {near}")
    found = [n for n in named if "Elephant Hill" in n or "Squaw Flat" in n]
    assert found, near
    th = next(c["id"] for c in starts if any("Elephant Hill" in n for n in c["names"]) and c["id"] in dist) \
        if any("Elephant Hill" in n for n in found) else next(c["id"] for c in starts if any("Squaw Flat" in n for n in c["names"]))
    r = plan_trip(region, raw, tg, GenerateRequest.from_dict({"trailhead": str(th), "total": "6-14", "destination": "node/2315407768"}),
                  dem=ingest.load_dem(region), index=con)
    print(f"  trips: {[(c.kind, round(c.length_m / M_PER_MI, 2)) for c in r.report['candidates']]} {r.report['notes']}")
    assert any(c.kind in ("loop", "lollipop") for c in r.report["candidates"])


def test_west_rim_one_way(con):
    from pyproj import Transformer
    from shapely.geometry import Point
    from shapely.ops import transform

    from backcountry.export import read_gpx_line
    from backcountry.planning import GenerateRequest, plan_trip, suggest_finishes

    spec, region, raw, tg, *_ = zion(con)
    lava = start_node(tg, raw, "Lava Point Trailhead")
    grotto = start_node(tg, raw, "Grotto Trailhead")
    dem = ingest.load_dem(region)
    comp = qa_summary(tg)["node_component"]
    from backcountry.trailheads import find_candidates

    ths = [{"id": str(c["id"]), "name": " | ".join(c["names"]) or None, "node": c["id"], "in_graph": True, "source": "data",
            "verified": False, "lat": c["lat"], "lon": c["lon"], "kinds": c["kinds"]} for c in find_candidates(tg, raw, comp)["candidates"]]
    finishes = suggest_finishes(tg, ths, lava, 12 * M_PER_MI, 18 * M_PER_MI, dem=dem)
    print("\nFinishes from Lava Point, 12-18 mi: " + "; ".join(
        f"{f['name']} {f['length_m'] / M_PER_MI:.1f} mi +{f['gain_ft'] or 0:.0f}/-{f['loss_ft'] or 0:.0f} ft" for f in finishes))
    assert grotto in {f["node"] for f in finishes}
    track_path = local_track("zion_west_rim_top_down.gpx")
    if track_path is None:
        pytest.skip("the user's West Rim track is local only")
    to_utm = Transformer.from_crs("EPSG:4326", region.crs, always_xy=True).transform
    track = transform(to_utm, read_gpx_line(track_path))
    # the user started at West Rim Trailhead North and finished at the Grotto's south trailhead: plan between the
    # start points nearest the track's ends (from the data), and see whether one of the offered trips is theirs
    G = tg.graph

    def nearest(pt):
        return min(ths, key=lambda t: math.hypot(G.nodes[t["node"]]["x"] - pt[0], G.nodes[t["node"]]["y"] - pt[1]))
    s, f = nearest(track.coords[0]), nearest(track.coords[-1])
    t0 = time.monotonic()
    r = plan_trip(region, raw, tg, GenerateRequest.from_dict({"trailhead": str(s["node"]), "total": "12-18", "nights": 1,
                                                              "daily": "5-11", "type": "one_way", "finish": str(f["node"])}),
                  dem=dem, index=con)
    print(f"  {s['name']} to {f['name']}: {time.monotonic() - t0:.1f} s; your track {track.length / M_PER_MI:.2f} mi")
    best = 0.0
    for c in r.report["candidates"]:
        near = sum(1 for p in track.coords if c.route.line_utm.distance(Point(p[:2])) <= 100) / len(track.coords)
        print(f"  offered: {c.length_m / M_PER_MI:.2f} mi, +{c.route.gain_ft or 0:.0f}/-{c.route.loss_ft or 0:.0f} ft, "
              f"{near:.0%} of your track within 100 m")
        if c.length_m == pytest.approx(track.length, rel=0.05):
            best = max(best, near)
    assert best >= 0.9


def test_zion_short_and_full_names(con):
    group = osm_index.name_group(con, "Zion")
    print(f"\n'Zion' group: {group and group['name']}: {group and [a['name'] for a in group['areas']]}")
    assert group is not None and group["areas"][0]["name"] == "Zion National Park"
    for q in ("Zion National Park", "Zion NP"):
        assert osm_index.name_group(con, q) is None
        assert osm_index.search(con, q)[0]["name"] == "Zion National Park"
