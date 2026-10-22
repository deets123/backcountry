"""One chat turn: facts in, a checked reply and (maybe) a validated proposal out. Nothing is run or saved."""

import json

from backcountry.chat.facts import fact_sheet
from backcountry.chat.grounding import CANT_ANSWER, check_reply
from backcountry.chat.models import ChatModel
from backcountry.chat.proposal import RESPONSE_SCHEMA, SYSTEM_PROMPT, ProposalError, apply_changes
from backcountry.scoring import M_PER_MI

HISTORY = 6  # earlier messages sent along with the new one


def trail_names(tg) -> list[str]:
    return sorted({d["name"] for *_, d in tg.graph.edges(data=True) if d.get("name")})


def start_names(region) -> list[dict]:
    return [{"id": th["id"], "name": th.get("name") or th["id"]} for th in region.trailheads]


def _history(history: list[dict]) -> list[dict]:
    kept = [{"role": h["role"], "content": str(h.get("text") or "").strip()} for h in history
            if isinstance(h, dict) and h.get("role") in ("user", "assistant")]
    return [m for m in kept if m["content"]][-HISTORY:]


def chat_turn(model: ChatModel, run: dict, n: int, message: str, history: list[dict], trails: list[str], starts: list[dict]) -> dict:
    facts = fact_sheet(run, n, trails, starts)
    answer = model.complete(f"{SYSTEM_PROMPT}\n\nFACTS\n{facts}", [*_history(history), {"role": "user", "content": message}],
                            RESPONSE_SCHEMA)
    kind, reply, changes = answer["kind"], answer["reply"].strip(), answer.get("changes") or {}
    proposal = note = None
    camps = [cp["dist_m"] / M_PER_MI for cp in ((run["candidates"][n - 1].get("plan") or {}).get("camps") or [])]
    if kind == "change" and not changes:
        note = "No setting change was proposed; try saying what to change."
    elif kind == "change":
        try:
            request, lines = apply_changes(run["request"], changes, trails, starts, current_camps=camps)
            if lines:
                proposal = {"request": request, "changes": lines}
            else:
                note = "That wouldn't change any setting."
        except ProposalError as e:
            note = str(e)
    ok, reasons = check_reply(reply, f"{facts}\n{json.dumps(changes)}",  # numbers in the proposal may be quoted
                              check_levels=kind != "change")  # a change reply names target levels, not computed ones
    withheld = None
    if not ok:
        withheld = reasons[0]
        reply = "" if proposal else CANT_ANSWER
    return {"reply": reply, "kind": kind, "proposal": proposal, "note": note, "withheld": withheld, "model": model.name}
