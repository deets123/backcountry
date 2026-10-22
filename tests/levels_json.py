"""The engine's level table and rating rules, as JSON the web app ships with.

    uv run python tests/levels_json.py   # rewrites web/src/levels.json

The plan form needs them before any trip is generated, and offline. tests/test_levels_json.py fails when the
committed file no longer matches the engine, so "Easy" can't mean one thing on the phone and another here.
"""

import json
from pathlib import Path

from backcountry.difficulty import LEVEL_TARGETS, LEVELS, RULES

FILE = Path(__file__).resolve().parents[1] / "web" / "src" / "levels.json"


def build() -> dict:
    return {"levels": list(LEVELS), "level_targets": LEVEL_TARGETS, "rules": RULES}


if __name__ == "__main__":
    FILE.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {FILE}")
