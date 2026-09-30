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

## M1 follow-up — your decisions applied (2026-09-29)

**Decisions (from you)**
- Tracks are roads (`is_road`), usable when needed.
- `tertiary` is in the graph as a road flagged `last_resort=true`; M4 scoring will only use it when
  nothing else works. It's drawn dotted on the map.
- OSM is the source of truth for trails, including the 5.9 mi inside the wilderness that USFS doesn't
  list. USFS stays a cross-check. OSM already contains every USFS trail (0 gaps), so merging USFS
  geometry would add nothing.

**Fix**
- The map no longer requests OSM's volunteer tile servers. They blocked the page opened from disk
  ("Access blocked"), and CLAUDE.md rule 6 says to respect tile terms. Base maps are USGS Topo
  (default) and USGS Imagery + Topo, both public domain. The credit line keeps the OSM data attribution.

**New numbers (same OSM snapshot)**
- 580 nodes, 661 edges, 36 components (was 51); 214.6 mi total (116.6 trail, 98.1 road).
- C0: 189.2 mi. Tertiary roads merged 15 small components into it.
- C0 without roads: the core wilderness piece is now 69.5 mi (was 91.2). The Cabin Mountain /
  Canaan Valley trails (T1, 17.4 mi) connect to it only through a track, which is now a road.
- USFS check unchanged: 99.2% within 100 ft, 0 gaps. 184 dead ends, 8 possible missing junctions.

**Change to the M2 plan (needs your OK)**
- You don't have a CalTopo reference GPX. Proposed instead: validate elevation gain automatically
  against USGS 3DEP 1 m lidar where it covers Dolly Sods (an independent, higher-resolution source),
  plus a short optional CalTopo spot check. For that, I export one trail as GPX and you import it
  with step-by-step instructions.

## M2 — Elevation (done 2026-09-30)

**Done**
- `backcountry dem --region dolly_sods`: finds USGS 3DEP 1/3 arc-second (~10 m) tiles through the National Map
  API, keeping the newest version of each tile (n39w080 published 2023-08-16, n40w080 2026-05-12). It reads
  only the window covering every edge (the files are cloud-optimized) and caches it unmodified
  (EPSG:4269, 2418x3006 cells, 23 MB) with a sidecar JSON of sources and `retrieved_at`.
- `elevation.py`: sample every 33 ft (10 m) along each edge, bilinear interpolation, a 3-sample moving average,
  and gain/loss counted once past a 3.3 ft (1 m) threshold. Edges store `gain_ft_fwd`, `loss_ft_fwd`,
  `gain_ft_rev`, `loss_ft_rev`, `elev_min_ft`, `elev_max_ft`, plus a raw profile. Forward means the source
  way's direction (`osm_nodes[0]` onward). A missing DEM value makes the edge's elevation unknown (never filled).
- `qa` adds elevation when a DEM is cached: a report section (sources, method, range, missing edges, steepest
  edges), gain/loss in `edges.geojson`, `data/processed/<region>/profiles.json`, and map tooltips.
- `backcountry reference --region dolly_sods --trail NAME`: chains a named trail into one line from source
  vertices only (it refuses branches, loops, and gaps) and writes `tests/reference/<slug>.gpx`: exact OSM
  points, no elevations, so CalTopo uses its own terrain. It also writes a YAML stub for CalTopo's numbers, and
  computes our gain on both the 10 m DEM and USGS 1 m lidar (only that trail's corridor is downloaded).
- `tests/test_reference.py`: per reference, checks CalTopo numbers (distance ±5%, gain/loss ±15%) and
  1 m lidar (gain/loss ±15%). Each check skips until its data exists.

**Results**
- All 661 edges have elevation (0 missing). Network range 1,113–4,488 ft.
- Reference trail: **Blackbird Knob Trail** (TR 511), 5.44 mi, osm:way/15655078 + 15655225.
  - 10 m DEM: **794 ft gain, 696 ft loss** (in the GPX direction, from the FR 75 end).
  - 1 m lidar (WV_FEMAHQ_2018): 798 ft gain, 697 ft loss, a 0.5% difference.
  - Elevations along the trail differ by at most 6.6 ft (mean 0.8 ft) between the two.
  - Sensitivity: across smoothing of 1/3/5 samples and thresholds of 0–9.8 ft, gain ranges 752–863 ft.
    The chosen settings sit mid-range.
- Caveat: USGS may have built the 10 m DEM here from that same lidar, so their agreement shows our method
  is stable across resolution, not that it matches an outside tool. CalTopo is the outside check.

**Known wrong / caveats**
- Hysteresis restarts at each edge, so summing edge gains overstates a route's gain slightly.
  Routes (M3+) should recompute from joined profiles.
- Gain on very short edges is essentially their net change.

**Dependencies added:** rasterio and gpxpy (both in the CLAUDE.md stack), and numpy (already required by
rasterio/shapely; declared because the DEM math uses it directly).

**Acceptance (CalTopo, read by you on 2026-09-30 from the imported GPX's Profile box)**

| | CalTopo | ours (10 m DEM) | difference |
|---|---|---|---|
| distance | 5.44 mi | 5.44 mi | 0% |
| gain | 789 ft | 794 ft | +0.7% |
| loss | 687 ft | 696 ft | +1.3% |
| low / high | 3,661 / 4,085 ft | 3,656 / 4,088 ft | 5 ft / 3 ft |

`tests/test_reference.py` checks this (gain/loss ±15%, distance ±5%, low/high ±30 ft) and the lidar
comparison. Both pass. The 15% target was met with room to spare on one trail; more reference trails
would be cheap to add later with `backcountry reference`.

## M3 — Sanity routing (built 2026-09-30; waiting on your CalTopo distance)

**Done**
- `backcountry route --region dolly_sods --from X --to Y [--no-roads] [--road-factor N] [--reference NAME]`.
  `X`/`Y` can be a trailhead id from the region yaml (`red_creek`), an OSM node id, or `lat,lon`, which
  snaps to the nearest graph node and is refused beyond 656 ft (200 m).
- `route.py`: shortest path by real distance on a simple weighted graph (lightest of any parallel edges).
  Last-resort (tertiary) roads cost 10x their length, so they're used only when nothing else connects.
  `--road-factor N` makes every road mile cost N trail miles; `--no-roads` drops roads entirely.
  Reported distance is always the real one.
- Route geometry is the edges' source vertices joined in walking order (edges reversed as needed).
  Gain/loss come from one profile of the joined line, not from summing edge values.
- Outputs in `out/routes/<from>-to-<to>/`: `route.gpx` (exact vertices, Start/End waypoints, no elevations),
  `route.geojson`, and `summary.md` (distance, gain/loss or "unknown", segments in order with OSM ways,
  road miles, data sources with retrieval dates, and a "Not checked by this tool" section).
- Real-data test: a trailhead-to-trailhead route uses only graph edges and OSM vertices, and its line length
  equals the sum of its edges.

**Sample results (real data)**
- fisher_spring_run → red_creek: 5.34 mi, +317 / −1,696 ft, all trail (Fisher Spring Run Trail, Red Creek Trail).
- red_creek → blackbird_knob: 7.44 mi, +1,831 / −565 ft, last 2.10 mi on Dolly Sods Road (shortest by distance).
- bear_rocks → blackbird_knob: 2.18 mi, entirely on Dolly Sods Road (they're both on FR 75).
- bear_rocks → red_creek: 9.62 mi, 4.28 mi of road. `--road-factor` shifts these onto trails.

**Acceptance:** `tests/reference/fisher_spring_run_to_red_creek.gpx` is waiting for CalTopo's numbers
(distance within 5% is the M3 target; gain/loss get checked too).

**Notes**
- Red Creek Trail has a 0.04 mi unnamed connector (osm:way/1427983302) in the middle of it.
- Added module `route.py` (not in the CLAUDE.md layout); `loops.py` stays reserved for M4.
