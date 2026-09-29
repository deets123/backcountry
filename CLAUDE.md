# Backcountry Route Engine — Project Instructions

## What this is
A Python engine that generates multi-day backpacking routes from constraints
("2 nights, 18–26 mi loop, 6–11 mi/day, under 2,500 ft gain/day, from this trailhead")
using real trail geometry, then exports GPX for Garmin/CalTopo.

It is a personal tool first. The user is an experienced software engineer and
backpacker who will validate every route against CalTopo and his own trip knowledge.
Future phases may add a web map and friends as testers, but NOT yet.

Current scope: **one region (Dolly Sods Wilderness, WV), CLI only.**
Follow `docs/ROADMAP.md` milestone by milestone.

## Non-negotiable rules
1. **Never invent geometry.** Every route edge must come from source data (OSM way IDs).
   If the graph is disconnected, report the gap. Never draw a connecting line.
2. **Never invent facts** about water, campsites, permits, closures, trail conditions,
   or trailhead access. Not in code, comments, fixtures, or output text.
   If a fact isn't in a sourced dataset, the output says "unknown".
3. **Never call a route "safe."** Output describes distance, gain, and data gaps only.
4. **Provenance on everything.** Each edge carries its source (e.g. `osm:way/12345`)
   and `retrieved_at`. Each generated route lists its data sources and known gaps.
5. **No hardcoded real-world coordinates** for trailheads or waypoints unless they come
   from source data or from `regions/*.yaml` entries the user has marked `verified: true`.
6. **Respect data terms.** OSM data is ODbL: keep attribution. Use the Overpass API
   politely (cache raw responses; don't re-query on every run). Do not bulk-download or
   cache map tiles from tile servers that prohibit it.
7. **Distances in miles, elevation in feet** for all user-facing output. Compute
   internally in meters in a projected CRS (Dolly Sods: EPSG:32617 / UTM 17N).

## Tech stack
- Python 3.11+, managed with `uv`
- `osmnx` + `networkx` for fetching and graph building
- `geopandas`, `shapely`, `pyproj` for geometry
- `py3dep` (USGS 3DEP, ~10 m DEM) or `rasterio` for elevation
- `gpxpy` for GPX export, `folium` for an HTML preview map
- `typer` for the CLI, `pytest` for tests
- Do not add other dependencies without explaining why in the commit message.

## Repo layout
```
backcountry/
  CLAUDE.md
  PROGRESS.md            # you update this after every milestone
  docs/ROADMAP.md
  docs/vision.md         # long-term product ideas: context only, do NOT build from it
  regions/dolly_sods.yaml
  src/backcountry/
    ingest.py            # fetch + cache OSM trail data
    graph.py             # build/QA the trail graph
    elevation.py         # DEM sampling, gain/loss per edge
    loops.py             # candidate loop generation
    split.py             # day splitting
    scoring.py           # objective function
    export.py            # GPX, GeoJSON, HTML map, text summary
    cli.py
  data/raw/              # cached source data (gitignored), with retrieval timestamps
  data/processed/        # built graphs (gitignored)
  out/                   # generated routes (gitignored)
  tests/
    reference/           # user-supplied reference GPX tracks
```

## How to work
- Do **one milestone at a time.** At the end of each milestone, stop, run the
  acceptance checks, show me the output, and update `PROGRESS.md` with what was done,
  what's known to be wrong, and open questions.
- Commit at each milestone with a clear message.
- Prefer simple, inspectable code over clever code. I need to debug routes by hand.
- When the data is ambiguous (odd OSM tags, weird junctions), log it to a QA report
  instead of silently guessing.
- Write tests for invariants, not just happy paths: loops are closed, connected,
  use only real edges, and stay within the requested ranges.
- If a milestone turns out to be harder than planned, say so and propose a simpler
  version rather than building a sprawling one.
