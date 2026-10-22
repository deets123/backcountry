"""The engine's difficulty ratings on synthetic stretches, as a fixture for the phone's difficulty.ts.

    uv run python tests/difficulty_fixture.py   # rewrites web/src/fixtures/difficulty-parity.json

tests/test_difficulty_fixture.py fails when the committed file no longer matches what the engine produces,
so the levels the phone shows for moved camps can't drift from the engine's.
"""

import json
import math
from pathlib import Path

from backcountry.difficulty import RULES, rate_saved

FIXTURE = Path(__file__).resolve().parents[1] / "web" / "src" / "fixtures" / "difficulty-parity.json"
STEP = 30.48


def _profile(pieces, missing_at=None):
    """pieces: [(length_m, grade_pct)] from 300 m elevation; optional sample index with no elevation."""
    d, z = [0.0], [300.0]
    for length, grade in pieces:
        n = max(1, round(length / STEP))
        for _ in range(n):
            d.append(d[-1] + length / n)
            z.append(z[-1] + length / n * grade / 100 + 0.4 * math.sin(len(d) / 3))  # a little DEM-like noise
    zs = [None if i == missing_at else round(v, 3) for i, v in enumerate(z)]
    return {"d_m": [round(x, 3) for x in d], "z_m": zs}


def _tag(a, b, reason):
    return {"start_m": a, "end_m": b, "reason": reason, "sources": ["osm:way/1"]}


CASES = [  # (name, pieces, terrain, nights, camps_m, missing sample)
    ("flat day hike", [(5000, 0)], [], None, None, None),
    ("long steep climb raises one level", [(700, 0), (600, 28), (700, 0)], [], None, None, None),
    ("moderately steep: tagged, not raised", [(700, 0), (600, 18), (700, 0)], [], None, None, None),
    ("steep never into strenuous", [(9000, 9), (600, 30), (2000, 0)], [], 1, [6000.0], None),
    ("tagged scramble and untagged share", [(3000, 4), (3000, -4)], [_tag(500, 800, "scramble (tagged)"),
                                                                    _tag(800, 2000, None)], 1, [2500.0], None),
    ("descent doesn't count", [(8000, -6), (4000, 1)], [], 2, [4000.0, 9000.0], None),
    ("three days, backpacking", [(4000, 2), (5000, 8), (3000, -10), (6000, 3)], [_tag(9000, 9400, "demanding trail (tagged)")],
     2, [5000.0, 11000.0], None),
    ("no elevation", [(5000, 5)], [], 1, [2000.0], 40),
    ("strenuous long climb (day hike)", [(19300, 4.7)], [], None, None, None),
    ("a camp just inside a scramble: the next day only grazes it", [(6000, 1)], [_tag(1000, 3000.5, "scramble (tagged)")],
     1, [3000.0], None),
]


def build() -> dict:
    cases = []
    for name, pieces, terrain, nights, camps, missing in CASES:
        prof = _profile(pieces, missing)
        cases.append({"name": name, "profile": prof, "terrain": terrain, "nights": nights, "camps_m": camps,
                      "expected": rate_saved(prof, terrain, nights, camps)})
    return {"rules": RULES, "cases": cases}


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {FIXTURE}")
