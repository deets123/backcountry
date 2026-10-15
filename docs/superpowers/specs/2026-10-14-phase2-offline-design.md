# Phase 2 piece 3: Offline trips (design)

Status: approved in conversation 2026-10-14. Builds on
`docs/superpowers/specs/2026-10-13-phase2-api-and-web-app-design.md` (pieces 1–2, merged).

## Intent and constraints (agreed)

- **Plan online, carry offline.** Generating or re-planning a route needs the laptop server. A
  *saved* trip works with no signal: map (Topo and Imagery+Topo), route, days, camps, elevation
  profile, feature evidence, GPX downloads, and the user's GPS position on the route.
- Success: save the Bear Rocks trip at home, put the phone in airplane mode, force-quit the browser,
  reopen: the trip, its map and the GPS line work, and a camp can be moved.
- A user saves one to a few trips, not whole regions.
- Every CLAUDE.md rule holds offline: no invented geometry (camps move only along the saved route,
  GPS snaps only to it, unsaved map areas stay blank), "unknown" where data has no answer, no
  "safe", provenance and dates on saved data, miles and feet.

## Decisions (with the user)

| Question | Decision |
|---|---|
| HTTPS for the phone (service workers and geolocation need a secure context) | **Tailscale**: `tailscale serve --bg 8000` publishes `backcountry serve` at `https://<laptop>.<tailnet>.ts.net`. A README setup step; no code depends on it. `localhost` keeps working for development. |
| Map coverage per trip | **Trip bounding box plus 0.5 mi margin, zoom 12–16, USGS Topo and USGS Imagery+Topo.** Measured for Bear Rocks 19 mi: ~380 tiles per map, ~4 MB Topo + ~12 MB Imagery+Topo. |
| GPS | **Dot plus where-am-I on the route**, while the app is open. No background recording. |
| Camp changes offline | **Move a camp along the saved route**; the phone recomputes days. No offline re-plan of the route or of the sites-only optimizer. |
| Storage approach | **A: hand-written service worker + Cache Storage for tiles + IndexedDB for trip records.** No new runtime dependencies. |

Tile terms: USGS National Map data is public domain (attribution requested, already shown); the
Topo, ImageryTopo and ImageryOnly MapServers report `exportTilesAllowed: true` with
`maxExportTilesCount: 100000` (checked 2026-10-14). Saving a trip box tile by tile, a few at a
time, is within those terms (CLAUDE.md rule 6).

## Architecture

```
phone (PWA)                                              laptop
┌──────────────────────────────────────────────┐         ┌────────────────────────┐
│ app.ts ── views: plan / compare / trip / saved│  HTTPS  │ backcountry serve      │
│   │            (Tailscale)                   │ ◄─────► │ (unchanged API + web)  │
│   ├─ offline/save.ts ── tiles.ts (pure)      │         └────────────────────────┘
│   │      │  downloads tiles from USGS ──────────────────► basemap.nationalmap.gov
│   │      ├─► Cache Storage  trip-<id>  (tiles)│
│   │      └─► IndexedDB  trips (record, GPX, trails)
│   ├─ gps.ts (pure)  resplit.ts (pure)        │
│   └─ map.ts (+ setPosition, draggable camps) │
│ public/sw.js: shell cache; tiles cache-first │
└──────────────────────────────────────────────┘
```

The server does not change. Everything saved comes from responses the API already returns.

## Components

### `web/public/sw.js` (service worker, hand-written)
- `install`: caches the app shell (`/`, `index.html`, the built JS/CSS from a manifest Vite writes,
  `icon.svg`, `manifest.webmanifest`) under `shell-<version>`.
- `fetch`:
  - Tile URLs (`basemap.nationalmap.gov/.../tile/{z}/{y}/{x}`): look in every `trip-*` cache first,
    then the network; never written to a cache by the worker (only `save.ts` writes tiles).
  - `/api/*`: network only, never cached.
  - Shell requests: cache first, network fallback; navigation requests fall back to cached
    `index.html`.
- `activate`: deletes old `shell-*` caches only (never `trip-*`).
- Update: the page registers the worker; when a new worker is waiting, a banner says
  "Update available: reload". The app never reloads on its own.

### `web/src/offline/tiles.ts` (pure)
- `tripBox(c: Candidate, marginM = 805)`: the bbox of the candidate's geometry plus margin, in
  lon/lat.
- `tileList(box, zooms = [12..16], basemaps = ["topo", "hybrid"])`: tile URLs (Web Mercator
  x/y/z, the same `{z}/{y}/{x}` template as `basemaps.ts`).
- `estimate(tiles)`: count and an approximate byte size (Topo ~10 KB, Imagery+Topo ~31 KB per tile,
  from the 2026-10-14 measurement), shown as "about".

### `web/src/offline/save.ts`
- `saveTrip(run, n, opts)`: shows the estimate, then:
  1. Downloads tiles 4 at a time into cache `trip-<runId>-<n>`; one retry per failed tile; progress
     callback `(done, total, failed)`. An `AbortSignal` cancels; a cancel deletes the partial cache
     and record.
  2. Fetches the trip's GPX (whole trip and each day) and stores the exact bytes.
  3. Fetches `/trails` for the region and keeps only features intersecting the trip box.
  4. Calls `navigator.storage.persist()` and records whether it was granted.
  5. Writes the trip record (below). `complete` is true only if every tile, GPX file and the trails
     subset were saved; otherwise the missing tiles are listed by zoom.
- Storage errors (`QuotaExceededError`) stop the download and keep what was saved as an
  incomplete trip, with the message "Storage full: saved 410 of 760 tiles; the trip is incomplete."

### `web/src/offline/db.ts` (IndexedDB, no library)
- Database `backcountry`, store `trips`, key `id` (`<runId>-<n>`).
- Record: `{ id, savedAt, run, index, campOverrides: number[] | null, gpx: {whole, days[]},
  trails: TrailFeatureCollection, tiles: {cache, total, saved, missingByZoom}, bytes, persisted,
  complete }`. The full `Run` is stored, so data sources, known gaps, features and the profile come
  with it.
- `listTrips()`, `getTrip(id)`, `putTrip(rec)`, `deleteTrip(id)` (also deletes the tile cache),
  `storageUsed()` (`navigator.storage.estimate()`).

### `web/src/gps.ts` (pure)
- `locate(c: Candidate, profile, pos: {lon, lat, accuracyM}, lastMileM: number | null)` returns
  `{ alongM, offM, day, toCamp: {index, distM, gainFt|null} | null, ambiguous, accuracyFt }` or an
  off-route result.
- Snaps to the nearest point on the route polyline (pieces in order, distances in meters on a local
  equirectangular projection around the trip, which is accurate to well under a foot over a trip
  box). Where the route passes the same place twice (loop start/end, a lollipop stem), several
  candidate points tie within 50 ft: the one nearest `lastMileM` wins and `ambiguous` is set
  ("route passes here twice").
- Gain to the next camp uses the saved profile with the engine's rule: 3-sample centered moving
  average, then cumulative gain with 2 m hysteresis (`elevation.smooth`, `cumulative_gain_loss`),
  so numbers agree with the days table.
- Off route: `offM > 400` (≈ 0.25 mi) returns "off route: 0.4 mi from the nearest point on the
  route" and no mile/day numbers. Accuracy over 300 ft is reported as "position rough".

### `web/src/resplit.ts` (pure)
- `resplit(c: Candidate, campsAlongM: number[], limits)`: new `Day[]` (length, gain, loss, labels)
  from the saved profile with the same smoothing/hysteresis rule, plus violations worded exactly as
  the engine's `split_route` (daily range, gain cap). Camps keep their order and stay strictly
  inside the route.
- Camp lat/lon for a moved camp is interpolated on the saved route geometry; nearby known sites and
  the nearest water are looked up from the trip's saved `features` (within the engine's 500 m
  radius), still labeled "location unverified" / "presence unknown".

### `web/src/panels/saved.ts`
- Saved trips view: one card per trip ("Bear Rocks loop, 19.0 mi · saved Oct 14 · 16 MB ·
  complete/incomplete · storage kept/may be cleared"), Open, Delete, total storage used.

### Changes to existing modules
- `panels/trip.ts`: the Save for offline button becomes real (estimate, progress, cancel, result);
  a locate button and the GPS line; for saved trips, "Camp here", "Reset camps", "camps moved by
  you", the saved-data line ("Saved Oct 14 · trail data retrieved Sep 29 · map tiles from USGS,
  Oct 14"), and "map not saved here" when the view leaves the saved box or zoom range. GPX links
  serve the stored bytes for saved trips; with moved camps the per-day GPX is labeled "reflects the
  original camps".
- `app.ts`: `saved` view; on boot, if `/api/regions` fails, open Saved with "Offline: showing saved
  trips. Planning needs the server."; register the service worker; GPS watch on/off.
- `map.ts`: `setPosition(pos | null)` (dot plus accuracy circle), `setCampDrag(enabled, onMove)`
  (camp markers draggable, snapped to the route line on drop), profile marker via the trip panel.
- `store.ts`: `savedTrips`, `position`, `campOverrides` in state (not persisted to localStorage).

## Errors and honesty

- Save failures are visible: storage full, missing tiles by zoom, GPX or trails not saved; the trip
  is marked incomplete everywhere it appears.
- Saved data carries dates: when saved, when the OSM data was retrieved, tile source and date.
  Known gaps and water/campsite warnings show on every saved trip. Nothing claims current
  conditions.
- Location: permission denied or no fix shows "Location unavailable: <reason>"; the app works
  without GPS. Accuracy is shown in feet.
- No geometry is invented: camps move only along the saved route, GPS snaps only to it, unsaved
  map areas are blank.

## Testing

- Vitest:
  - `tiles.ts`: known tile x/y for fixed points at z12–16, counts for a fixed box, margin, both
    basemaps.
  - `gps.ts`: on route, off route (threshold), loop start/end tie broken by `lastMileM`, stem walked
    both ways, unknown elevation → gain null, accuracy wording.
  - `resplit.ts`: moving one camp changes only the two adjacent days; lengths sum to the route
    length; gain/loss sums; violation wording; unknown elevation.
  - **Parity with the engine:** a pytest writes a fixture (a stored run's profile, camp positions
    and the engine's `split_route` days for those positions) to `web/src/fixtures/`; a Vitest test
    checks `resplit` reproduces those days (miles to 0.01 mi, feet to 1 ft).
  - `db.ts` with `fake-indexeddb` (dev-only dependency, justified in its commit).
  - `panels/saved.ts` and the trip panel's saved-trip states.
- Playwright (synthetic API): tile requests routed to a local stub (no USGS traffic in tests); save
  a trip → `context.setOffline(true)` → reload → app opens on Saved → open the trip, map tiles load
  from cache → a mocked geolocation gives the expected mile line → drag a camp, days table updates
  → delete clears the record and cache.
- Manual acceptance on the user's phone over Tailscale (see Success above).

## Out of scope

Background or recorded GPS tracks, offline re-planning, saving whole regions, native packaging
(piece 5), accounts and sync between devices, regenerating GPX on the phone.

## Done when

The success scenario passes on the user's phone; all Vitest, Playwright and pytest suites pass;
the server and CLI are unchanged.
