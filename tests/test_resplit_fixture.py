import json

from resplit_fixture import FIXTURE, build

PLACES = 6  # a micrometre: the platforms' math libraries differ around 1e-9 m, which isn't a stale fixture


def _rounded(x):
    if isinstance(x, float):
        return round(x, PLACES)
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    return x


def test_rounding_ignores_platform_noise_but_not_real_changes():
    assert _rounded({"end_m": [3500.0000000003492]}) == _rounded({"end_m": [3499.9999999998836]})
    assert _rounded({"end_m": [3500.0]}) != _rounded({"end_m": [3500.01]})


def test_resplit_fixture_matches_the_engine():
    assert FIXTURE.exists(), "run `uv run python tests/resplit_fixture.py`"
    assert _rounded(json.loads(FIXTURE.read_text())) == _rounded(json.loads(json.dumps(build())))
