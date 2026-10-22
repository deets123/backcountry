"""Objective function for candidate routes (M4).

    penalty = miles outside the requested range * out_of_range
            + road miles * road
            + last-resort road miles * last_resort      (on top of `road`)
            + miles walked twice (lollipop stem) * repeat
            + after day splitting (M5), per day:
                miles outside the daily range * daily_out_of_range
              + thousands of ft over the daily gain cap * gain_over_cap
              + miles away from an equal split * imbalance
              + with per-day targets: level steps away from a day's level * level_miss,
                and % grade over a day's steepness cap * gain_over_cap

Lower is better. All weights are per mile so they're easy to reason about:
road=1.0 means a road mile is as unwelcome as an extra mile of distance.
"""

from dataclasses import dataclass

M_PER_MI = 1609.344


@dataclass(frozen=True)
class Weights:
    out_of_range: float = 10.0
    road: float = 1.0
    last_resort: float = 5.0
    repeat: float = 0.5
    daily_out_of_range: float = 10.0
    gain_over_cap: float = 5.0  # per 1,000 ft over the cap
    imbalance: float = 0.5
    level_miss: float = 5.0  # per level step a day lands away from its target level


def distance_deviation_m(length_m: float, min_m: float, max_m: float) -> float:
    return max(0.0, min_m - length_m, length_m - max_m)


def penalty(length_m: float, road_m: float, last_resort_m: float, repeated_m: float,
            min_m: float, max_m: float, w: Weights = Weights()) -> float:
    return (distance_deviation_m(length_m, min_m, max_m) * w.out_of_range
            + road_m * w.road
            + last_resort_m * w.last_resort
            + repeated_m * w.repeat) / M_PER_MI


def overlap(keys_a, keys_b, lengths: dict) -> float:
    """Length-weighted Jaccard overlap of two edge-key sets (0 = disjoint, 1 = identical)."""
    a, b = set(keys_a), set(keys_b)
    union = sum(lengths[k] for k in a | b)
    return sum(lengths[k] for k in a & b) / union if union else 0.0
