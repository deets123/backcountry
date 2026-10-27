"""A searched place as the trip's destination, on the made-up mini index (tests/fixtures/mini.osm)."""

import re

import pytest

from backcountry import areas, ingest
from backcountry.graph import build_graph
from backcountry.planning import GenerateRequest, RequestError, plan_trip
from backcountry.region import load_region

M_PER_MI = 1609.344


@pytest.fixture(scope="module")
def park(index_path, tmp_path_factory):
    from backcountry import osm_index

    d = tmp_path_factory.mktemp("dest")
    dirs = {"raw_dir": d / "raw", "areas_dir": d / "areas"}
    con = osm_index.open_index(index_path)
    spec = areas.area_from_ref(con, "relation/3003")
    areas.prepare_area(spec, con, dem_fetch=lambda *a, **k: None, **dirs)
    con.close()
    region = load_region(spec.id, regions_dir=d / "none", areas_dir=dirs["areas_dir"])
    raw = ingest.load_raw(region, dirs["raw_dir"])
    return region, raw, build_graph(raw, region.crs)


def plan(index, park, **body):
    region, raw, tg = park
    req = GenerateRequest.from_dict({"trailhead": "10.2495,-149.8505"} | body)  # Rim Trailhead
    return plan_trip(region, raw, tg, req, dem=None, index=index)


def test_peak_destination_resolves_to_the_nearest_trail_node(index, park):
    from backcountry.destination import resolve

    dest = resolve(index, park[2], "node/1060")
    assert dest.name == "Spur Top" and dest.kind == "point" and dest.node == 1029
    assert dest.offset_m < 100 and dest.note is None


def test_far_peak_gets_the_no_mapped_trail_note(index, park):
    from backcountry.destination import resolve

    dest = resolve(index, park[2], "node/1061")
    assert dest.node == 1029 and dest.offset_m > 400
    assert re.fullmatch(r"The nearest mapped trail point is 0\.\d mi from Far Knob; no mapped trail reaches it\.", dest.note)


def test_unknown_destination_is_a_request_error(index, park):
    from backcountry.destination import resolve

    with pytest.raises(RequestError) as e:
        resolve(index, park[2], "node/999999")
    assert e.value.field == "destination"


def test_out_and_back_ends_at_the_destination(index, park):
    r = plan(index, park, total="18-24", type="out_and_back", destination="node/1060")
    cands = r.report["candidates"]
    assert cands and all(c.turnaround == 1029 for c in cands)
    assert r.report["destination"]["name"] == "Spur Top"


def test_loop_passes_through_a_place_destination(index, park):
    from backcountry.destination import reached, resolve

    r = plan(index, park, total="25-30", type="loop", destination="node/1062")
    cands = r.report["candidates"]
    dest = resolve(index, park[2], "node/1062")
    assert dest.kind == "place" and cands and all(reached(c.route, dest) for c in cands)


def test_overnight_trip_includes_the_spur_as_a_side_trip(index, park):
    r = plan(index, park, total="33-38", nights=1, daily="10-25", type="loop", destination="node/1060")
    cands = r.report["candidates"]
    assert cands
    c = cands[0]
    assert 1029 in c.route.nodes and 33 * M_PER_MI <= c.length_m <= 38 * M_PER_MI
    assert any(re.fullmatch(r"Side trip to Spur Top, \d+\.\d mi round trip\.", n) for n in c.route.notes)


def test_destination_shorter_than_the_trip_offers_the_short_trip(index, park):
    r = plan(index, park, total="40-50", destination="node/1060")
    cands = r.report["candidates"]
    assert len(cands) == 1 and cands[0].kind == "out_and_back" and cands[0].turnaround == 1029
    miles = cands[0].length_m / M_PER_MI
    assert f"No trip from this start reaches Spur Top within 40–50 mi. The shortest out and back to it is {miles:.1f} mi." \
        in r.report["notes"]


def test_destination_unreachable_says_so(index, park):
    r = plan(index, park, total="18-24", destination="node/1065")
    assert r.report["candidates"] == []
    assert "Lonely Knob can't be reached from this start on the mapped trails." in r.report["notes"]


def test_trail_destination_walks_the_trail(index, park):
    from backcountry.destination import resolve

    dest = resolve(index, park[2], "way/2009")
    assert dest.kind == "trail" and dest.trail_name == "Peak Spur"
    r = plan(index, park, total="18-24", type="out_and_back", destination="way/2009")
    assert r.report["candidates"]
    assert all(any(e["name"] == "Peak Spur" for e in c.route.edges) for c in r.report["candidates"])


@pytest.mark.parametrize("bad", ["../x", "node/1; rm -rf /", "https://example.com/x", "node/", 5])
def test_destination_ref_is_validated(bad):
    with pytest.raises(RequestError) as e:
        GenerateRequest.from_dict({"trailhead": "1", "total": "2-5", "destination": bad})
    assert e.value.field == "destination"


def test_destination_needs_the_index(park):
    region, raw, tg = park
    with pytest.raises(RequestError) as e:
        plan_trip(region, raw, tg, GenerateRequest.from_dict({"trailhead": "10.2495,-149.8505", "total": "18-24",
                                                              "destination": "node/1060"}), dem=None)
    assert e.value.field == "destination"


def test_longer_out_and_back_takes_a_side_trip_to_a_spur_destination(index, park):
    # Spur Top is at the end of a spur: the out-and-back to it is ~21.5 mi; a longer one goes past the spur's
    # junction out East Spur (to ~17.7 mi) and takes the spur as a side trip (+8 mi): ~25.7 mi
    r = plan(index, park, total="24-27", type="out_and_back", destination="node/1060")
    cands = r.report["candidates"]
    assert cands
    side = [c for c in cands if c.turnaround != 1029]
    assert side and all(1029 in c.route.nodes for c in side)
    assert any(re.fullmatch(r"Side trip to Spur Top, \d+\.\d mi round trip\.", n) for c in side for n in c.route.notes)
    assert all(24 * M_PER_MI <= c.length_m <= 27 * M_PER_MI for c in cands)


def test_destination_cut_off_by_avoid_says_so(index, park):
    r = plan(index, park, total="18-24", avoid=["Peak Spur"], destination="node/1060")
    assert all(not any(e["name"] == "Peak Spur" for e in c.route.edges) for c in r.report["candidates"])
    assert r.report["candidates"] == []
    assert "Spur Top can't be reached from this start on the mapped trails." in r.report["notes"]


def test_destination_adds_no_must_visit_wording(index, park):
    for body in ({"total": "33-38", "nights": 1, "daily": "10-25", "type": "loop"}, {"total": "18-24", "avoid": ["Peak Spur"]}):
        r = plan(index, park, destination="node/1060", **body)
        assert r.report["must_visit_notes"] == [], r.report["must_visit_notes"]
