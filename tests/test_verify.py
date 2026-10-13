import pytest

from backcountry.graph import build_graph, qa_summary
from backcountry.region import Region
from backcountry.verify import verify
from conftest import CRS, bbox_xy, usfs_doc, usfs_line, usfs_point, usfs_polygon

REGION = Region(id="synthetic", name="Synthetic", crs=CRS, bbox=bbox_xy(-1000, -1000, 7000, 2000))


@pytest.fixture
def result(network):
    # An OSM track that USFS lists as a closed road.
    network.node("K", 6000, 0)
    network.node("L", 6000, 500)
    network.way(13, ["K", "L"], highway="track", name="Old Grade")
    raw = network.doc()
    usfs = usfs_doc({
        "trails": [
            usfs_line([(0, 5), (2000, 5)], trail_no="1", trail_name="MAIN TRAIL"),        # matches way 1, 5 m off
            usfs_line([(3000, -500), (3000, 500)], trail_no="2", trail_name="GHOST TRAIL"),  # not in OSM at all
            usfs_line([(1000, 0), (1000, 1300)], trail_no="3", trail_name="SPUR TRAIL"),     # OSM stops at D (y=800)
            usfs_line([(1000, 800), (1500, 800), (2000, 1000)], trail_no="4", trail_name="PRIVATE TRAIL"),
            usfs_line([(6500, -800), (9000, -800)], trail_no="5", trail_name="LONG TRAIL"),  # mostly outside bbox
        ],
        "roads_open": [usfs_line([(2000, 0), (2000, 1000)], id="1", name="FR ONE", oper_maint_level="3 - SUITABLE FOR PASSENGER CARS")],
        "roads_closed": [usfs_line([(6000, 0), (6000, 500)], id="9", name="OLD GRADE", oper_maint_level="1 - BASIC CUSTODIAL CARE (CLOSED)")],
        "rec_sites": [
            usfs_point(1000, 830, site_id="100", site_name="TEST TH", public_site_name="Test Trailhead", site_subtype="TRAILHEAD"),
            usfs_point(4000, 0, site_id="101", site_name="LONELY TH", public_site_name="Lonely Trailhead", site_subtype="TRAILHEAD"),
            usfs_point(2000, 0, site_id="102", site_name="SOME CAMPGROUND", site_subtype="CAMPGROUND"),
        ],
        "wilderness": [usfs_polygon([(-1000, -1000), (3000, -1000), (3000, 1500), (-1000, 1500)],
                                    wildernessname="Test Wilderness", boundarystatus="Final", gis_acres=1.0)],
    })
    tg = build_graph(raw, CRS)
    return network, tg, verify(tg, raw, usfs, REGION, qa_summary(tg)["node_component"])


def _trail(res, no):
    return next(t for t in res["usfs_trails"] if t["trail_no"] == no)


def test_matching_trail_fully_covered(result):
    _, _, res = result
    t = _trail(res, "1")
    assert t["within_match_pct"] == pytest.approx(100)
    assert t["median_offset_m"] == pytest.approx(5, abs=0.5)
    assert t["gaps"] == []
    # Main Trail's 2000 m plus the 10 m Stub edge, which lies within MATCH_M of the USFS line's end.
    assert t["osm_matched_m"] == pytest.approx(2010, abs=1)
    assert "Main Trail" in t["osm_names"]


def test_trail_missing_from_osm_is_one_gap(result):
    _, _, res = result
    t = _trail(res, "2")
    assert t["within_match_pct"] == 0
    assert len(t["gaps"]) == 1
    assert t["gaps"][0]["length_m"] == pytest.approx(1000, abs=15)
    assert t["gaps"][0]["in_osm"] is None


def test_trail_extending_past_osm_reports_partial_gap(result):
    _, _, res = result
    t = _trail(res, "3")
    assert len(t["gaps"]) == 1
    assert t["gaps"][0]["length_m"] == pytest.approx(425, abs=15)


def test_gap_on_non_routable_osm_way_says_why(result):
    _, _, res = result
    gap = _trail(res, "4")["gaps"][0]
    assert "osm:way/4" in gap["in_osm"] and "access=private" in gap["in_osm"]


def test_usfs_trail_is_clipped_to_region_bbox(result):
    _, _, res = result
    t = _trail(res, "5")
    assert t["usfs_m"] == pytest.approx(500, abs=5)
    assert t["outside_bbox_m"] == pytest.approx(2000, abs=5)


def test_osm_edges_classified_against_usfs(result):
    _, _, res = result
    by_name = {(r["name"], r["highway"]): r for r in res["osm_ways"]}
    assert by_name[("Main Trail", "path")]["usfs_match"].startswith("TR 1 ")
    assert by_name[("FR 1", "unclassified")]["usfs_match"].startswith("FR 1 ")
    old = by_name[("Old Grade", "track")]
    assert old["usfs_match"].startswith("FR 9 ") and old["usfs_closed_to_motor"] is True
    assert "BASIC CUSTODIAL CARE" in old["usfs_maint_level"]
    nm = by_name[("Near Miss Trail", "path")]
    assert nm["usfs_match"] is None and nm["inside_wilderness"] is True
    assert by_name[("Island Trail", "path")]["inside_wilderness"] is False


def test_usfs_trailheads_matched_to_osm_and_graph(result):
    network, _, res = result
    ths = {t["site_id"]: t for t in res["trailheads"]}
    assert set(ths) == {"100", "101"}  # campground ignored
    assert ths["100"]["osm_trailhead"]["source"] == "osm:node/9001"
    assert ths["100"]["osm_trailhead"]["distance_m"] == pytest.approx(10, abs=0.5)
    assert ths["100"]["nearest_node"]["id"] == network.ids["D"]
    assert ths["101"]["osm_trailhead"] is None


def test_verify_report_sections_units_and_attribution(result, tmp_path):
    from backcountry.export import write_verify_report

    _, _, res = result
    paths = write_verify_report(res, REGION, tmp_path)
    md = paths["report"].read_text()
    for heading in ("Official USFS trails", "USFS trail sections missing from the graph", "OSM tracks",
                    "OSM trails not in USFS data", "Trailheads", "USDA Forest Service", "OpenStreetMap contributors"):
        assert heading in md
    assert " mi" in md and " ft" in md and " m " not in md
    assert "GHOST TRAIL" in md


def test_map_with_usfs_overlay_and_readable_legend(result):
    from backcountry.export import qa_map
    from backcountry.graph import qa_summary

    network, tg, res = result
    raw = network.doc()
    html = qa_map(qa_summary(tg), tg, raw, REGION, verification=res).get_root().render()
    for label in ("USFS official trails", "USFS roads", "USFS trailheads", "Gaps vs USFS"):
        assert label in html
    assert 'id="bc-legend"' in html and "<details" in html
    for row in ("Main network (C0)", "Other components", "Road", "Dead end", "Possible missing junction",
                "USFS official trail", "USFS trailhead"):
        assert row in html


def test_winter_only_usfs_trails_are_reported_separately(network):
    from backcountry.export import verify_markdown

    raw = network.doc()
    usfs = usfs_doc({
        "trails": [usfs_line([(0, 5), (2000, 5)], trail_no="1", trail_name="MAIN TRAIL", trail_type="TERRA"),
                   usfs_line([(3000, -500), (3000, 500)], trail_no="2", trail_name="GHOST SNOMO", trail_type="SNOW")],
    })
    tg = build_graph(raw, CRS)
    res = verify(tg, raw, usfs, REGION, qa_summary(tg)["node_component"])
    by_no = {t["trail_no"]: t for t in res["usfs_trails"]}
    assert by_no["1"]["trail_type"] == "TERRA" and by_no["2"]["trail_type"] == "SNOW"
    md = verify_markdown(res, REGION)
    assert "USFS lists 1 ground trail" in md and "Winter-only USFS trails (1)" in md
    assert "100.0% of that mileage" in md  # the snowmobile trail's gap doesn't drag the hiking number down
