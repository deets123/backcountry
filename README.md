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

### API

`uv run backcountry serve --host 0.0.0.0` runs the engine as a local web service (the web app
in Phase 2 piece 2 uses it). Endpoints under `/api`: `regions`, `regions/{id}/trailheads`,
`regions/{id}/trails`, `regions/{id}/features?bbox=w,s,e,n`, `POST regions/{id}/generate`
(same settings as the command, as JSON), `runs/{id}`, `runs/{id}/candidates/{n}.gpx[?day=k]`,
`runs/{id}/candidates/{n}.geojson`. Runs are saved under `data/runs/`.

### Web app (phone or laptop)

One-time: `cd web && npm install && npm run build`. Then `uv run backcountry serve --host 0.0.0.0`
and open `http://<your-laptop-ip>:8000` on your phone (same Wi-Fi) or `http://localhost:8000` on the
laptop. Pick a trailhead on the map, set the limits, Generate, compare the candidates, open one,
download its GPX. Add it to your home screen for an app-style window.

Development: `uv run backcountry serve` in one terminal, `cd web && npm run dev` in another, open
http://localhost:5173. Tests: `npm test` (unit), `npm run test:e2e` (one end-to-end run against the API
on synthetic data; needs `npx playwright install chromium` once).

### Offline trips (phone)

Saving trips and showing your position need a secure (https) address. Plain `http://<laptop-ip>:8000`
works for planning only. The simplest secure address is Tailscale (free):

1. Install Tailscale on the laptop and the phone; sign in to the same account on both.
2. In the Tailscale admin console, turn on HTTPS certificates (DNS → HTTPS Certificates). Once.
3. On the laptop: `uv run backcountry serve`, then `tailscale serve --bg 8000`.
4. On the phone, with Tailscale on, open `https://<laptop-name>.<tailnet>.ts.net` (the address
   `tailscale serve` prints). Add it to your home screen.

Then: open a trip and tap **Save for offline** (on Wi-Fi; the 19 mi Bear Rocks loop is about 1,400 map tiles, 30 MB, and took 1.5 minutes).
With no signal, the app opens on **Saved trips**. In a saved trip, **Show my location** gives your mile,
day, and distance and climb to the next camp; drag a camp marker along the route (or tap **Camp here**)
to move a camp, and the days are recomputed on the phone. Saved trips can be deleted from the list.
Map tiles: USGS The National Map (public domain).

Other commands: `route --from A --to B` (shortest point-to-point), `trailheads` (candidate
trailheads from the data), `qa` (data quality report and map), `reference` (export a trail
for a CalTopo comparison). `uv run backcountry --help` lists them all.

## Regions

Two regions exist: `regions/dolly_sods.yaml` (Dolly Sods Wilderness, WV) and `regions/pemi.yaml`
(Pemigewasset Wilderness, NH). A region is a bounding box, a projected CRS, and a trailhead list;
nothing region-specific lives in the code. To add one, copy a yaml, adjust it, and run `ingest`,
`dem`, and optionally `verify` for it.

## Development

`uv run pytest` runs the tests; `npm test` and `npm run test:e2e` in `web/` run the web app's unit and browser
tests. GitHub Actions (`.github/workflows/tests.yml`) runs all three on every pull request and push to `main`;
tests that need the cached OSM/elevation data skip there, so run `uv run pytest` locally too before merging. See `CLAUDE.md` for the project rules, `docs/ROADMAP.md` for
the plan, and `PROGRESS.md` for what's done, what's known to be wrong, and the decisions made.

Trail data © OpenStreetMap contributors, ODbL. Elevation: USGS 3D Elevation Program.
Cross-check data: USDA Forest Service Enterprise Data Warehouse. Base maps: USGS The National Map.

## License

MIT (see `LICENSE`). The trail data the tool downloads is © OpenStreetMap contributors under
the ODbL, which is separate from this code license; keep the attribution the outputs carry.
