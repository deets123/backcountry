"""The engine's day split for fixed camp positions, as a fixture for the phone's resplit.ts.

    uv run python tests/resplit_fixture.py   # rewrites web/src/fixtures/resplit-parity.json

tests/test_resplit_fixture.py fails when the committed file no longer matches what the engine produces,
so the phone's numbers and the engine's can't drift apart silently.
"""

import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import CRS, build_network  # noqa: E402

from backcountry.elevation import densify  # noqa: E402
from backcountry.graph import build_graph  # noqa: E402
from backcountry.route import shortest_route  # noqa: E402
from backcountry.split import split_route  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "web" / "src" / "fixtures" / "resplit-parity.json"
M = 1609.344
LIMITS = {"daily_min_m": 0.5 * M, "daily_max_m": 1.0 * M, "max_gain_ft": 150.0}
CASES = [  # (name, has elevation, camp distances in meters along the 3,500 m test route)
    ("one night, day 2 too long", True, [1400.0]),
    ("one night, day 1 too short", True, [600.0]),
    ("two nights", True, [700.0, 2600.0]),
    ("no elevation", False, [1750.0]),
]


def _route(elevation: bool):
    net = build_network()
    tg = build_graph(net.doc(), crs=CRS)
    route = shortest_route(tg, net.ids["A"], net.ids["F"])
    d, _ = densify(route.line_utm)
    if elevation:  # rolling terrain: some ups and downs under the 2 m hysteresis, some well over it
        z = [1000 + 12 * math.sin(x / 150) + 3 * math.sin(x / 23) + 0.01 * x for x in d]
        route.profile, route.gain_ft = {"d_m": d, "z_m": z}, 0.0  # split_route only needs gain_ft to be known
    else:
        route.profile, route.gain_ft = {"d_m": d, "z_m": [None] * len(d)}, None
    return route


def build() -> dict:
    cases = []
    for name, elevation, camps in CASES:
        route = _route(elevation)
        plan = split_route(route, len(camps), LIMITS["daily_min_m"], LIMITS["daily_max_m"], LIMITS["max_gain_ft"],
                           pins_m=camps)
        days = [{k: (float(v) if isinstance(v, float) else v) for k, v in asdict(day).items() if k != "rating"}
                for day in plan.days]  # ratings: difficulty-parity.json
        cases.append({"name": name, "profile": route.profile, "camps_m": camps, "days": days,
                      "violations": list(plan.violations)})
    return {"limits": LIMITS, "cases": cases}


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {FIXTURE}")
