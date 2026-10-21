"""The model comparison: run the chat test set against one model and score it.

Cases live in evals/chat_cases.yaml. Each runs against the same Bear Rocks run (built from cached Dolly Sods data,
so no OSM-derived file is committed) and says what must be true of the answer. Results go to evals/results/ (git-ignored).
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from backcountry.chat.models import ModelError
from backcountry.chat.service import chat_turn, start_names, trail_names
from backcountry.scoring import M_PER_MI

EVAL_REQUEST = {"trailhead": "bear_rocks", "total": "18-26", "nights": 2, "daily": "6-11", "max_gain_day": 2500}
EXPECT_KEYS = {"kind", "request", "request_contains", "lower", "day_shorter", "day_longer", "no_proposal", "grounded",
               "reply_includes_any", "proposal_or_note_includes", "mentions_hardest_day", "mentions_longest_day",
               "mentions_number"}


def load_cases(path: Path) -> list[dict]:
    cases = yaml.safe_load(path.read_text())
    for c in cases:
        if not {"id", "message", "expect"} <= set(c):
            raise ValueError(f"case {c.get('id')!r} needs id, message and expect")
        unknown = set(c["expect"]) - EXPECT_KEYS
        if unknown:
            raise ValueError(f"case {c['id']!r}: unknown expectation(s) {sorted(unknown)}")
    return cases


def _day_miles(request: dict, run: dict, n: int) -> list[float] | None:
    """Day lengths (miles) a proposal's camp pins would give, or None without a full set of pins."""
    pins = [float(x) for x in request.get("camps") or []]
    if not pins or len(pins) != (request.get("nights") or 0):
        return None
    bounds = [0.0, *pins, run["candidates"][n - 1]["length_m"] / M_PER_MI]
    return [b - a for a, b in zip(bounds, bounds[1:])]


def _mentions_day(reply: str, day: int) -> bool:
    return re.search(rf"\bday {day}\b", reply, re.IGNORECASE) is not None


_SAME = re.compile(r"\b(same|equal|identical|tied?)\b", re.IGNORECASE)


def _days_text(days: list[int]) -> str:
    return str(days[0]) if len(days) == 1 else ", ".join(map(str, days[:-1])) + f" or {days[-1]}"


def _mentions_number(reply: str, value: float) -> bool:
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", reply):
        decimals = len(token.split(".")[1]) if "." in token else 0
        if abs(float(token.replace(",", "")) - value) <= 0.5 * 10 ** -decimals + 1e-9:
            return True
    return False


def check_case(case: dict, result: dict | None, error: str | None, run: dict, n: int = 1) -> list[str]:
    if error:
        return [f"error: {error}"]
    e, problems = case["expect"], []
    c, current = run["candidates"][n - 1], run["request"]
    proposal = result.get("proposal")
    req = proposal["request"] if proposal else None
    reply = result.get("reply") or ""
    if "kind" in e and result["kind"] != e["kind"]:
        problems.append(f"kind is {result['kind']}, expected {e['kind']}")
    needs_proposal = any(k in e for k in ("request", "request_contains", "lower", "day_shorter", "day_longer")) or e.get("kind") == "change"
    if needs_proposal and "proposal_or_note_includes" not in e and req is None:
        problems.append("no proposal")
    if req is not None:
        for key, want in (e.get("request") or {}).items():
            got = req.get(key)
            same = ([float(x) for x in got] == [float(x) for x in want]) if isinstance(got, list) and isinstance(want, list) else got == want
            if not same:
                problems.append(f"{key} is {got}, expected {want}")
        for key, want in (e.get("request_contains") or {}).items():
            if want not in (req.get(key) or []):
                problems.append(f"{key} doesn't include {want}")
        if "lower" in e:
            key = e["lower"]
            before, after = current.get(key), req.get(key)
            if after is None or (before is not None and not after < before):
                problems.append(f"{key} did not go down ({before} → {after})")
        for key, sign in (("day_shorter", -1), ("day_longer", 1)):
            if key not in e:
                continue
            day = e[key]
            now = c["plan"]["days"][day - 1]["length_m"] / M_PER_MI
            miles = _day_miles(req, run, n)
            if miles is not None:
                ok = (miles[day - 1] - now) * sign > 0.05
            else:
                ok = sign < 0 and req["daily"] is not None and req["daily"][1] < now
            if not ok:
                problems.append(f"day {day} is not {'shorter' if sign < 0 else 'longer'}")
    if e.get("no_proposal") and req is not None:
        problems.append("made a proposal")
    if e.get("grounded") and result.get("withheld"):
        problems.append(f"reply was withheld ({result['withheld']})")
    if "reply_includes_any" in e and not any(w.lower() in reply.lower() for w in e["reply_includes_any"]):
        problems.append(f"reply has none of: {', '.join(e['reply_includes_any'])}")
    if "proposal_or_note_includes" in e:
        text = " ".join((proposal or {}).get("changes", [])) + " " + (result.get("note") or "") + " " + json.dumps(req or {})
        if e["proposal_or_note_includes"].lower() not in text.lower():
            problems.append(f"neither proposal nor note mentions {e['proposal_or_note_includes']}")
    days = (c.get("plan") or {}).get("days") or []
    for flag, key, tol in (("mentions_hardest_day", "gain_ft", 1.0), ("mentions_longest_day", "length_m", 0.01 * M_PER_MI)):
        if not (e.get(flag) and days):
            continue
        top = max(d[key] or 0 for d in days)
        tied = [d["index"] for d in days if top - (d[key] or 0) <= tol]  # days within rounding of the top one
        if not (any(_mentions_day(reply, i) for i in tied) or (len(tied) > 1 and _SAME.search(reply))):
            problems.append(f"reply doesn't name day {_days_text(tied)}")
    if "mentions_number" in e:
        key = e["mentions_number"]
        value = c[key] / M_PER_MI if key.endswith("_m") else c[key]
        if not _mentions_number(reply, value):
            problems.append(f"reply doesn't give {key} ({value:.2f})")
    return problems


def run_eval(model, run: dict, cases: list[dict], trails: list[str], starts: list[dict], clock=time.monotonic) -> dict:
    rows = []
    for case in cases:
        t0 = clock()
        result = error = None
        try:
            result = chat_turn(model, run, 1, case["message"], case.get("history") or [], trails, starts)
        except ModelError as ex:
            error = str(ex)
        seconds = clock() - t0
        problems = check_case(case, result, error, run)
        rows.append({"id": case["id"], "ok": not problems, "problems": problems, "seconds": round(seconds, 2),
                     "kind": result and result["kind"], "reply": result and result["reply"],
                     "proposal": result and result["proposal"], "note": result and result["note"],
                     "withheld": result and result["withheld"], "error": error})
    summary = {"model": model.name, "cases": len(rows), "passed": sum(r["ok"] for r in rows),
               "errors": sum(r["error"] is not None for r in rows), "withheld": sum(bool(r["withheld"]) for r in rows),
               "mean_s": round(sum(r["seconds"] for r in rows) / max(len(rows), 1), 2)}
    return {"summary": summary, "cases": rows}


def summary_table(results: list[dict]) -> str:
    lines = [f"{'model':<28} {'passed':>8} {'errors':>7} {'withheld':>9} {'mean s':>7}"]
    for r in results:
        s = r["summary"]
        lines.append(f"{s['model']:<28} {s['passed']}/{s['cases']:<6} {s['errors']:>7} {s['withheld']:>9} {s['mean_s']:>7.1f}")
    return "\n".join(lines)


def default_run() -> tuple[dict, list[str], list[dict]]:
    """The eval's Bear Rocks run, planned from cached Dolly Sods data, with the region's names."""
    from backcountry.api.store import RegionStore, Settings
    from backcountry.planning import GenerateRequest, plan_trip
    from backcountry.serialize import run_payload

    loaded = RegionStore(Settings.default()).get("dolly_sods")
    result = plan_trip(loaded.region, loaded.raw, loaded.tg, GenerateRequest.from_dict(EVAL_REQUEST), dem=loaded.dem)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run = run_payload("eval", loaded.region, result, loaded.raw, loaded.tg, now, diff=None)
    return run, trail_names(loaded.tg), start_names(loaded.region)


def save_result(out: dict, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"{stamp}-{re.sub(r'[^A-Za-z0-9.]+', '_', out['summary']['model'])}.json"
    path.write_text(json.dumps(out, indent=1))
    return path
