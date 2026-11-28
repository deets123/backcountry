# Park rules, permits and closures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every trip carries a dated, sourced "Before you go" block (permits to walk, permits to camp, day-use tickets, entrance fee, closures, rules) built from the OpenStreetMap tags already in each area's cut plus the National Park Service data service, with "unknown" wherever both are silent.

**Architecture:** A new `notices.py` holds one vocabulary (a notice dict) and three pure builders: OSM notices from a route's edges and camps, NPS notices from a park's cached alerts/things-to-do/park entry matched to the route by shared word runs, and `assemble()` that orders them and appends the unknown line. A new `nps.py` is a standard-library client with a per-park disk cache and TTLs; the server maps each loaded area to a park once and refreshes in the background, never in a plan's path. The engine attaches notices to each candidate at plan time; the API re-matches stored runs against the current cache for saved trips; the web app shows the block, chips and a park-notices sheet; the chat may only restate notices.

**Tech Stack:** Python 3.11+ standard library for the client (urllib, json), existing networkx/shapely graph, FastAPI, TypeScript web app (Vite, Vitest, Playwright). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-26-permits-and-closures-design.md`

## Global Constraints

- Rules 1 to 7 of `CLAUDE.md`: never invent facts; "unknown" where sources are silent; provenance on everything; miles and feet; no raw OSM ids in user-facing text; ODbL attribution kept.
- The only new outbound host is `developer.nps.gov` (`https://developer.nps.gov/api/v1`). The key is read from `BACKCOUNTRY_NPS_API_KEY`, sent as the `X-Api-Key` header, never put in a URL, log line, error message, payload or fixture. `.env` is never committed.
- Client: 10 s timeout, responses capped at 5 MB and parsed as JSON only, `parkCode` must match `^[a-z0-9]{2,8}$` before it names a cache folder. TTLs: alerts 6 h, things to do and park entry 7 d, parks list 30 d. Pages of 50.
- Text from NPS is HTML-stripped, cut at 400 characters with an ellipsis; links kept only when `http(s)://`. The web app never puts source text through `innerHTML` unescaped: `esc()` on text, `esc()` on link hrefs.
- Matching: a notice attaches to a route item when its title or text and the item's name share a run of two or more consecutive whole words (case-insensitive, punctuation and apostrophes dropped) with at least one word outside `GENERIC = {trail, trailhead, trailheads, road, loop, lot, parking, mountain, peak, lake, creek, north, south, east, west, upper, lower, the, to, and, of}`. Trailhead names are split on `" | "` and `" @ "`. No distance rule.
- Permit semantics follow `access_exclusion_reason`: `foot=permit`, or `access=permit` with `foot` absent or not in `yes|designated|permissive`.
- Closure text from OSM comes from trail ways only (`is_road` false); a closed way near a destination is one within 400 m; a camp's site is one within the existing 500 m (`NEARBY_CAMP_M`).
- Alerts never change routing; only `access`/`foot` tags and `avoid_permit` do.
- Block order: route-matched closures, then route-matched permits/tickets/camps, then park-wide closures, permits/tickets, fee, rules, cautions, info, then the fixed line `Everything else (water, conditions, permits and closures the sources don't list): unknown.`
- New data folder `data/nps/` git-ignored like the others; `.env.example` gains `BACKCOUNTRY_NPS_API_KEY=` with no value.
- Every UI string in this plan is exact copy from the spec unless marked "builder's wording".

## Review Focus

1. An NPS description containing `<script>` or an `onerror` attribute: the trip shows it as text, nothing executes (Task 1 `test_html_to_text_strips_tags_and_keeps_only_http_links`, Task 11 `trip.test.ts` "notice text is escaped").
2. A park whose feed returns no alerts, no matching things to do and no entrance fees: the block still ends with the unknown line and says nothing that reads as "all clear" (Task 4 `test_assemble_with_empty_park_data_has_only_the_unknown_line`).
3. Names with apostrophes, hyphens and mixed case ("Barney's Trail @ Lava Point Campground Loop", "Old Rag Lower Lot"): matching still works and never matches on generic words alone (Task 1 `test_match_ignores_punctuation_and_generic_only_runs`).
4. The NPS API answering 429, 500 or non-JSON: the cache is kept and marked stale, no exception reaches a plan or the notices endpoint, the plan's timing is unchanged (Task 3 `test_failed_refresh_keeps_the_cache_and_marks_it_stale`, Task 6 `test_plan_does_not_wait_for_the_nps_fetch`).
5. A saved trip from before this piece (no `notices` on its candidate) and a hand-made region (no `area` dict): the refresh endpoint and `park_for` work with what exists (Task 6 `test_run_notices_for_a_run_saved_before_notices_existed`, Task 3 `test_park_for_a_hand_made_region_uses_its_bbox`).

---

## File structure

- Create `src/backcountry/notices.py`: the notice dict, `GENERIC`, `KIND_ORDER`, `UNKNOWN_LINE`, `html_to_text`, `tokens`, `match_items`, `alert_kind`, `osm_route_notices`, `closed_ways_near`, `camp_notices`, `nps_notices`, `assemble`, `route_items`, `payload_items`.
- Create `src/backcountry/nps.py`: `NpsClient` (fetch, cache, TTLs, parks list, `park_for`, background `kick`).
- Modify `src/backcountry/ingest.py` (`KEEP_TAGS`, `is_permit`), `graph.py` (`permit` edge attribute, excluded records keep closure tags), `loops.py` (`Candidate.notices`, `search(exclude_keys=)`), `planning.py` (`GenerateRequest.avoid_permit`, `_excluded`, `park=` through `plan_trip`/`plan_route`, closure reason in destination notes), `serialize.py` (`notices` in the candidate payload, `KNOWN_GAPS`), `export.py` (`notices_markdown`, summary wording), `api/store.py` (`Settings.nps_key`/`nps_dir`, `LoadedRegion.park_code`, `RegionStore.on_loaded`), `api/app.py` (`app.state.nps`, `nps_fetch=`), `api/endpoints.py` (two endpoints, `park=` into plans), `api/area_worker.py` (`status_summary` line), `chat/facts.py`, `chat/grounding.py`, `chat/proposal.py`, `cli.py` (`--avoid-permit`, `notices`).
- Web: create `web/src/panels/notices.ts`; modify `types.ts`, `api.ts`, `store.ts`, `request.ts`, `panels/plan.ts`, `panels/compare.ts`, `panels/trip.ts`, `app.ts`, `e2e` spec.
- Tests: create `tests/test_notices.py`, `tests/test_nps.py`, `tests/fixtures/nps/` (trimmed real responses: `parks_small.json`, `shen_alerts.json`, `shen_things.json`, `zion_alerts.json`, `zion_things.json`, `zion_park.json`, no key anywhere); extend `test_graph.py`, `test_planning.py`, `test_serialize.py`, `test_export.py`, `test_api.py`, `test_chat_facts.py`, `test_chat_grounding.py`, `test_chat_proposal.py`, `test_cli.py`; web `panels/notices.test.ts`, `request.test.ts`, `panels/plan.test.ts`, `panels/compare.test.ts`, `panels/trip.test.ts`, `api.test.ts`, one e2e assertion.
- Docs: `README.md`, `PROGRESS.md`, `docs/ROADMAP.md`, `.env.example`, `.gitignore`.

Branch `permits-closures` in worktree `.worktrees/permits-closures` (created with superpowers:using-git-worktrees at execution start; copy the spec and this plan in and commit them first: `docs: permits and closures design and plan`). From a worktree the data lives in the main checkout: export `BACKCOUNTRY_RAW_DIR`, `BACKCOUNTRY_OSM_DIR`, `BACKCOUNTRY_AREAS_DIR` as `scripts/heavy-tests` does when running real-data checks.

---

### Task 1: Notice vocabulary, text cleaning, matching and alert kinds

**Files:**
- Create: `src/backcountry/notices.py`
- Test: `tests/test_notices.py`

**Interfaces:**
- Produces:
  - `GENERIC: frozenset[str]`, `KINDS = ("closure", "permit", "ticket", "camp", "fee", "rule", "caution", "info", "unknown")`, `KIND_ORDER: dict[str, int]` in that order, `UNKNOWN_LINE: str` (the spec's exact sentence), `MAX_TEXT = 400`.
  - `notice(kind: str, text: str, *, applies_to: str | None = None, matched: bool = False, source: str | None = None, ref: str | None = None, as_of: str | None = None, checked_at: str | None = None, links: list[str] = ()) -> dict` returning `{"kind", "text", "applies_to", "matched", "source": {"name", "ref", "as_of", "checked_at"} | None, "links"}`; raises `ValueError` for a kind not in `KINDS`; cuts text at `MAX_TEXT` with "…"; keeps only `http(s)://` links, deduplicated, in order.
  - `html_to_text(html: str) -> tuple[str, list[str]]`: tags removed, the contents of `script` and `style` elements dropped, `href` values collected, entities unescaped, whitespace collapsed.
  - `tokens(text: str) -> list[str]`: lowercase words, apostrophes removed inside words ("Barney's" → "barneys"), other punctuation as separators.
  - `name_parts(name: str) -> list[str]`: split on `" | "` and `" @ "`, stripped, empty parts dropped.
  - `match_items(text: str, items: list[tuple[str, str]]) -> list[str]`: items are `(label, name)`; returns `f"{label}: {name}"` for each item whose some part shares a qualifying run with `text`, in item order, no duplicates.
  - `alert_kind(title: str, text: str, category: str) -> str` per the spec's precedence.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notices.py
import pytest
from backcountry import notices as N

def test_notice_shape_and_text_cap():
    n = N.notice("permit", "x" * 500, applies_to="trail: A", matched=True, source="OpenStreetMap", ref="osm:way/1",
                 as_of="2026-10-26", links=["https://a.example/p", "javascript:alert(1)", "https://a.example/p"])
    assert set(n) == {"kind", "text", "applies_to", "matched", "source", "links"}
    assert n["text"].endswith("…") and len(n["text"]) == N.MAX_TEXT + 1
    assert n["links"] == ["https://a.example/p"]
    assert n["source"] == {"name": "OpenStreetMap", "ref": "osm:way/1", "as_of": "2026-10-26", "checked_at": None}
    with pytest.raises(ValueError):
        N.notice("warning", "x")

def test_html_to_text_strips_tags_and_keeps_only_http_links():
    html = ('<p>You must purchase a $1&nbsp;<a href="https://www.nps.gov/shen/x.htm">ticket</a> first.</p>'
            '<script>alert(1)</script><img src=x onerror="alert(1)"><a href="javascript:evil()">bad</a>')
    text, links = N.html_to_text(html)
    assert text == "You must purchase a $1 ticket first. bad"
    assert "<" not in text and "onerror" not in text and "alert" not in text
    assert links == ["https://www.nps.gov/shen/x.htm"]

def test_match_runs_of_two_words_with_a_non_generic_word():
    items = [("start", "West Rim Trailhead North | West Rim Trail @ Lava Point Road"), ("destination", "Old Rag Mountain"),
             ("trail", "Watchman Trail"), ("trail", "Angels Landing Trail")]
    assert N.match_items("Road to Lava Point and West Rim Trailhead closed", items) == ["start: West Rim Trailhead North | West Rim Trail @ Lava Point Road"]
    assert N.match_items("Ticket Required to Hike Old Rag", items) == ["destination: Old Rag Mountain"]
    assert N.match_items("The Watchman Trail is closed pending repairs.", items) == ["trail: Watchman Trail"]
    assert N.match_items("All hikers need a permit to hike Angels Landing.", items) == ["trail: Angels Landing Trail"]
    assert N.match_items("West Rim to Scout Lookout", [("trail", "West Rim Trail")]) == ["trail: West Rim Trail"]

def test_match_ignores_punctuation_and_generic_only_runs():
    assert N.match_items("Barneys Trail reroute", [("start", "Barney's Trail @ Lava Point Campground Loop")]) == ["start: Barney's Trail @ Lava Point Campground Loop"]
    assert N.match_items("the trail to the lake", [("trail", "The Trail"), ("trail", "Lower Lot")]) == []
    assert N.match_items("Angels Landing", [("trail", "West Rim Trail")]) == []
    assert N.match_items("old rag LOWER LOT closed", [("start", "Old Rag Lower Lot")]) == ["start: Old Rag Lower Lot"]

def test_alert_kind_precedence():
    assert N.alert_kind("Watchman Trail closed", "", "Information") == "closure"
    assert N.alert_kind("Ticket Required to Hike Old Rag", "", "Information") == "ticket"
    assert N.alert_kind("Angels Landing Pilot Permit Program", "", "Information") == "permit"
    assert N.alert_kind("Active Closure", "zone is closed", "Park Closure") == "closure"
    assert N.alert_kind("Toxic cyanobacteria", "", "Caution") == "caution"
    assert N.alert_kind("Infrastructure Improvements to Old Rag Trailhead", "lot is closed. Day-use tickets are still required.", "Information") == "ticket"
    assert N.alert_kind("Get Skyline Drive Status Updates", "text SHENALERTS", "Information") == "info"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_notices.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backcountry.notices'`

- [ ] **Step 3: Implement the module header, `notice`, `html_to_text`, `tokens`, `name_parts`, `match_items`, `alert_kind` in `src/backcountry/notices.py`**

`html_to_text` uses `html.parser.HTMLParser` from the standard library (collect `href` on `a` start tags, text in `handle_data`, skip `script`/`style` content is not required: tags are removed, text kept). `match_items`: for each part, every n-gram of 2 to 6 words from `tokens(part)` that contains a word outside `GENERIC`, checked as a consecutive sublist of `tokens(text)`. `alert_kind`: title words `closed|closure` → closure; title `ticket` → ticket; title `permit|reservation` → permit; category `Park Closure` → closure; `Danger|Caution` → caution; text `ticket` → ticket; text `permit|reservation` → permit; else info. Module docstring: notices are quoted, dated, sourced statements; nothing here concludes anything.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_notices.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/notices.py tests/test_notices.py
git commit -m "notices: vocabulary, HTML to text, word-run matching, alert kinds"
```

---

### Task 2: Permit edges, closure tags on excluded ways, and OSM route notices

**Files:**
- Modify: `src/backcountry/ingest.py:49-50` (`KEEP_TAGS`), add `is_permit` next to `access_exclusion_reason`
- Modify: `src/backcountry/graph.py:41-50` (`_way_record` keeps closure tags), `:115-135` (`_add_edge` adds `permit`)
- Modify: `src/backcountry/notices.py`
- Test: `tests/test_graph.py`, `tests/test_notices.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `ingest.KEEP_TAGS` adds `"dog", "fee", "reservation", "permit", "operator", "website", "note", "description", "opening_hours", "seasonal", "damage:date", "damage:type"`.
  - `ingest.is_permit(tags: dict) -> bool`.
  - Edge attribute `permit: bool`; `TrailGraph.excluded[*]["tags"]`: dict of `access, foot, note, description, damage:date, damage:type` when present.
  - `notices.osm_route_notices(edges: list[dict], cut_date: str | None) -> list[dict]` (edges as `route.edges`: `name`, `tags`, `is_road`, `length_m`, `source`, `permit`).
  - `notices.camp_notices(camps: list, sites: list[dict], cut_date: str | None) -> list[dict]` (camps are `split.Camp`; sites are `ingest.camp_sites(raw)` records with `source`, `name`, `tags`).
  - `notices.closed_ways_near(tg, lon: float, lat: float, within_m: float = 400.0) -> list[str]`: sentences like `Hidden Canyon is tagged closed in OpenStreetMap: "Closed indefinitely due to 2019 rockfall." (landslide, 2019-08-24)` for excluded ways with a `note`, `description` or `damage:*` tag within `within_m` of the point (nearest first); unnamed ways say "An unnamed trail".

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph.py (append)
def test_permit_follows_foot_over_access(network):
    from backcountry.ingest import is_permit
    assert is_permit({"foot": "permit"})
    assert is_permit({"access": "permit"})
    assert not is_permit({"access": "permit", "foot": "designated"})
    assert not is_permit({"access": "permit", "foot": "yes"})
    assert not is_permit({"access": "yes"})

def test_edges_carry_permit_and_rule_tags_and_excluded_ways_keep_closure_tags(network):
    network.way(20, ["G", "H"], highway="path", name="Permit Spur", access="permit", foot="permit", fee="yes",
                operator="https://www.recreation.gov/permits/1", dog="no")
    network.way(21, ["D", "E"], highway="path", name="Closed Path", access="no", note="Closed indefinitely due to 2019 rockfall.",
                **{"damage:date": "2019-08-24", "damage:type": "landslide"})
    tg = build_graph(network.doc(), CRS)
    d = next(d for *_, d in tg.graph.edges(data=True) if d["way_id"] == 20)
    assert d["permit"] is True and d["tags"]["dog"] == "no" and d["tags"]["operator"].startswith("https://")
    assert all(d["permit"] is False for *_, d in tg.graph.edges(data=True) if d["way_id"] == 1)
    closed = next(x for x in tg.excluded if x["way_id"] == 21)
    assert closed["tags"] == {"access": "no", "note": "Closed indefinitely due to 2019 rockfall.", "damage:date": "2019-08-24", "damage:type": "landslide"}
```

```python
# tests/test_notices.py (append)
def _edge(name, length_m, permit=False, is_road=False, **tags):
    return {"name": name, "tags": tags, "is_road": is_road, "length_m": length_m, "source": "osm:way/1", "permit": permit}

def test_osm_route_notices_group_permits_dogs_hours_and_trail_closures():
    edges = [_edge("Angels Landing Trail", 700.0, permit=True, access="permit", foot="permit", fee="yes", reservation="required",
                   operator="https://www.recreation.gov/permits/4675310", dog="no"),
             _edge("West Rim Trail", 3000.0, dog="no", opening_hours="sunrise-sunset"),
             _edge("Scenic Drive", 500.0, is_road=True, description="Closed to private vehicles March-October"),
             _edge("Hidden Trail", 200.0, note="Closed indefinitely due to 2019 rockfall.")]
    out = N.osm_route_notices(edges, "2026-10-26")
    kinds = [(n["kind"], n["applies_to"]) for n in out]
    assert ("permit", "trail: Angels Landing Trail") in kinds and ("closure", "trail: Hidden Trail") in kinds
    assert not any("Scenic Drive" in n["text"] for n in out)  # a road's vehicle closure is not a hiking closure
    permit = next(n for n in out if n["kind"] == "permit")
    assert permit["text"].startswith("Permit to hike Angels Landing Trail, 0.4 mi of this trip.")
    assert "Reservation required" in permit["text"] and "fee" in permit["text"].lower()
    assert permit["links"] == ["https://www.recreation.gov/permits/4675310"] and permit["source"]["as_of"] == "2026-10-26"
    dogs = next(n for n in out if n["kind"] == "rule" and n["text"].startswith("Dogs: no"))
    assert "2.3 mi" in dogs["text"] and "Angels Landing Trail" in dogs["text"] and "West Rim Trail" in dogs["text"]
    assert any(n["kind"] == "rule" and "opening hours sunrise-sunset" in n["text"] for n in out)
    assert all(n["matched"] for n in out) and all("osm:" not in n["text"] for n in out)

def test_camp_notices_quote_the_site_tags_within_500_m():
    from backcountry.split import Camp
    sites = [{"source": "osm:node/7", "name": "La Verkin Creek 7", "tags": {"access": "permit", "fee": "yes", "reservation": "no", "capacity:persons": "2", "website": "https://www.nps.gov/zion/wild.htm"}},
             {"source": "osm:node/8", "name": "Plain Site", "tags": {"tourism": "camp_site"}}]
    camps = [Camp(index=1, dist_m=1000.0, lat=0.0, lon=0.0, on_source="osm:way/1", on_name="T",
                  nearby=[{"source": "osm:node/7", "kind": "camp_pitch", "name": "La Verkin Creek 7", "distance_m": 120.0}]),
             Camp(index=2, dist_m=9000.0, lat=0.0, lon=0.0, on_source="osm:way/1", on_name="T",
                  nearby=[{"source": "osm:node/8", "kind": "camp_site", "name": "Plain Site", "distance_m": 50.0}]),
             Camp(index=3, dist_m=12000.0, lat=0.0, lon=0.0, on_source="osm:way/1", on_name="T", nearby=[])]
    out = N.camp_notices(camps, sites, "2026-10-26")
    assert len(out) == 1 and out[0]["kind"] == "camp" and out[0]["applies_to"] == "camp 1"
    assert out[0]["text"] == "Camp 1 near La Verkin Creek 7 (OpenStreetMap): permit, fee, no reservation, 2 people."
    assert out[0]["links"] == ["https://www.nps.gov/zion/wild.htm"]

def test_closed_ways_near_a_point_quote_the_reason(network):
    from backcountry.graph import build_graph
    from tests.conftest import CRS
    network.way(21, ["D", "E"], highway="path", name="Hidden Canyon", access="no", note="Closed indefinitely due to 2019 rockfall.",
                **{"damage:date": "2019-08-24", "damage:type": "landslide"})
    tg = build_graph(network.doc(), CRS)
    lon, lat = network.coords[network.ids["d1"]]
    assert N.closed_ways_near(tg, lon, lat) == ['Hidden Canyon is tagged closed in OpenStreetMap: "Closed indefinitely due to 2019 rockfall." (landslide, 2019-08-24)']
    far_lon, far_lat = network.coords[network.ids["G"]]
    assert N.closed_ways_near(tg, far_lon, far_lat) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_graph.py tests/test_notices.py -q`
Expected: the five new tests FAIL (`is_permit` missing, `permit` key missing, `osm_route_notices` missing)

- [ ] **Step 3: Implement**

`ingest.is_permit(tags)`: `foot == "permit" or (access == "permit" and foot not in FOOT_ALLOWED)`. `graph._add_edge` adds `permit=is_permit(tags)`; `_way_record` adds `tags={k: tags[k] for k in ("access", "foot", "note", "description", "damage:date", "damage:type") if k in tags}`. `osm_route_notices`: group edges by `name or "an unnamed trail"`; permit groups → one `permit` notice (text `Permit to hike {name}, {miles:.1f} mi of this trip.` then sentences from `reservation` ("Reservation required." / "Reservation: {v}."), `fee=yes` ("Fee."), `permit` ("Permit: {v}."), `note`/`description` quoted); `dog` values → one `rule` per value `Dogs: {value}, on {miles:.1f} mi ({names}).`; `opening_hours`/`seasonal` → `rule` `{name}: opening hours {v}.` / `{name}: seasonal {v}.`; trail edges whose `note` or `description` matches `closed|closure` → `closure` `{name}: "{text}"` plus ` (damage:type, damage:date)` when present. All `matched=True`, `source="OpenStreetMap"`, `ref` = first edge source, `as_of=cut_date`, links from `operator`/`website`. `camp_notices`: for each camp the site is `camp.site` or `camp.nearby[0]` when its `distance_m <= 500`; look its tags up by `source` in `sites`; a notice only when any of `access, fee, reservation, permit` is present; parts: `access=permit` → "permit", `fee=yes` → "fee", `reservation=required` → "reservation required", `reservation=no` → "no reservation", `capacity:persons` → "{n} people". `closed_ways_near`: project the point with `pyproj` into `tg.crs`, measure `LineString` distance for each excluded record that has `tags` with `note`, `description` or `damage:type`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_graph.py tests/test_notices.py -q`
Expected: all pass

- [ ] **Step 5: Run the whole suite (the `tags` field on edges and excluded records touches QA and export)**

Run: `uv run pytest -q -x`
Expected: pass

- [ ] **Step 6: Commit**

```bash
git add src/backcountry/ingest.py src/backcountry/graph.py src/backcountry/notices.py tests/test_graph.py tests/test_notices.py
git commit -m "notices: permit edges follow OSM foot semantics; OSM route, camp and closure notices"
```

---

### Task 3: The NPS client with a disk cache

**Files:**
- Create: `src/backcountry/nps.py`
- Create: `tests/fixtures/nps/parks_small.json`, `shen_alerts.json`, `shen_things.json`, `zion_alerts.json`, `zion_things.json` (real responses: the alerts files are already recorded in the session scratchpad `fetched/`; record `thingstodo?parkCode=zion&limit=50` and `thingstodo?parkCode=shen&limit=50` with `DEMO_KEY`, one call each, and `parks?parkCode=zion`, `shen`, `yose`, `grca` once each; `parks_small.json` keeps those four entries with `"total": "4"`; the things files keep every record so matching tests see the real neighbours; grep the files for `api_key` and `DEMO_KEY` before committing: none)
- Test: `tests/test_nps.py`

**Interfaces:**
- Produces:
  ```python
  HOST = "https://developer.nps.gov/api/v1"
  PARK_CODE = re.compile(r"^[a-z0-9]{2,8}$")
  ALERTS_TTL_S, THINGS_TTL_S, PARKS_TTL_S = 6 * 3600, 7 * 86400, 30 * 86400
  MAX_BYTES, TIMEOUT_S, PAGE = 5_000_000, 10, 50

  class NpsClient:
      def __init__(self, cache_dir: Path | None, key: str | None, fetch=None, clock=time.time, index_opener=None, thread: bool = True)
      configured: bool            # key and cache_dir both set
      def parks(self) -> list[dict]                      # the cached list; fetched when configured and missing/past TTL
      def park_for(self, region) -> str | None           # spec's three-step mapping; None when no parks list
      def park_name(self, code: str) -> str | None
      def cached(self, code: str) -> dict | None         # {"park", "alerts", "things", "fetched_at", "stale", "error"} or None
      def refresh(self, code: str) -> dict | None        # fetch what is past TTL; keeps the cache on failure (stale=True, error=str)
      def kick(self, code: str) -> None                  # refresh in a daemon thread when due; one in flight per code
      def due(self, code: str) -> bool
  ```
  `fetch(url: str, headers: dict) -> bytes` is a public attribute (tests swap it); the default uses `urllib.request` with `TIMEOUT_S`, reads at most `MAX_BYTES + 1` bytes and raises `ValueError("response too big")` past the cap, and lets `urllib.error.URLError`/`HTTPError` through. Cache files: `<cache_dir>/parks.json` and `<cache_dir>/<code>/{park,alerts,things}.json`, each `{"fetched_at": iso, "data": ...}`, written atomically (`.tmp` then `os.replace`). A piece that fails with nothing cached is empty (`alerts`/`things` → `[]`, `park` → `None`) with `stale=True`; `refresh` returns `None` only when all three are missing. Logging goes to `backcountry.nps` and never includes headers or the key. The park entry is the `/parks?parkCode={code}&limit=1` record whose `parkCode` equals `code`. The containing-area mapping step (step 3 of `park_for`) has no unit test: it needs the real index and is covered by acceptance check 6 in Task 12.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_nps.py
import json, logging
from pathlib import Path
import pytest
from backcountry import nps
from backcountry.region import load_region

FIX = Path(__file__).parent / "fixtures" / "nps"

class FakeFetch:
    def __init__(self, pages: dict[str, bytes], fail: dict[str, Exception] | None = None):
        self.pages, self.fail, self.calls = pages, fail or {}, []
    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        for key, exc in self.fail.items():
            if key in url: raise exc
        for key, body in self.pages.items():
            if key in url: return body
        raise AssertionError(f"unexpected url {url}")

def _pages():
    return {"/parks?": (FIX / "parks_small.json").read_bytes(), "parkCode=shen&": (FIX / "shen_alerts.json").read_bytes()}

def _client(tmp_path, fetch, clock, key="k-secret"):
    return nps.NpsClient(tmp_path / "nps", key, fetch=fetch, clock=clock, thread=False)

def test_key_travels_in_the_header_never_in_the_url_or_the_log(tmp_path, caplog):
    fetch = FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes(),
                       "/alerts?": (FIX / "shen_alerts.json").read_bytes(),
                       "/thingstodo?": (FIX / "shen_things.json").read_bytes()})
    c = _client(tmp_path, fetch, lambda: 1_000.0)
    with caplog.at_level(logging.DEBUG, logger="backcountry.nps"):
        data = c.refresh("shen")
    assert all("k-secret" not in url for url, _ in fetch.calls)
    assert all(h.get("X-Api-Key") == "k-secret" for _, h in fetch.calls)
    assert "k-secret" not in caplog.text and "k-secret" not in json.dumps(data)
    assert data["alerts"] and data["things"] and data["fetched_at"] and data["stale"] is False

def test_cache_is_reused_within_ttl_and_refetched_after(tmp_path):
    now = [1_000.0]
    fetch = FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes(), "/alerts?": (FIX / "shen_alerts.json").read_bytes(),
                       "/thingstodo?": (FIX / "shen_things.json").read_bytes()})
    c = _client(tmp_path, fetch, lambda: now[0])
    c.refresh("shen"); n = len(fetch.calls)
    assert c.due("shen") is False and c.refresh("shen") is not None and len(fetch.calls) == n
    now[0] += nps.ALERTS_TTL_S + 1
    assert c.due("shen") is True
    c.refresh("shen")
    assert [u for u, _ in fetch.calls[n:]] and all("/alerts?" in u for u, _ in fetch.calls[n:])  # only alerts were due

def test_failed_refresh_keeps_the_cache_and_marks_it_stale(tmp_path):
    import urllib.error
    now = [1_000.0]
    good = FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes(), "/alerts?": (FIX / "shen_alerts.json").read_bytes(),
                      "/thingstodo?": (FIX / "shen_things.json").read_bytes()})
    c = _client(tmp_path, good, lambda: now[0]); first = c.refresh("shen")
    now[0] += nps.ALERTS_TTL_S + 1
    c.fetch = FakeFetch({}, fail={"/alerts?": urllib.error.HTTPError("u", 429, "Too Many", {}, None)})
    data = c.refresh("shen")
    assert data["alerts"] == first["alerts"] and data["stale"] is True and "429" in data["error"]
    c.fetch = FakeFetch({"/alerts?": b"<html>not json</html>"})
    assert c.refresh("shen")["stale"] is True

def test_oversize_responses_and_bad_park_codes_are_refused(tmp_path):
    def big(url, headers):
        if "/alerts?" in url: raise ValueError("response too big")  # what the default fetch raises past MAX_BYTES
        return {"/parks?": (FIX / "parks_small.json").read_bytes(), "/thingstodo?": (FIX / "shen_things.json").read_bytes()}[next(k for k in ("/parks?", "/thingstodo?") if k in url)]
    c = _client(tmp_path, big, lambda: 1.0)
    with pytest.raises(ValueError):
        c.cached("../etc")
    d = c.refresh("shen")
    assert d["stale"] is True and d["alerts"] == [] and "too big" in d["error"] and d["things"]
    assert sorted(p.name for p in (tmp_path / "nps").iterdir()) == ["parks.json", "shen"]

def test_unconfigured_client_reads_the_cache_and_never_fetches(tmp_path):
    fetch = FakeFetch({})
    c = nps.NpsClient(tmp_path / "nps", None, fetch=fetch, clock=lambda: 1.0, thread=False)
    assert c.configured is False and c.cached("shen") is None and c.refresh("shen") is None and fetch.calls == []

def test_pagination_follows_total(tmp_path):
    page1 = json.dumps({"total": "60", "limit": "50", "start": "0", "data": [{"id": str(i)} for i in range(50)]}).encode()
    page2 = json.dumps({"total": "60", "limit": "50", "start": "50", "data": [{"id": str(i)} for i in range(50, 60)]}).encode()
    fetch = FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes(), "start=50": page2, "/thingstodo?": page1, "/alerts?": b'{"total":"0","data":[]}'})
    c = _client(tmp_path, fetch, lambda: 1.0)
    assert len(c.refresh("shen")["things"]) == 60

def test_park_for_matches_by_name_and_group_members(tmp_path):
    c = _client(tmp_path, FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes()}), lambda: 1.0, key="k")
    zion = type("R", (), {"name": "Zion National Park", "area": {"name": "Zion National Park", "osm": None}, "bbox": None})()
    assert c.park_for(zion) == "zion"
    group = type("R", (), {"name": "Zion (park and nearby wilderness)", "area": {"name": "Zion", "osm": None, "members": [["relation/1", "Zion Wilderness"], ["relation/2", "Zion National Park"]]}, "bbox": None})()
    assert c.park_for(group) == "zion"
    nowhere = type("R", (), {"name": "Pine Hill Wilderness", "area": {"name": "Pine Hill Wilderness", "osm": None}, "bbox": None})()
    assert c.park_for(nowhere) is None

def test_park_for_a_hand_made_region_uses_its_bbox(tmp_path):
    c = _client(tmp_path, FakeFetch({"/parks?": (FIX / "parks_small.json").read_bytes()}), lambda: 1.0)
    region = load_region("dolly_sods")
    assert c.park_for(region) is None  # no NPS unit point inside Dolly Sods' box
    from backcountry.region import BBox
    fake = type("R", (), {"name": "Somewhere", "area": None, "bbox": BBox(south=37.0, west=-113.3, north=37.6, east=-112.8)})()
    assert c.park_for(fake) == "zion"  # Zion's point (37.30, -113.05) is inside
```

`parks_small.json` holds the real entries for `zion`, `shen`, `yose` and `grca` (fields `parkCode, fullName, name, designation, states, latitude, longitude, url, entranceFees`), nothing else.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_nps.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backcountry.nps'`

- [ ] **Step 3: Implement `src/backcountry/nps.py`**

`refresh(code)`: validate code (`ValueError`), load each of park/alerts/things from cache, refetch those missing or past TTL (`/parks?parkCode={code}&limit=1` for the park entry; `/alerts?parkCode=&limit=50&start=`; `/thingstodo?parkCode=&limit=50&start=`), parse `json.loads` (any exception → keep the old file, set `stale=True`, `error=str(e)` without the URL's query string), write atomically, return the assembled dict with `fetched_at` = the oldest of the three files' stamps as ISO. `park_for`: `_norm(s)` = lowercase, `&`→`and`, non-alphanumerics to spaces, collapsed; step 1 on `region.area["name"]`, each `members` name, then `region.name`; step 2 with `shapely` `Point(lon, lat).within(shape)` where shape is `osm_index.area_shape(con, area["osm"])` when `index_opener` and `area.get("osm")`, else `box(west, south, east, north)` from `region.bbox`; several hits → the one whose `designation` contains "National Park", else the first by `fullName`; step 3 when `index_opener`: `osm_index.containing_areas(con, center_lon, center_lat)` smallest first, the first whose normalised name equals a unit's `fullName`. `kick`: `threading.Thread(daemon=True)` guarded by a per-code lock; `thread=False` runs inline (tests).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_nps.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/nps.py tests/test_nps.py tests/fixtures/nps
git commit -m "nps: National Park Service client with a per-park disk cache and TTLs; key only in the header"
```

---

### Task 4: NPS notices and assembling the block

**Files:**
- Modify: `src/backcountry/notices.py`
- Test: `tests/test_notices.py`

**Interfaces:**
- Consumes: Task 1, Task 3's `cached()` dict shape (`park`: the `/parks` entry; `alerts`, `things`: lists; `fetched_at`; `stale`; `error`).
- Produces:
  - `nps_notices(park_data: dict, items: list[tuple[str, str]]) -> list[dict]`: alerts (all, park-wide unless matched), matched things to do, the entrance fee. When several items match, `applies_to` is the first by label priority `destination`, `trail`, `start`, `finish`, `camp N site`.
  - `assemble(osm: list[dict], park: dict | None, items: list[tuple[str, str]]) -> tuple[list[dict], bool]` where `park = {"code": str | None, "configured": bool, "data": dict | None}`; returns the ordered block ending with the `unknown` notice, and `pending` (True when `code` and `configured` and `data is None`). Adds the two info lines: no code → `No official notice feed for this area.`; not configured → `Park notices from the National Park Service aren't set up on this server.`; `data["stale"]` → the fee/alert notices' `checked_at` is kept and one `info` line `Couldn't refresh park notices; last checked {time}.` (builder's wording).
  - `route_items(trail_names: list[str], start: str | None, finish: str | None, destination: str | None, camp_sites: list[tuple[int, str]]) -> list[tuple[str, str]]` → labels `trail`, `start`, `finish`, `destination`, `camp N site`.
  - `payload_items(run: dict, candidate: dict) -> list[tuple[str, str]]` from a stored run: `segments[].label` (named, unique, in order), `run["start"]["name"]`, `run["search"]["finish"]["name"]`, `run["search"]["destination"]["name"]`, `plan.camps[].nearby[0].name`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notices.py (append)
import json
from pathlib import Path
FIX = Path(__file__).parent / "fixtures" / "nps"

def _park(code):
    alerts = json.loads((FIX / f"{code}_alerts.json").read_text())["data"]
    things = json.loads((FIX / f"{code}_things.json").read_text())["data"]
    park = next(p for p in json.loads((FIX / "parks_small.json").read_text())["data"] if p["parkCode"] == code)
    return {"park": park, "alerts": alerts, "things": things, "fetched_at": "2026-11-26T10:04:00Z", "stale": False, "error": None}

def test_old_rag_gets_the_ticket_alert_the_circuit_record_and_the_fee():
    items = N.route_items(["Ridge Trail", "Saddle Trail"], "Old Rag Lower Lot", None, "Old Rag Mountain", [])
    out = N.nps_notices(_park("shen"), items)
    ticket = next(n for n in out if n["kind"] == "ticket" and n["text"].startswith("Ticket Required to Hike Old Rag"))
    assert ticket["matched"] and ticket["applies_to"] == "destination: Old Rag Mountain"
    assert ticket["source"] == {"name": "National Park Service", "ref": None, "as_of": "2024-01-17", "checked_at": "2026-11-26T10:04:00Z"}
    assert ticket["links"] == ["https://www.nps.gov/shen/planyourvisit/faqs-oldrag.htm"]
    circuit = next(n for n in out if n["kind"] == "ticket" and n["text"].startswith("Old Rag Circuit:"))
    assert "day-use ticket" in circuit["text"] and "<" not in circuit["text"]
    pets = next(n for n in out if n["kind"] == "rule" and "Pets" in n["text"])
    assert "Ridge, Saddle" in pets["text"]
    fee = next(n for n in out if n["kind"] == "fee")
    assert fee["text"].startswith("Entrance fee: Shenandoah National Park") and "$" in fee["text"] and fee["applies_to"] == "park"
    assert not any("Nicholson Hollow" in n["text"] for n in out)  # an unmatched record is not shown

def test_west_rim_day_hike_gets_the_road_closure_but_not_the_angels_landing_record():
    items = N.route_items(["West Rim Trail"], "West Rim Trailhead North | West Rim Trail @ Lava Point Road", "Grotto Trailhead", None, [])
    out = N.nps_notices(_park("zion"), items)
    road = next(n for n in out if n["kind"] == "closure" and "Lava Point" in n["text"])
    assert road["matched"] and "West Rim" in road["applies_to"]  # the trail or the start, both named West Rim
    assert not any(n["matched"] and "Angels Landing" in n["text"] for n in out)
    assert any(not n["matched"] and n["kind"] == "permit" and "Angels Landing" in n["text"] for n in out)  # park-wide, still shown

def test_assemble_orders_matched_closures_first_and_ends_with_the_unknown_line():
    osm = [N.notice("permit", "Permit to hike Angels Landing Trail, 0.4 mi of this trip.", applies_to="trail: Angels Landing Trail", matched=True, source="OpenStreetMap")]
    items = N.route_items(["Angels Landing Trail", "West Rim Trail"], "Grotto Trailhead", None, "Angels Landing", [])
    block, pending = N.assemble(osm, {"code": "zion", "configured": True, "data": _park("zion")}, items)
    kinds = [(n["matched"], n["kind"]) for n in block]
    assert pending is False and block[-1]["kind"] == "unknown" and block[-1]["text"] == N.UNKNOWN_LINE
    matched = [k for m, k in kinds if m]
    assert matched == sorted(matched, key=N.KIND_ORDER.get) and kinds.index((True, "permit")) < kinds.index((False, "closure"))
    assert kinds.index((False, "fee")) < kinds.index((False, "caution")) < kinds.index((False, "info"))
    assert "Watchman Trail closed" in " ".join(n["text"] for n in block)

def test_assemble_with_empty_park_data_has_only_the_unknown_line():
    empty = {"park": {"fullName": "Quiet Park", "entranceFees": [], "url": "https://www.nps.gov/quie/index.htm"}, "alerts": [], "things": [],
             "fetched_at": "2026-11-26T10:04:00Z", "stale": False, "error": None}
    block, pending = N.assemble([], {"code": "quie", "configured": True, "data": empty}, [])
    assert [n["kind"] for n in block] == ["fee", "unknown"] and "none listed" in block[0]["text"]

def test_assemble_without_a_park_or_a_key_says_so_and_flags_pending():
    block, pending = N.assemble([], {"code": None, "configured": True, "data": None}, [])
    assert [n["text"] for n in block][:1] == ["No official notice feed for this area."] and pending is False
    block, pending = N.assemble([], {"code": "zion", "configured": False, "data": None}, [])
    assert block[0]["text"] == "Park notices from the National Park Service aren't set up on this server." and pending is False
    block, pending = N.assemble([], {"code": "zion", "configured": True, "data": None}, [])
    assert pending is True and [n["kind"] for n in block] == ["unknown"]

def test_payload_items_read_a_stored_run():
    run = {"start": {"name": "Grotto Trailhead"}, "search": {"finish": None, "destination": {"name": "Angels Landing"}}}
    cand = {"segments": [{"label": "West Rim Trail"}, {"label": "Angels Landing Trail"}, {"label": "West Rim Trail"}],
            "plan": {"camps": [{"nearby": [{"name": "La Verkin Creek 7"}]}, {"nearby": []}]}}
    assert N.payload_items(run, cand) == [("trail", "West Rim Trail"), ("trail", "Angels Landing Trail"), ("start", "Grotto Trailhead"),
                                          ("destination", "Angels Landing"), ("camp 1 site", "La Verkin Creek 7")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_notices.py -q`
Expected: the six new tests FAIL with `AttributeError` on `nps_notices`/`assemble`/`route_items`/`payload_items`

- [ ] **Step 3: Implement**

`nps_notices`: alerts → `alert_kind`, `text = f"{title}: {body}"` with `body, links = html_to_text(description)`, `links += [url]`, `as_of = lastIndexedDate[:10]`, `matched labels = match_items(title + " " + body, items)`, `applies_to = labels[0] if labels else "park"`; things → `labels = match_items(title + " " + " ".join(tags), items)`, skipped when empty, then: `isReservationRequired == "true"` → kind `ticket` if "ticket" in the reservation text else `permit`, text `f"{title}: {reservation text}"`; `doFeesApply == "true"` → `fee` with `feeDescription` unless it is only the entrance-fee sentence (text contains "entrance fee" and no digit); pets → `rule` `f"Pets: {petsDescription}"` when `arePetsPermitted == "false"` or `arePetsPermittedWithRestrictions == "true"`; `seasonDescription`, `timeOfDayDescription` → `rule` `f"{title}: {text}"` when non-empty; `url` as link; `as_of=None`, `checked_at=fetched_at`. Fee: `entranceFees` → `Entrance fee: {fullName}: {title} ${cost}; …` (up to 3) else `Entrance fee: none listed by the National Park Service for {fullName}.`, link `park["url"]`. `assemble`: concatenate, sort with key `(0 if matched and kind in {closure, permit, ticket, camp} else 1, KIND_ORDER[kind], 0 if matched else 1)`, append info lines and the unknown notice.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_notices.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/notices.py tests/test_notices.py
git commit -m "notices: NPS alerts, things to do and fees matched to the route; assemble the Before you go block"
```

---

### Task 5: Engine wiring: avoid_permit, notices on candidates, closure reasons, payload and summaries

**Files:**
- Modify: `src/backcountry/loops.py:52-75` (`Candidate.notices`), `:343-347` and `:365` (`search(exclude_keys=frozenset())`)
- Modify: `src/backcountry/planning.py:141-215` (`avoid_permit`), `:337-370` (`_destination_notes`), `:456-485` (`plan_route`), `:583-606` (`_destination_search`), `:606-689` (`plan_trip`)
- Modify: `src/backcountry/serialize.py:16-23` (`KNOWN_GAPS`), `:126-150` (`notices` in `candidate_payload`)
- Modify: `src/backcountry/export.py` (`notices_markdown`; `write_route` summary; `write_candidates` footer; the `bc-src` map line)
- Test: `tests/test_planning.py`, `tests/test_serialize.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: Tasks 2 and 4.
- Produces:
  - `GenerateRequest.avoid_permit: bool = False` (from_dict `_flag`, to_dict key `avoid_permit`).
  - `planning.permit_keys(tg) -> frozenset[str]`; `planning._excluded(tg, req) -> frozenset[str]` = avoided names ∪ permit keys when `req.avoid_permit`.
  - `loops.search(..., exclude_keys=frozenset())`: merged into `excluded`.
  - `plan_trip(..., park: dict | None = None)` and `plan_route(..., park: dict | None = None)`: `park` as in `assemble`; default `None` behaves as `{"code": None, "configured": False, "data": None}`. Each candidate gets `c.notices: list[dict]`; `report["notices_pending"]: bool`.
  - `serialize.candidate_payload` adds `"notices": c.notices or []`; `KNOWN_GAPS` drops "permits: not checked" and "closures: not checked" and gains `"permits and closures: only what OpenStreetMap and the National Park Service list (see Before you go); everything else unknown"`.
  - `export.notices_markdown(notices: list[dict]) -> list[str]` (a "## Before you go" section: one bullet per notice with its source and date, links as bare URLs).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_planning.py (append; REGION, CRS, build_graph, GenerateRequest, plan_trip are already imported there)
from backcountry.export import route_segments

def _permit_grid(grid):
    col2 = next(e for e in grid.elements if e["type"] == "way" and e["id"] == 113)  # "Col 2", part of the right-hand loops
    col2["tags"] |= {"access": "permit", "foot": "permit"}
    return grid

def _labels(c):
    return [s["label"] for s in route_segments(c.route)]

def test_avoid_permit_excludes_permit_edges_like_avoid(grid):
    from backcountry.planning import permit_keys
    g = _permit_grid(grid); raw = g.doc(); tg = build_graph(raw, CRS)
    assert permit_keys(tg) == {k for *_, k, d in tg.graph.edges(keys=True, data=True) if d["name"] == "Col 2"}
    base = {"trailhead": str(g.ids["g00"]), "total": "2-8", "type": "loop", "count": 10}
    with_permit = plan_trip(REGION, raw, tg, GenerateRequest.from_dict(base), dem=None)
    assert any("Col 2" in _labels(c) for c in with_permit.report["candidates"])
    req = GenerateRequest.from_dict(base | {"avoid_permit": True})
    assert req.avoid_permit is True and req.to_dict()["avoid_permit"] is True
    without = plan_trip(REGION, raw, tg, req, dem=None)
    assert without.report["candidates"] and all("Col 2" not in _labels(c) for c in without.report["candidates"])

def test_candidates_carry_notices_and_pending_follows_the_park(grid):
    g = _permit_grid(grid); raw = g.doc(); tg = build_graph(raw, CRS)
    req = GenerateRequest.from_dict({"trailhead": str(g.ids["g00"]), "total": "2-8", "type": "loop", "count": 10})
    r = plan_trip(REGION, raw, tg, req, dem=None)
    for c in r.report["candidates"]:
        assert c.notices[-1]["kind"] == "unknown" and any(n["text"] == "No official notice feed for this area." for n in c.notices)
    on_permit = next(c for c in r.report["candidates"] if "Col 2" in _labels(c))
    assert any(n["kind"] == "permit" and n["applies_to"] == "trail: Col 2" and n["matched"] for n in on_permit.notices)
    r2 = plan_trip(REGION, raw, tg, req, dem=None, park={"code": "zion", "configured": True, "data": None})
    assert r2.report["notices_pending"] is True
    assert all([n["kind"] for n in c.notices if n["source"] is None or n["source"]["name"] != "OpenStreetMap"] == ["unknown"] for c in r2.report["candidates"])
```

```python
# tests/test_destination.py (append; uses the module's `park` and `index` fixtures)
def test_destination_on_a_closed_way_names_the_closure(index, park):
    import copy
    region, raw, _ = park
    raw2 = copy.deepcopy(raw)
    spur = next(e for e in raw2["response"]["elements"] if e["type"] == "way" and e["id"] == 2009)  # Peak Spur, to Spur Top
    spur["tags"] |= {"access": "no", "note": "Closed indefinitely due to 2019 rockfall.", "damage:date": "2019-08-24", "damage:type": "landslide"}
    tg2 = build_graph(raw2, region.crs)
    req = GenerateRequest.from_dict({"trailhead": "10.2495,-149.8505", "total": "18-24", "destination": "node/1060"})
    r = plan_trip(region, raw2, tg2, req, dem=None, index=index)
    assert 'Peak Spur is tagged closed in OpenStreetMap: "Closed indefinitely due to 2019 rockfall." (landslide, 2019-08-24)' in r.report["notes"]
```

```python
# tests/test_serialize.py (append)
def test_payload_carries_notices_and_the_gap_list_points_at_them(planned):
    run = run_payload("r", *planned)  # match the file's existing helper usage
    c = run["candidates"][0]
    assert c["notices"][-1]["kind"] == "unknown"
    assert not any(g.startswith("permits:") or g.startswith("closures:") for g in run["known_gaps"])
    assert any("Before you go" in g for g in run["known_gaps"])
```

```python
# tests/test_export.py (append)
def test_summary_has_a_before_you_go_section(planned_dir):
    text = (planned_dir / "candidate_1" / "summary.md").read_text()
    assert "## Before you go" in text and "Everything else (water, conditions, permits and closures the sources don't list): unknown." in text
    assert "permits, closures" not in text.split("## Known gaps")[1].split("##")[0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_planning.py tests/test_destination.py tests/test_serialize.py tests/test_export.py -q`
Expected: the new tests FAIL (`avoid_permit` unknown key ignored, `notices` missing, no closure sentence, no section)

- [ ] **Step 3: Implement**

`loops.Candidate.notices: list | None = None`. `loops.search(..., exclude_keys=frozenset())`: `excluded = set(exclude_keys) | (avoided_keys(tg, avoid) if avoid else set())`. `planning.permit_keys(tg)`: keys of edges with `d["permit"]`. `_excluded(tg, req)`; used in `_destination_search` (replacing the `avoided_keys` call), `_destination_notes` (same), and passed as `exclude_keys=_excluded(tg, req)` into `search`. In `plan_trip`, right after `_destination_search`, when `dest is not None`: `dest_notes += closed_ways_near(tg, *dest.center)` (`Destination.center` is `(lon, lat)`); the sentences land in `report["notes"]` with the other destination notes. When `avoid_permit` made the destination unreachable (`dest_kw is None` and `permit_keys(tg) & reachable-without-permit` differ), `_destination_search`'s existing wording gains " (trails that need a permit are avoided)". In `plan_trip.finish()` and `plan_route`: `trail_names = [s["label"] for s in route_segments(c.route) if ...named]`, `items = route_items(trail_names, start["name"], finish name, dest.name if dest else None, [(cp.index, cp.nearby[0]["name"]) ...])`, `osm = osm_route_notices(c.route.edges, cut) + camp_notices(c.plan.camps, steps.sites, cut) if c.plan else ...`, `c.notices, pending = assemble(osm, park or {"code": None, "configured": False, "data": None}, items)`, `report["notices_pending"] = pending`; `cut = raw.get("cut") or raw["retrieved_at"][:10]`. `serialize`: `"notices": c.notices or []`. `export.notices_markdown`: `["## Before you go", ""] + [f"- {n['text']} — {source name}{', ' + as_of if as_of}{', checked ' + checked_at if checked_at}{' ' + ' '.join(links)}" ...]`; `write_route` summary inserts it before "## Known gaps" and the Known gaps paragraph becomes "These are not checked by this tool: water, trail conditions, trailhead access. Permits and closures: only what Before you go lists; everything else is unknown. Camp sites and water …" ; `write_candidates` footer: "Water, trail conditions and trailhead access: **unknown**. Permits and closures: see each candidate's Before you go."; the map `bc-src` line: "Water, conditions: unknown; permits and closures: only as listed."

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `uv run pytest tests/test_planning.py tests/test_destination.py tests/test_serialize.py tests/test_export.py -q && uv run pytest -q -x`
Expected: pass; golden-route tests unchanged (notices don't change routing)

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/loops.py src/backcountry/planning.py src/backcountry/serialize.py src/backcountry/export.py tests/test_planning.py tests/test_destination.py tests/test_serialize.py tests/test_export.py
git commit -m "engine: avoid trails that need a permit; notices on every candidate; closure reasons at a destination; Before you go in summaries"
```

---

### Task 6: Server: settings, park mapping on load, background refresh, two endpoints, security

**Files:**
- Modify: `src/backcountry/api/store.py:25-51` (`Settings.nps_key`, `nps_dir`), `:57-70` (`LoadedRegion.park_code: str | None = None`), `:115-120`, `:200-235` (`RegionStore.on_loaded`)
- Modify: `src/backcountry/api/app.py` (`create_app(..., nps_fetch=None)`, `app.state.nps`, `regions.on_loaded`)
- Modify: `src/backcountry/api/endpoints.py:208-235` (`_plan` passes `park=`), `:336-375` (`update_trip` passes `park=`), new `GET /regions/{region_id}/notices`, new `GET /runs/{run_id}/notices`
- Modify: `src/backcountry/api/area_worker.py:272-280` (`status_summary` adds `nps: {"configured", "cache_kb"}`), `src/backcountry/cli.py:468-484` (startup line)
- Modify: `.gitignore` (`data/nps/*`, `!data/nps/.gitkeep`), `.env.example`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Tasks 3, 4, 5.
- Produces:
  - `Settings.nps_key: str | None = None`, `Settings.nps_dir: Path | None = None`; `Settings.default()` reads `BACKCOUNTRY_NPS_API_KEY` and sets `nps_dir=REPO_ROOT / "data" / "nps"`.
  - `app.state.nps: NpsClient(settings.nps_dir, settings.nps_key, fetch=nps_fetch, index_opener=app.state.areas.index)`.
  - `app.state.nps.park_of(loaded) -> dict`: `{"code", "configured", "data"}` for `assemble`; maps once (`loaded.park_code`, `loaded.park_mapped`), kicks a refresh when due.
  - `GET /api/regions/{region_id}/notices` → `{"configured": bool, "park": {"code": str, "name": str} | None, "pending": bool, "stale": bool, "error": str | null, "checked_at": str | None, "notices": [...]}` (park-wide: alerts and fee via `nps_notices(data, [])`, info lines, no unknown line).
  - `GET /api/runs/{run_id}/notices?candidate=n` → `{"notices": [...], "pending": bool, "checked_at": str | None}` re-assembled from the stored candidate's OpenStreetMap notices (`[n for n in c.get("notices", []) if (n.get("source") or {}).get("name") == "OpenStreetMap"]`) and the park's current data with `payload_items`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py (append)
from pathlib import Path
FIX = Path(__file__).parent / "fixtures" / "nps"

def _nps_pages():
    return {"/parks?": (FIX / "parks_small.json").read_bytes(), "/alerts?": (FIX / "zion_alerts.json").read_bytes(),
            "/thingstodo?": (FIX / "zion_things.json").read_bytes()}

def _fake_fetch(pages, calls):
    def fetch(url, headers):
        calls.append((url, headers))
        for k, v in pages.items():
            if k in url: return v
        raise AssertionError(url)
    return fetch

def test_region_notices_without_a_key_say_so(client):
    r = client.get("/api/regions/dolly_sods/notices")
    assert r.status_code == 200 and r.json()["configured"] is False and r.json()["park"] is None
    assert r.json()["notices"][0]["text"] == "Park notices from the National Park Service aren't set up on this server."

@pytest.fixture
def zion_env(tmp_path, grid):
    """A hand-made region whose box holds Zion's NPS point, with the synthetic grid as its trails (the grid's coordinates
    are elsewhere; only the bbox matters for park_for), and a key set."""
    regions = tmp_path / "regions"; regions.mkdir()
    (regions / "zion_box.yaml").write_text(yaml.safe_dump({"id": "zion_box", "name": "Zion box", "crs": "EPSG:32617",
        "bbox": {"south": 37.0, "west": -113.3, "north": 37.6, "east": -112.8}, "trailheads": []}))
    raw_dir = tmp_path / "raw" / "zion_box"; raw_dir.mkdir(parents=True)
    (raw_dir / "overpass_20260101T000000Z.json").write_text(json.dumps(grid.doc()))
    return Settings(raw_dir=tmp_path / "raw", runs_dir=tmp_path / "runs", regions_dir=regions, nps_key="k-secret", nps_dir=tmp_path / "nps",
                    area_thread=False)  # NPS refreshes run inline, so a GET sees the fetched data

def test_region_notices_with_a_key_map_the_park_and_hide_the_key(zion_env):
    calls = []
    env = zion_env
    c = TestClient(create_app(env, web_dist=env.runs_dir / "no-web", nps_fetch=_fake_fetch(_nps_pages(), calls)))
    r = c.get("/api/regions/zion_box/notices")
    assert r.json()["configured"] is True and r.json()["park"] == {"code": "zion", "name": "Zion National Park"}
    assert any(n["kind"] == "fee" for n in r.json()["notices"]) and any("Watchman" in n["text"] for n in r.json()["notices"])
    assert "k-secret" not in r.text and all("k-secret" not in u for u, _ in calls)

def test_plan_does_not_wait_for_the_nps_fetch(zion_env, grid):
    import threading
    gate = threading.Event()
    def slow_fetch(url, headers):
        gate.wait(5.0)  # the plan must return before this
        return _nps_pages()[next(k for k in _nps_pages() if k in url)]
    env = zion_env
    c = TestClient(create_app(env, web_dist=env.runs_dir / "no-web", nps_fetch=slow_fetch))
    t0 = time.perf_counter()
    r = c.post("/api/regions/zion_box/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2-8", "type": "loop"})
    assert r.status_code == 200 and time.perf_counter() - t0 < 4.0
    assert r.json()["search"]["notices_pending"] is True
    gate.set()

def test_run_notices_rematch_a_stored_run_and_fill_in_after_the_fetch(zion_env, grid):
    env = zion_env
    c = TestClient(create_app(env, web_dist=env.runs_dir / "no-web", nps_fetch=_fake_fetch(_nps_pages(), [])))
    c.get("/api/regions/zion_box/notices")  # maps the park and (inline in tests: Settings.area_thread False → nps thread off) fills the cache
    run = c.post("/api/regions/zion_box/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2-8", "type": "loop"}).json()
    r = c.get(f"/api/runs/{run['run_id']}/notices", params={"candidate": 1})
    assert r.status_code == 200 and r.json()["pending"] is False and r.json()["notices"][-1]["kind"] == "unknown"
    assert any(n["kind"] == "fee" for n in r.json()["notices"])
    assert c.get(f"/api/runs/{run['run_id']}/notices", params={"candidate": 9}).status_code == 404

def test_run_notices_for_a_run_saved_before_notices_existed(env, client, grid):
    run = client.post("/api/regions/dolly_sods/generate", json={"trailhead": str(grid.ids["g00"]), "total": "2-8", "type": "loop"}).json()
    path = env.runs_dir / f"{run['run_id']}.json"
    doc = json.loads(path.read_text()); [c.pop("notices") for c in doc["candidates"]]; path.write_text(json.dumps(doc))
    r = client.get(f"/api/runs/{run['run_id']}/notices", params={"candidate": 1})
    assert r.status_code == 200 and r.json()["notices"][-1]["kind"] == "unknown"

def test_startup_summary_reports_park_notices(env):
    from backcountry.api.area_worker import status_summary
    app = create_app(env, web_dist=env.runs_dir / "no-web")
    assert status_summary(env, app.state.areas, app.state.nps)["nps"] == {"configured": False, "cache_kb": 0}
```

The NPS client runs its refresh inline (no thread) when `Settings.area_thread` is False: `create_app` passes `thread=settings.area_thread` to `NpsClient`. `zion_env` sets `area_thread=False`; `test_plan_does_not_wait_for_the_nps_fetch` sets `zion_env.area_thread = True` before building its app so the slow fetch runs on a thread and the plan returns without it. `test_api.py` needs `import time` and `import yaml` for these tests.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_api.py -q -k "notices or nps or startup"`
Expected: FAIL (404 on the new routes, `Settings` has no `nps_key`)

- [ ] **Step 3: Implement**

`RegionStore.on_loaded: callable | None` attribute, called with `loaded` after it is stored (outside the locks). `NpsClient.park_of(loaded)`: if not `loaded.park_mapped`: `loaded.park_code = self.park_for(loaded.region)` (with `index_opener` wrapped in try/except `NoIndex` → None), `loaded.park_mapped = True`; if `configured` and code and `due(code)`: `kick(code)`; return `{"code": code, "configured": self.configured, "data": self.cached(code) if code else None}`. `_plan` and `update_trip` pass `park=app.state.nps.park_of(loaded)`. The notices endpoints as specified; `GET /runs/{id}/notices` validates `candidate` with `_candidate`. `.gitignore` and `.env.example` lines; `status_summary(settings, worker, nps)` adds the `nps` dict (`cache_kb` = size of `nps_dir` in KB); the startup line appends "Park notices: on (cache N KB)." or "Park notices: off (set BACKCOUNTRY_NPS_API_KEY)." (builder's wording).

- [ ] **Step 4: Run the tests, then the suite**

Run: `uv run pytest tests/test_api.py -q && uv run pytest -q -x`
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/api src/backcountry/cli.py .gitignore .env.example data/nps/.gitkeep tests/test_api.py
git commit -m "api: park notices per region and per saved run; areas mapped to NPS units on load; background refresh; key stays in the header"
```

---

### Task 7: Chat: notices in the facts, restatement-only honesty rule, prompt

**Files:**
- Modify: `src/backcountry/chat/facts.py:128-178`, `src/backcountry/chat/grounding.py`, `src/backcountry/chat/proposal.py:51-53`
- Test: `tests/test_chat_facts.py`, `tests/test_chat_grounding.py`, `tests/test_chat_proposal.py`

**Interfaces:**
- Consumes: candidate payload `notices`.
- Produces:
  - Fact lines `Notice ({kind}): {text} [{source name}, {as_of or checked_at}]` for every notice but `unknown`, then the existing "Not checked by this tool" line.
  - `grounding.NEGATION = re.compile(r"\b(no|not|don'?t|doesn'?t|never|without|isn'?t|aren'?t|won'?t|none|unnecessary|free of)\b", re.I)`.
  - `grounding._FACT_NOTICE = re.compile(r"^Notice \((\w+)\): (.*)$", re.M)`.
  - `check_reply`: a clause that hits a banned permit/closure/allowed pattern is excused when it has no `NEGATION` match and `match_items(clause, [(kind, text) for notices of the matching family])` is non-empty, where families are `permit|ticket|camp` for permit words, `closure` for open/closed, `fee` for fee, `rule` for allowed/dogs/pets.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chat_facts.py (append; take the file's existing run fixture and add to candidate 1:
#   "notices": [N.notice("permit", "Permit to hike Angels Landing Trail, 0.4 mi of this trip.", applies_to="trail: Angels Landing Trail",
#                        matched=True, source="OpenStreetMap", as_of="2026-10-26"), N.notice("unknown", N.UNKNOWN_LINE)])
def test_facts_list_notices_with_their_source_and_date(run_with_notices):
    text = fact_sheet(run_with_notices, 1, [], [])
    assert "Notice (permit): Permit to hike Angels Landing Trail, 0.4 mi of this trip. [OpenStreetMap, 2026-10-26]" in text
    assert "Notice (unknown)" not in text
```

```python
# tests/test_chat_grounding.py (append)
FACTS = ("Route: 5.70 mi.\nNotice (permit): Permit to hike Angels Landing Trail, 0.4 mi of this trip. Reservation required. Fee. [OpenStreetMap, 2026-10-26]\n"
         "Notice (closure): Watchman Trail closed: The Watchman Trail is closed pending repairs. [National Park Service, 2026-09-29]\n"
         "Trail names in this region: Angels Landing Trail; Watchman Trail; West Rim Trail")

def test_a_reply_may_restate_a_notice():
    assert check_reply("Angels Landing Trail needs a permit; a reservation is required.", FACTS)[0]
    assert check_reply("The Watchman Trail is closed, per the National Park Service.", FACTS)[0]

def test_negative_and_unsupported_claims_stay_withheld():
    assert not check_reply("You don't need a permit for Angels Landing Trail.", FACTS)[0]
    assert not check_reply("West Rim Trail needs a permit.", FACTS)[0]
    assert not check_reply("Everything is open.", FACTS)[0]
    assert not check_reply("No permit is needed for this trip.", FACTS)[0]
```

```python
# tests/test_chat_proposal.py (append)
def test_prompt_tells_the_model_to_quote_notices_and_never_conclude_from_absence():
    from backcountry.chat.proposal import SYSTEM_PROMPT
    assert "Notice" in SYSTEM_PROMPT and "never conclude" in SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_chat_facts.py tests/test_chat_grounding.py tests/test_chat_proposal.py -q`
Expected: the new tests FAIL

- [ ] **Step 3: Implement**

`facts.fact_sheet`: after the route notes, `lines += [f"Notice ({n['kind']}): {n['text']} [{src}, {date}]" for n in c.get("notices", []) if n["kind"] != "unknown"]`. `grounding.check_reply`: parse `_FACT_NOTICE` into `{kind: [texts]}`; in the banned loop, before appending `why`, compute `family` from `why` ("makes a permit claim" → permit family; "says something is open or closed" → closure; "says something is allowed or not" → rule) and excuse when `not NEGATION.search(clause)` and `match_items(clause, [(k, t) for k in family for t in notices.get(k, [])])`. `proposal.SYSTEM_PROMPT` lines 51 to 53 → "Permits, tickets, fees, closures and rules: only what the Notice lines in the FACTS say, quoted with their source. Never conclude from their absence; if no Notice covers it, say it is unknown. Water, trail conditions, weather and trailhead access are not known to this app. Say they are unknown or not checked. Never say water is there or reliable, and never say a route is safe."

- [ ] **Step 4: Run the tests, the chat suite, and then the whole suite**

Run: `uv run pytest tests/test_chat_facts.py tests/test_chat_grounding.py tests/test_chat_proposal.py tests/test_chat_api.py -q && uv run pytest -q -x`
Expected: pass; the existing `test_banned_claims_are_withheld_unless_said_as_unknown` and `test_positive_permit_legality_and_water_claims_are_withheld` still pass (their facts have no Notice lines)

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/chat tests/test_chat_facts.py tests/test_chat_grounding.py tests/test_chat_proposal.py
git commit -m "chat: notices in the facts; permit and closure claims pass only when they restate a notice"
```

---

### Task 8: CLI: `--avoid-permit` and `backcountry notices`

**Files:**
- Modify: `src/backcountry/cli.py:284-350` (generate), new command `notices`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 5, 6 (`NpsClient`, `Settings.default()`).
- Produces: `generate --avoid-permit` (bool flag → `avoid_permit`); `notices --region <id> [--refresh]`: prints the mapped park (or "No official notice feed for this area."), the checked time, then one line per park-wide notice as `[{kind}] {text} ({source}, {date}) {links}`; `--refresh` fetches when a key is set, else prints the cache and "No BACKCOUNTRY_NPS_API_KEY set; showing the cache." (builder's wording).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py (append)
def test_generate_accepts_avoid_permit(cli_env):
    result = runner.invoke(app, ["generate", "--region", "dolly_sods", "--trailhead", START, "--total", "2-8", "--avoid-permit", "--out-dir", str(cli_env / "out")])
    assert result.exit_code == 0, result.output
    assert json.loads((cli_env / "out" / "search.json").read_text())  # ran
    assert (cli_env / "out" / "candidate_1" / "summary.md").read_text().count("## Before you go") == 1

def test_notices_command_without_a_key_prints_the_no_feed_line(cli_env, monkeypatch):
    monkeypatch.delenv("BACKCOUNTRY_NPS_API_KEY", raising=False)
    result = runner.invoke(app, ["notices", "--region", "dolly_sods"])
    assert result.exit_code == 0 and "No official notice feed for this area." in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -q -k "avoid_permit or notices"`
Expected: FAIL (`No such option: --avoid-permit`, `No such command 'notices'`)

- [ ] **Step 3: Implement**

Add `avoid_permit: bool = typer.Option(False, "--avoid-permit", help="Stay off trails tagged as needing a permit (OpenStreetMap access/foot=permit).")` into the request dict and the CLI's own `request` record. `notices` command builds `Settings.default()` and `NpsClient(settings.nps_dir, settings.nps_key, index_opener=None)`, loads the region with `_load`, maps with `park_for`, prints as specified using `nps_notices(data, [])`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli.py -q`
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add src/backcountry/cli.py tests/test_cli.py
git commit -m "cli: --avoid-permit and a notices command that shows what the server would show"
```

---

### Task 9: Web types, API calls, request form switch

**Files:**
- Modify: `web/src/types.ts`, `web/src/api.ts`, `web/src/request.ts`, `web/src/store.ts`, `web/src/panels/plan.ts:143`
- Test: `web/src/request.test.ts`, `web/src/api.test.ts`, `web/src/panels/plan.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export type NoticeKind = "closure" | "permit" | "ticket" | "camp" | "fee" | "rule" | "caution" | "info" | "unknown";
  export interface Notice { kind: NoticeKind; text: string; applies_to: string | null; matched: boolean;
    source: { name: string | null; ref: string | null; as_of: string | null; checked_at: string | null } | null; links: string[] }
  export interface ParkNotices { configured: boolean; park: { code: string; name: string } | null; pending: boolean; stale: boolean; error: string | null; checked_at: string | null; notices: Notice[] }
  export interface RunNotices { notices: Notice[]; pending: boolean; checked_at: string | null }
  // Candidate.notices?: Notice[]; Run.search.notices_pending?: boolean; GenerateRequest.avoid_permit?: boolean
  export function getRegionNotices(region: string): Promise<ParkNotices>   // GET /api/regions/{id}/notices
  export function getRunNotices(runId: string, n: number): Promise<RunNotices>  // GET /api/runs/{id}/notices?candidate=n
  // FormState.avoidPermit: boolean (default false) ↔ request.avoid_permit; fromRun reads it
  // State.parkNotices: ParkNotices | null (default null); State.noticesNew: string[] (keys "kind|text" new since the saved copy)
  ```
  Plan form: `<label><input type="checkbox" name="avoidPermit"${f.avoidPermit ? " checked" : ""}> Avoid trails that need a permit</label>` after "No roads at all".

- [ ] **Step 1: Write the failing tests**

```ts
// web/src/request.test.ts (append)
it("sends avoid_permit and reads it back from a run", () => {
  const form = { ...emptyForm(), trailhead: "th1", total: "18-26", avoidPermit: true };
  const r = toRequest(form, [{ id: "th1", in_graph: true } as Trailhead]);
  expect(r.ok && r.request.avoid_permit).toBe(true);
  expect(fromRun({ ...sampleRun(), request: { ...sampleRun().request, avoid_permit: true } }).avoidPermit).toBe(true);
});
// web/src/api.test.ts (append)
it("fetches park and run notices from their endpoints", async () => { /* mock fetch; assert URLs /api/regions/zion/notices and /api/runs/r1/notices?candidate=2 */ });
// web/src/panels/plan.test.ts (append)
it("has the avoid-permit switch", () => { expect(renderPlan(stateWith({ form: { ...emptyForm(), avoidPermit: true } }))).toContain('name="avoidPermit" checked'); });
```

Match each file's existing helpers (`emptyForm`, `sampleRun`, `stateWith` or their local equivalents).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/request.test.ts src/api.test.ts src/panels/plan.test.ts`
Expected: FAIL (type errors / missing exports)

- [ ] **Step 3: Implement the types, the two API functions, the form field, the store defaults and the switch**

- [ ] **Step 4: Run the web unit tests and the type check**

Run: `cd web && npx tsc --noEmit && npx vitest run`
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add web/src/types.ts web/src/api.ts web/src/request.ts web/src/store.ts web/src/panels/plan.ts web/src/request.test.ts web/src/api.test.ts web/src/panels/plan.test.ts
git commit -m "web: notice types and endpoints; avoid-permit switch"
```

---

### Task 10: Web: notices rendering, park notices on the plan screen, chips on the compare cards

**Files:**
- Create: `web/src/panels/notices.ts`
- Modify: `web/src/panels/plan.ts:118-155` (park notices line + sheet), `web/src/panels/compare.ts:14-22` (chips), `web/src/app.ts` (`pickRegion`/area open → `getRegionNotices`, polled every 5 s while `pending`, up to 2 minutes), `web/src/store.ts`
- Test: `web/src/panels/notices.test.ts`, `web/src/panels/plan.test.ts`, `web/src/panels/compare.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export function noticeKey(n: Notice): string                       // `${n.kind}|${n.text}`
  export function kindLabel(k: NoticeKind): string                   // "Closed", "Permit", "Ticket", "Camping", "Fee", "Rule", "Caution", "Notice", ""  (builder's wording)
  export function noticesHtml(notices: Notice[], newKeys?: Set<string>): string
  export function chipsHtml(notices: Notice[] | undefined): string    // one chip each for permit, ticket, closure when a *matched* notice of that kind exists
  export function parkLineHtml(p: ParkNotices | null, open: boolean): string
  ```
  `noticesHtml`: `<ul class="notices">` with one `<li class="notice notice-{kind}{ matched ? ' matched' : ''}">` per notice: `<b>{kindLabel}</b> {esc(text)}` then `<span class="note">` with `applies_to` as "Attached to: {esc(applies_to)}" when matched, the source line `{source.name}{, as_of}{, checked hh:mm}`, links as `<a href="${esc(url)}" target="_blank" rel="noopener">link</a>` only when `/^https?:\/\//.test(url)`, and `<span class="new">new since you saved</span>` when `newKeys` has the key. The `unknown` notice renders as a plain `<li class="note">`. `parkLineHtml`: nothing when `p === null` or `p.park === null && !p.pending && p.notices.length === 0`; else `Park notices: {closures} closures, {others} more · National Park Service, checked {hh:mm}` (with "Checking park notices…" while pending, and p.error → "couldn't refresh; last checked …") opening a `<details id="park-notices">` with `noticesHtml(p.notices)`.

- [ ] **Step 1: Write the failing tests**

```ts
// web/src/panels/notices.test.ts
import { describe, expect, it } from "vitest";
import { chipsHtml, noticeKey, noticesHtml, parkLineHtml } from "./notices";
const n = (kind: any, text: string, matched = false, links: string[] = []) => ({ kind, text, applies_to: matched ? "trail: X" : "park", matched, source: { name: "National Park Service", ref: null, as_of: "2026-09-16", checked_at: "2026-11-26T10:04:00Z" }, links });
describe("notices", () => {
  it("escapes text and keeps only http links", () => {
    const html = noticesHtml([n("info", "<img src=x onerror=alert(1)> & more", false, ["javascript:x", "https://www.nps.gov/a"])]);
    expect(html).not.toContain("<img"); expect(html).toContain("&lt;img"); expect(html).toContain('href="https://www.nps.gov/a"'); expect(html).not.toContain("javascript:");
  });
  it("marks new notices and shows attachment and dates", () => {
    const html = noticesHtml([n("closure", "Watchman Trail closed", true)], new Set([noticeKey(n("closure", "Watchman Trail closed", true))]));
    expect(html).toContain("new since you saved"); expect(html).toContain("Attached to: trail: X"); expect(html).toContain("2026-09-16");
  });
  it("chips only for matched permit, ticket and closure", () => {
    expect(chipsHtml([n("permit", "p", true), n("closure", "c", false), n("fee", "f", true)])).toBe('<span class="chip chip-permit">permit</span>');
    expect(chipsHtml(undefined)).toBe("");
  });
  it("park line counts closures and says when it is checking", () => {
    expect(parkLineHtml({ configured: true, park: { code: "zion", name: "Zion National Park" }, pending: true, stale: false, error: null, checked_at: null, notices: [] }, false)).toContain("Checking park notices…");
    const p = { configured: true, park: { code: "zion", name: "Zion National Park" }, pending: false, stale: false, error: null, checked_at: "2026-11-26T10:04:00Z", notices: [n("closure", "a"), n("closure", "b"), n("fee", "c")] };
    expect(parkLineHtml(p, false)).toContain("2 closures, 1 more");
    expect(parkLineHtml(null, false)).toBe("");
  });
});
// web/src/panels/compare.test.ts (append): a card with a matched permit notice contains 'chip-permit'
// web/src/panels/plan.test.ts (append): renderPlan with state.parkNotices set contains "Park notices:"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/panels/notices.test.ts src/panels/compare.test.ts src/panels/plan.test.ts`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `notices.ts`, wire `chipsHtml` into `cardHtml`, `parkLineHtml` into `renderPlan` under the area name, and the fetch/poll in `app.ts` (`parkNotices` reset to null on region change; the poll stops when `pending` is false or after 2 minutes; errors leave `parkNotices` null)**

CSS: `.notices`, `.notice.matched`, `.chip`, `.new` in `web/src/style.css` (or the file the app uses), modest.

- [ ] **Step 4: Run the web unit tests and type check**

Run: `cd web && npx tsc --noEmit && npx vitest run`
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add web/src/panels/notices.ts web/src/panels/notices.test.ts web/src/panels/plan.ts web/src/panels/compare.ts web/src/app.ts web/src/store.ts web/src/panels/plan.test.ts web/src/panels/compare.test.ts web/src/style.css
git commit -m "web: park notices on the plan screen; notice chips on candidates"
```

---

### Task 11: Web: "Before you go" on the trip, refresh for saved trips, pending fill-in, e2e

**Files:**
- Modify: `web/src/panels/trip.ts:139-170`, `web/src/app.ts:209-215` (`openTrip`), `:295-308` (`openSaved`), the plan-result path (`notices_pending`)
- Modify: `web/e2e/*.spec.ts` (the existing plan-to-trip scenario: one assertion)
- Test: `web/src/panels/trip.test.ts`, `web/src/app.test.ts` or the file that tests actions (match the existing pattern)

**Interfaces:**
- Consumes: Task 9, Task 10.
- Produces:
  - Trip view: `<h2>Before you go</h2>` right after the route notes, with `noticesHtml(c.notices ?? [], new Set(s.noticesNew))`; while `run.search.notices_pending` or a refresh is in flight: `<p class="status" id="notices-status">Checking park notices…</p>`; a saved trip offline shows the saved block with its dates and no status line.
  - `actions.refreshNotices()`: when online, `getRunNotices(run.run_id, selected)` → replace `candidate.notices` in `state.run` (and in the saved record via `putTrip` when the trip is saved, then `refreshSaved()`), set `noticesNew` to keys present now and absent from the saved copy, set `status` "Park notices checked hh:mm" (builder's wording). Called from `openTrip` and `openSaved`; while the answer says `pending`, retried every 5 s for up to 2 minutes.
  - `run_id === "partial"` runs (snapshots) are never refreshed.

- [ ] **Step 1: Write the failing tests**

```ts
// web/src/panels/trip.test.ts (append)
it("shows Before you go with the unknown line and marks new notices", () => {
  const s = tripState({ notices: [n("permit", "Permit to hike Angels Landing Trail, 0.4 mi of this trip.", true), n("unknown", UNKNOWN_LINE)] , noticesNew: ["permit|Permit to hike Angels Landing Trail, 0.4 mi of this trip."] });
  const html = renderTrip(s);
  expect(html.indexOf("<h2>Before you go</h2>")).toBeLessThan(html.indexOf("<h2>Days</h2>"));
  expect(html).toContain("new since you saved"); expect(html).toContain(UNKNOWN_LINE);
});
it("says it is checking while notices are pending", () => {
  const s = tripState({ notices: [], pending: true });
  expect(renderTrip(s)).toContain("Checking park notices…");
});
it("notice text is escaped", () => {
  expect(renderTrip(tripState({ notices: [n("info", "<script>x</script>")] }))).not.toContain("<script>");
});
// actions test (match the existing actions test file): openSaved online calls getRunNotices, stores the refreshed run with putTrip, and noticesNew holds the keys that were not in the saved copy; offline it calls nothing.
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/panels/trip.test.ts`
Expected: FAIL

- [ ] **Step 3: Implement the trip block, `refreshNotices`, the calls from `openTrip`/`openSaved`, and the pending poll; add to the e2e plan-to-trip scenario `await expect(page.getByRole("heading", { name: "Before you go" })).toBeVisible()` and `await expect(page.getByText("Park notices from the National Park Service aren't set up on this server.")).toBeVisible()` (the e2e server has no key)**

- [ ] **Step 4: Run web unit tests, type check, build, and the e2e**

Run: `cd web && npx tsc --noEmit && npx vitest run && npm run build && npm run test:e2e`
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add web/src web/e2e
git commit -m "web: Before you go on the trip; saved trips refresh their notices and mark what's new"
```

---

### Task 12: Docs, acceptance on real data, measurements

**Files:**
- Modify: `README.md` (a "Permits, closures and park notices" section after "Any US area"; the operator key setup; the `notices` command), `PROGRESS.md` (new section "Park rules, permits and closures (built 2026-11-26…)"), `docs/ROADMAP.md` (mark built; next piece recreation.gov if gaps remain), `CLAUDE.md` unchanged.
- Test: `scripts/measure-speed` (unchanged; run it)

- [ ] **Step 1: Run the full Python suite and the heavy run from the worktree**

Run: `uv run pytest -q && scripts/heavy-tests --no-eval`
Expected: pass; record the counts

- [ ] **Step 2: Acceptance on real data with the public demo key (30 requests an hour: run once)**

Run, from the worktree with `BACKCOUNTRY_RAW_DIR`, `BACKCOUNTRY_OSM_DIR`, `BACKCOUNTRY_AREAS_DIR` pointing at the main checkout and `BACKCOUNTRY_NPS_API_KEY=DEMO_KEY`, `uv run backcountry serve --port 8011` in the background, then:

```bash
# Old Rag day hike (Shenandoah): open the park, list trailheads, plan from the Old Rag lot
curl -s localhost:8011/api/search?q=Shenandoah%20National%20Park ; curl -s -X POST localhost:8011/api/areas -d '{"ref":"<relation>"}'
curl -s -X POST localhost:8011/api/regions/<shen-area>/generate -d '{"trailhead":"<old rag lot id>","total":"8-10","type":"any","destination":"<Old Rag Mountain ref>"}' | python3 -c 'import json,sys; r=json.load(sys.stdin); [print(n["kind"], "|", n["text"][:120], "|", n["applies_to"]) for n in r["candidates"][0]["notices"]]'
# Angels Landing day hike from the Grotto; then with avoid_permit true
# West Rim one way Lava Point to the Grotto, day hike; then with 1 night
# Dolly Sods 2 nights
```

Expected, per the spec's acceptance checks 1 to 5: the Old Rag block has the ticket alert attached to the destination, the Circuit record, the parking lot alert, the entrance fee and the pets rule; Angels Landing has the OSM permit with the recreation.gov link and the NPS permit alert, and with `avoid_permit` the note says the trail needs a permit; the West Rim day hike has no walking permit and the Lava Point closure attached to the start; with a night at a wilderness site a `camp` notice; Dolly Sods says "No official notice feed for this area." Check 6 (mapping step 3): `POST /api/areas {"box": [...]}` with a small box inside Zion Canyon, then `GET /api/regions/<box-area>/notices` → `park.code == "zion"`. Write the actual output into `PROGRESS.md`, including anything that did not match.

- [ ] **Step 3: Measure**

Run: `scripts/measure-speed` (Zion, Yosemite, Grand Canyon open and plan)
Expected: within noise of PROGRESS.md's last table (open 17/50/95 s; Zion plan 33.6 s, Yosemite 9.8 s); record the numbers.

- [ ] **Step 4: Write the docs, then commit**

```bash
git add README.md PROGRESS.md docs/ROADMAP.md
git commit -m "docs: permits, closures and park notices; acceptance and measurements"
```

- [ ] **Step 5: Final whole-branch review (superpowers:executing-plans), fix Critical/Important with a failing test first, push, open the PR to main**

```bash
git push -u origin permits-closures
gh pr create --base main --title "Park rules, permits and closures: Before you go on every trip" --body "…"
```
