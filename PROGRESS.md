# Progress

## M0 — Project setup (done 2026-09-29)

**Done**
- `uv` project (`pyproject.toml`, Python 3.12 via `.python-version`, `requires-python >=3.11`),
  hatchling build, `backcountry` console script → `backcountry.cli:app`.
- Package skeleton per CLAUDE.md under `src/backcountry/`. Modules for later milestones
  (`elevation`, `loops`, `split`, `scoring`) are docstring-only stubs.
- Added `region.py` (not in the CLAUDE.md layout) to load/validate `regions/*.yaml`.
  It rejects inverted bboxes and any trailhead entry with coordinates that isn't `verified: true`.
- `regions/dolly_sods.yaml`: roadmap starting bbox (38.94–39.10, -79.42 to -79.26),
  `EPSG:32617`, empty `trailheads:`.
- `.gitignore` covers `data/raw`, `data/processed`, `out/`.
- Moved `ROADMAP.md` from the repo root to `docs/ROADMAP.md` to match CLAUDE.md.

**Acceptance:** `uv run pytest` → 5 passed. `uv run backcountry --help` lists `ingest`, `qa`, `trailheads`.

**Dependencies added:** folium, networkx, pyproj, shapely, typer (all in the CLAUDE.md stack),
plus `pyyaml` (not in the stack) to read `regions/*.yaml`. osmnx, geopandas, py3dep, gpxpy
are deferred until a milestone needs them.

**Known wrong / open questions**
- `docs/vision.md` doesn't exist yet. I haven't created it; that's your call.

## M1 — Ingest + graph + QA report (built 2026-09-29; awaiting your CalTopo review)

**Done**
- `backcountry ingest --region dolly_sods [--refresh]`: one Overpass query (all `highway=*` ways,
  `highway=trailhead` nodes, `amenity=parking`, and the boundary matched by `boundary_name_regex`).
  The raw JSON is cached at `data/raw/dolly_sods/overpass_<UTC stamp>.json` with `retrieved_at`,
  `osm_base`, the query, and ODbL attribution. No re-query unless you pass `--refresh`.
- `graph.py`: undirected `networkx.MultiGraph`. Each edge is a contiguous run of one OSM way's nodes
  between graph nodes, with `source=osm:way/<id>`, `retrieved_at`, `osm_nodes`, `length_m`
  (EPSG:32617), WGS84 + UTM geometry, kept tags, and `is_road`. Graph nodes are way endpoints,
  junctions, and nodes shared with non-routable highways (so road crossings are startable nodes).
- `backcountry qa --region dolly_sods` writes `out/qa/dolly_sods/{qa_report.md, qa_report.json, qa_map.html}`
  and `data/processed/dolly_sods/{edges,nodes}.geojson`. The report covers counts, miles by type,
  bbox coverage, components (with the gap to C0), a trail-only breakdown of C0, named ways by component,
  possible missing junctions, dead ends with notes, short edges, non-routable roads touching the network,
  excluded ways, and access overrides.
- `backcountry trailheads --region dolly_sods [--all]`: candidates from `highway=trailhead` nodes,
  parking within 656 ft of a trail node, and trail/road junctions. IDs are graph node IDs (OSM node IDs).
- 63 tests: synthetic-fixture invariants (provenance, geometry is a slice of the source way,
  way lengths preserved, access exclusion, splitting, self-loops, QA cases, trailheads, CLI, export)
  plus the same invariants on the real cached data.

**Acceptance output (data retrieved 2026-09-29T23:16:53Z)**
- 565 nodes, 628 edges, 51 components; 198.19 mi total (146.44 trail, 51.75 road).
- **C0 main: 166.42 mi** (125.85 trail, 40.58 road). Every named Dolly Sods trail in the data is in C0.
  The other 50 components are small (≤ 7.3 mi). Most are private/service-road fragments east of FR 75,
  plus North Fork Mountain Trail (C2) and an unnamed county track (C1, `ref=CR 28/2`).
- Without roads, C0's core wilderness trail system is one 91.2 mi piece (T0).
- OSM wilderness boundary (osm:relation/14568112) is inside the bbox (margins 0.76–2.18 mi). bbox unchanged.
- 200 dead ends (78 are trail ends in C0), 11 possible missing junctions, 53 short edges,
  151 excluded ways (150 `access=private`, mostly driveways), 2 foot-overrides-access ways.
- 9 OSM trailheads, all snapped at 0 ft (they sit on the network) and all in C0. 49 C0 candidates total.
- **Still open:** the "Done when" review against CalTopo is yours to do.

**Decisions that differ from the literal roadmap/stack (say if you want them reversed)**
1. **No osmnx.** Fetching is a plain Overpass POST (stdlib `urllib`), and the graph builder is ~60 lines.
   Reasons: one-way-per-edge provenance (osmnx simplification merges ways into list-valued edges),
   our own raw cache with `retrieved_at`, and an explicit excluded list instead of silent filtering.
2. **Added `certifi`** (new dependency). The python.org macOS build has no CA bundle, so urllib's TLS failed.
3. **Access:** also exclude `foot=private`. A specific `foot=yes|designated|permissive` overrides
   `access=no|private` (OSM semantics). Both overrides are listed in the report.
4. **Graph nodes include crossings with non-routable roads** (e.g. tertiary), so they can be trailheads.
5. **No clipping.** Ways crossing the bbox keep their full geometry (e.g. Cabin Mount Road runs north
   to ~39.18). Clipping would invent endpoints.
6. Added modules `region.py` and `trailheads.py` (not in the CLAUDE.md layout).

**Known wrong / caveats**
- `highway=track` counts as trail (per roadmap), so Roaring Plains Road, FR 479, Old Craig Road,
  Dayton Carr, Barger Hollow, and Pipeline Access Road (`access=destination`) count as trail miles.
  Their tags don't say whether they're open to vehicles or what shape they're in. 29.9 mi of track total.
- `tertiary` isn't routable, so parts of Dolly Sods Road (FR 75), Laneville Road, Jordan Run Road,
  and Timberline Road aren't in the graph. C0 is still connected through the unclassified parts.
  Dolly Lane (C7) sits 27 ft off an unclassified Dolly Sods Road segment.
- The bbox takes in trails west of the wilderness boundary (around Timberline Drive, Freeland Road,
  Blackwater View, Beall). They join C0 by roads, which inflates C0 and the trailhead list.
- The near-miss rule (dead end within 82 ft of an edge not reachable within 656 ft) is a heuristic.
- A parking-based candidate snaps to the nearest trail node, which may not be where the trail
  actually leaves the lot.

**Open questions** (answered in the USFS cross-check section below)
1. Should `highway=track` be `is_road=true` (penalized) instead of trail?
2. Should `tertiary` forest roads (FR 75's tertiary sections) be routable as `is_road`?
3. Trim the bbox's west edge to drop Canaan Valley, or keep it?
4. Which trailhead candidates do you want added to `regions/dolly_sods.yaml` as `verified: true`?

## M1 addendum — independent check against USFS data (2026-09-29)

CalTopo's default MapBuilder Topo layer takes its trails from OpenStreetMap (its last OSM import
was 2026-01-17), so matching it mostly re-checks OSM. The independent source is the USFS
Enterprise Data Warehouse: official NFS trails, roads (with maintenance level and a
"closed to motorized" layer), recreation sites, and wilderness boundaries. These are public domain.

**Done**
- `backcountry verify --region dolly_sods [--refresh]`: 5 USFS layer queries, cached at
  `data/raw/dolly_sods/usfs_<stamp>.json`. It writes `out/qa/dolly_sods/verify_report.{md,json}` and adds
  USFS layers to `qa_map.html`: official trails drawn over the OSM lines, trailhead badges, roads,
  wilderness boundary, and gaps. `qa` also adds the overlay whenever a USFS cache exists.
  USFS geometry is never routed on.
- 12 trailheads added to `regions/dolly_sods.yaml`, each pointing at a graph node (an OSM node id, so no
  hardcoded coordinates) and noting its USFS match. All are `verified: false` until you check them.
- Legend rebuilt: collapsible, grouped sections, real line/marker swatches, 13px text.
  The dead-end layer is now off by default.
- `region.py` validates trailhead entries (an integer `node`, or `lat`/`lon` only with `verified: true`;
  ids must be unique).

**Results (OSM retrieved 2026-09-29T23:16:53Z, USFS 2026-09-29T23:55:40Z)**
- USFS lists 22 official trails with 53.05 mi inside the bbox. Routable OSM lines are within 100 ft of
  **99.2%** of that mileage, within 250 ft of 99.9%. **0 gaps.** Every trail's median offset is 8–26 ft.
- 12 USFS trailheads, all within 141 ft of a graph node in C0. 8 match an OSM `highway=trailhead`.
- **Disagreement:** OSM puts Wilderness Way Trailhead (osm:node/402290253) 1.1 mi from USFS site 90570.
  The yaml entry uses the node nearest the USFS site and carries a warning note.
- 10 OSM trail groups (5.9 mi) lie mostly inside the USFS wilderness boundary with no USFS record:
  Old Craig Road (track, 1.84 mi), Alder Run, Pine Plantation, Screwdriver, Mine, 19 View, and
  pieces of Dobbin Slashings / Raven Ridge / Dobbin Grade / Little Stonecoal. Worth your review.
- Tracks per USFS: Roaring Plains Road = FR 70 and an unnamed track = FR 776, both open roads with
  maintenance level 2 (high-clearance vehicles). FR 479 and FR 782 are in the USFS "closed to motorized
  use" layer. The rest have no USFS record. None of the tracks is a USFS trail.
- USFS also shows part of Roaring Plains West Wilderness (boundary "Provisional") inside the bbox.

**Decisions**
- bbox: kept. It covers the whole wilderness, all 22 USFS trails, and all 12 USFS trailheads. The extra
  western trails are real and only connect to C0 by road.
- `tertiary`: recommend leaving it out. It's 16.4 mi of county roads (Jordan Run, Laneville, Timberline),
  of which only 0.12 mi is Dolly Sods Road. Adding it would merge 15 small components (mostly driveways)
  and add 23 mi of road to C0.

**Open**
- Tracks → `is_road`? (Recommended, since none are USFS trails. Waiting on your call.)
- Wilderness Way location, and the 5.9 mi of OSM-only wilderness trail: your call after checking.
- Flip `verified: true` on the trailheads you've checked.
