import json

from difficulty_fixture import FIXTURE, build
from test_resplit_fixture import _rounded


def test_difficulty_fixture_matches_the_engine():
    assert FIXTURE.exists(), "run `uv run python tests/difficulty_fixture.py`"
    assert _rounded(json.loads(FIXTURE.read_text())) == _rounded(json.loads(json.dumps(build())))


def test_the_fixture_covers_every_level_and_reason():
    cases = build()["cases"]
    ratings = [r for c in cases for r in ([c["expected"]["rating"]] if c["expected"]["rating"] else [])
               + [d["rating"] for d in c["expected"]["days"]]]
    assert {r["level"] for r in ratings} >= {"easy", "moderate", "hard", "strenuous", None}
    assert {x for r in ratings for x in r["reasons"]} >= {"steep", "scramble (tagged)", "demanding trail (tagged)"}
