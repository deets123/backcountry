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
