# Phase 2, pieces 1–2: API and web app

Date: 2026-10-13. Status: approved in conversation; implementation plan to follow.

## Goal

Make the Phase 1 engine usable from a browser on a laptop and a phone: pick a trailhead on a
map, set the trip limits, compare the candidate loops, look at one trip's days, camps and
features, and download its GPX. This is the first of six Phase 2 pieces and the base for the
rest (offline, LLM layer, app-store packaging, accounts/hosting, alerts/permits).

## Intent and constraints (agreed)

- Destination: web and phones, later the app stores, with offline use of saved trips.
- Offline model: **plan online, carry offline**. Generating or changing a trip needs the server;
  saved trips, maps, days, camps and GPS position must work without signal (piece 3).
- The engine stays the single source of routes. The app draws only what the API sends and never
  invents geometry. Every CLAUDE.md rule (provenance, "unknown", no "safe") applies to API
  responses and screens.
- First versions run on the user's laptop; the phone connects over home Wi-Fi. Hosting and
  accounts come with piece 5.
- Units: miles and feet everywhere the user reads.

## Non-goals (this piece)

Accounts, hosting, offline storage, the chat/LLM box, app-store packaging, alerts/permits,
editing geometry by hand, elevation data other than the cached DEM.

## Approach

One web app that becomes the phone app: a FastAPI service wrapping the engine, and a
TypeScript + Vite + MapLibre GL JS progressive web app. Capacitor wraps the same web app for
the stores later. Chosen over server-rendered pages (no path to offline or stores) and a native
app (two front ends, slower to first use).

## Layout

```
api/backcountry_api/      FastAPI app: routers, request/response models, run storage
web/                      Vite + TypeScript app: src/ (map, panel, state, api client), public/
src/backcountry/          engine, unchanged; the API imports it like cli.py does
data/runs/<run_id>.json   saved runs (gitignored)
```

`uv run backcountry serve [--host 0.0.0.0 --port 8000]` starts the API and serves the built web
app; in development `npm run dev` in `web/` proxies `/api` to it. The phone opens
`http://<laptop-ip>:8000`.

## API

All responses are JSON. Every run/candidate response carries `data_sources` (name, retrieval
date, attribution) and `known_gaps` (the fixed list: water, campsites, permits, closures, trail
conditions, trailhead access). Errors are `{"error": "<the same wording the CLI prints>"}` with
400 for bad requests, 404 for unknown ids, 500 only for real failures.

| Method, path | Purpose |
|---|---|
| `GET /api/regions` | regions with caches present: id, name, bbox, what's cached (OSM date, DEM, USFS) |
| `GET /api/regions/{id}/trailheads` | yaml trailheads (id, name, node, lat, lon, verified, usfs note) plus candidate trailheads from the data (`source: "data"`, unverified) |
| `GET /api/regions/{id}/trails` | the routable graph as GeoJSON (edges with name, highway, is_road, source), cached per region |
| `GET /api/regions/{id}/features?bbox=w,s,e,n` | camp sites and water features in view, with evidence (tags, last edit, version, OSM url) |
| `POST /api/regions/{id}/generate` | body: `GenerateRequest` (below); returns a `Run` |
| `GET /api/runs/{run_id}` | a saved `Run` |
| `GET /api/runs/{run_id}/candidates/{n}.gpx` | the candidate's GPX, bytes identical to the CLI's `route.gpx` for the same request; `?day=k` gives `day_k.gpx` |
| `GET /api/runs/{run_id}/candidates/{n}.geojson` | the candidate's GeoJSON |

`GenerateRequest` mirrors `generate`: `trailhead` (yaml id, node id or "lat,lon"), `total: [lo, hi]`,
`nights`, `daily: [lo, hi]`, `max_gain_day`, `type` (loop/lollipop/any), `count`, `no_roads`,
`road_weight`, `repeat_weight`, `camps: [spec]`, `camps_at_sites`, `must_visit: [spec]`,
`avoid: [spec]`, `direction`. Validation errors name the field and reuse the CLI wording.

`Run`: `run_id`, `region`, `request`, `generated_at`, `search` (the engine report minus candidates:
loops found, pairs tried, seconds, widened, truncated, notes, must-visit notes, avoided edges),
`candidates: [Candidate]`, `data_sources`, `known_gaps`, and `diff` (against the previous run with
the same region+trailhead, same content as `diff.md`, or null).

`Candidate`: `index`, `kind`, `length_m`, `gain_ft`, `loss_ft`, `road_m`, `last_resort_m`,
`repeated_m`, `penalty`, `segments` (label, highway, is_road, last_resort, length_m, sources),
`geometry` (GeoJSON LineString of the whole walk), `pieces` (one per day: name, GeoJSON
LineString with elevations, length_m, gain_ft, loss_ft), `waypoints` (start/end/camps/junctions
with lat, lon, ele, kind, description), `plan` (days, camps with nearby sites and nearest water,
ok, violations, notes) or null, `features` (along-route list with evidence) and `profile`
(d_m, z_m) for the elevation chart. Numbers stay in meters/feet as the engine produces them;
the app formats miles.

Generation is synchronous (5–15 s today). If a region ever needs longer, `generate` returns a
job id and `GET /api/jobs/{id}` polls; the app's progress state is written for that from the
start so the change is server-only.

## Web app

One page, three states, map always visible. Phone-first: the panel is a bottom sheet with a
drag handle (collapsed, half, full); on wide screens it docks to the left.

1. **Plan.** Region picker (if more than one). Trails drawn from `/trails` (thin, muted), trailheads
   as markers; tap one or pick from a list. Settings: nights, total range, daily range, gain cap,
   shape, roads allowed, direction, count; optional must-visit and avoid (tap a trail on the map
   to add its name, or type). Generate button; progress text from the request until the run
   returns.
2. **Compare.** Candidates drawn in distinct colors; a horizontal card strip: distance, gain/loss,
   road miles, days OK (yes/NO), penalty, trail names in order. Tapping a card highlights that
   loop and dims the others. Search notes and must-visit notes appear as sentences above the cards.
   A "what changed" line opens the diff when the run has one.
3. **Trip.** The chosen candidate: days in their colors, S/E and camp badges, junction dots (toggle),
   per-day table, elevation profile (SVG, day boundaries marked, distance in miles, feet on the
   axis), features as dots whose popup shows evidence and the satellite/OSM links, "Known gaps"
   block always visible at the bottom of the sheet. Buttons: Download GPX (trip / one day),
   Re-plan (returns to Plan with the request filled in), Save for offline (disabled, labeled
   "coming").

Base maps: USGS Topo (default), Imagery + Topo, Imagery, from The National Map. No OSM tile
servers. Attribution shown on the map.

App state is one object (region, trailhead, request, run, selected candidate, ui) in a small
store; components render from it. The last region/trailhead/request persist in `localStorage`.
No framework beyond Vite + TypeScript + MapLibre; if components get unwieldy, Preact is the
fallback, decided then.

## Storage

- Runs: `data/runs/<run_id>.json` (full `Run`), written by `generate`, read by the `runs` endpoints.
  GPX/GeoJSON are produced from the stored run on request. A run id is a timestamp plus a short
  random suffix.
- Region caches: unchanged (`data/raw`, `data/processed`); the API loads a region's graph once
  per process and keeps it in memory.
- Browser: `localStorage` for last settings only. Offline trip storage (IndexedDB) is piece 3; the
  `Run` shape is what will be stored, so nothing is reformatted later.

## Errors and honesty

- Engine messages reach the screen verbatim: dead-end-spur hint, widened range, pinned camp off
  route, unreachable must-visit, constraint violations per day, elevation unknown.
- A failed generation shows the reason in the panel; the map keeps the last good state.
- Water and camp-site warnings and the Known gaps block are part of every trip view and cannot
  be dismissed.
- The API validates with the same functions the CLI uses (`_parse_range`, `resolve_point`,
  `avoided_keys`, `_must_spec` move into a shared module so both call the same code).

## Testing

- API (pytest, FastAPI TestClient) on the synthetic grid: every endpoint; validation errors;
  run persistence and reload; GPX download equals the CLI's bytes for the same request and
  validates against the GPX 1.1 schema; features endpoint returns evidence fields.
- Web (Vitest): request ↔ form mapping, candidate card and day table formatting (miles/feet),
  store transitions. One Playwright smoke test against the test API: load, pick trailhead,
  generate on the grid, see cards, download a GPX.
- Manual acceptance on the user's phone over Wi-Fi: plan a Dolly Sods trip, compare, open a trip,
  download the GPX; the file matches the CLI's for the same request.

## Done when

A Dolly Sods trip can be planned from a phone on home Wi-Fi: trailhead picked on the map,
settings entered, three candidates compared, one opened with days and camps, GPX downloaded and
byte-identical to the CLI's output for the same request. All tests pass; the CLI is unchanged.

## Later pieces (not designed here)

3. Offline: save a trip (Run + tiles for its bbox at a few zooms from The National Map + DEM
   profile) to IndexedDB; service worker; GPS position along the route.
4. LLM layer: a chat box that turns requests into `GenerateRequest` changes and explains runs
   from their numbers; never produces geometry.
5. App-store packaging (Capacitor), hosting, accounts.
6. Alerts and permit metadata with provenance.

## Decisions log

- Approach A (PWA + Python API) over server pages or native: only A reaches offline and the
  stores without a rebuild.
- Synchronous generation now; job polling is a server-side change later.
- Runs as JSON files, not a database, until multi-user hosting.
- TypeScript without a UI framework to start, matching the project's plain-code preference.
