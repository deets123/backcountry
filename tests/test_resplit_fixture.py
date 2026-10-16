import json

from resplit_fixture import FIXTURE, build


def test_resplit_fixture_matches_the_engine():
    assert FIXTURE.exists(), "run `uv run python tests/resplit_fixture.py`"
    assert json.loads(FIXTURE.read_text()) == json.loads(json.dumps(build()))
