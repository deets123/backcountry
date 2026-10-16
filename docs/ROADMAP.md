# Roadmap — Phase 1: Dolly Sods CLI

Goal of Phase 1: I can run one command, get 3 real candidate loops split into days,
load the GPX into CalTopo/Garmin, and use one on an actual trip.

Target command:
```
backcountry generate --region dolly_sods --trailhead <id> --nights 2 \
  --total 18-26 --daily 6-11 --max-gain-day 2500 --type loop
```

---

## M0 — Project setup
- `uv` project, package skeleton per CLAUDE.md, `pytest` running, `.gitignore` for data/out.
- `regions/dolly_sods.yaml` with a bounding box (start with roughly
  lat 38.94–39.10, lon -79.42 to -79.26, then adjust to cover the whole wilderness
  plus access roads), CRS `EPSG:32617`, and an empty `trailheads:` list.

**Done when:** `uv run pytest` passes and `uv run backcountry --help` works.

## M1 — Ingest + graph + QA report
- Fetch trails for the region bbox via osmnx/Overpass. Include `highway=path|footway|track`
  plus `unclassified/service` forest roads, flagged `is_road=true` so they can be
  penalized or excluded later.
- Keep useful tags: `name`, `ref`, `sac_scale`, `trail_visibility`, `access`, `foot`.
  Drop ways with `access=no|private` or `foot=no` into an excluded list with the reason.
- Cache the raw response in `data/raw/` with a `retrieved_at` timestamp.
- Build an undirected graph whose nodes are junctions/endpoints and whose edges are
  trail segments with `length_m`, geometry, source way IDs, and tags.
- `backcountry qa --region dolly_sods` writes a report: node/edge counts, total trail
  miles, connected components (with sizes), dead ends, very short edges (<20 m),
  and an HTML map coloring each component.
- `backcountry trailheads --region dolly_sods` lists candidate trailheads from source
  data (`highway=trailhead`, parking near trail endpoints, trail/road intersections)
  with IDs I can pick. Nothing gets hardcoded.

**Done when:** the QA map shows the main trail network as one large component, and I
review it against CalTopo. Any missing trails are listed as OSM data gaps, not patched.

## M2 — Elevation
- Get a DEM for the bbox from USGS 3DEP and cache it.
- For each edge: densify the geometry (~10–15 m spacing), sample elevation, smooth,
  and compute gain/loss in both directions using a small hysteresis threshold so DEM
  noise doesn't inflate totals. Store `gain_ft_fwd`, `gain_ft_rev`, `loss_ft_*`.
- Store per-edge elevation profiles for later charts.

**Done when:** a test compares total gain on a reference GPX in `tests/reference/`
(I'll supply one exported from CalTopo) and it lands within ~15% of CalTopo's figure.
Report the difference either way.

## M3 — Sanity routing
- `backcountry route --from <node> --to <node>`: shortest path, exported as GPX
  with distance and gain.
- Snap arbitrary lat/lon input to the nearest graph node; refuse if it's more than
  ~200 m away.

**Done when:** a point-to-point route between two trailheads matches CalTopo distance
within ~5%.

## M4 — Loop generation (the core)
- Input: start node, total distance range, type `loop` (and allow a "lollipop" with
  a shared out-and-back stem).
- Approach: work on the junction graph (it's small, likely a few hundred edges).
  Search cycles through the start node with depth-first search pruned by distance,
  limiting edge reuse to the lollipop stem. If that proves too slow, propose an
  alternative before switching.
- Score candidates with `scoring.py` (see below) and return the best 3 that are
  meaningfully different (e.g. edge-overlap Jaccard < 0.5).
- Hard exclusions: excluded/access-restricted ways, and roads if `--no-roads`.

Initial objective (weights configurable):
```
penalty = distance_deviation_from_range
        + road_miles * road_weight
        + repeated_miles * repeat_weight
        + elevation_over_cap (applied after day split)
```

**Done when:** for 2 or 3 trailheads, 3 loops each come back, every one closed and
connected in tests, and I can load them in CalTopo and confirm they follow real trails.

## M5 — Day splitting
- Given a loop and nights N, choose N camp points along it (at nodes, or interpolated
  points along edges) to keep each day in the daily-mileage range and under the gain cap.
- Use dynamic programming over candidate split points to minimize imbalance and
  overage. If no split satisfies the constraints, return the best one and state
  exactly which constraint failed.
- Camp points are labeled `camp_location: unverified`. If OSM has `tourism=camp_site`
  or `camp_pitch` nearby, list it with its source. Otherwise say none was found.
  Don't claim legality or water.

**Done when:** splits respect ranges on my test cases, and failures are explained clearly.

## M6 — Outputs
Per candidate route, write to `out/<run_id>/`:
- `route.gpx`: one track per day plus waypoints (start, camps, junctions).
- `route.geojson`
- `map.html` (folium; each day in a different color; attribution shown)
- `summary.md`: total and per-day miles/gain/loss, trail names in order, road miles,
  data sources with retrieval dates, and a "Known gaps" section (water, campsites,
  permits, closures, conditions: "not checked by this tool").

**Done when:** the GPX imports cleanly into CalTopo and Garmin Connect as a course.

## M7 — Constraints
- `--must-visit <node|lat,lon>` and `--avoid <trail name|way id>`.
- `--direction cw|ccw`.
- Re-run and diff against the previous result so I can see what changed.

**Done when:** "include Bear Rocks" and "avoid a specific trail" style requests work
on real examples.

## M8 — Second region (generality test)
- Add `regions/pemi.yaml` (Pemigewasset Wilderness, NH).
- Nothing Dolly Sods-specific should need to change in the code. Anything that does
  is a bug in the abstraction.

**Done when:** a Pemi loop generates and validates against CalTopo.

---

## Explicitly out of scope for Phase 1
Web or mobile UI, natural-language/LLM input, weather, permits, closures, water
data, offline tiles, user accounts, the TripOS integration, and social features.
Keep them in `docs/vision.md` until Phase 1 is done.

## Phase 2 (started 2026-10-13; Phase 1 complete)
Destination: web and phones, later the app stores, with offline use of saved trips
("plan online, carry offline"). Pieces, in order; each gets its own design and plan:

1. **API**: the engine as a FastAPI service. 2. **Web app**: MapLibre progressive web app,
   phone-first (pieces 1–2 designed together: `docs/superpowers/specs/2026-10-13-phase2-api-and-web-app-design.md`).
3. **Offline** (built 2026-10-15; `docs/superpowers/specs/2026-10-14-phase2-offline-design.md`): saved trips with map tiles, elevation, camps, GPS position.
4. **LLM layer**: turns "make day 2 shorter" into constraint changes, never into geometry.
5. **App-store packaging** (Capacitor), hosting, accounts.
6. **Alerts and permit metadata** with provenance.
