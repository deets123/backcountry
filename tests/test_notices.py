"""Notices: quoted, dated, sourced statements about permits, fees, closures and rules (notices.py)."""

import pytest

from backcountry import notices as N


def test_notice_shape_and_text_cap():
    n = N.notice("permit", "x" * 500, applies_to="trail: A", matched=True, source="OpenStreetMap", ref="osm:way/1",
                 as_of="2026-10-26", links=["https://a.example/p", "javascript:alert(1)", "https://a.example/p"])
    assert set(n) == {"kind", "text", "applies_to", "matched", "source", "links"}
    assert n["text"].endswith("…") and len(n["text"]) == N.MAX_TEXT + 1
    assert n["links"] == ["https://a.example/p"]
    assert n["source"] == {"name": "OpenStreetMap", "ref": "osm:way/1", "as_of": "2026-10-26", "checked_at": None}
    with pytest.raises(ValueError):
        N.notice("warning", "x")


def test_html_to_text_strips_tags_and_keeps_only_http_links():
    html = ('<p>You must purchase a $1&nbsp;<a href="https://www.nps.gov/shen/x.htm">ticket</a> first.</p>'
            '<script>alert(1)</script><img src=x onerror="alert(1)"><a href="javascript:evil()">bad</a>')
    text, links = N.html_to_text(html)
    assert text == "You must purchase a $1 ticket first. bad"
    assert "<" not in text and "onerror" not in text and "alert" not in text
    assert links == ["https://www.nps.gov/shen/x.htm"]


def test_match_runs_of_two_words_with_a_non_generic_word():
    items = [("start", "West Rim Trailhead North | West Rim Trail @ Lava Point Road"), ("destination", "Old Rag Mountain"),
             ("trail", "Watchman Trail"), ("trail", "Angels Landing Trail")]
    assert N.match_items("Road to Lava Point and West Rim Trailhead closed", items) == [
        "start: West Rim Trailhead North | West Rim Trail @ Lava Point Road"]
    assert N.match_items("Ticket Required to Hike Old Rag", items) == ["destination: Old Rag Mountain"]
    assert N.match_items("The Watchman Trail is closed pending repairs.", items) == ["trail: Watchman Trail"]
    assert N.match_items("All hikers need a permit to hike Angels Landing.", items) == ["trail: Angels Landing Trail"]
    assert N.match_items("West Rim to Scout Lookout", [("trail", "West Rim Trail")]) == ["trail: West Rim Trail"]


def test_match_ignores_punctuation_and_generic_only_runs():
    assert N.match_items("Barneys Trail reroute", [("start", "Barney's Trail @ Lava Point Campground Loop")]) == [
        "start: Barney's Trail @ Lava Point Campground Loop"]
    assert N.match_items("the trail to the lake", [("trail", "The Trail"), ("trail", "Lower Lot")]) == []
    assert N.match_items("Angels Landing", [("trail", "West Rim Trail")]) == []
    assert N.match_items("old rag LOWER LOT closed", [("start", "Old Rag Lower Lot")]) == ["start: Old Rag Lower Lot"]


def test_alert_kind_precedence():
    assert N.alert_kind("Watchman Trail closed", "", "Information") == "closure"
    assert N.alert_kind("Ticket Required to Hike Old Rag", "", "Information") == "ticket"
    assert N.alert_kind("Angels Landing Pilot Permit Program", "", "Information") == "permit"
    assert N.alert_kind("Active Closure", "zone is closed", "Park Closure") == "closure"
    assert N.alert_kind("Toxic cyanobacteria", "", "Caution") == "caution"
    assert N.alert_kind("Infrastructure Improvements to Old Rag Trailhead",
                        "lot is closed. Day-use tickets are still required.", "Information") == "ticket"
    assert N.alert_kind("Get Skyline Drive Status Updates", "text SHENALERTS", "Information") == "info"


# --- from OpenStreetMap tags ---------------------------------------------------------------------------


def _edge(name, length_m, permit=False, is_road=False, **tags):
    return {"name": name, "tags": tags, "is_road": is_road, "length_m": length_m, "source": "osm:way/1", "permit": permit}


def test_osm_route_notices_group_permits_dogs_hours_and_trail_closures():
    edges = [_edge("Angels Landing Trail", 700.0, permit=True, access="permit", foot="permit", fee="yes", reservation="required",
                   operator="https://www.recreation.gov/permits/4675310", dog="no"),
             _edge("West Rim Trail", 3000.0, dog="no", opening_hours="sunrise-sunset"),
             _edge("Scenic Drive", 500.0, is_road=True, description="Closed to private vehicles March-October"),
             _edge("Hidden Trail", 200.0, note="Closed indefinitely due to 2019 rockfall.")]
    out = N.osm_route_notices(edges, "2026-10-26")
    kinds = [(n["kind"], n["applies_to"]) for n in out]
    assert ("permit", "trail: Angels Landing Trail") in kinds and ("closure", "trail: Hidden Trail") in kinds
    assert not any("Scenic Drive" in n["text"] for n in out)  # a road's vehicle closure is not a hiking closure
    permit = next(n for n in out if n["kind"] == "permit")
    assert permit["text"].startswith("Permit to hike Angels Landing Trail, 0.4 mi of this trip.")
    assert "Reservation required" in permit["text"] and "fee" in permit["text"].lower()
    assert permit["links"] == ["https://www.recreation.gov/permits/4675310"] and permit["source"]["as_of"] == "2026-10-26"
    dogs = next(n for n in out if n["kind"] == "rule" and n["text"].startswith("Dogs: no"))
    assert "2.3 mi" in dogs["text"] and "Angels Landing Trail" in dogs["text"] and "West Rim Trail" in dogs["text"]
    assert any(n["kind"] == "rule" and "opening hours sunrise-sunset" in n["text"] for n in out)
    assert all(n["matched"] for n in out) and all("osm:" not in n["text"] for n in out)


def test_camp_notices_quote_the_site_tags_within_500_m():
    from backcountry.split import Camp

    sites = [{"source": "osm:node/7", "name": "La Verkin Creek 7",
              "tags": {"access": "permit", "fee": "yes", "reservation": "no", "capacity:persons": "2",
                       "website": "https://www.nps.gov/zion/wild.htm"}},
             {"source": "osm:node/8", "name": "Plain Site", "tags": {"tourism": "camp_site"}}]
    camps = [Camp(index=1, dist_m=1000.0, lat=0.0, lon=0.0, on_source="osm:way/1", on_name="T",
                  nearby=[{"source": "osm:node/7", "kind": "camp_pitch", "name": "La Verkin Creek 7", "distance_m": 120.0}]),
             Camp(index=2, dist_m=9000.0, lat=0.0, lon=0.0, on_source="osm:way/1", on_name="T",
                  nearby=[{"source": "osm:node/8", "kind": "camp_site", "name": "Plain Site", "distance_m": 50.0}]),
             Camp(index=3, dist_m=12000.0, lat=0.0, lon=0.0, on_source="osm:way/1", on_name="T", nearby=[])]
    out = N.camp_notices(camps, sites, "2026-10-26")
    assert len(out) == 1 and out[0]["kind"] == "camp" and out[0]["applies_to"] == "camp 1"
    assert out[0]["text"] == "Camp 1 near La Verkin Creek 7 (OpenStreetMap): permit, fee, no reservation, 2 people."
    assert out[0]["links"] == ["https://www.nps.gov/zion/wild.htm"]


def test_closed_ways_near_a_point_quote_the_reason(network):
    from backcountry.graph import build_graph
    from conftest import CRS

    network.way(21, ["D", "E"], highway="path", name="Hidden Canyon", access="no", note="Closed indefinitely due to 2019 rockfall.",
                **{"damage:date": "2019-08-24", "damage:type": "landslide"})
    tg = build_graph(network.doc(), CRS)
    lon, lat = network.coords[network.ids["d1"]]
    assert N.closed_ways_near(tg, lon, lat) == [
        'Hidden Canyon is tagged closed in OpenStreetMap: "Closed indefinitely due to 2019 rockfall." (landslide, 2019-08-24)']
    far_lon, far_lat = network.coords[network.ids["G"]]
    assert N.closed_ways_near(tg, far_lon, far_lat) == []


# --- from the National Park Service, and the assembled block -------------------------------------------

import json
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "nps"


def _alerts_only(code, park=None):
    """Park data with the real recorded alerts and no things to do (those recordings come with the parks list)."""
    alerts = json.loads((FIX / f"{code}_alerts.json").read_text())["data"]
    return {"park": park, "alerts": alerts, "things": [], "fetched_at": "2026-11-26T10:04:00Z", "stale": False, "error": None}


def test_alerts_become_notices_matched_to_the_route_by_name():
    items = N.route_items(["West Rim Trail"], "West Rim Trailhead North | West Rim Trail @ Lava Point Road", "Grotto Trailhead", None, [])
    out = N.nps_notices(_alerts_only("zion"), items)
    assert len(out) == 6 and all(n["source"]["name"] == "National Park Service" for n in out)
    road = next(n for n in out if n["kind"] == "closure" and "Lava Point" in n["text"])
    assert road["matched"] and "West Rim" in road["applies_to"] and road["source"]["as_of"] == "2026-11-21"
    assert road["source"]["checked_at"] == "2026-11-26T10:04:00Z"
    watchman = next(n for n in out if "Watchman" in n["text"])
    assert watchman["kind"] == "closure" and not watchman["matched"] and watchman["applies_to"] == "park"
    angels = next(n for n in out if "Angels Landing" in n["text"])
    assert angels["kind"] == "permit" and not angels["matched"]
    assert angels["links"] == ["https://www.nps.gov/zion/planyourvisit/angels-landing-hiking-permits.htm"]
    assert any(n["kind"] == "caution" and "cyanobacteria" in n["text"] for n in out)
    assert all("<" not in n["text"] for n in out)


def test_the_old_rag_ticket_alert_attaches_to_the_destination_first():
    items = N.route_items(["Ridge Trail", "Saddle Trail"], "Old Rag Lower Lot", None, "Old Rag Mountain", [])
    out = N.nps_notices(_alerts_only("shen"), items)
    ticket = next(n for n in out if n["kind"] == "ticket" and n["text"].startswith("Ticket Required to Hike Old Rag"))
    assert ticket["matched"] and ticket["applies_to"] == "destination: Old Rag Mountain"  # the destination outranks the start
    assert ticket["links"] == ["https://www.nps.gov/shen/planyourvisit/faqs-oldrag.htm"] and ticket["source"]["as_of"] == "2024-01-17"
    lot = next(n for n in out if "Infrastructure Improvements" in n["text"])
    assert lot["kind"] == "ticket" and lot["matched"]  # names Old Rag Trailhead; its text has both the closure and the tickets


def test_assemble_orders_matched_closures_first_and_ends_with_the_unknown_line():
    osm = [N.notice("permit", "Permit to hike Angels Landing Trail, 0.4 mi of this trip.", applies_to="trail: Angels Landing Trail",
                    matched=True, source="OpenStreetMap")]
    items = N.route_items(["Angels Landing Trail", "West Rim Trail"], "Grotto Trailhead", None, "Angels Landing", [])
    block, pending = N.assemble(osm, {"code": "zion", "configured": True, "data": _alerts_only("zion")}, items)
    kinds = [(n["matched"], n["kind"]) for n in block]
    assert pending is False and block[-1]["kind"] == "unknown" and block[-1]["text"] == N.UNKNOWN_LINE
    matched = [k for m, k in kinds if m]
    assert matched == sorted(matched, key=N.KIND_ORDER.get) and matched[0] == "closure"  # the Lava Point road, via West Rim Trail
    assert kinds.index((True, "permit")) < kinds.index((False, "closure"))
    assert kinds.index((False, "closure")) < kinds.index((False, "caution")) < kinds.index((False, "info"))
    assert "Watchman Trail closed" in " ".join(n["text"] for n in block)


def test_assemble_with_empty_park_data_has_only_the_unknown_line():
    empty = {"park": {"fullName": "Quiet Park", "entranceFees": [], "url": "https://www.nps.gov/quie/index.htm"}, "alerts": [], "things": [],
             "fetched_at": "2026-11-26T10:04:00Z", "stale": False, "error": None}
    block, pending = N.assemble([], {"code": "quie", "configured": True, "data": empty}, [])
    assert pending is False and [n["kind"] for n in block] == ["fee", "unknown"] and "none listed" in block[0]["text"]
    assert block[0]["links"] == ["https://www.nps.gov/quie/index.htm"]


def test_assemble_without_a_park_or_a_key_says_so_and_flags_pending():
    block, pending = N.assemble([], {"code": None, "configured": True, "data": None}, [])
    assert [n["text"] for n in block] == ["No official notice feed for this area.", N.UNKNOWN_LINE] and pending is False
    block, pending = N.assemble([], {"code": "zion", "configured": False, "data": None}, [])
    assert block[0]["text"] == "Park notices from the National Park Service aren't set up on this server." and pending is False
    block, pending = N.assemble([], {"code": "zion", "configured": True, "data": None}, [])
    assert pending is True and [n["kind"] for n in block] == ["unknown"]
    block, pending = N.assemble([], None, [])
    assert block[0]["text"] == "Park notices from the National Park Service aren't set up on this server." and pending is False


def test_stale_park_data_says_when_it_was_last_checked():
    data = _alerts_only("zion") | {"stale": True, "error": "HTTP Error 429"}
    block, _ = N.assemble([], {"code": "zion", "configured": True, "data": data}, [])
    assert any(n["kind"] == "info" and n["text"].startswith("Couldn't refresh park notices; last checked 2026-11-26") for n in block)


def test_payload_items_read_a_stored_run():
    run = {"start": {"name": "Grotto Trailhead"}, "search": {"finish": None, "destination": {"name": "Angels Landing"}}}
    cand = {"segments": [{"label": "West Rim Trail"}, {"label": "Angels Landing Trail"}, {"label": "West Rim Trail"}],
            "plan": {"camps": [{"nearby": [{"name": "La Verkin Creek 7"}]}, {"nearby": []}]}}
    assert N.payload_items(run, cand) == [("trail", "West Rim Trail"), ("trail", "Angels Landing Trail"), ("start", "Grotto Trailhead"),
                                          ("destination", "Angels Landing"), ("camp 1 site", "La Verkin Creek 7")]
    assert N.payload_items({"start": {}, "search": {}}, {"segments": [], "plan": None}) == []
