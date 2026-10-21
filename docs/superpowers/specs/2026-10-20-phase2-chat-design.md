# Phase 2 piece 4: Chat layer (design)

Status: approved in conversation 2026-10-20. Builds on the API and web app
(`docs/superpowers/specs/2026-10-13-phase2-api-and-web-app-design.md`) and offline trips
(`docs/superpowers/specs/2026-10-14-phase2-offline-design.md`), both merged.

## Intent and constraints (agreed)

- A chat box in the web app does two things in this version:
  1. **Change settings**: "make day 2 shorter", "avoid roads", "3 nights instead", "camp near Red Creek" become a
     proposed change to the trip settings that the user confirms with **Run**; the engine re-plans as usual.
  2. **Explain results**: "which day is hardest?", "how far is camp 2 from water?" are answered only from the open
     trip's numbers and data, with the same "unknown" wording the app uses; it says when it can't answer.
- **Local model first.** The chat runs on a local model through Ollama (already installed on the user's M1 Pro,
  16 GB, with qwen3:14b, qwen3.5:9b, gemma4:12b, gpt-oss:20b, qwen2.5:7b-instruct, llama3.2, llama3). Paid Claude
  models are compared later on the same test set, before the user pays for anything.
- The chat never produces geometry and never invents facts. Routes come only from the engine; every CLAUDE.md rule
  (no invented water/campsites/permits/closures/conditions/access, never "safe", miles and feet, no raw OSM ids in
  user-facing text) applies to chat replies.
- Never commit API keys, secrets or `.env` files (the repo is public). `.env` is git-ignored before any key exists;
  `.env.example` (no values) is committed.
- The chat needs the laptop server; offline trips keep working without it.

## Decisions (with the user)

| Question | Decision |
|---|---|
| Scope | Change settings + explain results. Not: planning from scratch by talking, moving camps from chat. |
| Model | Local (Ollama) first; compare models with an eval set before paying. Claude support later behind the same interface. |
| How words become changes | **A: structured proposal.** The model returns `{reply, kind, changes}` in a fixed JSON shape; the server validates the changes with the engine's own rules; the user taps Run. |

## Architecture

```
web chat box ──POST /api/chat──► chat.facts (fact sheet from the stored run)
                                  chat.models (Ollama now; Claude later) ── model ──► {reply, kind, changes}
                                  chat.proposal (merge + GenerateRequest.from_dict + names check + before/after)
                                  chat.grounding (honesty check on the reply)
             ◄── {reply, kind, proposal?, withheld?, model} ──
Run ──POST /api/regions/{id}/generate (existing, unchanged)──► Compare
```

## Components

### `src/backcountry/chat/facts.py`
`fact_sheet(run: dict, n: int, region_trails: list[str], trailheads: list[dict]) -> str` builds a short plain-text
sheet, miles and feet only:
- Current settings in words (trailhead name, type, total range, nights, daily range, gain cap, roads, direction,
  must-visit, avoid, camp pins, camps-at-sites).
- The route: miles, gain/loss, road miles, repeated miles, trail names in order.
- Each day: from → to, miles, gain, loss. Each camp: mile, trail, how it was placed, "location unverified", known
  sites within 1,640 ft, nearest water with "(presence unknown)".
- Campsites and water along the route: mile, kind, name, off-route distance, "(presence unknown)" for water,
  "(existence and status unknown)" for campsites.
- Engine notes and constraint violations, verbatim. Known gaps ("Not checked: …").
- Trail names in the region and usable trailhead names (for changes).
No raw OSM ids. `facts_numbers(sheet) -> set` returns the numbers it contains (for grounding).

### `src/backcountry/chat/models.py`
- `ChatModel` protocol: `complete(system: str, messages: list[dict], schema: dict, timeout_s: float) -> dict`.
- `OllamaModel(name, url)`: POST to Ollama's `/api/chat` with `format` set to the JSON schema, `temperature: 0`,
  `stream: false`, using Python's standard library (`urllib.request`), so no new dependency.
- `FakeModel(responses)` for tests.
- `model_from_env()` reads `BACKCOUNTRY_LLM` (`ollama:<model>`, default `ollama:qwen3:14b` until the eval picks one)
  and `OLLAMA_URL` (default `http://127.0.0.1:11434`) from the environment, loading `.env` at the repo root if present
  (a few lines of parsing; no dependency).
- Errors: `ModelUnavailable` (connection refused/unreachable), `ModelTimeout` (60 s), `ModelBadOutput` (not JSON or
  wrong shape, after one retry with the error fed back).

### `src/backcountry/chat/proposal.py`
- `SYSTEM_PROMPT`: the rules (answer only from the facts; never say a route, water or campsite is safe, open, legal,
  available or present; changes only through `changes`; ask one question when a request is ambiguous; short replies;
  miles and feet).
- `RESPONSE_SCHEMA`: `{reply: string, kind: "change"|"explain"|"unclear", changes: object}` where `changes` may hold
  only: `nights`, `total` ([lo, hi] miles), `daily` ([lo, hi] miles), `max_gain_day` (feet or null), `type`
  (loop/lollipop/any), `no_roads`, `direction` (cw/ccw/null), `must_visit_add`, `must_visit_remove`, `avoid_add`,
  `avoid_remove`, `camps` (list of miles along the route, or [] to clear), `camps_at_sites`, `trailhead`.
- `apply_changes(request: dict, changes: dict, trail_names, trailheads) -> (GenerateRequest, list[str])`: merges,
  resolves trailhead names to ids, checks trail names exist ("no trail named 'Red Creek Trial' here; did you mean
  Red Creek Trail?" via difflib), validates with `GenerateRequest.from_dict`, and returns the before/after lines
  ("Nights: 2 → 3", "Camps: placed for mileage → pinned at mile 6.3 and 11.0"). Raises `ProposalError(message)`
  with the reason when anything fails; a change that changes nothing returns no lines and is said plainly.

### `src/backcountry/chat/grounding.py`
`check_reply(reply: str, sheet: str, allowed_names: set[str]) -> (ok: bool, reasons: list[str])`:
- Numbers: each number in the reply (with or without units) must match a number in the sheet after rounding to the
  reply's precision (6.3 matches 6.34; 784 matches 784; 1,640 matches 1640). Small integers 0–10 are allowed when
  they are day/camp/night counts present in the sheet.
- Banned claims (case-insensitive patterns): "safe", "water is (available|flowing|drinkable|reliable|there)",
  "reliable water", "(open|closed) (to|for)", "legal", "permitted", "no permit", "permit (isn't|is not) needed",
  "conditions are", "the trail is (clear|in good shape)", and similar; "unknown"/"not checked" phrasing is fine.
- Trail and place names: capitalized multi-word names followed by Trail/Run/Road/Creek/etc. must appear in the
  sheet.
- A failing reply is withheld: the user sees "I can't answer that from this trip's data." (for `explain`), or the
  proposal without the model's prose (for `change`), plus a small "(answer withheld: <first reason>)".

### `src/backcountry/api/endpoints.py`: `POST /api/chat`
Request: `{run_id, candidate, message, history: [{role, text}] (last 6)}`.
Response: `{reply, kind, proposal: {request, changes: [lines]} | null, withheld: string | null, model}`.
Errors as JSON like the rest of the API: 400 bad request, 404 unknown run/candidate, 503 model unavailable
("The chat model isn't reachable (Ollama at 127.0.0.1:11434). Start Ollama, or plan with the form."), 504 timeout,
502 bad model output. The run itself is never modified; Run uses the existing generate endpoint.

### CLI: `backcountry chat-eval`
`backcountry chat-eval --model ollama:qwen3:14b [--model …] [--cases evals/chat_cases.yaml]`:
- Runs each case against a stored Dolly Sods run fixture (`evals/run_bear_rocks.json`, a real run saved once).
- Case format: `message`, optional `history`, and expectations: `kind`, `changes` (field → expected value or
  direction like `"<"` relative to the current value), `reply_must_include` / `reply_must_not_include`,
  `grounded: true`.
- About 30 cases: ~20 change requests (shorter day 2, 3 nights, avoid roads, skip Dolly Sods Road, camp near Red
  Creek, less climbing per day, start from another trailhead, clockwise, a misspelled trail, ambiguous "make it
  easier" → unclear) and ~10 questions (hardest day, water near camp 2, "is the water reliable?" → unknown, "is this
  safe?" → never yes, total road miles, a question the facts can't answer → can't answer).
- Prints a table per model: changes correct, valid JSON, honesty failures, unclear handled, mean seconds; writes
  `evals/results/<date>-<model>.json` (git-ignored).

### Web
- A chat box at the bottom of the Compare and Trip views: message list, input, Send. "Thinking… (local model, can
  take ~20 s)" while waiting.
- A proposal shows as a card listing the changed settings, with **Run** (calls the existing generate with the
  proposal's request) and **Dismiss**. Explanations never have a Run button.
- Withheld answers show the fallback text and the small reason. Errors show the server's wording.
- Offline or a saved trip without the server: "Chat needs the server" and the box is disabled.
- Chat history lives in memory for the session only.
- Plain styling; visual polish is the separate frontend pass.

## Configuration and secrets
- `.gitignore` gains `.env` and `evals/results/` first, before any key exists.
- `.env.example` (committed) lists `BACKCOUNTRY_LLM=ollama:qwen3:14b` and `OLLAMA_URL=http://127.0.0.1:11434`, with
  a commented `ANTHROPIC_API_KEY=` placeholder for later.
- Local models send nothing off the laptop. When Claude is added later, the fact sheet and messages go to
  Anthropic; the README will say so.

## Testing
- pytest (fake model, no Ollama needed): fact sheet from a stored run (units, unknown wording, no raw ids); merge and
  validation of each change field; unknown names with suggestions; grounding (matching/non-matching numbers,
  banned claims, invented names); retry on bad JSON, timeout, unavailable model; `/api/chat` end to end with the
  fake model injected; `chat-eval` scoring logic on fake outputs.
- Vitest: chat panel rendering (messages, proposal card, Run/Dismiss, withheld note, unavailable/offline states).
- Playwright: synthetic API with a fake model (selected by an environment variable in `scripts/test_api_server.py`):
  "make day 1 shorter" → card → Run → Compare shows new candidates.
- Manual: `chat-eval` across the installed local models; the user's phone check over Tailscale.

## Out of scope
Planning from scratch by talking, moving camps from chat, chat offline, voice, chat history across sessions,
Claude support itself (added after the local eval, with the `anthropic` dependency and the user's key), visual polish.

## Done when
On the user's phone, a change typed on a Bear Rocks trip gives a sensible proposal, Run gives new candidates, and a
question gets an answer from the trip's numbers or an honest "can't answer"; all suites pass; `chat-eval` has scored
every installed local model and the default model is chosen from those results.
