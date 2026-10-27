"""M6 outputs: per-day GPX tracks, waypoints, GeoJSON, map, summary."""

import json

import gpxpy
import pytest

from backcountry.export import write_route
from backcountry.features import along_route
from backcountry.graph import build_graph
from backcountry.region import BBox, Region
from backcountry.route import add_elevation, shortest_route
from backcountry.split import split_route
from conftest import CRS, lonlat, plane_dem

M = 1609.344
REGION = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=BBox(36.0, -81.1, 36.2, -80.9))


@pytest.fixture
def outputs(network, tmp_path):
    tg = build_graph(network.doc(), crs=CRS)
    route = add_elevation(shortest_route(tg, network.ids["A"], network.ids["F"]), plane_dem(0.1, 0.0, 1000), CRS)
    plan = split_route(route, nights=2, daily_min_m=0.5 * M, daily_max_m=1.0 * M, pins_m=[1200.0])
    lon, lat = lonlat(600, -40)
    water = {"points": [{"source": "osm:node/60", "kind": "spring", "name": "Cold Spring", "lat": lat, "lon": lon, "tags": {}}],
             "lines": [{"source": "osm:way/70", "kind": "stream", "name": "Test Run", "tags": {},
                        "coords": [list(lonlat(1500, -300)), list(lonlat(1500, 300))]}]}
    feats = along_route(route, water=water, plan=plan)
    paths = write_route(route, REGION, tmp_path / "r", "A Trailhead", "F", "2026-01-01T00:00:00Z", plan=plan, features=feats)
    return route, plan, paths, tmp_path / "r"


def test_gpx_is_one_continuous_track_covering_the_whole_route(outputs):
    # Garmin Connect imports only a file's first track, so the whole-trip GPX is one track and one segment;
    # days are in the per-day files and as camp waypoints.
    route, plan, paths, _ = outputs
    gpx = gpxpy.parse(paths["gpx"].read_text())
    assert len(gpx.tracks) == 1 and len(gpx.tracks[0].segments) == 1
    assert gpx.tracks[0].name == gpx.name
    pts = [(p.longitude, p.latitude) for p in gpx.tracks[0].segments[0].points]
    assert all(a != b for a, b in zip(pts, pts[1:]))  # day boundaries aren't doubled
    assert pts[0] == pytest.approx(route.line_wgs84.coords[0]) and pts[-1] == pytest.approx(route.line_wgs84.coords[-1])
    for lon, lat in route.line_wgs84.coords:  # every source vertex is still there
        assert any(abs(lon - x) < 1e-7 and abs(lat - y) < 1e-7 for x, y in pts)
    assert gpx.tracks[0].length_2d() == pytest.approx(route.length_m, rel=0.01)
    assert {w.name for w in gpx.waypoints} >= {f"Camp {c.index}" for c in plan.camps}


def test_gpx_points_carry_dem_elevation_in_meters(outputs):
    route, _, paths, _ = outputs
    gpx = gpxpy.parse(paths["gpx"].read_text())
    first = gpx.tracks[0].segments[0].points[0]
    assert first.elevation == pytest.approx(1000.0, abs=0.5)  # plane: z = 1000 + 0.1 * x, x=0 at A
    assert all(p.elevation is not None for t in gpx.tracks for s in t.segments for p in s.points)


def test_gpx_waypoints_start_end_camps_and_junctions(outputs):
    _, plan, paths, _ = outputs
    gpx = gpxpy.parse(paths["gpx"].read_text())
    names = [w.name for w in gpx.waypoints]
    assert names[:2] == ["Start", "Finish"] and "Camp 1" in names and "Camp 2" in names  # A to B: ends differ
    jcts = [w for w in gpx.waypoints if w.name.startswith("Jct")]
    assert jcts and all("FR 1" in w.description or "Override Trail" in w.description for w in jcts)
    assert all("osm:" not in w.description for w in gpx.waypoints)
    assert all(w.elevation is not None for w in gpx.waypoints)


def test_per_day_gpx_files(outputs):
    _, plan, paths, out = outputs
    for d in plan.days:
        g = gpxpy.parse((out / f"day_{d.index}.gpx").read_text())
        assert len(g.tracks) == 1 and g.tracks[0].name.startswith(f"Day {d.index}")
        assert g.tracks[0].length_2d() == pytest.approx(d.length_m, rel=0.01)


def test_geojson_one_feature_per_day_plus_points(outputs):
    _, plan, paths, _ = outputs
    fc = json.loads(paths["geojson"].read_text())
    lines = [f for f in fc["features"] if f["geometry"]["type"] == "LineString"]
    points = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
    assert [f["properties"]["name"] for f in lines] == [f"Day {d.index}" for d in plan.days]
    assert all("gain_ft" in f["properties"] and "length_m" in f["properties"] for f in lines)
    assert {p["properties"]["name"] for p in points} >= {"Start", "Finish", "Camp 1", "Camp 2"}


def test_map_colors_days_and_shows_features(outputs):
    _, _, paths, _ = outputs
    html = paths["map"].read_text()
    for text in ("Day 1", "Day 2", "Day 3", "Camp 1", "Cold Spring", "Stream crossing", "basemap.nationalmap.gov",
                 "OpenStreetMap contributors", "unknown"):
        assert text in html, text
    assert "tile.openstreetmap.org" not in html and "osm:node/" not in html


def test_summary_has_known_gaps_and_per_day_table(outputs):
    _, _, paths, _ = outputs
    md = paths["summary"].read_text()
    assert "## Known gaps" in md and "not checked by this tool" in md
    for text in ("Day 1", "Day 2", "Day 3", "retrieved 2026-01-01", "synthetic plane", "Cold Spring"):
        assert text in md, text
    assert " m " not in md and "safe" not in md.lower()


def test_feature_popups_carry_evidence_and_imagery_links(network, tmp_path):
    tg = build_graph(network.doc(), crs=CRS)
    route = add_elevation(shortest_route(tg, network.ids["A"], network.ids["F"]), plane_dem(0.1, 0.0, 1000), CRS)
    lon, lat = lonlat(1200, 80)
    sites = [{"source": "osm:node/50", "kind": "camp_site", "name": None, "lat": lat, "lon": lon,
              "tags": {"tourism": "camp_site", "backcountry": "yes"},
              "meta": {"timestamp": "2024-05-02T00:00:00Z", "version": 3}, "url": "https://www.openstreetmap.org/node/50"}]
    feats = along_route(route, camp_sites=sites)
    paths = write_route(route, REGION, tmp_path / "r", "A", "F", "2026-01-01T00:00:00Z", features=feats)
    html = paths["map"].read_text()
    assert "backcountry=yes" in html and "2024-05-02" in html and "version 3" in html
    assert "google.com/maps/@" in html and "openstreetmap.org/node/50" in html
    assert "osm:node/" not in html and "camp_site" not in html and "Campsite" in html
    md = paths["summary"].read_text()
    assert "backcountry=yes" in md and "2024-05-02" in md
    assert "osm:node/" not in md


def test_basemap_choice(network, tmp_path):
    tg = build_graph(network.doc(), crs=CRS)
    route = shortest_route(tg, network.ids["A"], network.ids["F"])
    html = write_route(route, REGION, tmp_path / "r", "A", "F", "2026-01-01T00:00:00Z", basemap="satellite")["map"].read_text()
    assert "USGSImageryOnly" in html
    with pytest.raises(ValueError, match="basemap"):
        write_route(route, REGION, tmp_path / "r2", "A", "F", "2026-01-01T00:00:00Z", basemap="mars")
