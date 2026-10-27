# Destinations, Trip Shapes and Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plan faster (first trips in ~10 s, never over 60 s; new areas show trails in 15–30 s), plan out-and-back
and one-way trips, and treat a searched place as the trip's destination.

**Architecture:** Speed first: measure, cut the search to the reachable part of the network, return a first
snapshot of results early through a polling plan job, read elevation only in grid cells that hold trails (in
parallel), open areas in two steps, and prepare popular areas ahead within a reserved disk share. Then trip
shapes in `loops.py` (out-and-back, one-way) and destinations resolved server-side from the US index, wired
through `GenerateRequest`, the API, outputs, retrace and the web app. Search names (abbreviations, short-name
groups) last.

**Tech Stack:** Python 3.11 (networkx, shapely, rasterio, FastAPI, typer, pytest), TypeScript web app (vitest,
Playwright). No new dependencies (`concurrent.futures` is stdlib).

**Spec:** `docs/superpowers/specs/2026-10-27-trip-shapes-and-speed-design.md`

## Global Constraints

- CLAUDE.md rules hold: never invent geometry or facts (say "unknown"); never call a route "safe"; provenance on
  every edge and route; no raw OSM ids in user-facing text; miles and feet in output, meters internally.
- No new dependencies.
- Shuttle drive: no drive time or road distance computed by us; it is "unknown" in the summary and the GPX. Button
  note, verbatim: "Check road conditions; backcountry roads may be closed or need high clearance."
- Point destination "near enough": 400 m (0.25 mi) straight line from the nearest trail node. Farther gives,
  verbatim pattern: "The nearest mapped trail point is {d} mi from {name}; no mapped trail reaches it."
- Up to 5 suggested finishes. "any" = loop, lollipop, out-and-back (never one-way).
- Abbreviations: NP National Park, NF National Forest, NM National Monument, NRA National Recreation Area,
  SP State Park, W / WA Wilderness.
- Prepared areas: reserved share 8 GB (`BACKCOUNTRY_PREPARED_GB`, default 8) of the area disk budget; user-opened
  areas never evict prepared ones.
- Search budget: first snapshot after `FIRST_RESULTS_S = 8.0`, stop by `MAX_SECONDS = 60.0`.
- The user's GPX tracks in `tests/reference/local/` are AllTrails exports: local checks only, never committed,
  copied, or symlinked into a worktree.
- Security (any-area rules): client input names an OSM ref (validated against `areas._REF`), a box, a point or a
  trailhead; the server re-looks every ref up in its own index; map links are built on the phone from trailhead
  coordinates only.
- Existing regions, golden routes (`tests/test_reference.py`, heavy tests), saved and offline trips unchanged.
- Day hikes already exist: `nights` null (form: nights blank) plans a day hike. Do not add `nights: 0`.

## Review Focus

1. A destination on a different piece of the network from the start (or cut off by avoid/no-roads): a plain
   "can't be reached from this start" note, not an exception or an empty screen. Test: Task 9
   `test_destination_unreachable_says_so`.
2. One-way with a finish equal to the start, not on the network, or on another component: a `RequestError` on
   `finish` naming the problem. Test: Task 10 `test_one_way_bad_finish_is_a_request_error`.
3. A phone that starts plans and walks away, or many plans at once: jobs are capped (`MAX_PLAN_JOBS = 2`
   running, more → 429 Busy) and forgotten 10 min after finishing. Test: Task 4
   `test_plan_jobs_are_capped_and_expire`.
4. Saved trips from before this piece (no `finish`/`destination`, kind loop/lollipop) and saved out-and-back or
   one-way trips still open, export and update. Out-and-back routes walk keys twice in reverse; retrace must
   follow them. Test: Task 11 `test_retrace_follows_an_out_and_back`.
5. Elevation cells: a trail running along a cell border, or between two trail-bearing cells, never samples NaN.
   Test: Task 5 `test_corridor_samples_match_a_full_read_along_trails`.

---

### Task 1: Measure where the time goes

**Files:**
- Modify: `src/backcountry/planning.py` (`plan_trip`), `src/backcountry/api/store.py` (region load)
- Create: `scripts/measure-speed`
- Test: `tests/test_planning.py`

**Interfaces:**
- Produces: `report["timings"]: dict[str, float]` on every `plan_trip` result, keys
  `"resolve", "search", "day_split", "finish"` (seconds, 2 dp; `search` excludes the evaluate time spent inside
  it, `day_split` is the summed evaluate/day-hike time). `LoadedRegion.timings: dict` with `"graph"`,
  `"elevation_sampling"`.

- [ ] **Step 1: Write the failing test** `test_plan_trip_reports_timings` in `tests/test_planning.py`: plan on the
  grid fixture, assert `set(result.report["timings"]) == {"resolve", "search", "day_split", "finish"}` and every
  value is a float ≥ 0.
- [ ] **Step 2: Run** `uv run pytest tests/test_planning.py::test_plan_trip_reports_timings -v` → FAIL (KeyError).
- [ ] **Step 3: Implement** with `time.perf_counter()` around each phase; wrap `steps.evaluate`/`day_hike` to sum
  their time.
- [ ] **Step 4: Run** the test → PASS.
- [ ] **Step 5: Write `scripts/measure-speed`** (executable Python, `uv run`): skips with a message if no US index.
  For each case it prints one row: area open (cut+graph, elevation fetch from `prepare_area`'s `seconds`, plus
  region load timings) and plan timings. Cases: Zion from "Grotto Trailhead" 2 nights 18–26 mi; Yosemite from
  "Bridalveil Creek" 2 nights 18–26 mi; Grand Canyon open only. `--fresh` prepares into a temp raw dir so the
  first-open numbers are real. Output is a Markdown table for PROGRESS.md.
- [ ] **Step 6: Run it** `scripts/measure-speed --fresh > $WORKSPACE/speed-before.md` and read it. Ledger the
  numbers as the baseline; they decide whether Task 5's corridor and Task 6's ordering are worth it (if region
  load's elevation sampling dominates, ledger a ruling and add it to Task 5's scope).
- [ ] **Step 7: Commit** `perf: time each planning step; scripts/measure-speed`.

### Task 2: Search only the reachable network

**Files:**
- Modify: `src/backcountry/loops.py` (`loop_graph`, `_dijkstra`, `waypoint_loops`, `search`, `MAX_SECONDS`)
- Test: `tests/test_loops.py`

**Interfaces:**
- Produces: `loop_graph(tg, start, no_roads=False, exclude_keys=frozenset(), reach_m: float | None = None)`:
  nodes whose shortest trail distance from `start` exceeds `reach_m` are dropped *before* spur pruning and
  merging. `search` passes `reach_m = max_m * (1 + WIDEN) / 2` (the widened retry stays inside).
  `_dijkstra(H, source, used=..., forbidden=..., cutoff=None)`. `report["loop_graph"]["reach_m"]`.
  `MAX_SECONDS = 60.0`.

- [ ] **Step 1: Write failing tests**
  - `test_loop_graph_drops_nodes_out_of_reach`: on the grid, `loop_graph(tg, T, reach_m=1600)`: g22, g12, g21 and
    g02 aren't in the graph, and g00/g10/g01 are.
  - `test_reach_limit_finds_the_same_loops`: for three ranges on the grid (2–5 km, 4–7 km, 5–9 km), the candidate
    key sets from `waypoint_loops` on `loop_graph(..., reach_m=hi/2)` equal those on `loop_graph(..., reach_m=None)`.
  - `test_search_budget_is_60_seconds`: `loops.MAX_SECONDS == 60.0`.
- [ ] **Step 2: Run** `uv run pytest tests/test_loops.py -k "reach or budget" -v` → FAIL.
- [ ] **Step 3: Implement**: one `nx.single_source_dijkstra_path_length(G_component, start, cutoff=reach_m,
  weight="length_m")` over the component, then build H from edges with both ends in reach. In `waypoint_loops`,
  pass `cutoff=hi - dist so far` to the leg Dijkstras (`hi - dist_s[a]` for legs from a,
  `hi - dist_s[a] - dist_a[b]` for the leg back from b).
- [ ] **Step 4: Run** `uv run pytest tests/test_loops.py tests/test_planning.py tests/test_reference.py -q` → all
  pass; then `scripts/heavy-tests > $WORKSPACE/heavy-t2.txt 2>&1; tail -5 $WORKSPACE/heavy-t2.txt` → golden
  routes unchanged.
- [ ] **Step 5: Re-measure** with `scripts/measure-speed` (not `--fresh`) and ledger the planning numbers.
- [ ] **Step 6: Commit** `perf: search only the part of the network a trip can reach`.

### Task 3: A first snapshot of results, then the rest

**Files:**
- Modify: `src/backcountry/loops.py` (`search`, `waypoint_loops`), `src/backcountry/planning.py` (`plan_trip`)
- Test: `tests/test_loops.py`, `tests/test_planning.py`

**Interfaces:**
- Produces: `FIRST_RESULTS_S = 8.0` in `loops.py`. `search(..., on_snapshot=None, first_s=FIRST_RESULTS_S,
  clock=time.time)`: once `clock() - t0 >= first_s` (checked each outer `waypoint_loops` iteration via a
  `tick` callback), and only once, it scores and picks from what is found so far exactly as the final step does
  (factor the final sort/choose/must-visit/direction into `_choose(found, ...) -> list[Candidate]`) and calls
  `on_snapshot(report_copy)` with `report_copy["partial"] = True`. Not called if the search ends first.
  `plan_trip(..., on_snapshot=None)` passes through a wrapper that runs the day-hike/suggest steps on the
  snapshot's candidates before calling the caller's `on_snapshot(PlanResult)`. Final reports carry
  `"partial": False`.

- [ ] **Step 1: Write failing tests**
  - `test_snapshot_comes_after_first_s_with_scored_candidates` (`test_loops.py`): a fake clock advancing 5 s per
    call; `search(..., on_snapshot=snaps.append, first_s=8)` on the grid. Exactly one snapshot,
    `snaps[0]["partial"] is True`, its candidates are sorted by penalty and each passes `_check_invariants`.
  - `test_no_snapshot_when_search_finishes_first`: real clock, `first_s=999` → `snaps == []`, and
    `report["partial"] is False`.
  - `test_plan_trip_snapshot_has_day_plans` (`test_planning.py`): with nights=1 and a fake clock, the snapshot
    `PlanResult`'s candidates each have `.plan`.
- [ ] **Step 2: Run** `uv run pytest tests/test_loops.py tests/test_planning.py -k snapshot -v` → FAIL.
- [ ] **Step 3: Implement** as in Interfaces. The snapshot never stops or changes the search.
- [ ] **Step 4: Run** `uv run pytest tests/test_loops.py tests/test_planning.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: the search hands over a first set of trips after 8 seconds`.

### Task 4: Plan jobs in the API, and the phone shows trips as they come

**Files:**
- Create: `src/backcountry/api/plan_jobs.py`
- Modify: `src/backcountry/api/endpoints.py`, `src/backcountry/api/app.py`, `web/src/api.ts`, `web/src/app.ts`,
  `web/src/types.ts`, `web/src/panels/compare.ts`
- Test: `tests/test_api.py`, `web/src/app.test.ts`, `web/src/panels/compare.test.ts`

**Interfaces:**
- Consumes: `plan_trip(..., on_snapshot=)` (Task 3).
- Produces:
  - `class PlanJobs(settings, regions, runs, clock=time.monotonic)` with `start(region_id, body) -> str` (job id,
    `secrets.token_hex(8)`), `get(job_id) -> dict`, `MAX_PLAN_JOBS = 2` running (a third → `Busy`), finished jobs
    forgotten `JOB_TTL_S = 600` after finishing. Jobs run on threads (`settings.area_thread` False → run inline in
    `start`, like `AreaWorker`, so tests stay synchronous).
  - `POST /regions/{region_id}/plans` → `{"job_id"}` (validates the body with `GenerateRequest.from_dict` before
    starting: bad settings are a 400 at once).
  - `GET /plans/{job_id}` → `{"status": "searching"|"done"|"failed", "run": <run payload or null>, "message"}`.
    While searching, `run` is the snapshot (`run["partial"] = True`, not saved). When done, `run` is the final
    run, saved exactly as `/generate` saves it today. Job ids are checked against `^[0-9a-f]{16}$`.
  - `/generate` stays as is (CLI, chat, tests).
  - Web: `startPlan(region, req) -> Promise<{job_id}>`, `getPlan(jobId) -> Promise<PlanJob>` in `api.ts`;
    `actions.generate` posts, then polls every 1.5 s; a partial run renders the compare view with the status
    line "Still looking for better trips…"; the final run replaces it. A failed poll shows the error and a
    "Try again" button that re-runs `generate` with the same form.
- [ ] **Step 1: Write failing tests** (`tests/test_api.py`):
  - `test_plan_job_returns_the_final_run_and_saves_it`: POST then GET → `status == "done"`, `run["partial"] is
    False`, `runs` has it.
  - `test_plan_job_bad_settings_are_a_400`: body with `total: "26-18"` → 400, field `total`.
  - `test_plan_jobs_are_capped_and_expire` (Review Focus 3): with a blocking fake planner, 2 running jobs then a
    third → 429; after finishing and advancing the fake clock 601 s, `GET` → 404.
  - `test_plan_job_id_is_validated`: `GET /plans/..%2Fx` → 400 or 404, never a file read.
- [ ] **Step 2: Run** `uv run pytest tests/test_api.py -k plan_job -v` → FAIL (404 route).
- [ ] **Step 3: Implement** `plan_jobs.py` and the endpoints.
- [ ] **Step 4: Run** `uv run pytest tests/test_api.py -q` → PASS.
- [ ] **Step 5: Write failing web tests**: `app.test.ts` "generate shows a partial run, then the final one"
  (stub api: first poll partial, second done → store.run ends as the final, status cleared);
  `compare.test.ts` "a partial run says it's still looking".
- [ ] **Step 6: Run** `cd web && npx vitest run src/app.test.ts src/panels/compare.test.ts` → FAIL.
- [ ] **Step 7: Implement** the web side.
- [ ] **Step 8: Run** `cd web && npx vitest run && npx playwright test` → PASS.
- [ ] **Step 9: Commit** `feat: plans run as jobs; the phone shows the first trips while the search goes on`.

### Task 5: Elevation only where the trails are, read in parallel

**Files:**
- Modify: `src/backcountry/elevation.py`, `src/backcountry/ingest.py` (`fetch_dem`, `_read_merged`),
  `src/backcountry/areas.py` (`prepare_area`)
- Test: `tests/test_elevation.py`, `tests/test_ingest.py`, `tests/test_areas.py`

**Interfaces:**
- Produces:
  - `DEM_CELL_DEG = 0.05`, `CORRIDOR_M = 150.0` in `elevation.py`; `corridor_cells(tg, cell_deg=DEM_CELL_DEG,
    buffer_m=CORRIDOR_M) -> list[tuple[float, float, float, float]]`: the grid cells (aligned to multiples of
    `cell_deg`, lon/lat w,s,e,n) that any trail or road edge, buffered by `buffer_m`, touches. Sorted.
  - `fetch_dem(..., cells=None, workers=DEM_WORKERS)` with `DEM_WORKERS = 6`: with `cells`, reads each cell's
    window (`reader(urls, cell)`) on a `ThreadPoolExecutor`, and writes one raster on the full-bounds grid
    with NaN outside the cells. Metadata adds `"cells": len(cells)`. Provenance otherwise unchanged. Without
    `cells`, today's single read.
  - `prepare_area` passes `cells=corridor_cells(tg)`.
- [ ] **Step 1: Write failing tests**
  - `test_corridor_cells_cover_only_trail_cells` (`test_elevation.py`): a graph with two trails 20 km apart →
    cells only around each, none between; every edge point lies in some cell.
  - `test_corridor_samples_match_a_full_read_along_trails` (Review Focus 5): a synthetic plane DEM served by a
    fake reader; a trail running exactly along a cell border and one crossing two cells; `annotate` with the
    corridor DEM equals `annotate` with the full DEM (gain/loss per edge within 0.1 ft), and no sample is NaN.
  - `test_fetch_dem_reads_cells_in_parallel_and_keeps_provenance` (`test_ingest.py`): fake reader records calls;
    with 4 cells it is called 4 times (from more than one thread), metadata has `cells == 4`, `sources` and
    `attribution` as before, and the tif is NaN outside the cells.
- [ ] **Step 2: Run** `uv run pytest tests/test_elevation.py tests/test_ingest.py -k "corridor or cells" -v` → FAIL.
- [ ] **Step 3: Implement.** A failed cell read fails the whole fetch (nothing half-cached; atomic write as today).
- [ ] **Step 4: Run** `uv run pytest -q > $WORKSPACE/t5.txt; tail -3 $WORKSPACE/t5.txt` → all pass.
- [ ] **Step 5: Measure** `scripts/measure-speed --fresh` and ledger: Grand Canyon first open and its raw-dir size,
  before vs after. If Grand Canyon's elevation is still over 60 s, ledger the number (spec: prepared ahead then
  counts).
- [ ] **Step 6: Commit** `perf: fetch elevation only in cells that hold trails, six at a time`.

### Task 6: Open in two steps

**Files:**
- Modify: `src/backcountry/areas.py` (`prepare_area`), `src/backcountry/api/area_worker.py`,
  `src/backcountry/api/store.py`, `web/src/panels/search.ts`, `web/src/app.ts`, `web/src/panels/plan.ts`
- Test: `tests/test_api_areas.py`, `tests/test_areas.py`, `web/src/panels/search.test.ts`, `web/src/panels/plan.test.ts`

**Interfaces:**
- Produces:
  - `prepare_area(..., on_trails=None)`: writes the cut (atomic) right after the graph is built, calls
    `on_trails()`, then fetches elevation. If the cut for this date exists but no DEM covers the graph, it fetches
    only the DEM (no longer returns early with `already`).
  - Worker statuses: `"queued" | "preparing" | "trails" | "ready" | "failed"`. `"trails"` = cut ready, elevation
    still coming. `_ready(area_id)` = cut for the index date **and** a DEM cache. A request for an area with a cut
    but no DEM (e.g. the server died mid-elevation) queues an elevation-only preparation and answers `"trails"`.
    On `"ready"` the worker calls `store.forget(area_id)` so the next load includes the DEM.
  - Messages (verbatim): queued/preparing `"Loading trails…"`, trails `"Adding elevation…"`.
  - Web: on `"trails"`, `openSearchResult` calls `pickRegion` (trails and trailheads show) and keeps polling;
    the plan form's Plan button is disabled with the note "Adding elevation…"; on `"ready"` it reloads the region
    and enables Plan. A failed status shows the message and a "Try again" button that re-opens the same target,
    keeping the form.
- [ ] **Step 1: Write failing tests**
  - `test_area_shows_trails_before_elevation` (`test_api_areas.py`): a DEM fetch fake that blocks on an Event;
    status goes `preparing` → `trails` (and `/regions/{id}/trails` works then) → `ready` after releasing it.
  - `test_cut_without_elevation_is_not_ready` : write a cut, no DEM → `request()` answers `trails` and queues an
    elevation-only run; after `run_pending()`, `ready`, and the cut file is unchanged.
  - `web/src/panels/search.test.ts` "trails status opens the region and keeps polling until ready".
  - `web/src/panels/plan.test.ts` "Plan is disabled while elevation is coming".
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/test_api_areas.py tests/test_areas.py tests/test_security_areas.py -q`
  and `cd web && npx vitest run && npx playwright test` → PASS.
- [ ] **Step 5: Commit** `feat: new areas show trails first and add elevation behind them`.

### Task 7: Prepare popular areas ahead, in a reserved share

**Files:**
- Modify: `src/backcountry/api/area_worker.py` (`_make_room`, `_usage`), `src/backcountry/api/store.py`
  (`Settings`), `src/backcountry/areas.py`, `src/backcountry/cli.py`, `src/backcountry/osm_index.py`
- Test: `tests/test_api_areas.py`, `tests/test_areas.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `PREPARED_FILE = "prepared.json"` in the areas dir: `{"areas": [area_id, ...], "prepared_at": iso}`.
    `areas.prepared_ids(areas_dir) -> set[str]`.
  - `osm_index.popular_areas(con) -> list[dict]` in order: areas whose label is "National Park" (largest first),
    then "National Monument" and "National Recreation Area", then labels containing "Wilderness". All
    `area_km2 <= MAX_AREA_KM2`, US only.
  - `areas.prepare_popular(con, budget_gb, raw_dir, areas_dir, dem_fetch=ingest.fetch_dem, limit=None,
    log=print) -> dict`: wilderness areas are ranked by trail miles from their cut (cut is cheap) before
    preparing; prepares in order until the next one would push prepared usage over `budget_gb`, records each in
    `prepared.json` as it finishes, skips ones already fresh. Returns `{"prepared": [...], "skipped": [...],
    "used_gb": float}`.
  - CLI: `backcountry areas prepare-popular [--budget-gb 8] [--limit N]`.
  - `Settings.prepared_gb` from `BACKCOUNTRY_PREPARED_GB` (default 8.0). `AreaWorker._make_room` never removes a
    prepared id and budgets user areas at `disk_gb - prepared usage`.
- [ ] **Step 1: Write failing tests**
  - `test_popular_areas_order` (`test_areas.py`, mini fixture): national parks before monuments before
    wilderness.
  - `test_prepare_popular_stops_at_the_budget`: fake DEM writing N bytes per area; with a budget of 2.5 areas,
    two are prepared and listed in `prepared.json`.
  - `test_user_areas_never_evict_prepared_ones` (`test_api_areas.py`): one prepared area plus user areas over
    budget → only user areas are removed.
  - `test_cli_prepare_popular` (`test_cli.py`): runs on the mini index with a fake DEM and prints a summary.
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q > $WORKSPACE/t7.txt; tail -3 $WORKSPACE/t7.txt` → PASS.
- [ ] **Step 5: Commit** `feat: prepare national parks and popular wilderness ahead, in their own disk share`.
- [ ] **Step 6 (operator, after Task 14's checks; long):** run `caffeinate -i uv run backcountry areas
  prepare-popular` in the background on the real index; report progress to the user; ledger what fitted.

### Task 8: Out-and-back trips

**Files:**
- Modify: `src/backcountry/loops.py`, `src/backcountry/planning.py` (`KINDS`, `GenerateRequest`),
  `src/backcountry/scoring.py` if kind labels live there, `src/backcountry/chat/proposal.py` (type enum + prompt)
- Test: `tests/test_loops.py`, `tests/test_planning.py`, `tests/test_chat_proposal.py`

**Interfaces:**
- Produces:
  - `KINDS = {"loop": ("loop",), "lollipop": ("lollipop",), "out_and_back": ("out_and_back",),
    "one_way": ("one_way",), "any": ("loop", "lollipop", "out_and_back")}`. Error text: "type must be loop,
    lollipop, out_and_back, one_way or any".
  - `out_and_backs(tg, start, lo, hi, no_roads=False, exclude_keys=frozenset(), through: int | None = None,
    deadline=None, report=None) -> list[Candidate]` in `loops.py`, on `routing_graph` (spurs included):
    turnarounds are trail ends (degree 1) and junctions (degree ≥ 3) whose shortest distance d satisfies
    `lo <= 2d <= hi`; with `through`, only turnarounds whose path passes `through`, or `through` itself.
    Route = path out + the same edges back. `kind="out_and_back"`, `stem_keys` = the out keys (so
    `repeated_m` = half the length and `_check_invariants` holds), `turnaround: int` on the Candidate (new field,
    default None).
  - `search` collects out-and-backs when `"out_and_back" in kinds`; `report["out_and_backs_found"]`.
  - Candidate notes: `"Out and back, {x} mi each way."`
- [ ] **Step 1: Write failing tests**
  - `test_out_and_back_to_the_dead_spur` (grid): `type out_and_back`, range 2.5–3.5 km from g00 → a candidate
    ending at D and back; nodes are a palindrome; `_check_invariants` holds; `repeated_m == length_m / 2`.
  - `test_out_and_back_turnarounds_fit_the_range`: every candidate's `2 * d(turnaround)` within [lo, hi].
  - `test_any_includes_out_and_back_but_not_one_way`.
  - `test_chat_schema_knows_the_new_types` (`test_chat_proposal.py`): enum includes `out_and_back`, `one_way`.
- [ ] **Step 2: Run** `uv run pytest tests/test_loops.py tests/test_planning.py tests/test_chat_proposal.py -k
  "out_and_back or any_includes or new_types" -v` → FAIL.
- [ ] **Step 3: Implement.** Out-and-backs respect `avoid`, `no_roads`, `deadline`.
- [ ] **Step 4: Run** `uv run pytest -q > $WORKSPACE/t8.txt; tail -3 $WORKSPACE/t8.txt` → PASS.
- [ ] **Step 5: Commit** `feat: out-and-back trips`.

### Task 9: Destinations

**Files:**
- Create: `src/backcountry/destination.py`
- Modify: `src/backcountry/planning.py`, `src/backcountry/loops.py` (`_apply_must_visit` detour limit),
  `src/backcountry/api/endpoints.py` (`generate`, plan jobs, trailheads), `src/backcountry/osm_index.py`
- Test: `tests/test_destination.py`, `tests/test_planning.py`, `tests/test_api_areas.py`, `tests/test_security_areas.py`

**Interfaces:**
- Produces:
  - `GenerateRequest.destination: str | None` (an OSM ref like `node/356671932` or `way/222586968`, validated
    against `areas._REF`; `to_dict` includes it).
  - `destination.resolve(con, tg, ref) -> Destination` (dataclass: `ref, name, kind: "trail"|"point"|"place",
    node: int | None, center: (lon, lat), bbox, offset_m: float, note: str | None, trail_name: str | None`).
    The server looks the ref up in its index (`place_by_osm`); unknown → `RequestError("destination", "That
    place isn't in the trail data.")`. Trail (type_label "Trail"): `trail_name` set, reached by walking any
    edge with that name. Point (Peak, Viewpoint, Saddle, Arch, Cliff, Valley): nearest graph node; if
    `offset_m > 400` the note (Global Constraints wording). Place (Place label, e.g. Chesler Park): nearest node
    to the center; reached when the route passes within 400 m of the center or inside its bbox when the bbox
    has area.
  - `destination.reached(route, dest) -> bool`.
  - Planning with a destination: out-and-back uses `through=dest.node`; loop/lollipop search widens the low end
    by twice the spur distance from `dest.node` to the loop graph, applies `must_visit` with no 1-mile detour
    limit for the destination, then keeps only candidates in range that `reached()` says pass it. Detour notes:
    `"Side trip to {name}, {x} mi round trip."` If nothing fits: note `"No trip from this start reaches {name}
    within {lo}–{hi} mi. The shortest out and back to it is {x} mi."` and that shortest out-and-back is the one
    candidate (real length).
  - `plan_trip(..., index=None)`: an index connection for resolving destinations (the API passes
    `worker.index()`; no index → RequestError "Destinations need the trail index on this server.").
  - `GET /regions/{region_id}/trailheads?destination=<ref>`: each trailhead gains `to_destination_m` (shortest
    trail distance, or null if unreachable); sorted by it when given.
  - `report["destination"] = {"name", "kind", "center", "note"}`.
- [ ] **Step 1: Write failing tests** (`tests/test_destination.py`, mini index + grid-style cut; add to
  `tests/fixtures/mini.osm` a made-up peak node near the end of a spur trail, a "Place" node in a meadow ringed by
  trails, and a peak 600 m off any trail — made-up names in open ocean, like the existing fixture):
  - `test_peak_destination_resolves_to_the_nearest_trail_node`
  - `test_far_peak_gets_the_no_mapped_trail_note` (asserts the verbatim note with "0.4 mi")
  - `test_out_and_back_ends_at_the_destination`
  - `test_loop_passes_through_a_place_destination`
  - `test_overnight_trip_includes_the_spur_as_a_side_trip`: 1 night, range larger than the spur → a loop with a
    detour and the "Side trip to" note, length within range.
  - `test_destination_shorter_than_the_trip_offers_the_short_trip`
  - `test_destination_unreachable_says_so` (Review Focus 1): destination on another component → note, no
    candidates, no exception.
  - `test_trailheads_sorted_by_distance_to_destination` (`test_api_areas.py`)
  - `test_destination_ref_is_validated` (`test_security_areas.py`): `"../x"`, `"node/1; rm"`, a URL → 400.
- [ ] **Step 2: Run** `uv run pytest tests/test_destination.py tests/test_api_areas.py tests/test_security_areas.py -k
  destination -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q > $WORKSPACE/t9.txt; tail -3 $WORKSPACE/t9.txt` → PASS.
- [ ] **Step 5: Commit** `feat: a searched place is the trip's destination`.

### Task 10: One-way trips and suggested finishes

**Files:**
- Modify: `src/backcountry/loops.py`, `src/backcountry/planning.py`, `src/backcountry/api/endpoints.py`
- Test: `tests/test_loops.py`, `tests/test_planning.py`, `tests/test_api.py`

**Interfaces:**
- Produces:
  - `GenerateRequest.finish: str | None` (trailhead id, node id or lat,lon, resolved like `trailhead`); required
    when `type == "one_way"` (`RequestError("finish", "A one-way trip needs a finish trailhead.")`), ignored
    otherwise.
  - `one_ways(tg, start, finish, lo, hi, no_roads=False, exclude_keys=frozenset(), through=None, deadline=None,
    report=None) -> list[Candidate]`: the shortest path, plus via-junction alternatives (start→a→finish, second
    leg avoiding the first leg's edges) in range; with `through`, start→through→finish (a spur gives an
    out-and-back in the middle, recorded in `detour_keys`). `kind="one_way"`, `finish: int` on the Candidate.
  - `suggest_finishes(tg, region, raw, node_component, start, lo, hi, through=None, dem=None) -> list[dict]`: up to
    5 trailheads (yaml + `trailheads.find_candidates`) on the start's component, not the start, whose
    shortest distance (via `through` if given) is in [lo, hi], ranked by closeness to the range middle. Each:
    `{"id", "name", "node", "lat", "lon", "length_m", "gain_ft", "loss_ft", "straight_line_m", "source",
    "verified"}` (gain/loss from the shortest route with the DEM; null without one).
  - `GET /regions/{region_id}/finishes?trailhead=&total=&destination=` → `{"finishes": [...]}`.
  - Notes on one-way candidates: `"One way: start and finish are {x} mi apart in a straight line. Shuttle drive:
    unknown."`
- [ ] **Step 1: Write failing tests**
  - `test_one_way_from_T_to_g22` (grid): candidates start at T, end at g22, real edges, in range.
  - `test_suggest_finishes_fit_the_range_and_skip_the_start`
  - `test_one_way_bad_finish_is_a_request_error` (Review Focus 2): finish == start, an off-network point, missing
    finish → `RequestError` with `field == "finish"`.
  - `test_finishes_endpoint` (`test_api.py`): returns at most 5 with miles fields.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q > $WORKSPACE/t10.txt; tail -3 $WORKSPACE/t10.txt` → PASS.
- [ ] **Step 5: Commit** `feat: one-way trips with suggested finishes`.

### Task 11: Outputs, saved trips and updates for the new shapes

**Files:**
- Modify: `src/backcountry/chat/proposal.py` (keep `destination`/`finish`), `src/backcountry/export.py` (`route_waypoints`, summaries), `src/backcountry/serialize.py`
  (`candidate_payload`, `gpx_description`), `src/backcountry/retrace.py`, `src/backcountry/api/endpoints.py`
  (`update_trip` passes kind/finish), `src/backcountry/planning.py` (`plan_route`)
- Test: `tests/test_serialize.py`, `tests/test_export.py`, `tests/test_retrace.py`, `tests/test_api.py`

**Interfaces:**
- Produces: waypoints named `"Start"`/`"Finish"` (desc "Start: {name}" / "Finish: {name}") when the route's
  ends differ; `"Start"`/`"End"` as today when they're the same. Candidate payload: `kind` may be
  `out_and_back`/`one_way`; `finish` (`{"node", "lat", "lon", "name"}` or null); `turnaround` node or null. GPX
  description of a one-way trip includes "Shuttle drive: unknown." `plan_route(..., kind)` accepts the new kinds.
- [ ] **Step 1: Write failing tests**
  - `test_one_way_gpx_has_start_and_finish` (`test_serialize.py`)
  - `test_loop_gpx_unchanged` (start/end names as before)
  - `test_retrace_follows_an_out_and_back` (Review Focus 4, `test_retrace.py`): a saved out-and-back's keys walk
    the same edges back; retrace returns ok with the same keys.
  - `test_chat_proposal_keeps_destination_and_finish` (`tests/test_chat_proposal.py`): a proposal built from a
    one-way run with a destination keeps both fields in its request.
  - `test_old_saved_run_without_new_fields_opens_and_updates` (`test_api.py`): a run payload from before (no
    `finish`, `destination`, `turnaround`) loads, exports GPX and passes the update check.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q > $WORKSPACE/t11.txt; tail -3 $WORKSPACE/t11.txt` → PASS.
- [ ] **Step 5: Commit** `feat: GPX, saved trips and updates for out-and-back and one-way trips`.

### Task 12: The phone: trip types, day hike, destination, finishes, shuttle

**Files:**
- Modify: `web/src/types.ts`, `web/src/request.ts`, `web/src/store.ts`, `web/src/app.ts`, `web/src/api.ts`,
  `web/src/map.ts`, `web/src/panels/plan.ts`, `web/src/panels/search.ts`, `web/src/panels/trip.ts`,
  `web/src/panels/compare.ts`
- Test: `web/src/request.test.ts`, `web/src/panels/plan.test.ts`, `web/src/panels/trip.test.ts`,
  `web/src/panels/search.test.ts`, `web/e2e/*` (one new Playwright flow)

**Interfaces:**
- Consumes: `/finishes` (Task 10), `/trailheads?destination=` (Task 9), `destination`/`finish` request fields.
- Produces:
  - `FormState.type` gains `"out_and_back" | "one_way"`; `FormState.finish: string`; `FormState.dayHike:
    boolean` (true ↔ nights blank; the form shows a "Day hike" switch that hides nights/daily). Type labels:
    "Any", "Loop", "Lollipop", "Out and back", "One way".
  - `State.destination: { ref: string; name: string; center: [number, number] } | null`: set when a "place"
    search result is opened, cleared when an area/region is opened. Map: a marker and zoom to it. The trailhead
    list shows only trailheads with `to_destination_m` ≤ half the form's max total (full max for one-way),
    nearest first, all reachable ones while total is invalid; heading "Starts that reach {name}".
  - One way: after a start is picked, the form lists suggested finishes (name, miles, gain, loss, "{x} mi apart
    in a straight line"); tapping one sets `form.finish`. "No finishing trailhead fits {lo}–{hi} mi" when empty.
  - Trip panel, one-way: buttons "Shuttle drive in Google Maps" →
    `https://www.google.com/maps/dir/?api=1&origin={lat},{lon}&destination={lat},{lon}&travelmode=driving` and
    "Apple Maps" → `https://maps.apple.com/?saddr={lat},{lon}&daddr={lat},{lon}&dirflg=d` (coords from the
    candidate's start/finish waypoints, 5 dp), `target="_blank" rel="noopener"`, plus the verbatim
    road-conditions note.
- [ ] **Step 1: Write failing tests**
  - `request.test.ts`: "one way needs a finish"; "day hike sends nights null"; "destination is sent".
  - `plan.test.ts`: "type list has the five types"; "with a destination, only starts in reach are listed, nearest
    first"; "one way lists suggested finishes".
  - `trip.test.ts`: "one-way trip shows shuttle links with both trailheads and the note"; "a loop has no shuttle
    links".
  - `search.test.ts`: "opening a place sets the destination; opening an area clears it".
- [ ] **Step 2: Run** `cd web && npx vitest run` → the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd web && npx vitest run && npx playwright test` → PASS (add one e2e: search a place,
  pick a start, plan, see "Side trip to" or the out-and-back).
- [ ] **Step 5: Commit** `feat(web): trip types, day hike, destinations, finishes and shuttle links`.

### Task 13: Search names: short names and abbreviations

**Files:**
- Modify: `src/backcountry/osm_index.py` (`search`), `src/backcountry/areas.py`, `src/backcountry/api/endpoints.py`
  (`search`, `open_area`), `src/backcountry/api/area_worker.py` (`spec_for`), `web/src/types.ts`,
  `web/src/panels/search.ts`, `tests/fixtures/mini.osm`
- Test: `tests/test_osm_index.py`, `tests/test_areas.py`, `tests/test_api_areas.py`, `tests/test_security_areas.py`

**Interfaces:**
- Produces:
  - `osm_index.expand_abbreviations(text) -> str` (whole words only, case-insensitive: "zion np" → "zion
    National Park").
  - `osm_index.search` expands before matching; an area whose name equals the expanded text (case-insensitive) is
    first.
  - `osm_index.name_group(con, text) -> list[dict] | None`: when the text isn't a full area name, the areas whose
    name starts with the text followed by a space, that touch the best-ranked one (geometry intersects after a
    100 m buffer), more than one of them, and whose combined area is ≤ `MAX_AREA_KM2`. Else None.
  - Search result `{"kind": "group", "refs": [...], "name": "{Text} (park and nearby wilderness)" when the
    group is one park plus wilderness, else "{Text} (nearby areas)", "type_label": "Areas", "state", "bbox",
    "center"}`, listed first.
  - `areas.area_from_refs(con, refs) -> AreaSpec`: every ref validated and re-looked up, union bbox, id from
    the sorted refs, `boundary_name_regex` an alternation of the escaped names. `POST /areas {"refs": [...]}`
    (1–6 refs).
- [ ] **Step 1: Write failing tests** (add to `mini.osm` a made-up wilderness relation touching "Testpark
  National Park" and named "Testpark Wilderness"):
  - `test_abbreviations_expand_whole_words`: "testpark np" finds Testpark National Park first; "snp" unchanged.
  - `test_short_name_groups_touching_areas`: "Testpark" → first result is a group with both refs and the
    "(park and nearby wilderness)" name.
  - `test_full_name_opens_the_park_alone`: "Testpark National Park" → no group first.
  - `test_group_open_covers_both_areas` (`test_api_areas.py`).
  - `test_group_refs_are_validated` (`test_security_areas.py`): 7 refs, a non-ref string, a ref not in the index →
    400.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q` and `cd web && npx vitest run` → PASS.
- [ ] **Step 5: Commit** `feat: "Zion" opens the park and nearby wilderness; "Zion NP" just the park`.

### Task 14: Acceptance checks, measurements and docs

**Files:**
- Modify: `tests/test_real_areas.py`, `PROGRESS.md`, `docs/ROADMAP.md`, `README.md`

**Interfaces:**
- Consumes: everything above; the real US index; `tests/reference/local/zion_west_rim_top_down.gpx` (local
  only, test skips when missing).

- [ ] **Step 1: Write the real-data checks** in `tests/test_real_areas.py` (skip without the index):
  - `test_angels_landing_day_hike`: destination `node/356671932`, day hike, from "Grotto Trailhead": an
    out-and-back whose turnaround is the trail node nearest the peak, with its offset note if > 400 m.
  - `test_angels_landing_two_nights`: 2 nights from the Grotto → a candidate with "Side trip to Angels Landing".
  - `test_chesler_park_starts`: destination `node/2315407768` → trailhead list includes Elephant Hill or
    Squaw Flat (by name, if in the data; else the test records which ones are) and a loop through it is planned.
  - `test_west_rim_one_way`: from "Lava Point Trailhead", "Grotto Trailhead" is among the suggested finishes; the
    planned trip matches the local reference track within the tolerance `tests/test_reference.py` uses.
  - `test_zion_short_and_full_names`: "Zion" → group first; "Zion National Park" and "Zion NP" → the park alone.
- [ ] **Step 2: Run** `uv run pytest tests/test_real_areas.py -v > $WORKSPACE/real.txt; tail -20 $WORKSPACE/real.txt`
  and fix what fails (each fix: a failing unit test first).
- [ ] **Step 3: Measure after** `scripts/measure-speed --fresh > $WORKSPACE/speed-after.md`; compare with the
  baseline against the spec's targets.
- [ ] **Step 4: Full suites** `uv run pytest -q`, `scripts/heavy-tests`, `cd web && npx vitest run && npx playwright
  test`; record counts.
- [ ] **Step 5: Docs.** PROGRESS.md: a section "Destinations, trip shapes and speed (built 2026-10-27…)" with the
  before/after table, acceptance results, known gaps, test counts. ROADMAP: mark built; next is park rules and
  permits (note: OSM tags `access=permit` on the Angels Landing trail). README: the new trip types, the
  prepare-popular command, `BACKCOUNTRY_PREPARED_GB`.
- [ ] **Step 6: Secrets/data check** `git status --short; git diff --stat main...HEAD | tail -3`: no `.env`, no
  `data/`, no `tests/reference/local`.
- [ ] **Step 7: Commit** `docs: destinations, trip shapes and speed: measurements, checks, gaps`.
