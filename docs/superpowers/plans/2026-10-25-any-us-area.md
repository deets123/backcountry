# Any US Area by Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plan trips in any US park, protected area, named place or map area. Trail data comes from a local, pre-built US OpenStreetMap index, and saved trips move to newer data only when the user accepts the changes.

**Architecture:**
- **Index:** an operator-run CLI step downloads the Geofabrik US extract (checksum verified) and filters it, with pyosmium, into one SQLite index: ways, points, area polygons, and an FTS5 name search.
- **Areas:** an area is cut from the index into the same raw JSON shape that Overpass returns, under `data/raw/<area_id>/extract_<date>.json`. Its generated region file goes in `data/areas/<area_id>.yaml`. Everything downstream (graph, trailheads, planning, export) runs unchanged.
- **API and web:** the API gains search, background area preparation with global limits, and data cuts pinned per run. The web app replaces the region dropdown with a search box and adds the "newer data" offer on saved trips.

**Tech Stack:**
- Python 3.12 (uv), pyosmium (new), stdlib `sqlite3` (FTS5 + R*Tree, confirmed available: SQLite 3.45.1 in the venv), shapely, pyproj, FastAPI, pytest.
- TypeScript (Vite), Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-25-any-us-area-design.md`

## Global Constraints

- CLAUDE.md rules 1–7 hold for every new area:
  - OSM-only geometry, never bridged;
  - "unknown" for unsourced facts;
  - never "safe";
  - provenance per edge and per run;
  - trailheads only from OSM node ids, `verified: false`;
  - ODbL attribution;
  - miles and feet for users, meters in a UTM CRS internally.
- New dependency: `osmium` (pyosmium) only. Explain why in that task's commit message.
- Downloads come only from fixed hosts over HTTPS: `download.geofabrik.de` (OSM extract plus its `.md5`) and the USGS hosts `ingest.py` already uses (TNM API and its `downloadURL`s). User input is never a URL, path, or file.
- User text never reaches a shell, a file path, or an unparameterised SQL string. Area ids match `^[a-z0-9][a-z0-9-]{0,79}$`.
- `regions/` stays hand-curated; generated areas live in `data/areas/` (add to `.gitignore`). `data/osm/` holds the index (git-ignored).
- The UI never mentions downloading, file sizes or mirrors in the normal flow. The one exception is the saved-trip update offer, which states its approximate download size by the user's request. The preparation message is `Finding trails in <name>…`.
- US only. Boxes and points outside every US state boundary in the index are refused.
- Dolly Sods, Pemi, calibration areas, saved and offline trips behave exactly as before.

## Review Focus

1. **Same-named places in different states** ("Bear Creek", "Mill Creek"). Each result must show state and type, and opening one must open that one. Test in Task 1 (`test_search_same_name_two_states`).
2. **A place right on a park edge or outside every protected area** (a trailhead parking lot just outside Zion's boundary). It opens the capped area around the spot, or the park if it is within the padded bbox, never an error. Test in Task 3 (`test_place_outside_any_area_opens_capped_box`).
3. **Two requests for the same new area at once** (two phones, or a double tap). Prepare once; both get the same id and status. Test in Task 5 (`test_same_area_twice_prepares_once`).
4. **Server restart during preparation.** A half-written cut or DEM must never be served. Writes are atomic, and the status after restart is "not prepared". Test in Task 3 (`test_interrupted_cut_leaves_no_cache`).
5. **A saved trip whose route vanished in the new cut** (way deleted or split). The update says plainly that the route can't follow the same trails and keeps the old trip, never bridging. Test in Task 7 (`test_retrace_reports_missing_way`).

---

## File structure

- Create `src/backcountry/usdata.py`: download and verify the Geofabrik extract; the operator commands' logic.
- Create `src/backcountry/osm_index.py`: build the SQLite index from a PBF, plus `search`, `containing_areas`, `cut`.
- Create `src/backcountry/areas.py`: from a search result, box or point to an area spec, then a region file, a raw cut and a DEM; limits and eviction.
- Modify `src/backcountry/region.py`: load from `regions/` then `data/areas/`.
- Modify `src/backcountry/api/store.py`: areas dir, cut pinning, LRU of loaded areas, preparation worker.
- Modify `src/backcountry/api/endpoints.py`: search, areas, status, run update check, replan.
- Modify `src/backcountry/serialize.py`: `data_cut`, route nodes and keys, extract data source.
- Create `src/backcountry/retrace.py`: follow a saved route on a newer cut.
- Modify `src/backcountry/cli.py`: `backcountry usdata download|build|refresh`, `backcountry area prepare`.
- Web: modify `web/src/{api,types,store,app}.ts`, `web/src/panels/{plan,saved,trip}.ts`, `web/src/offline/db.ts`. Create `web/src/panels/search.ts` and `web/src/panels/update.ts`.
- Tests: `tests/test_usdata.py`, `tests/test_osm_index.py`, `tests/test_areas.py`, `tests/test_retrace.py`, `tests/test_api_areas.py`, `tests/test_real_areas.py` (heavy; skipped without the index), `tests/fixtures/mini.osm` (hand-written OSM XML: two "states", a park with a nested wilderness, trails, a trailhead, a peak and a duplicate place name, all with fake ids and coordinates far from any real place in the US.

  Ruling: fixtures use made-up coordinates inside a made-up state polygon, so rule 5 is not touched), web tests next to each module.

---

### Task 1: US OSM index (build, search, containing areas, cut)

**Files:** Create `src/backcountry/osm_index.py`, `tests/fixtures/mini.osm`, `tests/test_osm_index.py`. Modify `pyproject.toml` (add `osmium`).

**Interfaces:**
- Produces:
  - `build_index(pbf: Path, out: Path, extract_date: str) -> dict`: writes the SQLite file atomically (to `out.with_suffix('.tmp')`, then `os.replace`) and returns counts `{"ways", "points", "areas", "names"}`.
  - `open_index(path: Path) -> sqlite3.Connection`: read-only (`mode=ro` URI).
  - `search(con, text: str, limit: int = 10) -> list[dict]`: each result is `{"kind": "area"|"place", "osm": "relation/123", "name", "type_label", "state", "bbox": [w, s, e, n], "center": [lon, lat]}`.
  - `containing_areas(con, lon: float, lat: float) -> list[dict]`: area records holding the point, smallest first, each with `area_km2`, `type_label`, `park: bool`.
  - `cut(con, bbox: tuple[w, s, e, n], extract_date: str) -> dict`: the same top-level shape `ingest.fetch` writes, `{"retrieved_at", "source", "endpoint", "attribution", "osm_base", "bbox", "query", "response": {"elements": [...]}}`.
  - `index_meta(con) -> dict`: `{"extract_date", "built_at", "source_url"}`.
- Element shapes in `cut` must match what `graph.build_graph`, `trailheads.find_candidates`, `ingest.camp_sites`, `ingest.water_features` and `graph.bbox_coverage` read:
  - **Ways:** `{"type": "way", "id", "tags", "nodes", "geometry": [{"lat", "lon"}...], "bounds": {"minlat", "minlon", "maxlat", "maxlon"}, "timestamp", "version"}`.
  - **Nodes:** `{"type": "node", "id", "lat", "lon", "tags", "timestamp", "version"}`.
  - **Boundaries:** an element with `type` way or relation, its `tags`, and `bounds` (no member geometry needed).

**What the index keeps.** Everything `ingest.build_query` takes:
- all `highway` ways (roads are needed for trail–road junction trailheads);
- `highway=trailhead` nodes and `amenity=parking`;
- `tourism=camp_site|camp_pitch`, `natural=spring|water`, `waterway=stream|river`, `amenity=drinking_water`.

On top of that:
- **Areas** (multipolygons assembled with pyosmium's area handler): `boundary=national_park`, `boundary=protected_area`, `leisure=nature_reserve`, `boundary=administrative` with `admin_level=4` (states, used only for the state label and the US check; never search results). Store each area's tags and name, a `type_label`, its WKB polygon, `area_km2` (computed in an equal-area CRS, EPSG:5070), and its bbox.
- **Places** for name search: `natural=peak|saddle|arch|valley|cliff`, `place=locality|hamlet`, `tourism=viewpoint`, and named trail ways (`highway=path|footway` with a `name`).

**Labels and search:**
- `type_label` mapping, in this order:
  - `protect_class=2` or `boundary=national_park` → "National Park";
  - `protection_title` when present → that text;
  - `leisure=nature_reserve` → "Nature Reserve";
  - otherwise "Protected Area".
  - `park` is true for "National Park" and any `protection_title` containing "Park".
- Coordinates are stored as int32 microdegrees, delta-encoded, zlib-compressed blobs. Spatial lookups use an R*Tree per table. Names are in an FTS5 table (`name`, `kind`, `ref`).
- Ranking: exact name match first, then FTS prefix match. Areas come before places, parks before other areas, and larger areas first among equals. The `state` label is the state boundary containing the result's center.

- [ ] **Step 1: Write `tests/fixtures/mini.osm`.** Plain OSM XML with:
  - two state relations, "Statea" and "Stateb";
  - inside Statea, park relation "Testpark National Park" (`boundary=national_park`) with a nested `boundary=protected_area` "Testpark Wilderness";
  - three trail ways with names, forming a loop plus a spur, a `highway=trailhead` node, one `highway=unclassified` road meeting a trail, and a spring;
  - a `natural=peak` "Lone Peak" inside the park;
  - two `place=locality` nodes named "Bear Creek", one in each state.

  Coordinates go in an empty spot such as lon −150…−149, lat 10…11. They are fake and far from any real place in the US.
- [ ] **Step 2: Write failing tests** in `tests/test_osm_index.py`, with a session-scoped fixture that builds the index from `mini.osm` into `tmp_path`:

```python
def test_cut_matches_overpass_shape(index):
    raw = osm_index.cut(index, PARK_BBOX, "2026-10-20")
    tg = build_graph(raw, "EPSG:32605")  # graph builds unchanged
    assert tg.graph.number_of_edges() > 0
    assert raw["source"].startswith("OpenStreetMap via Geofabrik")
    assert raw["attribution"] == ingest.OSM_ATTRIBUTION and raw["osm_base"] == "2026-10-20"
    way = next(e for e in raw["response"]["elements"] if e["type"] == "way")
    assert len(way["geometry"]) == len(way["nodes"]) and {"timestamp", "version", "bounds"} <= way.keys()

def test_cut_keeps_trailhead_and_spring(index): ...  # find_candidates finds the trailhead; water_features finds the spring

def test_search_park_ranks_first(index):
    r = osm_index.search(index, "Testpark")
    assert [x["type_label"] for x in r[:2]] == ["National Park", "Protected Area"] and r[0]["state"] == "Statea"

def test_search_place_and_trail(index):
    assert osm_index.search(index, "Lone Peak")[0]["kind"] == "place"

def test_search_same_name_two_states(index):
    r = osm_index.search(index, "Bear Creek")
    assert sorted(x["state"] for x in r) == ["Statea", "Stateb"]

def test_containing_areas_smallest_first(index):
    names = [a["name"] for a in osm_index.containing_areas(index, *LONE_PEAK)]
    assert names == ["Testpark Wilderness", "Testpark National Park"]  # states excluded

def test_search_text_is_data(index):
    for q in ['" OR 1=1 --', "'; DROP TABLE ways; --", "name:*", "NEAR(a b)", "\x00", "a" * 5000]:
        osm_index.search(index, q)  # no exception, no SQL error
    assert osm_index.search(index, "Testpark")  # table still there
```

- [ ] **Step 3: Run the tests.** `uv run pytest tests/test_osm_index.py -q`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement.**
  - Add `osmium` with `uv add osmium`.
  - Implement `osm_index.py`. Use `osmium.FileProcessor(pbf).with_locations().with_areas()` (or the handler and area-manager equivalents) in one pass.
  - Escape FTS5 input by quoting each token as `"tok"` with inner quotes doubled, and pass it only as a bound parameter. Truncate queries to 100 characters.
- [ ] **Step 5: Run the tests.** `uv run pytest tests/test_osm_index.py -q`. Expected: PASS.
- [ ] **Step 6: Commit.**

```bash
git add pyproject.toml uv.lock src/backcountry/osm_index.py tests/fixtures/mini.osm tests/test_osm_index.py
git commit -m "osm_index: local US OSM index (ways, points, areas, name search) cut into the Overpass raw shape

Adds osmium (pyosmium): the standard reader for OSM PBF files. The US extract is ~11 GB PBF; Overpass
can't serve a country and asks not to be hammered, so areas are cut from a local index instead."
```

### Task 2: Download and verify the Geofabrik extract (operator CLI)

**Files:** Create `src/backcountry/usdata.py` and `tests/test_usdata.py`. Modify `src/backcountry/cli.py` and `.gitignore` (`data/osm/*`, `data/areas/*`, with `.gitkeep`).

**Interfaces:**
- Consumes: `osm_index.build_index`.
- Produces:
  - `GEOFABRIK_US_URL = "https://download.geofabrik.de/north-america/us-latest.osm.pbf"`.
  - `OSM_DIR = REPO_ROOT / "data" / "osm"`. It follows the `BACKCOUNTRY_OSM_DIR` env var the same way `RAW_DIR` follows `BACKCOUNTRY_RAW_DIR`.
  - `download_extract(dest_dir: Path, fetch_bytes=_stream_to, fetch_text=_http_text) -> Path`:
    - streams to `*.part`;
    - checks the MD5 against `<url>.md5`;
    - renames to `us-<YYYY-MM-DD>.osm.pbf`, with the date taken from the HTTP `Last-Modified` header, or from the download date when that header is missing;
    - deletes the part file and raises `FetchError` on mismatch.
  - `current_index(osm_dir: Path = OSM_DIR) -> Path | None`: the newest `us-*.sqlite`.
  - CLI commands:
    - `backcountry usdata download`;
    - `backcountry usdata build [--pbf PATH]`, which writes `us-<date>.sqlite`;
    - `backcountry usdata refresh` (download, then build; old index files are kept, since old cuts are separate files);
    - `backcountry usdata status`.
- The URL is a constant, and no command takes a URL argument.

- [ ] **Step 1: Write the failing tests.**
  - `test_md5_mismatch_discards_file`: a fake fetcher writes bytes and the fake `.md5` text disagrees. Expect `FetchError`, no `.pbf` and no `.part` left behind.
  - `test_md5_match_names_by_last_modified`.
  - `test_download_only_fixed_host`: the fake fetcher records URLs. Assert every URL starts with `https://download.geofabrik.de/`.
  - `test_cli_has_no_url_option`: `--help` for each `usdata` command shows no `url` option.
  - `test_current_index_picks_newest`.
- [ ] **Step 2: Run the tests.** `uv run pytest tests/test_usdata.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement `usdata.py`.** Reuse `ingest.USER_AGENT` and the certifi SSL context. Stream with an 8 MB chunk size, and write a progress line to stderr for the operator only.
- [ ] **Step 4: Run the tests.** `uv run pytest tests/test_usdata.py tests/test_cli.py -q`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `usdata: download the Geofabrik US extract (md5 checked) and build the local index`.

### Task 3: Areas: spec, region file, cut, DEM, size cap

**Files:** Create `src/backcountry/areas.py` and `tests/test_areas.py`. Modify `src/backcountry/region.py` and `tests/test_region.py`.

**Interfaces:**
- Consumes: `osm_index.search/containing_areas/cut/index_meta`, `ingest.fetch_dem`, `ingest.region_raw_dir`, `graph.build_graph`, `elevation.graph_bounds_ll`.
- Produces:
  - `AREAS_DIR = REPO_ROOT / "data" / "areas"`.
  - `MAX_AREA_KM2 = 2500.0`. This is a starting value; Task 9 measures and may change it.
  - `CAPPED_BOX_KM = 24.0`: the side of the capped square.
  - `BBOX_PAD_DEG = 0.01`.
  - `@dataclass(frozen=True) class AreaSpec(id: str, name: str, type_label: str, state: str | None, bbox: BBox, osm: str | None, capped: bool)`.
  - `area_from_result(con, result: dict) -> AreaSpec`:
    - a `kind == "area"` result gives that area;
    - a `kind == "place"` result gives the first of `containing_areas` that is a park, else the smallest under the cap, else a capped box around the point;
    - an area over the cap gives a capped box around the area's center, named after the area, with `capped=True`.
  - `area_from_box(con, bbox: tuple) -> AreaSpec`: refuses a box with no part inside a US state in the index (`ValueError("That area isn't in the US.")`); a box larger than the cap is cut down to the capped box around its center. It is named `Area near <nearest place name>`, or `Map area` when there are no places.
  - `area_from_point(con, lon, lat) -> AreaSpec`: the same rules as a place.
  - `area_id(spec_name: str, osm: str | None, bbox: BBox) -> str`: a slug of the name plus `-r123` / `-w123`. For boxes, add a short hash of the rounded bbox. Must match `^[a-z0-9][a-z0-9-]{0,79}$`.
  - `utm_crs(lon: float, lat: float) -> str`: `EPSG:326{zone:02d}`, with zone `int((lon + 180) // 6) + 1`.
  - `write_area_region(spec: AreaSpec, areas_dir: Path = AREAS_DIR) -> Path`: YAML that `load_region` accepts, with `trailheads: []` and the `boundary_name_regex` set to the exact name escaped with `re.escape`. Plus `area: {type_label, state, osm, capped}`, `generated: true`.
  - `prepare_area(spec, con, raw_dir=None, areas_dir=None, dem_fetch=ingest.fetch_dem) -> dict`:
    - writes the region file;
    - writes the cut as `extract_<date>.json` atomically into the area's raw dir;
    - builds the graph;
    - fetches the DEM for `graph_bounds_ll`;
    - returns `{"area_id", "cut": extract_date, "edges": n, "seconds": {...}}`.
    - Raises `ValueError("No trails were found there.")` when the cut has no routable edges.
- `region.py`:
  - `load_region(region_id, regions_dir=None, areas_dir=None)` looks in `regions_dir` first, then `areas_dir` (default `AREAS_DIR`).
  - `Region` gains `area: dict | None = None` and `generated: bool = False`.
  - `ingest.latest_cache` stays as is for prefix `overpass`. Add `CUT_PREFIX = "extract"`.
  - `ingest.load_raw(region, raw_dir, cut: str | None = None)` reads `overpass_*` for hand-made regions and `extract_*` for generated ones. With `cut`, it reads `extract_<cut-compact>*.json`; without, the newest.
  - `doc["cut"]` is set to the extract date (or `None` for Overpass caches).

- [ ] **Step 1: Write the failing tests** in `tests/test_areas.py`, using the Task 1 index fixture (move it to `tests/conftest.py`) and a fake `dem_fetch`:
  - `test_place_opens_park_not_wilderness`: "Lone Peak" opens Testpark National Park.
  - `test_park_search_opens_whole_park`: the bbox covers the park polygon's bounds.
  - `test_oversized_area_is_capped`: monkeypatch `MAX_AREA_KM2` small. The result has `capped` set, a bbox of about `CAPPED_BOX_KM`, and the area's name.
  - `test_box_outside_us_refused`.
  - `test_place_outside_any_area_opens_capped_box`: a locality outside the park.
  - `test_area_id_safe`: for names with `../`, quotes, unicode and 300 characters, the id matches the regex.
  - `test_prepare_area_writes_loadable_region`: `load_region(id, areas_dir=...)` works; `ingest.load_raw(region, raw_dir)` returns the cut with `cut == "2026-10-20"`; the trailhead endpoint's `find_candidates` returns the fixture trailhead as `osm_trailhead`, unverified.
  - `test_interrupted_cut_leaves_no_cache`: make the fake `dem_fetch` raise. No `extract_*.json` remains, and loading raises `FileNotFoundError`. To get this, write the cut last, after the DEM succeeds.
  - `test_utm_zone`: `utm_crs(-113.0, 37.3) == "EPSG:32612"` (Zion) and `utm_crs(-79.3, 39.0) == "EPSG:32617"` (Dolly Sods).
  - In `tests/test_region.py`: `test_curated_region_wins_over_area_with_same_id`.
- [ ] **Step 2: Run the tests.** `uv run pytest tests/test_areas.py tests/test_region.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `areas.py`, plus the region and ingest changes above.
- [ ] **Step 4: Run the tests.** `uv run pytest tests/test_areas.py tests/test_region.py tests/test_ingest.py -q`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `areas: any US area from a search result, box or point, cut from the local index`.

### Task 4: Cuts pinned per run, and the region store for open-ended areas

**Files:** Modify `src/backcountry/api/store.py`, `src/backcountry/serialize.py`, `src/backcountry/api/endpoints.py` (difficulty and chat use the run's cut), `tests/test_api.py` and `tests/test_serialize.py`.

**Interfaces:**
- Consumes: `ingest.load_raw(..., cut=)`, `region.load_region(..., areas_dir=)`.
- Produces:
  - `Settings` gains `areas_dir: Path | None`, `osm_dir: Path | None` and `max_loaded: int = 4`.
  - `RegionStore.get(region_id: str, cut: str | None = None) -> LoadedRegion`: cached per `(region_id, cut)`, least-recently-used eviction past `max_loaded`, with `cut=None` meaning newest. `LoadedRegion` gains `cut: str | None`.
  - `RegionStore.summaries()` lists hand-made regions only (calibration areas are not in `regions/`, so they stay out), plus `recent_areas()`.
  - `RegionStore.recent_areas(limit=8) -> list[dict]`: generated areas by the region file's mtime, which `get` touches.
  - Run payload gains:
    - `"data_cut": raw.get("cut")`;
    - `"route": {"nodes": [...], "keys": [...]}` per candidate (from `c.route.nodes` and the edge `key`s);
    - for generated areas, a first data source of `{"name": "OpenStreetMap via Geofabrik US extract", "retrieved_at": <extract date>, "attribution": OSM_ATTRIBUTION}`.
  - The `RunStore` index records `cut`.
  - `/api/difficulty` takes an optional `cut` in the body; the web app sends `run.data_cut`. `/api/chat` loads `get(run["region"], run.get("data_cut"))`.

- [ ] **Step 1: Write the failing tests.**
  - `test_run_records_cut_and_route_nodes`.
  - `test_old_cut_still_loads_after_new_cut`: write two cuts; `get(id, old)` and `get(id)` differ in `cut`.
  - `test_store_evicts_least_recently_used`: with `max_loaded=1`, `LoadedRegion.builds` increments on a switch back.
  - `test_regions_list_excludes_generated_areas`, and `recent_areas` lists them.
  - `test_chat_uses_runs_cut`.
  - `test_hand_made_runs_unchanged`: an existing Dolly Sods-style fixture run has `data_cut is None`, and `data_sources[0]["name"] == "OpenStreetMap via Overpass API"`.
- [ ] **Step 2: Run the tests.** `uv run pytest tests/test_api.py tests/test_serialize.py -q`. Expected: FAIL on the new tests.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.** `uv run pytest -q`. Expected: PASS, with no change in existing results.
- [ ] **Step 5: Commit.** Message: `api: runs pin their data cut; region store holds open-ended areas (LRU)`.

### Task 5: Search and background preparation API, with limits

**Files:** Modify `src/backcountry/api/endpoints.py`, `src/backcountry/api/store.py` and `src/backcountry/api/app.py`. Create `tests/test_api_areas.py`.

**Interfaces:**
- Consumes: Task 1 `search`, Task 3 `area_from_*` and `prepare_area`, Task 4 store.
- Produces:
  - **`GET /api/search?q=<text>`** returns `{"results": [...]}`:
    - hand-made regions whose `name` contains the text come first, as `{"kind": "region", "id", "name", "type_label": "Region"}` (spec: existing regions win);
    - then index results. Each carries a `ref` token, the result's `osm` string, which the server re-looks up; the client never sends a bbox for a named result.
    - `q` is 1–100 characters, otherwise 400.
    - With no index built, the response is `{"results": [...regions only], "index": false}`.
  - **`POST /api/areas`**, body exactly one of `{"ref": "relation/123"}`, `{"box": [w, s, e, n]}` or `{"point": [lon, lat]}`. Returns `{"area_id", "name", "status": "ready"|"preparing"|"queued", "message": "Finding trails in <name>…"}`.
    - Numbers are validated: finite, lon −180…180, lat −90…90.
    - A `ref` must match `^(node|way|relation)/\d{1,12}$` and be present in the index.
  - **`GET /api/areas/{area_id}/status`** returns `{"status": "ready"|"preparing"|"queued"|"failed"|"unknown", "message"}`. A failure message is plain, for example "No trails were found there." or "Couldn't get elevation for this area; try again later." It never carries a path or stack trace.
  - **`AreaWorker`** in `store.py`:
    - one background thread with a queue; one preparation at a time;
    - dedupes by `area_id`: a second request for a queued or preparing area returns the same status;
    - `MAX_QUEUE = 3`; when full, the response is 429 "The server is busy setting up other areas; try again in a few minutes."
    - `NEW_AREAS_PER_HOUR = 6`, a global sliding window over newly prepared ids, also 429.
    - The status of an area id lives in memory. After a restart, an unfinished area reads as `unknown`, so the client re-requests it.
  - **`AREA_DISK_BUDGET_GB = 40.0`** (raw dir areas, DEM included). Before a preparation, while the total is over budget, the worker deletes the least-recently-used generated area that no saved run references (per the `RunStore` index), meaning its raw dir and its region file. If it is still over budget, the response is 507 "The server is out of room for new areas right now." Hand-made regions are never touched.
  - Settings get `area_limits: dict | None` so tests can use small numbers. Values are read from env `BACKCOUNTRY_AREA_*` in `Settings.default()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_api_areas.py`, with `TestClient`, the fixture index, and a fake `dem_fetch`:
  - `test_search_regions_first`.
  - `test_search_validates_q`: empty, 101 characters → 400.
  - `test_search_without_index`.
  - `test_prepare_then_ready_then_generate`: POST `{"ref": park}`, poll status to `ready`, then `GET /trailheads` and `POST /generate` work, with miles in the output.
  - `test_same_area_twice_prepares_once`: two POSTs before the worker runs → one preparation (count calls on the fake).
  - `test_bad_inputs_refused`: for `{"ref": "../etc/passwd"}`, `{"ref": "http://evil/x"}`, `{"box": [0, 0, "1", 1]}`, `{"box": [nan...]}`, `{"point": [200, 0]}`, a box outside the US, `{}` and two keys → 400 each, and the fake fetchers recorded no URLs.
  - `test_rate_limit_and_queue`: with small limits → 429 messages.
  - `test_disk_budget_evicts_unreferenced_only`: an area with a saved run survives; an unreferenced one is removed; when everything is referenced → 507.
  - `test_status_unknown_id_is_unknown_not_error`, and `test_status_id_validated`: `../x` → 400.
  - `test_failure_message_has_no_paths`.
- [ ] **Step 2: Run the tests.** `uv run pytest tests/test_api_areas.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** `uv run pytest tests/test_api_areas.py tests/test_api.py -q`. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `api: search any US area and prepare it in the background, with global limits and a disk budget`.

### Task 6: Web search box, preparing state, map-area planning

**Files:** Create `web/src/panels/search.ts` and `web/src/panels/search.test.ts`. Modify `web/src/{api,types,store,app}.ts`, `web/src/panels/plan.ts` and their tests, and `web/e2e/smoke.spec.ts`.

**Interfaces:**
- Consumes: Task 5 endpoints.
- Produces:
  - In `api.ts`: `search(q) -> SearchResult[]`, `openArea(body) -> AreaStatus`, `areaStatus(id) -> AreaStatus`.
  - State gains `areaName: string | null` and `recentAreas: {id, name, type_label}[]`, persisted like `region`.
  - Actions:
    - `searchAreas(q)`, debounced 250 ms;
    - `openResult(r)`: a region calls `pickRegion`; anything else POSTs `/api/areas`, shows the status `Finding trails in <name>…`, polls status every 2 s up to 10 minutes, then calls `pickRegion(area_id)`;
    - `planHere()`: POSTs `{box}` from the current map view bounds.
  - **The plan panel:**
    - The Region `<select>` is replaced by a search input with a results list. Each row shows the name, plus `type_label` and `state` on a second line.
    - Recent areas show when the input is empty.
    - A "Plan in this map area" button.
    - The trailhead list and map tap keep working as today.
  - `pickRegion` must accept an id not in `s.regions` (generated areas): it fetches the region's bbox from the status or recent entry. `/api/regions/{id}/trailheads` and `/trails` already work for any loaded id.
  - **Copy:** no text containing "download", "MB", "GB" or "Geofabrik" in these panels. A Vitest asserts this over rendered HTML.

- [ ] **Step 1: Write failing Vitest tests.**
  - `search.test.ts`: renders results with type and state; an empty input shows recents; the copy check.
  - `app.test.ts`:
    - `openResult` for a region calls `pickRegion` with no POST;
    - for an area, it polls until ready and then loads trailheads;
    - a failed status shows its message as an error;
    - a 429 shows the server's message.
- [ ] **Step 2: Run them.** `cd web && npx vitest run`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** `cd web && npx vitest run && npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Update the e2e test.** In `smoke.spec.ts`, the stubbed API now has `/api/search`. Type "Dolly", pick the result, and the plan form shows the trailheads. Run `cd web && npx playwright test`. Expected: PASS.
- [ ] **Step 6: Commit.** Message: `web: search any US area; quiet preparing state; plan in this map area`.

### Task 7: Saved trips move to newer data only with the user's say-so

**Files:**
- Create `src/backcountry/retrace.py`, `tests/test_retrace.py`, `web/src/panels/update.ts` and `web/src/panels/update.test.ts`.
- Modify `src/backcountry/api/endpoints.py`, `web/src/offline/db.ts`, `web/src/panels/trip.ts` and `web/src/panels/saved.ts`, plus their tests.

**Interfaces:**
- Consumes: Task 4 `route.nodes/keys`, `data_cut`, `RegionStore.get(id, cut)`. Also `route.walk`, `route.add_elevation`, the planning `split` path (as used for `/api/difficulty`), and `export.diff_markdown`-style change lines.
- Produces:
  - `retrace(tg_new, saved_candidate: dict) -> dict`: returns `{"ok": True, "route": Route}` or `{"ok": False, "missing": [{"source": "osm:way/1", "name": "..."}]}`.
    - For each consecutive saved node pair with its saved key's way id, find a path in `tg_new` between the two nodes using only edges whose `way_id` equals that way. This handles a way split by a new junction.
    - If either node is missing, or there is no such path, record the way as missing.
    - Never route around a gap (rule 1).
    - A saved candidate without `route` (a run from before this piece) returns `{"ok": False, "missing": [], "reason": "saved before routes were recorded"}`.
  - **`GET /api/runs/{run_id}/update?candidate=n`** returns `{"newer": bool, "cut": old, "latest": new, "approx_bytes": int}`.
    - `newer` is true when the region's newest cut (or, for hand-made regions, the newest Overpass cache `retrieved_at`) is later than the run's.
    - `approx_bytes` is the trip's stored offline payload size plus its tile count × the average tile size already recorded by `offline/tiles.ts`. The server returns the data part; the web app adds its tile estimate.
  - **`POST /api/runs/{run_id}/update`**, body `{"candidate": n, "camps_m": [...] | null}`:
    - Retraces on the newest cut, re-elevates, and re-splits with the trip's camps kept where they still fit; otherwise it re-splits with the same request.
    - Returns `{"ok": true, "run": <new run payload with one candidate, data_cut=new>, "changes": [str...]}` or `{"ok": false, "message": "The route no longer follows the same trails in the newer data (missing: <trail names>). Your trip is unchanged."}`.
    - `changes` lines cover total miles, climbing, each day's level and miles, camps moved, water points added or removed along the route, and trails renamed. When nothing changed, `["No changes to this trip."]`.
    - The new run is saved; the old one is not modified.
  - **Web:**
    - When the trip view or saved list opens online, it calls the check.
    - If `newer` is set and the trip's `declinedCut !== latest`, it shows a banner: `Newer trail data for this area. Updating uses about <N> MB.` with buttons **Review changes** and **Not now**.
    - **Review changes** POSTs the update and lists the `changes`. **Accept** replaces the saved trip's run and offline copy (re-save tiles and data as `offline/save.ts` does). **Keep my trip** stores `declinedCut = latest` on the saved record in IndexedDB.
    - A failure message shows with the trip unchanged.
    - Nothing calls the update POST without a tap.

- [ ] **Step 1: Write the failing Python tests** in `tests/test_retrace.py`:
  - `test_retrace_same_cut_identical`: the same length and sources.
  - `test_retrace_way_split_by_new_junction`: build two fixture raw docs that differ by an added side trail splitting a way. Retrace succeeds with the same length.
  - `test_retrace_reports_missing_way`: the way is deleted in the new cut. `ok` is false, the way is named in `missing`, and the result has no route.
  - `test_retrace_old_run_without_route`.

  In `tests/test_api_areas.py`:
  - `test_update_check_newer_only_after_new_cut`.
  - `test_update_returns_changes_and_saves_new_run`.
  - `test_update_never_runs_on_get`: the GET doesn't create a run.
  - `test_update_missing_way_message`.
- [ ] **Step 2: Run them.** `uv run pytest tests/test_retrace.py tests/test_api_areas.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `retrace.py` and the endpoints.
- [ ] **Step 4: Run them.** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Write failing Vitest tests** in `update.test.ts` and the `trip.test.ts`/`saved.test.ts` additions:
  - the banner shows only when `newer` is set and the cut isn't declined;
  - **Not now** stores `declinedCut`;
  - **Accept** replaces the saved run and calls the save path;
  - the failure message keeps the trip;
  - no POST happens without a click;
  - offline, there is no check call and no banner.
- [ ] **Step 6: Run them.** `cd web && npx vitest run`. Expected: FAIL. Implement, then run `npx vitest run && npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 7: Commit.** Message: `saved trips: offer newer trail data, show the changes, apply only when accepted`.

### Task 8: Security hardening sweep

**Files:** Create `tests/test_security_areas.py`. Modify whatever these tests expose.

**Interfaces:**
- Consumes: everything above. Produces nothing new; this task is the acceptance check 5 gate.

- [ ] **Step 1: Write the tests.**
  - `test_no_user_controlled_fetch_hosts`: monkeypatch `urllib.request.urlopen` and the rasterio-opened URLs (patch `ingest._read_merged` and `ingest.tnm_search` to record their inputs). Exercise search, prepare and update with hostile strings. Every recorded URL's host must be in `{"download.geofabrik.de", "tnmaccess.nationalmap.gov"}` plus the `downloadURL` hosts USGS returns (`prd-tnm.s3.amazonaws.com`, as the existing DEM code sees them). Assert the TNM item URLs are https and on the USGS-listed host set before opening.
  - `test_area_ids_never_escape_dirs`: for each hostile name, every path written stays under `areas_dir` or `raw_dir`, checked by resolving each path and testing `is_relative_to`.
  - `test_index_opened_read_only_by_api`: opening the index with the API's connection and attempting an `INSERT` raises.
  - `test_downloaded_files_not_executable`: the mode on the written PBF, index and cut files has no executable bit.
  - `test_flood_of_boxes_capped`: 50 distinct boxes give at most `NEW_AREAS_PER_HOUR` preparations; the rest get 429.
- [ ] **Step 2: Run them.** `uv run pytest tests/test_security_areas.py -q`. Fix whatever fails at its source. The DEM host allowlist goes into `ingest.fetch_dem` as `ALLOWED_DEM_HOSTS`.
- [ ] **Step 3: Run the full suite.** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 4: Commit.** Message: `areas: security tests: fixed download hosts, safe ids, read-only index, flood caps`.

### Task 9: Real data, measurements, docs

**Files:** Create `tests/test_real_areas.py` (heavy; skipped unless `current_index()` exists). Modify `PROGRESS.md`, `README.md`, `docs/ROADMAP.md` and `scripts/heavy-tests` (pass `BACKCOUNTRY_OSM_DIR` through as for the raw dir).

- [ ] **Step 1: Operator run on the laptop.** `uv run backcountry usdata refresh`. Record the download size, build time, index size and peak memory (`/usr/bin/time -l`). If the build doesn't fit in 16 GB of RAM, use pyosmium's disk-backed `sparse_file_array` location index and say so in PROGRESS.
- [ ] **Step 2: Write the heavy tests.**
  - `test_zion_whole_park`: search "Zion" → the first result is Zion National Park. Prepare it. Trailheads include candidates near the West Rim and the Grotto / Angels Landing area, matched by OSM names containing "West Rim" and "Angels Landing" in the trail edges. A plan that `must` include the Angels Landing trail from a West Rim-side start returns a candidate.
  - `test_zion_reference_gpx_info`: rate the local Zion GPX (`tests/reference/local/zion_west_rim_top_down.gpx`, skipped if missing) on the whole-park area and print the level next to calibration's. Assert nothing; the spec makes this information only.
  - `test_chesler_park`: search "Chesler Park" → opens the Canyonlands / Needles area. A loop from a Needles trailhead passes through edges named "Chesler Park".
  - `test_new_area_timing`: prepare "Linville Gorge" and record the first and second open times in the test output.
  - `test_osm_lengths_consistent`: the edge lengths in the cut equal the source way lengths computed in UTM, within 0.1%.
- [ ] **Step 3: Run them.** `uv run pytest tests/test_real_areas.py -q -s`, with the measured times copied into PROGRESS. Set `MAX_AREA_KM2` from the measurements so the first preparation stays under about a minute on the laptop, and note the value and why.
- [ ] **Step 4: Update the docs.**
  - **README:** a section "Any US area" covering the operator setup commands, the disk use and the refresh.
  - **PROGRESS:** what was done, the measurements, known gaps, and the deferred items (park rules and permits are next).
  - **ROADMAP:** mark this piece done.
- [ ] **Step 5: Run the full suite and the chat eval.** `uv run pytest -q`, `cd web && npx vitest run && npx playwright test`, then `scripts/heavy-tests`. Expected: green, chat eval no worse than 36/41.
- [ ] **Step 6: Commit.** Message: `any US area: real-data checks (Zion, Chesler Park, Linville Gorge), measurements, docs`.
