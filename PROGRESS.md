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

## M3 — Sanity routing (done 2026-10-04)

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

**Acceptance (CalTopo, read by you on 2026-10-04 from the imported route's Profile box)**

| | CalTopo | ours, original settings | ours, new settings |
|---|---|---|---|
| distance | 5.34 mi | 5.34 mi (0%) | 5.34 mi (0%) |
| gain | 210 ft | 317 ft (+51%) | 209 ft (0%) |
| loss | 1,588 ft | 1,696 ft (+7%) | 1,587 ft (0%) |
| low / high | 2,625 / 4,003 ft | 2,625 / 4,004 ft | same |

Distance (the M3 target) was exact. Gain was not: on a mostly-downhill route the "gain" is only small
bumps, and our 33 ft sampling with a 3.3 ft threshold counted bumps CalTopo doesn't. CalTopo's profile
box says it samples every 100 ft. A sweep over sample spacing (33/66/100 ft), smoothing (1/3 samples)
and threshold (0–16 ft) against both references found two families that fit: 100 ft sampling with a
6.6 ft threshold (both references within 4%), or 33 ft sampling with a 16 ft threshold (within 11%).

**Decision:** elevation settings are now 100 ft sampling, 3-sample smoothing, 6.6 ft threshold
(`elevation.py`: SAMPLE_M 30.48, SMOOTH_SAMPLES 3, HYSTERESIS_M 2.0). This departs from the roadmap's
"~10–15 m" densify spacing on purpose: finer sampling over-counts relative to the tool you compare
against. Blackbird Knob now reads 758 / 660 ft (−4% vs CalTopo; lidar 759 / 659), Fisher Spring Run →
Red Creek 209 / 1,587 ft (0%). Two references isn't many; more cost one `backcountry route --reference`
run plus a CalTopo import each, and `tests/test_reference.py` keeps every one of them as a regression test.

**Notes**
- Red Creek Trail has a 0.04 mi unnamed connector (osm:way/1427983302) in the middle of it.
- Added module `route.py` (not in the CLAUDE.md layout); `loops.py` stays reserved for M4.

## M4 — Loop generation (done 2026-10-11)

**Done**
- `backcountry generate --region dolly_sods --trailhead bear_rocks --total 18-26 [--type loop|lollipop|any]
  [--count 3] [--no-roads] [--road-weight 1] [--repeat-weight 0.5]`.
- `loops.py`: a reduced loop graph (start's component, dead-end spurs pruned, pass-through nodes merged into
  super-edges that remember their real edges). Loops are built from waypoints: for one or two via junctions
  (a, b), shortest path out to a, shortest a→b avoiding used edges, shortest back avoiding both. No edge is
  walked twice. Lollipops (stem out, loop, stem back) are tried from the 8 nearest junctions when fewer than
  `count` plain loops exist or `--type lollipop` is asked for; only stem edges repeat.
- `scoring.py`: penalty = miles outside range ×10 + road miles ×1 + last-resort road miles ×5 + miles walked
  twice ×0.5 (per mile; configurable). Candidates are the lowest-penalty loops that overlap each other by
  less than 50% (length-weighted Jaccard on edge keys). If nothing fits the range, the search widens by 25%
  and reports the deviation in the penalty. A 90 s time budget is reported if hit.
- Outputs in `out/loops/<trailhead>_<range>mi/`: `candidates.md` (table + segments per candidate),
  `candidates_map.html` (all candidates, USGS base), `search.json`, and `candidate_<i>/` with `route.gpx`,
  `summary.md`, `route.geojson`.
- Tests on a synthetic grid (exact loop counts, road penalty ordering, `--no-roads`, lollipop stems, diversity,
  configurable weights) plus the roadmap acceptance on real data: 3 closed, connected, real-edge loops
  for each of 3 trailheads.

**Roadmap deviation: the DFS cycle enumeration was replaced.** The roadmap's approach (DFS over cycles
through the start, pruned by distance) was implemented first. On the real loop graph (212 junctions,
325 edges, median edge 0.14 mi) the number of simple cycles explodes: 3 under 6 mi, 93 under 10 mi,
45,004 under 12 mi, 395,000+ under 14 mi (timed out), so an 18–26 mi search could not finish in 10 minutes.
The waypoint construction above finds ~270 distinct in-range loops per trailhead in 4 s. It doesn't
enumerate every cycle; it builds the ones a hiker would plan (shortest connections between waypoints),
which is what "3 meaningfully different candidates" needs.

**Results (18–26 mi)**
- bear_rocks: 21.98 mi (+2,976 ft, 0.81 mi road), 19.05 mi (+2,013 ft, 0.81 mi road), 18.33 mi (+2,062 ft, 2.18 mi road)
- red_creek: 22.31 mi (3.18 mi road), 21.20 mi (3.71 mi road), 18.51 mi (5.26 mi road)
- blackbird_knob: 18.72 mi (1.37 mi road), 20.88 mi (1.51 mi road), 19.29 mi (2.10 mi road)
- Every loop's gain equals its loss, as a closed loop must.

**Acceptance:** you imported `out/loops/bear_rocks_18-26mi/candidate_1/route.gpx` into CalTopo on
2026-10-11 and confirmed it follows the trail lines throughout (Red Creek, Stonecoal Run, Breathed Mountain,
Blackbird Knob Trail, back to Bear Rocks). Note that CalTopo's trail layer is itself OSM-derived; the
independent geometry check remains the USFS comparison from M1.

**Known limits / open**
- Waypoint loops can cross themselves at a junction (edges never repeat, nodes may). Not seen in the
  results so far; say if you'd rather forbid it.
- Elevation enters scoring only in M5 (per-day gain cap after splitting).

## M5 — Day splitting (done 2026-10-12)

**Done**
- `generate ... --nights 2 --daily 6-11 --max-gain-day 2500`: every scored loop is split into nights+1 days and
  the day plan's cost joins the ranking (roadmap objective: elevation over cap applied after the split).
- `split.py`: dynamic programming over candidate camp points every ~0.1 mi along the route profile. Per-day
  cost = miles outside the daily range ×10 + thousands of ft over the gain cap ×5 + miles away from an equal
  split ×0.5 (weights in `scoring.Weights`). Day gain/loss come from running totals of the same hysteresis
  rule, so the days add up to the route exactly. If nothing fits, the least-bad split is returned and each
  violated constraint is named, plus a note when the total can't fit the day range at all.
- Camps are points on the route line, labeled `camp_location: unverified`, with the OSM way they're on.
  OSM `tourism=camp_site|camp_pitch` features within 1,640 ft (500 m) are listed with their source; otherwise
  "no OSM camp site within 1,640 ft". Nothing is said about legality or water.
- Outputs: `candidates.md` and each `candidate_<i>/summary.md` get a Days section; `route.gpx` gets a
  waypoint per camp. Per-day GPX tracks come in M6.
- OSM data refreshed 2026-10-12 so the raw cache includes camp sites (76 in the bbox, 1 named). The graph
  was unchanged by the refresh.

**Results, the roadmap's target request** (`--trailhead bear_rocks --nights 2 --total 18-26 --daily 6-11
--max-gain-day 2500 --type loop`, 11 s):
1. 19.02 mi, +2,023 ft: days 6.34 / 6.34 / 6.34 mi, +784 / +331 / +908 ft.
2. 22.16 mi, +2,984 ft: days 7.39 / 7.39 / 7.39 mi, +413 / +1,403 / +1,168 ft; camps near 11 and 6 OSM camp
   sites (Red Creek, Big Stonecoal).
3. 18.33 mi, +2,051 ft: days 6.06 / 6.15 / 6.12 mi, +474 / +800 / +777 ft.
All three plans satisfy every constraint. An impossible request (1 night, 6–8 mi days) returns the shortest
loops with "can't fit 2 days of 6.0–8.0 mi: it is 18.33 mi ..." and each over-limit day named.

**Ideas not built (say if you want them)**
- Prefer camps near known OSM camp sites (a soft bonus). Right now camps are placed purely for mileage.
- A per-day gain *minimum* or "big day first" preference.

## M5b — Customizable camps, camp sites and water along the route (done 2026-10-12, your request)

**Done**
- `--camp <spec>` (repeatable) pins camps: miles in (`6.5`), `lat,lon` (snapped to the route within 200 m),
  or a camp site id (`osm:node/402289780`). Pinned camps stay put; the planner places the rest. A day may not
  pass over a pin. A pin that a loop doesn't pass near is reported ("Pinned camp ... is 1.2 mi from this
  route") and costs that loop 100 penalty points, so loops that pass the pin win.
- `--camps-at-sites`: designated-site mode. Camps only at known OSM camp sites within 200 m of the route.
  If there aren't enough, it says so and falls back to mileage placement with `days ok = NO`.
- Camp sites and water along each route (`features.py`): OSM camp sites/pitches, springs, drinking-water taps
  and water bodies within 492 ft (150 m), USFS campgrounds within 500 m, and every stream/river crossing,
  each with mile marker, day, distance off route, and source. Each camp lists its nearest water feature.
  Every water mention carries: "Whether water is actually there, flowing, or drinkable is unknown."
- OSM refreshed 2026-10-12 with the new feature queries. In this bbox OSM has 52 streams/rivers (30 named),
  35 ponds/lakes, 76 camp sites, and **no springs or drinking-water taps tagged**.

**Results (Bear Rocks, 2 nights, 18–26 mi, 6–11 mi/day, 2,500 ft/day)**
- Default: candidate 1's mileage camps sit 0.8 and 1.6 mi from the nearest stream crossing, while the loop
  passes ~20 known sites along Stonecoal Run (miles 7.4–9.4) with three crossings.
- `--camps-at-sites`: 22.97 mi loop, days 7.66 / 7.66 / 7.66 mi, camps at osm:node/12284468382 (Rocky Point
  Trail, 5 sites nearby) and osm:node/12714841235 (Big Stonecoal Trail, 7 sites nearby, stream crossing 201 ft).
- `--camp osm:node/402289780`: the ranking shifts to loops passing that site; days 7.67 / 7.69 / 7.59 mi.

**Phase 1 scope change:** the roadmap excluded water data. At your request it's in, as sourced map features
with an explicit unknown-presence warning, never as a claim that water exists.

**Not built (ideas)**
- A soft preference for camps near known sites or water, instead of pins/sites-only.
- USGS NHD hydrography as a second water source: it has springs/seeps and flowlines OSM lacks here.
- Editing camps in the field is the Phase 2 app; the engine side (pins) is ready for it.

## M6 — Outputs (done 2026-10-13; CalTopo import verified, Garmin Connect not yet tried)

**Done** (per candidate, in `out/loops/<trailhead>_<range>mi/candidate_<i>/`)
- `route.gpx`: one track per day ("Day 1: Start to Camp 1", ...) plus waypoints: Start, End, each Camp, and a
  "Jct n: <next trail>" waypoint at every trail-name change (29 waypoints on the Bear Rocks loop). Track and
  waypoint points carry DEM elevations (meters, interpolated from the route profile) so devices can show a
  profile. Day tracks share their boundary point; every OSM vertex is kept.
- `day_<n>.gpx`: one file per day with one track and that day's waypoints, for importing a single day as a course.
- `route.geojson`: one LineString per day (length, gain, loss) plus Point features for the waypoints.
- `map.html`: each day in its own color, S/E and camp badges, junction layer (off by default), OSM/USFS camp
  sites and water features as dots with "status unknown" / "presence unknown" tooltips, USGS base maps, legend
  with attribution and retrieval date.
- `summary.md`: totals and per-day miles/gain/loss, segments in order with OSM ways, road miles, camps with
  nearest water, features along the route with the water warning, data sources with retrieval dates, a
  "Known gaps" section ("not checked by this tool: water, campsites, permits, closures, trail conditions,
  trailhead access"), and a Files list.
- Reference GPX files (`write_trail_gpx`) deliberately stay elevation-free so CalTopo computes its own profile.
- `route.gpx`, `day_1.gpx`, and a reference GPX validate against the GPX 1.1 XSD
  (`xmllint --noout --schema gpx.xsd <file>`; xmllint is a macOS system tool, the XSD is from topografix.com).

**Acceptance:** you imported `candidate_1/route.gpx` into CalTopo on 2026-10-13 and exported the map back
(`candidate_1/test.gpx`, creator CALTOPO). The export holds our three day tracks by name (7.67 / 7.67 / 7.66 mi),
all 1,307 track points (none missing or moved), all 29 waypoints with names, and our elevations (median 1.7 ft,
max 3.3 ft rounding difference). Garmin Connect import hasn't been tried; if it only takes one track per file,
`day_<n>.gpx` exists for that.

**Not built**
- An elevation profile chart (per-edge profiles are stored for it; `profiles.json`).

## M6 follow-up — satellite mode and campsite evidence (2026-10-13, your request)

- `--basemap topo|hybrid|satellite` on `generate`; every map also has all three USGS base layers in its
  layer box (public domain; imagery is ~1 m, native to zoom 16).
- Clicking a camp site or water dot now shows evidence: all OSM tags, last edit date and version, and links to
  that exact spot in Google satellite view, on openstreetmap.org, and its edit history. The summary's feature
  table has "OSM last edit" and "tags" columns and a how-to-check note.
- The Overpass query now uses `out meta geom` (edit timestamp/version); OSM refreshed 2026-10-13.
- The site you flagged, osm:node/12830535560 at mile 21.3 on the ridge: mapped 2025-05-12, one edit,
  `description=no water`. In the bbox: 76 camp sites; 54 `backcountry=yes`, 30 `informal=yes`, 11 with a
  `survey:date`, 49 edited only once.

## M7 — Constraints (done 2026-10-13)

**Done**
- `--must-visit <spot>` (repeatable): a trailhead id, OSM node id, `lat,lon` (snapped within 200 m), or a trail
  name. A loop qualifies if it passes the node (or uses an edge of the named trail). A node off the loop is
  reached by an out-and-back detour from the loop's nearest point, at most 1 mi one way, counted as repeated
  miles (`Candidate.detour_keys`). Unreachable spots are named in the output.
- `--avoid <name or way id>` (repeatable): those edges are removed before the search (`generate` and `route`).
  Whole-name, case-insensitive; an unknown name is an error.
- `--direction cw|ccw`: loops are walked in that orientation (shoelace sign of the walked line; the whole walk is
  reversed when needed). Figure-8 loops have no clear orientation; none have appeared so far.
- Re-running into the same output folder writes `diff.md`: request changes, then per candidate "unchanged" /
  "changed (N% of edges shared, trails added/dropped, camps moved)" / "new", and previous candidates that are gone.
  `candidates.json` (current) and `candidates.previous.json` back it.
- When nothing fits, the output now says why and what to try (e.g. the start became a dead-end spur after
  `--avoid`, so only lollipops are possible: `--type any`).

**Real examples (Bear Rocks, 2 nights, 18–26 mi, 6–11 mi/day, 2,500 ft/day)**
- `--must-visit "Rocky Point Trail" --avoid "Dobbin Grade Trail" --direction cw`: 3 clockwise loops in 5 s, all on
  Rocky Point Trail, none on Dobbin Grade (83 of 229 loops qualified).
- Adding `--avoid "Raven Ridge Trail"` leaves the trailhead on a spur: 0 loops, with the hint; `--type any` then
  gives 3 lollipops that start with 6.5 mi on Dolly Sods Road (reported as road miles). `diff.md` lists the old
  three as gone and the new three as new.

## M8 — Second region: Pemigewasset Wilderness, NH (built 2026-10-13; awaiting your CalTopo check)

**Done**
- `regions/pemi.yaml`: bbox 44.00–44.26 N, −71.72 to −71.38 W, EPSG:32619 (UTM 19N), USFS cross-check on,
  10 trailheads by graph node from USFS recreation sites (Lincoln Woods, Zealand, Gale River, Garfield, Hancock,
  North Twin, Ethan Pond, Sawyer River, Signal Ridge, Hale Brook). All `verified: false`.
- Data: 3,780 OSM elements (5x Dolly Sods); 2,932 nodes, 3,049 edges, 280 components; C0 = 383 mi (Bondcliff,
  Franconia Brook, Ethan Pond, Nancy Pond, ...). bbox contains the OSM wilderness relation with 1–4.6 mi margins.
  DEM: two 1/3" tiles (n44w072, n45w072), 4595x3327 cells.
- USFS: 80 ground trails, 194.7 mi in bbox, **98.5% within 100 ft**, 4 gaps totaling 0.93 mi (Ethan Pond 787 ft,
  East Side 951 ft, Lincoln Brook 328 ft, Gale River 2,854 ft). 47 winter-only (snowmobile/XC) trails listed separately.
- `generate --trailhead lincoln_woods --nights 2 --total 24-32 --daily 7-12 --max-gain-day 3500 --type loop`, 7 s:
  1. 30.15 mi, +5,230 ft: Lincoln Woods > Franconia Brook > Twin Brook > Garfield Ridge > Twinway > Zeacliff >
     Ethan Pond > Thoreau Falls; days 10.0 / 10.0 / 10.1 mi.
  2. 29.32 mi, +4,848 ft: Lincoln Woods > East Side > Wilderness Trail > Shoal Pond > Ethan Pond > Zeacliff > Twinway >
     Bondcliff; days 9.8 / 9.8 / 9.8 mi.
  Only 32 loops fit the range from Lincoln Woods, so 2 candidates clear the 50% overlap bar.

**What the second region exposed (fixed)**
- Overpass returned HTTP 504 on the bigger query and the tool showed a raw traceback. Now: one polite retry after
  30 s, then a clear message (`ingest.FetchError`).
- The USFS report mixed snowmobile/ski trails (`trail_type=SNOW`) into hiking coverage, which read as 90% with
  30 gaps. They're now reported separately; hiking coverage is scored on TERRA trails only.
- Nothing in the routing, splitting, elevation, or output code needed to change. Region-specific knowledge lives
  only in the yaml and the caches.

**Data notes (not fixed; they're the data)**
- OSM names a trail "Thoreau Falls Trail (Bridge Removed ...)"; the summary shows the name as is. This tool doesn't
  interpret such notes.
- Only 1 of 24 USFS trailheads has an OSM `highway=trailhead` tag in this area, so the yaml entries use the nearest
  graph node to the USFS site (23–204 ft away).

**Acceptance (yours):** import `out/loops/lincoln_woods_24-32mi/candidate_1/route.gpx` into CalTopo and check it
follows the trails. That completes Phase 1.

## Phase 2 piece 1 — API over the engine (done 2026-10-14)

Spec: `docs/superpowers/specs/2026-10-13-phase2-api-and-web-app-design.md`; plan:
`docs/superpowers/plans/2026-10-13-phase2-api.md`. Built on branch `phase2-api`.

**Done**
- `planning.py`: `GenerateRequest` (validates settings, names the bad field) and `plan_trip` (the generate
  workflow). The CLI `generate` command and the API call the same code.
- `serialize.py`: `Run` / `Candidate` JSON shapes and GPX built from a stored run's pieces and waypoints;
  `write_route` uses the same builders, and a test proves an API download equals the CLI's `route.gpx` byte for byte.
- `src/backcountry/api/`: FastAPI app. `GET /api/regions`, `/regions/{id}/trailheads` (yaml entries, with
  `in_graph: false` when a node is gone, plus data-derived candidates), `/regions/{id}/trails` (GeoJSON),
  `/regions/{id}/features?bbox=`, `POST /regions/{id}/generate` (returns a saved `Run` with `data_sources`,
  `known_gaps`, and a `diff` against the previous run for the same trailhead), `GET /runs/{id}`,
  `/runs/{id}/candidates/{n}.gpx[?day=k]`, `/runs/{id}/candidates/{n}.geojson`. Every error is JSON
  `{"error": ...}`: 400 (with `field`) for bad settings and malformed requests, 404 for unknown regions/runs/
  candidates, 500 for engine failures (traceback in the server log). Graph built once per process, under a
  lock. Runs under `data/runs/` (atomic writes, `index.json` for lookups; unreadable files are skipped).
- `backcountry serve [--host 0.0.0.0] [--port 8000]` (uvicorn); serves `web/dist` at `/` once it exists.
- Live check: Bear Rocks 2-night request through the API in 13.5 s, three valid plans, GPX download with
  `application/gpx+xml` and a filename.

**Dependencies added:** fastapi, uvicorn (runtime); httpx2 (dev, for Starlette's TestClient).

**Reviews:** a checkpoint review (Tasks 1–3) and a final whole-branch review by fresh reviewer subagents, both
"approve with fixes"; all six Important findings fixed with tests (NotFound-only 404s, graph build lock, CLI record
format kept, strict JSON validation, raw_dir isolation, JSON error contract for 400/500, resilient run store).
Deferred minors: GeoJSON download duplicates export's feature list and has no Content-Disposition; the
`export`/`serialize` local import; `/trails` recomputed per call and `/features` misses a stream that crosses the
bbox between vertices; `/trailheads` returns null lat/lon for a verified yaml entry whose snap fails; test
fixtures use the default `web/dist` path; CLI `--must-visit` error lacks the `--field:` prefix.

**Deviations from the spec/plan (recorded in the ledger)**
- The API lives in `src/backcountry/api/` (a subpackage) instead of a separate `api/` folder: one install.
- `plan_trip(..., dem=)` lets the API pass the store's DEM.

## Phase 2 piece 2 — Web app (built 2026-10-14; awaiting your phone check)

Spec: `docs/superpowers/specs/2026-10-13-phase2-api-and-web-app-design.md`; plan:
`docs/superpowers/plans/2026-10-14-phase2-web-app.md`. Built on branch `phase2-web`. Lives in `web/`
(Vite + TypeScript + MapLibre, no framework); `backcountry serve` serves the built `web/dist`.

**Done**
- One page, three states in a bottom sheet (side panel on wide screens): **Plan** (region, trailhead
  list from `/trailheads` with data-derived entries marked "(from the data, unverified)" and unreachable ones
  disabled, the same settings as the CLI, Generate), **Compare** (one card per candidate with miles, gain,
  road and repeated miles, the trail names, search notes, "widened"/"truncated" sentences, the diff
  against the previous run), **Trip** (days table, camps with "pinned by you / at a known campsite /
  placed for mileage" and "Location unverified", nearest water "(presence unknown)", elevation profile
  SVG with camp lines and mile/feet axes or "Elevation unknown" when there is no DEM, campsites and water
  table with the water warning, GPX whole-trip and per-day plus GeoJSON downloads, Re-plan, a disabled
  "Save for offline (coming)" button, data sources, Known gaps last).
- Map: USGS Topo / Imagery+Topo / Imagery base maps (switcher top-left, attribution under it), trail lines,
  yaml trailheads as TH badges and data-derived starts as grey dots (tap either to pick; tap anywhere on a
  trail picks that point and the status line names the trail so it can be typed under must-visit/avoid), candidate lines in
  distinct colors (tap to select), the opened trip colored by day with S/E and C1.. badges, feature dots
  whose popups show the OSM tags, last edit date, satellite view and OpenStreetMap links, and the
  "existence/presence unknown" line; a "Show trail junctions" toggle (off by default). No raw OSM ids in
  any text.
- Errors are shown verbatim with the field named; a failed or timed-out (2 min) search keeps the previous
  run on screen. Region, form and base map persist in localStorage. The sheet taps through
  collapsed/half/full and follows a vertical drag.
- Tests: Vitest 33 (11 files), Playwright 2 (builds the app, runs the API on the synthetic grid via
  `scripts/test_api_server.py`, plans a 1-night loop, opens candidate 2, downloads its GPX and checks it
  holds a track; refuses a bad range with the field named), pytest 286 passed / 1 skipped.
- Found by the end-to-end test and fixed: typing into a field and then tapping Generate lost the tap,
  because the panel re-rendered on blur. Form edits now go through an `editForm` action that records the
  form without re-rendering.
- Final review (fresh reviewer subagent): "with fixes". Fixed: MapLibre layer click handlers were registered
  on every `setTrip`/`setCandidates` and MapLibre keeps them keyed by layer id, so compare → open → back →
  open another candidate → tap a dot opened two popups, one for the wrong candidate's feature (reproduced on
  real data: 2 popups → 1); attribution was under the bottom sheet on phones and the base-map switcher under
  the docked panel on laptops (both now map controls at top-left; the map sits beside the panel on wide
  screens); Pemigewasset's 522 data-derived starts were one DOM marker each (now a circle layer); fields
  under 16px made iOS Safari zoom; a saved trailhead that has since left the network was sent to the API
  (now refused with the reason); the compare heading showed a bare node id for a data-derived start (now
  its name, or "Map point lat,lon"); a new view opened scrolled wherever the previous one was. Vitest 38,
  Playwright 6.

**Not done / deferred**
- Reviewer minors left as they are: a wrong-shaped (not just invalid) persisted form blanks the panel;
  the junction checkbox unticks on a sheet tap while the dots stay; a region switch during a search can
  land the old region's run; a 2xx non-JSON reply shows "Nothing generated yet" with no error; regions with
  no cached data are selectable (the API's 404 text shows); fitBounds ignores the phone sheet and the map
  isn't re-fitted to the results; mouse drags that end off the 6 px handle do nothing; "version null" in a
  popup when OSM has no version; the pre-fit map centre is Dolly Sods even when the saved region is Pemi;
  `app.test.ts` tests `sheet.ts`. Spec item not built: tapping a trail doesn't add it to must-visit (the
  status line names it instead).
- Your phone check over Wi-Fi (`npm run build`, `uv run backcountry serve --host 0.0.0.0`) is the manual
  acceptance; I only checked a phone-sized headless browser.
- "Save for offline" is a disabled button (piece 3). No accounts, no hosting (piece 5).
- Piece 1's deferred minors still stand (see above).

## Phase 2 piece 3 — Offline trips (built 2026-10-15; awaiting your phone check)

Spec: `docs/superpowers/specs/2026-10-14-phase2-offline-design.md`; plan:
`docs/superpowers/plans/2026-10-14-phase2-offline.md`. Built on branch `phase2-offline`. The server and CLI are unchanged.

**Done**
- Service worker (`web/sw/sw.js`, built into `dist/sw.js` with the app's file list): the app opens with no signal;
  USGS tiles come from saved-trip caches first; `/api` is never cached. A new version shows "A new version of the
  app is ready. Reload" and never reloads on its own.
- **Save for offline** on a trip: shows the estimate first ("about 1376 map tiles, 28 MB" for the 19 mi Bear Rocks
  loop), then downloads the trip box plus 0.5 mi at zoom 12–16 for USGS Topo and Imagery+Topo (4 at a time, one
  retry), the GPX files exactly as the server built them, and trail lines near the route. Progress and Cancel while
  saving. A save that loses tiles or hits the storage limit is kept and marked **incomplete** with the reason
  ("Storage full: saved 410 of 760 tiles…", "Map tiles not saved: z16: 38 missing."). The app asks the browser to
  keep the data and says on the card when it didn't agree.
- **Saved trips** view (from the plan panel, or automatically when the server can't be reached): size, date,
  complete/incomplete, open, delete (record and tiles). Interrupted saves are cleaned up at the next start; a trip
  whose map the browser cleared says so when opened.
- **Show my location**: blue dot with an accuracy circle and a line like "mile 7.2 of 19.0 · Day 2 · 2.1 mi and
  +540 ft to Camp 2 · 110 ft off route · ±15 ft", a "you" marker on the elevation profile; "Off route: 0.4 mi from
  the nearest point on the route" past ~0.25 mi; "(the route passes here twice)" on a loop's start/end or a stem,
  using your last position to choose; "position rough" past 300 ft accuracy. Needs https (see README: Tailscale).
- **Moving camps** on a saved trip: drag a camp marker along the route, or **Camp here** at your GPS position. Days,
  gain/loss, violations ("Constraint not met: …"), the day colors and the profile are recomputed on the phone with
  the engine's rules; a camp dropped off the route or past its neighbour is refused with the reason. Moves are stored
  with the trip (Reset camps restores the engine's plan). Saved GPX files keep the original camps, and say so.
- Engine parity: `tests/resplit_fixture.py` writes `web/src/fixtures/resplit-parity.json` from the real
  `split_route`; Vitest checks the phone reproduces it, and `tests/test_resplit_fixture.py` fails if the file goes
  stale (regenerate with `uv run python tests/resplit_fixture.py`).
- Real-data check (headless phone-sized browser, production build, real USGS tiles): Bear Rocks 19 mi loop saved
  complete in 84 s, 30 MB; reopened in airplane mode with the topo map, route, days and camps. The spec's ~15–20 MB
  guess was measured on a tighter box; the agreed 0.5 mi margin roughly doubles it.
- Tests: Vitest 89 (20 files), Playwright 7 (new: save → offline reload → saved tiles served from the cache → GPS
  line → Camp here → move survives a reload → delete), pytest 287 passed / 1 skipped.

**Found while building and fixed**
- The service worker filled its placeholders in a comment instead of the code (my plan's template); caught by a new
  test against the real template.
- The app couldn't start offline: servers send `Vary: Origin`, so cached scripts never matched; lookups now ignore
  Vary (found by the offline end-to-end test).
- A camp move could be lost if the app closed right after it (the screen updated before the phone stored it); it is
  now stored first (found by the end-to-end test failing 2 runs in 6).

**Final review** (fresh reviewer subagent): "with fixes", 0 Critical, 7 Important — all fixed with tests: a save
finishing after you opened another trip no longer marks that trip saved (it could write the wrong camps into the
record); incomplete or cleared trips offer **Save again** (keeps moved camps, fetches fresh tiles); the "map not saved
here" note is judged from the saved tiles and says when satellite (never saved) is selected; camps you didn't move
keep the engine's record instead of all saying "moved by you"; GPS fixes and save progress update in place (Cancel and
Back taps no longer get swallowed mid-save); with saved trips on the phone, the app waits at most 5 s for the server
at start. Tests after: Vitest 98, Playwright 8, pytest 287 passed / 1 skipped.
Deferred minors: tile lookups open every trip cache per request; no backoff on 429/5xx; the worker's version ignores
index.html/manifest-only changes; the saved list loads whole records; no GeoJSON for saved trips; camp labels in the
profile aren't escaped (engine strings); losing signal mid-session doesn't flip to offline; opening a saved trip
replaces region trails until reload; "0 / 0" first progress line and no thousands separator in the estimate; a tiny
accidental drag moves a camp; tsconfig reformatting.

**Known limits**
- On a tall phone screen the first view of a saved trip shows land above and below the saved area, so the app
  notes "Part of this view is outside the saved map area" until you zoom in. That note is true.
- iOS can still clear saved trips when the browser doesn't grant persistent storage; the card says when that's the
  case. Installing to the home screen makes a grant more likely.
- For a moved camp, "known campsites nearby" only counts sites already listed along the route (within ~490 ft of
  it); the phone doesn't store the region's full campsite list. The trip view says "listed along the route".
- Position updates only while the app is open and the screen is on (browser limit).

**Not done (later pieces)**: background track recording, re-planning offline, saving whole regions, rebuilding GPX
on the phone, the native app (piece 5).

### Your phone check, round 1 (2026-10-15)

- **Fixed: the whole-trip GPX showed only day 1 in Garmin Connect.** The file had one track per day, and Garmin
  Connect imports only a file's first track (CalTopo shows all tracks, so the earlier CalTopo check didn't catch it).
  The whole-trip GPX is now one continuous track (Bear Rocks: 1 track, 1,225 points, 19.05 mi), with camps and
  junctions as waypoints; the per-day files are unchanged. The CLI's `route.gpx` uses the same builder, so it changed
  too. Trips already saved on the phone keep the old file: delete and save them again.
- **Your frontend feedback, for the UI pass after the backend pieces:** the campsites-and-water table is a long,
  report-style list that feels odd in an app; the app overall looks primitive.


## Phase 2 piece 4 — Chat layer (built 2026-10-21; awaiting your phone check)

Spec: `docs/superpowers/specs/2026-10-20-phase2-chat-design.md`; plan:
`docs/superpowers/plans/2026-10-20-phase2-chat.md`. Built on branch `phase2-chat`. No new dependencies.

**Done**
- `src/backcountry/chat/`: a fact sheet per trip (settings, route, days, camps, water and campsites with
  "presence unknown" / "existence and status unknown", engine notes, known gaps, trail and trailhead names; miles and
  feet; no raw OSM ids; long lists capped); a local model through Ollama using only the standard library
  (`think: false`, `temperature: 0`, an 8,192-token window, the answer forced into a JSON shape, one retry); proposals
  merged into the current settings and validated by the engine's own `GenerateRequest` (misspelled trails get "did you
  mean …?", impossible values get the engine's wording, a no-op says "That wouldn't change any setting."); an honesty
  check that withholds replies with numbers, trail names or claims (safe, water there, open, legal, permitted,
  conditions) the facts don't support.
- `POST /api/chat`: one turn; 503 when Ollama isn't running ("The chat model isn't reachable (Ollama at
  127.0.0.1:11434). Start Ollama, or plan with the form."), 504 after 60 s, 502 for nonsense output. It never runs or
  saves anything.
- Web: a chat box under Compare and Trip. A change shows as a card ("Miles per day: 6-11 mi → 6-9 mi") with **Run**
  (the normal generate, and the form then shows what ran) and **Dismiss**. "Thinking… (local model, can take ~20 s)";
  "Chat needs the server." offline; withheld answers show "(answer withheld: <reason>)".
- `backcountry chat-eval --model ollama:<m> [--model …]`: 32 cases (21 changes or unclear requests, 11 questions)
  against a Bear Rocks 2-night run planned from the cached data; results in `evals/results/` (git-ignored).
- Settings: `.env.example` (committed, no values); `.env` is git-ignored.

**Local model comparison** (2026-10-21, M1 Pro 16 GB, final instructions and honesty check):

| model | passed | errors | withheld | mean s |
|---|---|---|---|---|
| **qwen3:14b** (default) | **28/32** | 0 | 0 | 5.4 |
| gemma4:12b | 25/32 | 0 | 1 | 4.0 |
| qwen3.5:9b | 22/32 | 2 | 2 | 4.0 |
| qwen2.5:7b-instruct | 18/32 | 0 | 1 | 3.4 |
| llama3.2 | 16/32 | 0 | 2 | 2.0 |
| llama3 | 14/32 | 0 | 0 | 2.8 |
| gpt-oss:20b | 5/32 | 27 timeouts | 0 | 57.8 |

- qwen3:14b and gemma4:12b were scored on Ollama 0.40.7; the others on 0.23.2 (upgraded during this piece).
  gemma4:12b didn't load at all on 0.23.2. gpt-oss:20b is too big for 16 GB alongside everything else.
- Chosen default: **qwen3:14b**, most cases passed and nothing withheld, ~5 s per answer once loaded (the first
  message after a while loads the model, ~10–15 s). gemma4:12b is a close, slightly faster second.
- How the numbers moved: the first qwen3:14b run scored 23/32, mostly from the instructions (it asked "would you like
  me to…?" instead of proposing; it built camp lists wrong) and two cases that didn't fit the eval route; fixed, it
  scored 29. The final review found that the instructions' camp example used the eval route's own miles (the
  expected answers), so the example was rewritten with made-up miles; the honest score is 28.
- Remaining qwen3:14b misses: shortening day 2 or day 3 sometimes gives no proposal; "I want to hike the Breathed
  Mountain Trail" (and its misspelling) gets "not listed" although the name is in its facts.

**Final review** (fresh reviewer subagent): "with fixes". Fixed with tests: the honesty check let unit conversions
("19 km") and positive permit/water/"allowed" claims through, and one "unknown" in a sentence excused the whole
sentence (now per clause); the camp example leaked the eval's answers; chat proposals from an earlier result stayed
runnable after a new run (now marked "From an earlier result; not runnable here", and history is the current
result's only); pins at the current camps showed as a change; a change with no fields said nothing; point
must-visits showed raw ids on the card; the trip view now says chat uses the engine's camps when you moved one. The
eval then found two false withholds from the stricter check ("roads are allowed", "determine if a permit is
required"), also fixed.
Deferred minors: "Which Trail…" at a sentence start is withheld as an unknown name; Run posts to the selected region,
not the run's (same as Re-plan); no client-side timeout on the chat request; history texts aren't length-capped on
the server; `.env` doesn't accept `export KEY=` or inline comments; the 300-name cap is alphabetical; "11.0" vs "11"
camps show a spurious change line.

**Tests**: pytest 332 passed / 1 skipped, Vitest 107, Playwright 9 (new: a chat proposal shows what changes and runs
only when tapped, with a fake model, so no Ollama is needed).

**Not done (later)**: Claude models (one new dependency, `anthropic`, and your key, behind the same `ChatModel`
interface; the fact sheet and messages would then go to Anthropic); planning from scratch by talking; moving camps
from chat; chat offline; history across sessions.
