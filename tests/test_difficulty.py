"""Difficulty ratings on synthetic profiles. Distances in meters, elevations in meters."""

from types import SimpleNamespace

import pytest

from backcountry.difficulty import RULES, effort_score, rate_stretch, route_terrain, steepest_climb, trip_level, Rating
from backcountry.elevation import FT_PER_M, SAMPLE_M
from backcountry.scoring import M_PER_MI


def profile(pieces):
    """pieces: [(length_m, grade_pct), ...] walked in order from 0 m elevation."""
    d, z = [0.0], [0.0]
    for length, grade in pieces:
        n = max(1, round(length / SAMPLE_M))
        for i in range(1, n + 1):
            d.append(d[-1] + length / n)
            z.append(z[-1] + length / n * grade / 100)
    return d, z


def gain_ft(z):
    return sum(max(0.0, b - a) for a, b in zip(z, z[1:])) * FT_PER_M


def rate(d, z, terrain=(), scale="backpacking", gain=None):
    return rate_stretch(d, z, list(terrain), 0.0, d[-1], gain_ft(z) if gain is None else gain, scale)


def edge(length_m, source, is_road=False, **tags):
    return {"length_m": length_m, "source": source, "is_road": is_road, "tags": tags}


def test_effort_score_is_the_shenandoah_formula():
    assert effort_score(2200, 10) == pytest.approx(209.76, abs=0.01)


def test_descent_does_not_count():
    up = profile([(10 * M_PER_MI, 2000 / FT_PER_M / (10 * M_PER_MI) * 100)])
    down = (up[0], [-v for v in up[1]])
    assert rate(*down).level == "easy"
    assert rate(*up).level in ("moderate", "hard", "strenuous")


def test_long_steep_climb_raises_by_one_level_only():
    d, z = profile([(700, 0), (600, 20), (700, 0)])
    base = rate_stretch(d, z, [], 0.0, d[-1], gain_ft(z), "backpacking", RULES | {"steep": RULES["steep"] | {"grade_pct": 99}})
    r = rate(d, z)
    assert base.level == "easy"
    assert r.level == "moderate" and "steep" in r.reasons
    assert r.steep["grade_pct"] == pytest.approx(20, abs=0.5)


def test_short_steep_pitch_does_not_raise():
    d, z = profile([(900, 0), (150, 30), (900, 0)])
    r = rate(d, z)
    assert r.level == "easy" and r.reasons == []


def test_tagged_scramble_makes_at_least_hard_but_not_strenuous():
    tag = [{"start_m": 500.0, "end_m": 800.0, "reason": "scramble (tagged)", "sources": ["osm:way/1"]}]
    d, z = profile([(2000, 0)])
    r = rate(d, z, tag)
    assert r.level == "hard" and r.reasons == ["scramble (tagged)"]
    d, z = profile([(20000, 7.5)])
    assert rate(d, z).level == "strenuous"
    assert rate(d, z, tag).level == "strenuous"


def test_short_tagged_stretch_does_not_raise_the_level():
    route = SimpleNamespace(edges=[edge(500, "osm:way/1"), edge(50, "osm:way/2", sac_scale="alpine_hiking"),
                                   edge(500, "osm:way/3"), edge(300, "osm:way/4", is_road=True, sac_scale="alpine_hiking")])
    terrain = route_terrain(route, RULES["terrain"]["min_m"])
    assert [t for t in terrain if t["reason"]] == []
    d, z = profile([(1350, 0)])
    assert rate(d, z, terrain).level == "easy"


def test_rate_day_without_elevation_is_unknown():
    d, _ = profile([(5000, 5)])
    r = rate_stretch(d, [None] * len(d), [], 0.0, d[-1], None, "backpacking")
    assert r.level is None and r.reasons == [] and r.effort is None


def test_scales_differ():
    d, z = profile([(8 * M_PER_MI, 1800 / FT_PER_M / (8 * M_PER_MI) * 100)])
    day, pack = rate(d, z, scale="day_hike"), rate(d, z, scale="backpacking")
    assert day.effort == pytest.approx(169.7, abs=1)
    assert RULES["scales"]["backpacking"]["cutoffs"] != RULES["scales"]["day_hike"]["cutoffs"]
    assert ["easy", "moderate", "hard", "strenuous"].index(pack.level) == ["easy", "moderate", "hard", "strenuous"].index(day.level) + 1


def test_trip_level_is_the_hardest_day():
    def r(level):
        return Rating(level=level, reasons=[], effort=None, steep=None, tagged=[], untagged_share=1.0)
    assert trip_level([r("easy"), r("hard"), r("moderate")]) == "hard"
    assert trip_level([r("easy"), r(None)]) is None
    assert trip_level([]) is None


def test_route_terrain_merges_and_keeps_source():
    route = SimpleNamespace(edges=[edge(100, "osm:way/1"), edge(80, "osm:way/2", sac_scale="alpine_hiking"),
                                   edge(90, "osm:way/3", sac_scale="alpine_hiking"), edge(200, "osm:way/4", sac_scale="hiking")])
    terrain = route_terrain(route, RULES["terrain"]["min_m"])
    assert terrain == [
        {"start_m": 100.0, "end_m": 270.0, "reason": "scramble (tagged)", "sources": ["osm:way/2", "osm:way/3"]},
        {"start_m": 270.0, "end_m": 470.0, "reason": None, "sources": ["osm:way/4"]},
    ]


def test_untagged_share_counts_stretch_without_terrain_data():
    terrain = [{"start_m": 0.0, "end_m": 500.0, "reason": None, "sources": ["osm:way/4"]}]
    d, z = profile([(2000, 0)])
    assert rate(d, z, terrain).untagged_share == pytest.approx(0.75)


def test_steepest_climb_is_uphill_only_and_needs_the_window():
    d, z = profile([(1000, -25), (1000, 5)])
    s = steepest_climb(d, z, 0.0, d[-1], 400)
    assert s["grade_pct"] == pytest.approx(5, abs=0.5)
    assert steepest_climb(d, z, 0.0, 300.0, 400) is None
