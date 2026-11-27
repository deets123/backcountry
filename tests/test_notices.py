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
