# Per-day Difficulty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Label every day and trip easy / moderate / hard / strenuous with a short sourced reason. Let users set
per-day targets (numbers or levels) from the form and the chat. Filter loops by trip level. When targets can't all be
met, return the closest fit with an engine-checked suggestion.

**Architecture:**
- A new pure module, `src/backcountry/difficulty.py`, rates a stretch of route from its elevation profile and OSM
  terrain tags, using a versioned rules table with two scales: day hike and backpacking.
- The day splitter scores each day against its own targets, including the level.
- `plan_trip` filters by trip level and adds the closest-fit suggestion.
- The run payload carries levels, terrain intervals and the rules, so the phone can re-rate moved camps offline.
  `web/src/difficulty.ts` is a TS port held to the Python by a parity fixture, like `resplit.ts`.
- Calibration against the user's known trips runs locally from cached data and git-ignored reference tracks.

**Tech Stack:** Python 3.12 (uv, numpy, networkx, pytest), FastAPI, TypeScript (Vite, Vitest, Playwright). No new
dependencies.

**Spec:** `docs/superpowers/specs/2026-10-21-per-day-difficulty-design.md`

## Global Constraints

- CLAUDE.md rules: sourced data only; "unknown" where data is silent; never "safe"; provenance; miles and feet in
  user-facing text; no raw OSM ids (`osm:way/…`) in user-facing text.
- No new dependencies.
- Level names exactly `easy`, `moderate`, `hard`, `strenuous`; shown capitalized ("Easy").
- Effort score: `sqrt(gain_ft × 2 × miles)` (Shenandoah NP). Descent never counts. Steepness: uphill only, raises a
  level by one at most. Terrain tags make a day at least hard, never strenuous by themselves. Levels only go up from
  the effort level.
- A trip's level is its hardest day's level. A day hike (no nights) is one day on the day-hike scale; days of a trip
  with nights use the backpacking scale.
- A day set to a level must come out at that level, or it is reported as a miss.
- The trip-level filter matches exactly.
- Short labels in the app ("**Hard** · steep"); numbers and places only behind a tap.
- The user's reference GPX files stay in `tests/reference/local/` (git-ignored, AllTrails exports, never committed).
- Every task ends with `uv run pytest -q` green; web tasks also run `cd web && npx vitest run && npx tsc --noEmit`.

## Review Focus

1. A route with no elevation (no DEM cached): levels must say "unknown", never a guessed level. Test: Task 1
   `test_rate_day_without_elevation_is_unknown`, Task 4 `test_plan_without_dem_has_unknown_levels`.
2. Per-day targets that contradict the trip settings (day targets summing to more than the total's high end, or
   targets for day 5 on a 2-night trip): refused with a clear field error, not silently ignored. Test: Task 3
   `test_day_targets_beyond_the_trip_are_refused`, `test_day_targets_that_cannot_fit_the_total_are_refused`.
3. A tagged scramble shorter than the minimum length, or a tag on a road segment: doesn't raise the level. Test:
   Task 1 `test_short_tagged_stretch_does_not_raise_the_level`.
4. A moved camp on an old saved trip, before the levels refresh: shows no level, not a level computed without
   terrain. Test: Task 9 `old saved trip shows no levels until refreshed`.
5. The chat says "day 2 is easy" when the computed level is hard: withheld. Test: Task 7
   `test_level_claims_must_match_the_computed_levels`.

---

## File structure

| File | Responsibility |
|---|---|
| `src/backcountry/difficulty.py` (new) | `LEVELS`, `RULES`, `LEVEL_TARGETS`, `effort_score`, `steepest_climb`, `route_terrain`, `rate_stretch`, `trip_level`, `Rating` |
| `src/backcountry/route.py` | route edges keep the terrain tags they came with |
| `src/backcountry/planning.py` | `DayTarget`, `GenerateRequest.days` and `.level`, trip-level filter, suggestion |
| `src/backcountry/split.py` | per-day targets and level in the day-split cost; level-miss violations |
| `src/backcountry/serialize.py` | levels on days and candidates, `terrain` on candidates, `difficulty_rules` on the run |
| `src/backcountry/api/endpoints.py` | `POST /api/difficulty` (re-rate a saved trip) |
| `src/backcountry/calibration.py` (new) | `route_along_track`, `rate_reference_trip` |
| `tests/calibration/` (new) | calibration area region files and `cases.yaml` |
| `src/backcountry/chat/{proposal,facts,grounding}.py`, `evals/chat_cases.yaml` | levels in chat |
| `web/src/difficulty.ts` (new) + `web/src/fixtures/difficulty-parity.json` | TS port of `rate_stretch` |
| `web/src/{types,request,api,resplit,store,app}.ts`, `web/src/panels/{plan,compare,trip,saved}.ts`, `styles.css` | screens |
| `tests/difficulty_fixture.py` (new) | writes the parity fixture |

---

### Task 1: The difficulty rating

**Files:**
- Create: `src/backcountry/difficulty.py`, `tests/test_difficulty.py`
- Modify: `src/backcountry/route.py:116-117` (route edges also keep `tags`)

**Interfaces:**
- Produces:
  - `LEVELS = ("easy", "moderate", "hard", "strenuous")`
  - `RULES: dict`, version 1, JSON-serializable:
    - `{"version": 1, "scales": {"day_hike": {"cutoffs": [50, 100, 200]}, "backpacking": {"cutoffs": [35, 70, 140]}}, "steep": {"window_m": 400, "grade_pct": 15}, "terrain": {"min_m": 100, "sac_scale": [...], "trail_visibility": [...]}}`
    - The `sac_scale` list is `demanding_mountain_hiking`, `alpine_hiking`, `demanding_alpine_hiking`, `difficult_alpine_hiking`; `trail_visibility` is `bad`, `very_bad`, `horrible`, `no`.
    - Starting values, tuned in Task 2.
  - `LEVEL_TARGETS: dict[scale][level] -> {"miles": [lo, hi], "max_gain_ft": float | None, "max_grade_pct": float | None}`. Starting values:
    - backpacking: easy [2, 6] / 1,000 / 12; moderate [5, 10] / 2,000 / 15; hard [8, 13] / 3,000 / 20; strenuous [10, 16] / 4,500 / None.
    - day_hike: easy [1, 4] / 500 / 10; moderate [3, 7] / 1,500 / 15; hard [5, 10] / 3,000 / 20; strenuous [7, 14] / 5,000 / None.
  - `effort_score(gain_ft: float, miles: float) -> float`
  - `steepest_climb(d_m: list[float], z_m: list[float | None], start_m: float, end_m: float, window_m: float) -> dict | None`:
    - returns `{"grade_pct", "start_m", "end_m"}` for the steepest uphill window inside the stretch, on the smoothed profile (`elevation.smooth`);
    - `None` when elevation is unknown or the stretch is shorter than the window.
  - `route_terrain(route, min_m: float) -> list[dict]`:
    - walking-order intervals `{"start_m", "end_m", "reason", "source"}`, where `reason` is one of `"scramble (tagged)"`, `"demanding trail (tagged)"`, `"hard to follow (tagged)"`;
    - only non-road edges; adjacent same-reason edges merge, and merged intervals shorter than `min_m` are dropped;
    - `source` keeps provenance (`osm:way/…`) for the details view and is never shown as text.
    - `alpine_hiking` or harder gives "scramble (tagged)"; `demanding_mountain_hiking` gives "demanding trail (tagged)"; the visibility values give "hard to follow (tagged)".
  - `@dataclass Rating`:
    - `level: str | None` (None = unknown); `reasons: list[str]` (short tags such as `"steep"`); `effort: float | None`;
    - `steep: dict | None`; `tagged: list[dict]` (intervals overlapping the stretch); `untagged_share: float` (0–1, the share of the stretch with no terrain data at all).
  - `rate_stretch(d_m, z_m, terrain, start_m, end_m, gain_ft, scale: str, rules: dict = RULES) -> Rating`
  - `level_index(level: str) -> int`; `trip_level(ratings: list[Rating]) -> str | None` (the hardest day; None if any is None)
  - `Rating.to_dict() -> dict`

- [ ] **Step 1: Write the failing tests** in `tests/test_difficulty.py`, using synthetic profiles (lists of `d_m`, `z_m`):
  - `test_effort_score_is_the_shenandoah_formula`: `effort_score(2200, 10) == pytest.approx(209.76, abs=0.01)`.
  - `test_descent_does_not_count`: the same 10 mi stretch, climbing 2,000 ft vs descending 2,000 ft. The descending stretch rates `easy`; the climbing one rates higher.
  - `test_long_steep_climb_raises_by_one_level_only`: a stretch whose effort is `easy` but with a 600 m climb at 20% rates `moderate` with `"steep" in reasons`; never `hard`.
  - `test_short_steep_pitch_does_not_raise`: a 150 m climb at 30% inside an otherwise gentle stretch keeps the effort level.
  - `test_tagged_scramble_makes_at_least_hard_but_not_strenuous`: an `easy`-effort stretch with a 300 m "scramble (tagged)" interval rates `hard`; a `strenuous`-effort stretch with it stays `strenuous`.
  - `test_short_tagged_stretch_does_not_raise_the_level`: a 50 m tagged interval (below `min_m`) is dropped by `route_terrain`, and a tag on a road edge is ignored.
  - `test_rate_day_without_elevation_is_unknown`: `z_m` all None → `Rating.level is None`, `reasons == []`.
  - `test_scales_differ`: the same 8 mi / 1,800 ft stretch rates one level harder on `backpacking` than on `day_hike` (effort ≈ 170: hard vs moderate with the starting cutoffs).
  - `test_trip_level_is_the_hardest_day`: `trip_level([easy, hard, moderate]) == "hard"`; `trip_level([easy, unknown]) is None`.
  - `test_route_terrain_merges_and_keeps_source`: two adjacent `alpine_hiking` edges merge into one interval with both sources.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_difficulty.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'backcountry.difficulty'`.

- [ ] **Step 3: Implement `difficulty.py`, and add `"tags"` to the keys copied into route edges (`route.py:116`)**

The level comes from the cutoffs (effort below `cutoffs[0]` is easy, and so on), then +1 if `steep` meets
`rules["steep"]["grade_pct"]`, capped at one step, then `max(level, hard)` if any overlapping tagged interval exists.
`reasons` lists only what raised the level: `"steep"` and/or the tag reasons, de-duplicated, in that order.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_difficulty.py -q && uv run pytest -q`
Expected: 10 passed; whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/difficulty.py src/backcountry/route.py tests/test_difficulty.py
git commit -m "difficulty: rate a stretch of route (effort, sustained climbs, OSM terrain tags; day-hike and backpacking scales)"
```

---

### Task 2: Calibration against the user's trips

**Files:**
- Create: `src/backcountry/calibration.py`, `tests/calibration/regions/{old_rag,zion_west_rim,mount_tammany,needles}.yaml`, `tests/calibration/cases.yaml`, `tests/test_calibration.py`
- Modify: `src/backcountry/cli.py` (`ingest` and `dem` accept `--regions-dir`), `src/backcountry/difficulty.py` (tuned `RULES`)

**Interfaces:**
- Consumes: Task 1 (`rate_stretch`, `route_terrain`, `trip_level`, `RULES`); `ingest.fetch`, `ingest.fetch_dem`, `route.shortest_route`, `route.add_elevation`, `split.split_route`.
- Produces:
  - `route_along_track(tg, track: LineString, step_m: float = 200.0) -> Route`: snaps track points every `step_m` to the nearest graph nodes and joins them with shortest paths. Raises `ValueError` naming the gap when two snapped nodes aren't connected, and never draws a line (CLAUDE.md rule 1).
  - `rate_reference_trip(case: dict, regions_dir: Path) -> dict`, returning `{"id", "expected", "got", "days": [...], "notes": [...]}`.

Calibration areas are region files (bbox plus a UTM CRS) in `tests/calibration/regions/`, not in `regions/`, so
they never appear in the app. Their bboxes come from the reference tracks' bounds plus 0.01°:
- `old_rag`: EPSG:32617
- `zion_west_rim`: EPSG:32612
- `mount_tammany`: EPSG:32618
- `needles`: EPSG:32612

`cases.yaml` entries look like `{id, region, type: day_hike | backpacking, nights, track: <path under tests/reference/local> | null, waypoints: [...] | null, camps_mi: [...] | null, expected}`:
- `old_rag`: day hike, track `old_rag_mountain_loop.gpx`, expected `hard`
- `zion_west_rim`: backpacking, nights 1, track `zion_west_rim_top_down.gpx`, camps placed by the engine until the user gives the camp, expected `moderate`
- `mount_tammany`: day hike, expected `moderate`, route `pending`
- `needles`: expected `hard`, route `pending`

- [ ] **Step 1: Write the failing tests** in `tests/test_calibration.py`:
  - `test_route_along_track_follows_the_grid` (synthetic grid from `conftest`): a track drawn along Row 0 then Col 0 gives a route over exactly those edges.
  - `test_route_along_track_reports_a_gap_instead_of_drawing`: a track crossing two disconnected components raises `ValueError` naming where.
  - `test_reference_trips_match_the_users_levels` (parametrized over `cases.yaml`):
    - skips (with the reason) when a case's route is `pending`, its GPX is missing, or its region has no cached data;
    - otherwise asserts `got == expected` and prints the per-day effort, steepest climb and reasons on failure.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_calibration.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'backcountry.calibration'`.

- [ ] **Step 3: Implement `calibration.py` and the `--regions-dir` option on `ingest` and `dem`**

- [ ] **Step 4: Fetch the calibration data once** (cached; polite Overpass use)

Run, for `old_rag` and `zion_west_rim` (the two with tracks):
`uv run backcountry ingest --region <id> --regions-dir tests/calibration/regions`, then
`uv run backcountry dem --region <id> --regions-dir tests/calibration/regions`.
Expected: cached files under `data/raw/<id>/` (git-ignored).

- [ ] **Step 5: Run the calibration and tune `RULES`**

Run: `uv run pytest tests/test_calibration.py -q -s`
Expected first: the two route-building tests pass, and the reference cases print their numbers.
- Tune `RULES` (cutoffs per scale, `steep.window_m`, `steep.grade_pct`) until `old_rag` → hard and `zion_west_rim` → moderate.
- Record each change and its reason in the ledger. With the starting cutoffs, Old Rag scores about 221, i.e. strenuous, so the day-hike strenuous cutoff has to move above it.
- Then check that the Dolly Sods Bear Rocks eval run (`chat.eval.default_run`) gives no strenuous day and nothing below moderate for its 6.3 mi / 784 ft day 1 on backpacking. If it does, ledger the tradeoff instead of forcing it.
- Re-run Task 1's tests: the starting-value assertions in `test_scales_differ` may need the tuned cutoffs. Update their numbers, not their meaning.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: green. `mount_tammany` and `needles` skip as "route pending: ask the user"; the two others pass.

- [ ] **Step 7: Commit**

```bash
git add src/backcountry/calibration.py src/backcountry/cli.py src/backcountry/difficulty.py tests/calibration tests/test_calibration.py tests/test_difficulty.py
git commit -m "difficulty: calibrate against the user's known trips (Old Rag hard, Zion West Rim moderate)"
```

---

### Task 3: Per-day targets and trip level in the request

**Files:**
- Modify: `src/backcountry/planning.py`
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: `LEVELS`, `LEVEL_TARGETS` (Task 1).
- Produces:
  - `@dataclass DayTarget`:
    - `day: int`; `level: str | None = None`; `miles: tuple[float, float] | None = None`; `max_gain_ft: float | None = None`; `max_grade_pct: float | None = None`;
    - `to_dict()`;
    - `resolved(scale: str, trip: GenerateRequest) -> dict`: the effective `{"level", "miles", "max_gain_ft", "max_grade_pct"}`. Explicit numbers win over the level's table values, which win over the trip-wide settings.
  - `GenerateRequest.days: list[DayTarget]` (default `[]`) and `GenerateRequest.level: str | None` (trip-level filter), parsed in `from_dict` (keys `days`, `level`) and written by `to_dict`.
  - `GenerateRequest.targets_for(ndays: int) -> list[dict]`: one resolved target per day, with untargeted days taking the trip-wide `daily` and `max_gain_day`.

- [ ] **Step 1: Write the failing tests** in `tests/test_planning.py`:
  - `test_day_targets_round_trip`: `from_dict({... "days": [{"day": 1, "level": "easy"}, {"day": 2, "miles": "8-12", "max_gain_ft": 3000}], "level": "hard"}).to_dict()` keeps both, with miles as `[8.0, 12.0]`.
  - `test_unknown_level_is_refused`: `{"days": [{"day": 1, "level": "chill"}]}` → `RequestError` field `days`, message `day 1: level must be easy, moderate, hard or strenuous`. The same check applies to the trip `level`, with field `level`.
  - `test_day_targets_beyond_the_trip_are_refused`: nights 2 and a target for day 4 → `RequestError("days", "day 4: this trip has 3 days")`. Targets on a day hike (no nights) → `"days": "per-day targets need nights"`.
  - `test_day_targets_that_cannot_fit_the_total_are_refused`: nights 1, total 10–12, day targets with miles [8, 9] and [8, 9] → `RequestError("days", "the day targets add up to 16-18 mi, outside the total 10-12 mi")`.
  - `test_targets_for_fills_untargeted_days_from_the_trip`: nights 3, daily 6–11, day 1 easy → `targets_for(4)[0]["miles"] == LEVEL_TARGETS["backpacking"]["easy"]["miles"]`, and `targets_for(4)[1]["miles"] == [6.0, 11.0]` with `level None`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_planning.py -q`
Expected: FAIL on the new tests (`unexpected keyword` / missing attribute).

- [ ] **Step 3: Implement in `planning.py`**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_planning.py -q && uv run pytest -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/planning.py tests/test_planning.py
git commit -m "planning: per-day targets (level or numbers) and a trip-level filter in the request"
```

---

### Task 4: Splitting days against per-day targets; levels in the output

**Files:**
- Modify: `src/backcountry/split.py`, `src/backcountry/planning.py` (`evaluate`, day hikes), `src/backcountry/serialize.py`
- Test: `tests/test_split.py`, `tests/test_outputs.py`

**Interfaces:**
- Consumes: Task 1 (`rate_stretch`, `route_terrain`, `RULES`), Task 3 (`targets_for`).
- Produces:
  - `split_route(..., targets: list[dict] | None = None, terrain: list[dict] | None = None, rules: dict = RULES)`. When `targets` is given, day k's cost uses `targets[k]`:
    - miles outside its range, weighted like today's daily range;
    - gain over its cap, weighted like today's gain cap;
    - steepest sustained climb over its grade cap: `weights.gain_over_cap` per percentage point;
    - a level mismatch: `Weights.level_miss`, a new field defaulting to 5.0, per level step between the target level and the level `rate_stretch` gives that candidate day.
    - The imbalance target for day k is the midpoint of its miles range instead of `total / ndays`.
  - `Day` gains `rating: dict | None`, i.e. `Rating.to_dict()` (backpacking scale).
  - New violation wording:
    - `"Day 2 came out moderate, not hard"`;
    - `"Day 1: 6.20 mi is over your 5.00 mi target"` when the limit came from a day target (the trip-wide wording is unchanged otherwise);
    - `"Day 3: 18% grade for 0.4 mi is over your 15% target"`.
  - Candidate payload gains `level` (trip level or None), `rating` (for day hikes: the whole route on the day-hike scale; else None) and `terrain` (`route_terrain` intervals). The run payload gains `difficulty_rules` (the `RULES` used).

Efficient steepness per candidate day: precompute each candidate point's uphill window grade once, then take a
range-max over the day's points. The builder picks the method; a simple per-start numpy `maximum.accumulate` is fine
at about 150 m spacing.

- [ ] **Step 1: Write the failing tests**:
  - `tests/test_split.py::test_day_targets_shape_the_split`: nights 3 on a synthetic 30 km profile; day 1 target miles [2, 5], others [8, 12] → day 1 ≤ 5 mi, and the others share the rest.
  - `tests/test_split.py::test_level_target_is_met_or_reported`: a flat profile with day 2 targeted `hard` → no split can make it hard, so `violations` contains `"Day 2 came out easy, not hard"`.
  - `tests/test_split.py::test_days_carry_ratings`: every `Day.rating["level"]` is set when elevation is known.
  - `tests/test_outputs.py::test_payload_has_levels_terrain_and_rules`: `run_payload` of a synthetic plan has `candidates[0]["level"]`, `["terrain"] == []` (the grid has no tags), `plan.days[0].rating.level`, and `run["difficulty_rules"]["version"] == 1`.
  - `tests/test_outputs.py::test_plan_without_dem_has_unknown_levels`: no DEM → every day's `rating.level is None` and the candidate `level is None`.
  - `tests/test_outputs.py::test_day_hike_is_rated_on_the_day_hike_scale`: nights None → `candidates[0]["rating"]` is set, and `level == rating.level`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_split.py tests/test_outputs.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q` (with real data: `scripts/heavy-tests --no-eval`)
Expected: green, and the real-data route tests unchanged (targets default to None, so splits are the same as before).

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/split.py src/backcountry/planning.py src/backcountry/serialize.py tests/test_split.py tests/test_outputs.py
git commit -m "split: per-day targets and levels in the day split; levels, terrain and rules in the run payload"
```

---

### Task 5: Trip-level filter and the closest-fit suggestion

**Files:**
- Modify: `src/backcountry/planning.py`, `src/backcountry/serialize.py`
- Test: `tests/test_planning.py`

**Interfaces:**
- Consumes: Tasks 3–4.
- Produces:
  - `plan_trip` applies `req.level`: candidates whose `level` ≠ `req.level` are dropped. If none remain, the closest (by level distance, then penalty) are kept, and the run's search notes gain `"No loop here comes out <level>; showing the closest (<levels>)."`.
  - `suggest_fix(candidate, req, ...) -> str | None` in `planning.py`. For a plan with target violations, it tries single relaxations in this order:
    - each missed day's miles high +1 mi or low −1 mi;
    - its gain cap +500 ft;
    - its level one step toward the computed level.

    For each it re-runs `split_route`, and returns the first that clears every violation, worded like `"Allowing day 3 up to 12.5 mi would fix it."` or `"Setting day 2 to moderate would fix it."`. It returns None if nothing single-step works, and never proposes something unchecked.
  - Candidate payload gains `suggestion: str | None`.

- [ ] **Step 1: Write the failing tests** in `tests/test_planning.py` (synthetic grid with a DEM stub, or the flat profile and targets from Task 4):
  - `test_level_filter_keeps_only_matching_trips`
  - `test_level_filter_falls_back_to_the_closest_with_a_note`
  - `test_suggestion_is_rechecked`: the returned suggestion, applied to the request, gives a plan with no violations.
  - `test_no_suggestion_when_nothing_single_step_works`

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_planning.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/planning.py src/backcountry/serialize.py tests/test_planning.py
git commit -m "planning: filter loops by trip level; closest fit with one engine-checked suggestion"
```

---

### Task 6: Re-rating saved trips (`POST /api/difficulty`)

**Files:**
- Modify: `src/backcountry/api/endpoints.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Tasks 1, 4.
- Produces `POST /api/difficulty` with body `{region, nights | null, camps_m: number[] | null, profile: {d_m, z_m}, segments: [...], length_m, targets: [...] | null}` (all from a saved trip's stored candidate). It returns `{terrain, days: [{rating}], level, rating (day hikes), difficulty_rules}`:
  - `terrain` is rebuilt from the region's current graph by the way ids in `segments[*].sources`.
  - A segment's interval counts only if **every** way in it carries the same terrain reason. A mixed segment adds nothing and counts as "terrain partly unknown" (`untagged_share`), so an old trip is never rated harder than its data supports.
  - Errors: 400 for bad input; 404 for an unknown region.

- [ ] **Step 1: Write the failing tests** in `tests/test_api.py`:
  - `test_difficulty_rerates_a_saved_candidate`: a candidate from a synthetic run, re-sent with moved `camps_m`, gives days whose `rating.level` matches what `split`/`rate_stretch` give for those camps.
  - `test_difficulty_mixed_segment_adds_no_terrain`: a segment whose ways carry different tags gives `terrain == []`.
  - `test_difficulty_bad_input_is_400`

- [ ] **Step 2: Run them to verify they fail** (`uv run pytest tests/test_api.py -q`; expected 404/405 on the new route)

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run tests to verify they pass** (`uv run pytest -q`; green)

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/api/endpoints.py tests/test_api.py
git commit -m "api: POST /api/difficulty re-rates a saved trip's days (camps moved on the phone included)"
```

---

### Task 7: Levels in the chat

**Files:**
- Modify: `src/backcountry/chat/proposal.py`, `src/backcountry/chat/facts.py`, `src/backcountry/chat/grounding.py`, `src/backcountry/chat/service.py`, `evals/chat_cases.yaml`
- Test: `tests/test_chat_proposal.py`, `tests/test_chat_facts.py`, `tests/test_chat_grounding.py`, `tests/test_chat_api.py`

**Interfaces:**
- Consumes: Task 3 (`DayTarget`, request keys `days`, `level`), Task 4 (payload ratings).
- Produces:
  - `CHANGE_FIELDS` gains:
    - `days`: an array of `{day: integer, level: easy|moderate|hard|strenuous|null, miles: [lo, hi] | null, max_gain_ft: number | null, max_grade_pct: number | null}`, where `[]` clears all day targets;
    - `level`: the trip level or null.
  - `apply_changes` merges `days` by day number (a later entry for the same day replaces the earlier one), validates through `GenerateRequest`, and clears camp pins when day targets change. Day targets replace hand-placed camps.
  - `describe_changes` lines:
    - `"Day 1: not set → Easy (2-6 mi, ≤ 1,000 ft, ≤ 12%)"` (the resolved numbers);
    - `"Trip level: any → Moderate"`.
  - The prompt says:
    - per-day requests and "late start" go in `days`, not `camps`;
    - light/chill/relaxed → easy, brutal/punishing → strenuous;
    - ask one question when the level is ambiguous;
    - never call a day by a level the FACTS don't give it.
  - The fact sheet gives each day's level and reasons (`Day 2: … level hard (steep)`) or `level unknown`, the trip level, and the day targets in the settings line.
  - `check_reply(reply, facts)` withholds `"says day N is <level>, but it is <level>"` when a sentence pairs `day N` with a level word that differs from that day's level in the facts. Applied per clause, like the other claims.
- Eval cases (`evals/chat_cases.yaml`):
  - `per-day-levels`: "day 1 easy, days 2 and 3 hard" → `request_contains`-style check on `days`;
  - `late-start`: "we start late on day 1, make it short" → a `days` entry for day 1;
  - `hardest-by-level`: "which day is hardest?" → mentions the hardest day;
  - `trip-level`: "something moderate" → `request: {level: moderate}`.

  Add an expectation key `days_include: {day, level}` to `EXPECT_KEYS` and `check_case`, with a unit test in `tests/test_chat_eval.py`.

- [ ] **Step 1: Write the failing tests**:
  - `test_day_levels_become_a_proposal_with_numbers` (proposal)
  - `test_day_targets_replace_camp_pins` (proposal)
  - `test_fact_sheet_has_day_levels_and_reasons` (facts)
  - `test_level_claims_must_match_the_computed_levels` (grounding): with facts giving day 2 `level hard`, the reply "Day 2 is easy." is withheld, and "Day 2 is hard because it's steep." passes.
  - `test_days_include_expectation` (eval)

- [ ] **Step 2: Run them to verify they fail** (`uv run pytest tests/test_chat_*.py -q`)

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run tests, then the eval** (`uv run pytest -q`, then `uv run backcountry chat-eval --model ollama:qwen3:14b`)

  Expected: green. Ledger the eval score, which shouldn't drop on the old cases. Failures on the new cases go to the ledger with the model's replies; fix the instructions (not the cases) where every miss has the same cause.

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/chat evals/chat_cases.yaml tests/test_chat_*.py
git commit -m "chat: per-day levels and trip level as proposals; levels in the facts; level claims checked"
```

---

### Task 8: The plan form's day strip and levels on results

**Files:**
- Modify: `web/src/types.ts`, `web/src/request.ts`, `web/src/panels/plan.ts`, `web/src/panels/compare.ts`, `web/src/panels/trip.ts`, `web/src/styles.css`
- Test: `web/src/request.test.ts`, `web/src/panels/plan.test.ts`, `web/src/panels/compare.test.ts`, `web/src/panels/trip.test.ts`, `web/e2e/smoke.spec.ts`

**Interfaces:**
- Consumes: the payload (Task 4–5): `Day.rating`, `Candidate.level`, `Candidate.rating`, `Candidate.suggestion`, `Run.difficulty_rules`; the request keys `days`, `level`.
- Produces:
  - Types:
    - `Rating {level: Level | null; reasons: string[]; effort: number | null; steep: {grade_pct, start_m, end_m} | null; tagged: {start_m, end_m, reason}[]; untagged_share: number}`
    - `Level = "easy" | "moderate" | "hard" | "strenuous"`
    - `DayTarget {day; level?; miles?; max_gain_ft?; max_grade_pct?}`
  - `FormState` gains `perDay: boolean`, `days: FormDay[]` (one per day: `{level: Level | "" | "custom", miles: string, maxGainFt: string, maxGradePct: string}`), `tripLevel: Level | ""`. `toRequest`/`fromRun` map them.
  - Plan panel:
    - A "Set days individually" switch shows a **day strip**: one block per day (nights + 1), flex width from target miles (default equal), colored by level, labeled `D1 Easy`.
    - Tapping a block opens its editor: four level buttons and three number inputs. Picking a level fills the numbers from `difficulty_rules`' level targets. Editing a number sets that day to `custom`.
    - The switch and strip are hidden when nights is empty. Plus a `Trip level` select (any, Easy, Moderate, Hard, Strenuous).
  - `levelChip(level: Level | null): string` in `panels/common.ts`: `<span class="lvl lvl-hard">Hard</span>`, or `<span class="lvl">Level unknown</span>`.
  - Compare cards show the trip level chip. Trip view days show `Day 1 · [Easy] · 4.6 mi · +720 ft` plus the reason tags joined with ", ". Under the days: violations as today, then `suggestion`.
  - Tapping a day row opens its details: effort score, the steep stretch ("18% for 0.4 mi at mile 14.2"), the tagged stretches, and `Terrain unknown for 82% of this day` when `untagged_share > 0`.
  - All text escaped with `esc`; no raw ids (the `source` in `tagged` is never rendered).

- [ ] **Step 1: Write the failing tests**:
  - `request.test.ts`:
    - "per-day targets map to the request and back"
    - "a level fills the numbers; editing one makes it custom"
    - "no day targets when the switch is off"
  - `plan.test.ts`:
    - "the day strip has one block per day, labeled with its level"
    - "tapping a block opens its editor"
    - "the strip is hidden without nights"
  - `compare.test.ts`: "cards show the trip level"
  - `trip.test.ts`:
    - "days show level, miles, gain and reasons"
    - "unknown level says Level unknown"
    - "details list the steep stretch and terrain unknown share"
    - "the suggestion shows under the days"
  - `e2e/smoke.spec.ts`: a new test "set day 1 easy in the day strip and generate", which checks that the trip view's day 1 shows a level chip.

- [ ] **Step 2: Run them to verify they fail** (`cd web && npx vitest run`)

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run all web tests** (`cd web && npx vitest run && npx tsc --noEmit && npm run test:e2e`; all green)

- [ ] **Step 5: Commit**

```bash
git add web/src web/e2e
git commit -m "web: day strip for per-day targets, trip-level filter, levels and reasons on results"
```

---

### Task 9: Levels on saved trips, offline

**Files:**
- Create: `web/src/difficulty.ts`, `web/src/difficulty.test.ts`, `tests/difficulty_fixture.py`, `web/src/fixtures/difficulty-parity.json`, `tests/test_difficulty_fixture.py`
- Modify: `web/src/resplit.ts`, `web/src/offline/db.ts`, `web/src/app.ts`, `web/src/api.ts`, `web/src/panels/trip.ts`, `web/src/panels/saved.ts`

**Interfaces:**
- Consumes: Task 1 (`rate_stretch` semantics and `RULES`), Task 6 (`POST /api/difficulty`), the run payload's `terrain` and `difficulty_rules`.
- Produces:
  - `rateStretch(profile, terrain, startM, endM, gainFt, scale, rules): Rating` in `difficulty.ts`: the same results as the Python, held by the parity fixture (levels exact; effort to 0.01; grade to 0.1).
  - `resplitDays(...)` gains `{terrain, rules, scale}` and sets each day's `rating`. It sets `rating` only when the stored candidate has `terrain` and `difficulty_rules`; otherwise `rating: null`.
  - `SavedTrip` gains `levels: {refreshedAt: string, terrain, rules} | null` (DB VERSION 2, with an upgrade that sets `null`).
  - On boot while online, saved trips without `levels`, or with older `rules.version`, call `api.difficulty(...)` and store the result. Failures are left for the next boot. Nothing is shown as a level until the refresh has stored terrain and rules.
  - `tests/difficulty_fixture.py` writes cases (synthetic profiles and terrain, both scales, steep/tagged/unknown) with the Python ratings. `tests/test_difficulty_fixture.py` fails when the file is stale, compared at 6 decimals like `test_resplit_fixture.py`.

- [ ] **Step 1: Write the failing tests**:
  - `difficulty.test.ts`: "matches the engine on every parity case"
  - `resplit.test.ts`:
    - "moved camps re-rate the two touched days"
    - "no rating without terrain and rules"
  - `trip.test.ts` / `saved.test.ts`: "old saved trip shows no levels until refreshed"
  - `app.test.ts` (or a new `levels.test.ts` for the refresh helper): "online boot refreshes saved trips missing levels and stores them"
  - `tests/test_difficulty_fixture.py::test_difficulty_fixture_matches_the_engine`

- [ ] **Step 2: Run them to verify they fail** (`uv run python tests/difficulty_fixture.py` first, then `uv run pytest tests/test_difficulty_fixture.py -q` and `cd web && npx vitest run`)

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run all suites** (`uv run pytest -q`; `cd web && npx vitest run && npx tsc --noEmit && npm run test:e2e`; all green)

- [ ] **Step 5: Commit**

```bash
git add web/src tests/difficulty_fixture.py tests/test_difficulty_fixture.py
git commit -m "offline: levels on saved trips (re-rated on the phone for moved camps; old trips refreshed when online)"
```

---

### Task 10: Docs and the acceptance check

**Files:**
- Modify: `README.md`, `PROGRESS.md`, `docs/ROADMAP.md`

- [ ] **Step 1: README**: a short "Difficulty" section:
  - the levels and the effort formula (credit Shenandoah NP);
  - what raises a level;
  - the two scales;
  - that terrain is "unknown" where OSM has no tags;
  - per-day targets in the form and chat;
  - calibration (`tests/calibration/`; reference tracks local-only).

- [ ] **Step 2: Real-data check**:
  - Plan a 3-night Pemi trip with day 1 easy, days 2–3 hard, day 4 easy, through the API (`uv run backcountry serve`).
  - Record the days, levels, reasons, violations and suggestion in PROGRESS.
  - Run `scripts/heavy-tests --eval`.

  Expected: suites green. The calibration prints Old Rag hard and Zion West Rim moderate; Tammany and Needles are pending the user's routes.

- [ ] **Step 3: PROGRESS and ROADMAP**:
  - In PROGRESS, `## Per-day difficulty (built <date>; awaiting your phone check)`: what was built, the tuned `RULES` and why, calibration results, the Pemi check, test counts, what's pending (Tammany and Needles routes; the Zion camp location), and the deferred items.
  - In ROADMAP, mark it built, with a pointer to the spec.

- [ ] **Step 4: Commit**

```bash
git add README.md PROGRESS.md docs/ROADMAP.md
git commit -m "docs: per-day difficulty usage, calibration results, and the Pemi check"
```
