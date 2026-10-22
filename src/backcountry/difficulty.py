"""Difficulty levels (easy / moderate / hard / strenuous) for a stretch of route.

Computed only from sourced data, in the stretch's direction of travel:

1. Effort score: Shenandoah National Park's formula, sqrt(gain_ft * 2 * miles)
   (nps.gov/shen, "How to Determine Hiking Difficulty"). Descent doesn't count.
   The cutoffs depend on the scale: a day hike, or a day of a backpacking trip
   (the same miles and climbing rate harder with a pack).
2. A sustained uphill (grade over a window along the smoothed profile) is
   tagged "steep"; a very steep one raises the level by one, at most, and never
   into strenuous. Short steep pitches don't count.
3. OSM terrain tags (sac_scale, trail_visibility) make a day at least hard,
   never strenuous by themselves: strenuous needs the effort.

Levels only go up from the effort level. Unknown elevation gives an unknown
level, never a guess. RULES carries a version so stored ratings can be
recomputed when the rules change; the web app's difficulty.ts is a port of
rate_stretch held to this one by a parity fixture.
"""

import bisect
import math
from dataclasses import asdict, dataclass
from types import SimpleNamespace

import numpy as np

from backcountry.elevation import SMOOTH_SAMPLES, smooth
from backcountry.scoring import M_PER_MI

LEVELS = ("easy", "moderate", "hard", "strenuous")

SCRAMBLE, DEMANDING, HARD_TO_FOLLOW = "scramble (tagged)", "demanding trail (tagged)", "hard to follow (tagged)"

RULES = {
    "version": 1,
    # effort below cutoffs[0] is easy, below cutoffs[1] moderate, below cutoffs[2] hard, else strenuous.
    # Tuned against the user's trips (tests/calibration/cases.yaml): Old Rag 217 (day hike) is hard,
    # Mount Tammany ~94 (day hike) moderate, Zion West Rim (backpacking) moderate for any camp from mile 6 to 10.
    "scales": {"day_hike": {"cutoffs": [60, 150, 250]}, "backpacking": {"cutoffs": [50, 140, 225]}},
    # tag_pct: shown as "steep"; raise_pct: also raises the level by one (not into strenuous).
    # Zion West Rim's 16% climb doesn't make its day hard; Old Rag's 25% is in a day already hard.
    "steep": {"window_m": 400, "tag_pct": 15, "raise_pct": 25},
    "terrain": {
        "min_m": 100,  # a tagged stretch shorter than this doesn't count
        "sac_scale": {"demanding_mountain_hiking": DEMANDING, "alpine_hiking": SCRAMBLE,
                      "demanding_alpine_hiking": SCRAMBLE, "difficult_alpine_hiking": SCRAMBLE},
        "trail_visibility": {"bad": HARD_TO_FOLLOW, "very_bad": HARD_TO_FOLLOW, "horrible": HARD_TO_FOLLOW,
                             "no": HARD_TO_FOLLOW},
    },
}

# What picking a level for a day sets: miles range, climbing cap (ft), steepness cap (% grade).
# The climbing cap keeps the top of the miles range inside the level: cap ~ cutoff**2 / (2 * miles_hi).
LEVEL_TARGETS = {
    "backpacking": {
        "easy": {"miles": [2, 5], "max_gain_ft": 250, "max_grade_pct": 10},
        "moderate": {"miles": [4, 8], "max_gain_ft": 1200, "max_grade_pct": 15},
        "hard": {"miles": [7, 12], "max_gain_ft": 2000, "max_grade_pct": 25},
        "strenuous": {"miles": [9, 16], "max_gain_ft": 4500, "max_grade_pct": None},
    },
    "day_hike": {
        "easy": {"miles": [1, 4], "max_gain_ft": 400, "max_grade_pct": 10},
        "moderate": {"miles": [3, 8], "max_gain_ft": 1400, "max_grade_pct": 15},
        "hard": {"miles": [5, 12], "max_gain_ft": 2600, "max_grade_pct": 25},
        "strenuous": {"miles": [8, 18], "max_gain_ft": 6000, "max_grade_pct": None},
    },
}


@dataclass
class Rating:
    level: str | None  # None = unknown
    reasons: list[str]  # short tags for what raised or set the level, e.g. "steep"
    effort: float | None
    steep: dict | None  # steepest sustained climb: {grade_pct, start_m, end_m}
    tagged: list[dict]  # terrain intervals with a reason overlapping the stretch
    untagged_share: float  # share of the stretch with no terrain data at all (0-1)

    def to_dict(self) -> dict:
        return asdict(self)


def level_index(level: str) -> int:
    return LEVELS.index(level)


def effort_score(gain_ft: float, miles: float) -> float:
    return math.sqrt(max(gain_ft, 0.0) * 2 * max(miles, 0.0))


def _stretch(d_m, z_m, start_m: float, end_m: float):
    """Indices of profile samples inside [start_m, end_m] (with a little slack for float camps)."""
    d = np.asarray(d_m, dtype=float)
    i, j = np.searchsorted(d, start_m - 1e-6, "left"), np.searchsorted(d, end_m + 1e-6, "right")
    return d[i:j], list(z_m[i:j])


def window_grades(d_m, z_m, window_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each profile sample p: the uphill grade (%) from p to the first sample at least `window_m` on,
    on the whole route's smoothed profile, and that sample's index. Grade is NaN where elevation is
    unknown or the window runs past the end. Smoothing the whole route (not each stretch) keeps a day's
    numbers the same however the day was cut, like the day split's gain.
    """
    d = np.asarray(d_m, dtype=float)
    z = np.array([np.nan if v is None else v for v in z_m], dtype=float)
    zs = smooth(z, SMOOTH_SAMPLES)
    end = np.searchsorted(d, d + window_m - 1e-6, "left")
    grade = np.full(len(d), np.nan)
    ok = end < len(d)
    i = np.nonzero(ok)[0]
    grade[i] = (zs[end[i]] - zs[i]) / (d[end[i]] - d[i]) * 100
    return d, grade, np.minimum(end, len(d) - 1)


def steepest_climb(d_m, z_m, start_m: float, end_m: float, window_m: float) -> dict | None:
    """The steepest uphill grade over `window_m` inside the stretch.

    None when elevation is unknown in the stretch, it is shorter than the window, or it never climbs.
    """
    d_s, z_s = _stretch(d_m, z_m, start_m, end_m)
    if len(d_s) < 2 or any(v is None for v in z_s) or d_s[-1] - d_s[0] < window_m - 1e-6:
        return None
    d, grade, end = window_grades(d_m, z_m, window_m)
    inside = (d >= start_m - 1e-6) & (d[end] <= end_m + 1e-6) & ~np.isnan(grade)
    if not inside.any():
        return None
    i = np.nonzero(inside)[0]
    best = int(i[np.argmax(grade[i])])
    if grade[best] <= 0:
        return None
    return {"grade_pct": float(grade[best]), "start_m": float(d[best]), "end_m": float(d[end[best]])}


def _edge_reason(edge: dict, rules: dict) -> tuple[bool, str | None]:
    """(has terrain data, reason or None) for one route edge."""
    tags = edge.get("tags") or {}
    terrain = rules["terrain"]
    sac, vis = tags.get("sac_scale"), tags.get("trail_visibility")
    reasons = [terrain["sac_scale"].get(sac), terrain["trail_visibility"].get(vis)]
    for r in (SCRAMBLE, DEMANDING, HARD_TO_FOLLOW):
        if r in reasons:
            return True, r
    return sac is not None or vis is not None, None


def route_terrain(route, min_m: float = RULES["terrain"]["min_m"], rules: dict = RULES) -> list[dict]:
    """Walking-order intervals of the route with terrain data: {start_m, end_m, reason, sources}.

    `reason` is a tag reason, or None where the trail is tagged but nothing in the tags raises a level.
    Roads carry no terrain. Adjacent edges with the same reason merge; merged intervals with a reason
    shorter than `min_m` are dropped. `sources` is provenance for the details view, never shown as text.
    """
    out, at = [], 0.0
    for e in route.edges:
        start, at = at, at + e["length_m"]
        if e.get("is_road"):
            continue
        known, reason = _edge_reason(e, rules)
        if not known:
            continue
        last = out[-1] if out else None
        if last and last["reason"] == reason and abs(last["end_m"] - start) < 1e-6:
            last["end_m"] = at
            if e["source"] not in last["sources"]:
                last["sources"].append(e["source"])
        else:
            out.append({"start_m": start, "end_m": at, "reason": reason, "sources": [e["source"]]})
    return [t for t in out if t["reason"] is None or t["end_m"] - t["start_m"] >= min_m]


def _overlap(t: dict, start_m: float, end_m: float) -> float:
    return max(0.0, min(t["end_m"], end_m) - max(t["start_m"], start_m))


def rate_stretch(d_m, z_m, terrain: list[dict], start_m: float, end_m: float, gain_ft: float | None,
                 scale: str, rules: dict = RULES) -> Rating:
    length = max(end_m - start_m, 0.0)
    tagged = [t for t in terrain if t["reason"] and _overlap(t, start_m, end_m) > 0]
    covered = sum(_overlap(t, start_m, end_m) for t in terrain)
    untagged = 1.0 - min(covered / length, 1.0) if length > 0 else 1.0
    _, z = _stretch(d_m, z_m, start_m, end_m)
    if gain_ft is None or not z or any(v is None for v in z):
        return Rating(None, [], None, None, tagged, untagged)
    effort = effort_score(gain_ft, length / M_PER_MI)
    level = bisect.bisect_right(rules["scales"][scale]["cutoffs"], effort)
    reasons = []
    steep = steepest_climb(d_m, z_m, start_m, end_m, rules["steep"]["window_m"])
    hard = level_index("hard")
    if steep and steep["grade_pct"] >= rules["steep"]["tag_pct"]:
        reasons.append("steep")
        if steep["grade_pct"] >= rules["steep"]["raise_pct"] and level < hard:
            level += 1
    if tagged:
        level = max(level, hard)
        reasons += list(dict.fromkeys(t["reason"] for t in tagged))
    return Rating(LEVELS[level], reasons, effort, steep, tagged, untagged)


def trip_level(ratings: list[Rating]) -> str | None:
    """A trip is as hard as its hardest day; unknown if any day is unknown."""
    if not ratings or any(r.level is None for r in ratings):
        return None
    return max((r.level for r in ratings), key=level_index)


def segments_terrain(tg, segments: list[dict], rules: dict = RULES) -> list[dict]:
    """Terrain intervals for a saved route, rebuilt from the graph's tags by the way ids in its segments.

    A segment counts only if every way in it is in the graph and gives the same terrain reason; a mixed or
    unknown segment adds nothing (terrain unknown there), so a saved trip is never rated harder than its
    data supports.
    """
    tags_by_source = {}
    for *_, d in tg.graph.edges(data=True):
        tags_by_source.setdefault(d["source"], d.get("tags") or {})
    edges = []
    for s in segments:
        found = [tags_by_source.get(src) for src in s["sources"]]
        results = {_edge_reason({"tags": t}, rules) for t in found if t is not None}
        uniform = None not in found and len(results) == 1 and next(iter(results))[0]
        tags = found[0] if uniform else {}
        edges.append({"length_m": s["length_m"], "is_road": s.get("is_road", False), "tags": tags,
                      "source": s["sources"][0] if s["sources"] else ""})
    out = route_terrain(SimpleNamespace(edges=edges), rules["terrain"]["min_m"], rules)
    for t in out:  # keep every way of the merged segments as provenance
        t["sources"] = list(dict.fromkeys(src for s, (a, b) in zip(segments, _spans(segments))
                                          if a < t["end_m"] and b > t["start_m"] for src in s["sources"]))
    return out


def _spans(segments):
    at = 0.0
    for s in segments:
        yield at, at + s["length_m"]
        at += s["length_m"]


def rate_saved(profile: dict, terrain: list[dict], nights: int | None, camps_m: list[float] | None,
               rules: dict = RULES) -> dict:
    """Days and levels for a saved route with these camps (as the phone stores them), the way split_route rates."""
    from backcountry.elevation import FT_PER_M, cumulative_gain_loss

    d, z = profile["d_m"], profile["z_m"]
    known = all(v is not None for v in z)
    G = cumulative_gain_loss(smooth(np.array(z, dtype=float)))[0] * FT_PER_M if known else None
    gain = (lambda a, b: float(np.interp(b, d, G) - np.interp(a, d, G))) if known else (lambda a, b: None)
    if not nights:
        r = rate_stretch(d, z, terrain, 0.0, d[-1], gain(0.0, d[-1]), "day_hike", rules)
        return {"terrain": terrain, "days": [], "level": r.level, "rating": r.to_dict(), "difficulty_rules": rules}
    bounds = [0.0, *camps_m, float(d[-1])]
    days = []
    for a, b in zip(bounds, bounds[1:]):
        r = rate_stretch(d, z, terrain, a, b, gain(a, b), "backpacking", rules)
        days.append({"start_m": a, "end_m": b, "gain_ft": gain(a, b), "rating": r.to_dict()})
    ratings = [Rating(**x["rating"]) for x in days]
    return {"terrain": terrain, "days": days, "level": trip_level(ratings), "rating": None, "difficulty_rules": rules}
