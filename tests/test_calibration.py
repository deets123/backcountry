"""Difficulty calibration against the user's known trips (tests/calibration/cases.yaml).

The reference-trip cases need the user's local GPX tracks (tests/reference/local/, never committed)
and cached OSM + DEM data for each calibration area, so they skip on GitHub.
"""

from pathlib import Path

import pytest
import yaml
from shapely.geometry import LineString

from backcountry.calibration import CALIBRATION_DIR, LOCAL_TRACKS, rate_reference_trip, route_along_track
from backcountry.graph import build_graph
from conftest import CRS, X0, Y0

CASES = yaml.safe_load((CALIBRATION_DIR / "cases.yaml").read_text())


def xy(b, *names):
    return [(X0 + b.xy[n][0], Y0 + b.xy[n][1]) for n in names]


def test_route_along_track_follows_the_grid(grid):
    tg = build_graph(grid.doc(), crs=CRS)
    track = LineString([(x + 7, y - 5) for x, y in xy(grid, "g20", "g10", "g00", "g01", "g02")])  # a few meters off
    route = route_along_track(tg, track)
    assert [e["name"] for e in route.edges] == ["Row 0", "Row 0", "Col 0", "Col 0"]
    assert route.nodes[0] == grid.ids["g20"] and route.nodes[-1] == grid.ids["g02"]
    assert route.length_m == pytest.approx(4000)


def test_route_along_track_reports_a_gap_instead_of_drawing(network):
    tg = build_graph(network.doc(), crs=CRS)
    track = LineString(xy(network, "C", "G"))
    with pytest.raises(ValueError, match=r"no trail connects mile 0\.\d+ and mile 1\.\d+ of the track"):
        route_along_track(tg, track)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reference_trips_match_the_users_levels(case):
    if case.get("track") is None:
        pytest.skip(f"{case['id']}: route pending: ask the user")
    if not (LOCAL_TRACKS / case["track"]).exists():
        pytest.skip(f"{case['id']}: reference track not on this machine (local only)")
    try:
        result = rate_reference_trip(case)
    except FileNotFoundError as e:
        pytest.skip(f"{case['id']}: {e}")
    print(f"\n{case['id']}: expected {result['expected']}, got {result['got']}")
    for i, day in enumerate(result["days"], 1):
        print(f"  day {i}: {day}")
    for note in result["notes"]:
        print(f"  {note}")
    assert result["got"] == case["expected"]
