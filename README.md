# Backcountry route engine

Generates multi-day backpacking loops from real trail data (OpenStreetMap), with elevation
from USGS, split into days with camp points, exported as GPX for CalTopo or Garmin.

Every route is assembled from real trail segments; nothing is drawn. Water, campsites,
permits, closures and trail conditions are **not checked**: the outputs list map features
with their sources and say "unknown" everywhere else. Nothing here says a route is safe.

## Running it

All commands run from this folder in a terminal. `uv` installs everything on first use.

One-time setup per region (cached afterwards; re-run with `--refresh` to update):

```
uv run backcountry ingest --region dolly_sods     # trail data from OpenStreetMap
uv run backcountry dem --region dolly_sods        # ~10 m elevation data from USGS 3DEP
uv run backcountry verify --region dolly_sods     # optional: cross-check against USFS trail data
```

Generate a trip:

```
uv run backcountry generate --region dolly_sods --trailhead bear_rocks \
    --nights 2 --total 18-26 --daily 6-11 --max-gain-day 2500
```

| Option | Meaning |
|---|---|
| `--trailhead` | an id from `regions/<region>.yaml`, an OSM node id, or `lat,lon` |
| `--total 18-26` | total loop distance range, miles |
| `--nights 2` | split into nights+1 days (needs `--daily`) |
| `--daily 6-11` | per-day distance range, miles |
| `--max-gain-day 2500` | per-day climbing cap, feet |
| `--type loop|lollipop|any` | loop shape (default any) |
| `--camps-at-sites` | camps only at known campsites on the route |
| `--camp 8.5` / `--camp lat,lon` | pin a camp (repeatable); the planner places the rest |
| `--must-visit X` | a spot or trail the loop must include (trailhead id, node id, `lat,lon`, or trail name); repeatable |
| `--avoid X` | a trail/road name or way id to stay off; repeatable |
| `--direction cw|ccw` | walk the loop clockwise or counterclockwise |
| `--no-roads`, `--road-weight N` | forbid roads, or make a road mile cost N trail miles |
| `--basemap topo|hybrid|satellite` | map background |
| `--count 3` | how many candidates |

Results go to `out/loops/<trailhead>_<total>mi/`:

- `candidates.md`, `candidates_map.html`: all candidates side by side
- `candidate_<i>/route.gpx`: one track per day plus Start/End/camp/junction waypoints (import this)
- `candidate_<i>/day_<n>.gpx`: one day per file
- `candidate_<i>/map.html`, `summary.md`, `route.geojson`
- `diff.md` when you re-run into the same folder: what changed since last time

Other commands: `route --from A --to B` (shortest point-to-point), `trailheads` (candidate
trailheads from the data), `qa` (data quality report and map), `reference` (export a trail
for a CalTopo comparison). `uv run backcountry --help` lists them all.

## Regions

Two regions exist: `regions/dolly_sods.yaml` (Dolly Sods Wilderness, WV) and `regions/pemi.yaml`
(Pemigewasset Wilderness, NH). A region is a bounding box, a projected CRS, and a trailhead list;
nothing region-specific lives in the code. To add one, copy a yaml, adjust it, and run `ingest`,
`dem`, and optionally `verify` for it.

## Development

`uv run pytest` runs the tests. See `CLAUDE.md` for the project rules, `docs/ROADMAP.md` for
the plan, and `PROGRESS.md` for what's done, what's known to be wrong, and the decisions made.

Trail data © OpenStreetMap contributors, ODbL. Elevation: USGS 3D Elevation Program.
Cross-check data: USDA Forest Service Enterprise Data Warehouse. Base maps: USGS The National Map.
