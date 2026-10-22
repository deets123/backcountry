import json

from levels_json import FILE, build


def test_levels_json_matches_the_engine():
    assert FILE.exists(), "run `uv run python tests/levels_json.py`"
    assert json.loads(FILE.read_text()) == json.loads(json.dumps(build()))
