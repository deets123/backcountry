# Phase 2 piece 2: Web app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A phone-first web app (one page, map always visible) that plans a trip against the piece-1 API: pick a trailhead on the map, set the limits, compare the candidate loops, open one trip with days/camps/features, download its GPX.

**Architecture:** `web/` is a Vite + TypeScript app with MapLibre GL JS and no UI framework. One state object in `store.ts`; every panel renders from it and dispatches changes back. `api.ts` is a typed client for the piece-1 endpoints. `map.ts` owns the MapLibre instance and its layers (USGS raster base maps, trails, trailheads, candidates, trip days, waypoints, features). Panels (`plan.ts`, `compare.ts`, `trip.ts`) render HTML strings into a bottom sheet that docks left on wide screens. `profile.ts` draws the elevation chart as inline SVG. Vitest tests the pure parts; one Playwright smoke test runs the real app against the API on the synthetic grid. `backcountry serve` serves `web/dist`.

**Tech Stack:** Node 20+, Vite 6, TypeScript 5, maplibre-gl 5, Vitest 3 (+ jsdom), @playwright/test; the piece-1 API (FastAPI) on Python.

**Spec:** `docs/superpowers/specs/2026-10-13-phase2-api-and-web-app-design.md`

## Global Constraints

- The app draws only geometry the API sends; it never computes routes or snaps points itself (map taps send `lat,lon` to the API, which snaps).
- Every trip view shows the API's `known_gaps` and the water/camp-site "unknown" wording, not dismissable. No "safe" anywhere in the UI text.
- User-facing numbers: miles (2 decimals) and feet (no decimals, thousands separators); the API's `_m`/`_ft` fields are converted in `format.ts` only.
- Engine messages (`search.notes`, `search.must_visit_notes`, `plan.notes`, `plan.violations`, candidate `notes`, API `error`) are shown verbatim.
- Base maps: USGS The National Map raster tiles only (`USGSTopo`, `USGSImageryTopo`, `USGSImageryOnly`); attribution shown on the map; no OSM tile servers.
- No raw OSM ids in visible text (feature popups use readable labels and "OpenStreetMap" links, like the Markdown summaries).
- New runtime dependency: `maplibre-gl`. Dev: `vite`, `typescript`, `vitest`, `jsdom`, `@playwright/test`. Explain them in the commit message. `web/node_modules` and `web/dist` are gitignored.
- The Python side changes only in Task 8 (test API server script); the API's contract does not change.
- Keep each TS module under ~250 lines; split before it grows past that.

## Review Focus

1. The API is down or slow (phone on the wrong Wi-Fi, `serve` not running, a 20 s search): the app must show the reason and keep the last good state, never a blank map or a spinner forever. Tests in Task 2 (client errors) and Task 5 (status line, timeout text).
2. A region with no DEM: `gain_ft` is null everywhere; cards, day tables and the profile must say "unknown"/draw nothing rather than show `NaN ft`. Tests in Tasks 6 and 7.
3. A candidate with `plan: null` (no `--nights`): the Trip view must still work (one piece, no days table, no camps). Test in Task 7.
4. A yaml trailhead with `in_graph: false` or `lat: null`: it must appear in the list, disabled with the reason, and never be sent as a start. Test in Task 5.
5. Resizing between phone and laptop widths, and re-opening the page: the sheet state and the saved request must survive without duplicating map layers or listeners. Tests in Task 3 (persistence) and Task 8 (layout classes).

---

### Task 1: Scaffold `web/` with Vite, TypeScript, Vitest, and `format.ts`

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`, `web/public/manifest.webmanifest`, `web/public/icon.svg`, `web/src/main.ts`, `web/src/styles.css`, `web/src/format.ts`
- Test: `web/src/format.test.ts`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `format.ts`: `miles(m: number | null | undefined): string` ("7.39 mi" / "unknown"), `feet(ft: number | null | undefined): string` ("2,023 ft" / "unknown"), `feetFromMeters(m)`, `signedFeet(gain, loss)` ("+2,023 / −2,023 ft" or "unknown"), `M_PER_MI = 1609.344`, `FT_PER_M = 3.28084`.
- `npm run dev` (Vite on 5173, proxies `/api` to `http://127.0.0.1:8000`), `npm run build` (to `web/dist`), `npm test` (Vitest).

- [ ] **Step 1: Create the project files**

`web/package.json`:
```json
{
  "name": "backcountry-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:e2e": "playwright test"
  },
  "dependencies": {
    "maplibre-gl": "^5.6.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.55.0",
    "jsdom": "^26.1.0",
    "typescript": "^5.8.0",
    "vite": "^6.3.0",
    "vitest": "^3.2.0"
  }
}
```

`web/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "types": ["vite/client"],
    "skipLibCheck": true
  },
  "include": ["src", "e2e"]
}
```

`web/vite.config.ts`:
```ts
import { defineConfig } from "vite";

const api = process.env.BACKCOUNTRY_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: { proxy: { "/api": api }, host: true },
  preview: { proxy: { "/api": api }, host: true },
  build: { outDir: "dist", sourcemap: true },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
```

`web/index.html`:
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="theme-color" content="#1b5e20" />
    <link rel="manifest" href="/manifest.webmanifest" />
    <link rel="icon" href="/icon.svg" type="image/svg+xml" />
    <title>Backcountry</title>
  </head>
  <body>
    <div id="map" aria-label="Map"></div>
    <aside id="sheet" class="sheet sheet-half" aria-label="Trip planner">
      <button id="sheet-handle" class="sheet-handle" aria-label="Expand or collapse the panel"></button>
      <div id="panel" class="panel"></div>
    </aside>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

`web/public/manifest.webmanifest`:
```json
{
  "name": "Backcountry",
  "short_name": "Backcountry",
  "description": "Multi-day backpacking loops from real trail data",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#1b5e20",
  "icons": [{ "src": "/icon.svg", "sizes": "any", "type": "image/svg+xml" }]
}
```

`web/public/icon.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#1b5e20"/><path d="M8 50 L24 22 L34 38 L40 30 L56 50 Z" fill="#fff"/></svg>
```

`web/src/styles.css`:
```css
:root { --green: #1b5e20; --ink: #1b1b1b; --muted: #666; --line: #ddd; --sheet-w: 380px; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: var(--ink); }
#map { position: fixed; inset: 0; }
.sheet { position: fixed; left: 0; right: 0; bottom: 0; background: #fff; border-radius: 14px 14px 0 0;
  box-shadow: 0 -2px 12px rgba(0,0,0,.25); display: flex; flex-direction: column; z-index: 10;
  transition: height .2s ease; padding-bottom: env(safe-area-inset-bottom); }
.sheet-collapsed { height: 56px; } .sheet-half { height: 46vh; } .sheet-full { height: 92vh; }
.sheet-handle { width: 48px; height: 6px; border-radius: 3px; background: #bbb; border: 0; margin: 10px auto 6px; padding: 0; cursor: pointer; }
.panel { overflow: auto; padding: 0 16px 16px; flex: 1; }
@media (min-width: 900px) {
  .sheet, .sheet-collapsed, .sheet-half, .sheet-full { top: 0; bottom: 0; left: 0; right: auto; width: var(--sheet-w); height: auto; border-radius: 0; }
  .sheet-handle { display: none; }
}
h1 { font-size: 18px; margin: 4px 0 8px; } h2 { font-size: 15px; margin: 14px 0 6px; }
label { display: block; font-size: 13px; color: var(--muted); margin-top: 8px; }
input, select, button { font: inherit; } input, select { width: 100%; padding: 8px; border: 1px solid var(--line); border-radius: 6px; }
.row { display: flex; gap: 8px; } .row > * { flex: 1; }
button.primary { background: var(--green); color: #fff; border: 0; border-radius: 8px; padding: 12px; width: 100%; margin-top: 12px; font-weight: 600; }
button.link { background: none; border: 0; color: var(--green); padding: 4px 0; text-decoration: underline; cursor: pointer; }
.status { font-size: 13px; color: var(--muted); margin-top: 8px; min-height: 1.4em; } .error { color: #b00020; }
.note { font-size: 13px; color: var(--muted); } .warn { background: #fff8e1; border-left: 3px solid #f9a825; padding: 8px 10px; font-size: 13px; margin: 10px 0; }
.cards { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 6px; } .card { flex: 0 0 240px; border: 2px solid var(--line); border-radius: 10px; padding: 10px; cursor: pointer; }
.card.selected { border-color: var(--green); } .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; } td, th { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--line); } th { color: var(--muted); font-weight: 600; }
.profile { width: 100%; height: 140px; display: block; }
.maplibregl-popup-content { font: 13px/1.4 -apple-system, sans-serif; max-width: 320px; }
```

`web/src/format.ts`:
```ts
export const M_PER_MI = 1609.344;
export const FT_PER_M = 3.28084;

function isNum(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

export function miles(m: number | null | undefined): string {
  return isNum(m) ? `${(m / M_PER_MI).toFixed(2)} mi` : "unknown";
}

export function feet(ft: number | null | undefined): string {
  return isNum(ft) ? `${Math.round(ft).toLocaleString("en-US")} ft` : "unknown";
}

export function feetFromMeters(m: number | null | undefined): string {
  return isNum(m) ? feet(m * FT_PER_M) : "unknown";
}

export function signedFeet(gain: number | null | undefined, loss: number | null | undefined): string {
  return isNum(gain) && isNum(loss) ? `+${Math.round(gain).toLocaleString("en-US")} / −${Math.round(loss).toLocaleString("en-US")} ft` : "unknown";
}
```

`web/src/main.ts` (placeholder until Task 5 wires the app; it must render something so the smoke of `npm run dev` is visible):
```ts
import "./styles.css";

document.getElementById("panel")!.innerHTML = "<h1>Backcountry</h1><p class=\"note\">Loading…</p>";
```

Add to the repo root `.gitignore`:
```
web/node_modules/
web/dist/
web/test-results/
web/playwright-report/
```

- [ ] **Step 2: Write the failing test**

`web/src/format.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { feet, feetFromMeters, miles, signedFeet } from "./format";

describe("format", () => {
  it("formats miles and feet in user units", () => {
    expect(miles(1609.344 * 7.39)).toBe("7.39 mi");
    expect(feet(2023.4)).toBe("2,023 ft");
    expect(feetFromMeters(1000)).toBe("3,281 ft");
    expect(signedFeet(2023, 2023)).toBe("+2,023 / −2,023 ft");
  });
  it("says unknown instead of NaN", () => {
    expect(miles(null)).toBe("unknown");
    expect(feet(undefined)).toBe("unknown");
    expect(signedFeet(null, 5)).toBe("unknown");
  });
});
```

- [ ] **Step 3: Install and run the test to verify it fails, then passes**

Run: `cd web && npm install && npm test`
Expected: the first run fails only if `format.ts` is missing; with the file present, 2 tests pass. (If you wrote `format.ts` before running once, temporarily rename it, run to see the red, restore it: the point is to see the test can fail.)

- [ ] **Step 4: Check the toolchain end to end**

Run: `cd web && npm run build && ls dist/index.html`
Expected: `dist/index.html` exists. Then from the repo root: `uv run pytest -q tests/test_api.py -k static` — still passes (the Python side mounts `web/dist` when present).

- [ ] **Step 5: Commit**

```bash
git add .gitignore web/package.json web/package-lock.json web/tsconfig.json web/vite.config.ts web/index.html web/public web/src
git commit -m "web: Vite + TypeScript scaffold, format helpers

New dependencies (web/): maplibre-gl (the map), vite + typescript (build),
vitest + jsdom (unit tests), @playwright/test (one end-to-end smoke test)."
```

---

### Task 2: Typed API client

**Files:**
- Create: `web/src/types.ts`, `web/src/api.ts`
- Test: `web/src/api.test.ts`

**Interfaces:**
- `types.ts` mirrors the API exactly (names below are the API's JSON keys): `Region`, `Trailhead`, `TrailFeatureCollection`, `FeaturesInView`, `GenerateRequest`, `Run`, `Candidate`, `Piece`, `Waypoint`, `Segment`, `Plan`, `Day`, `Camp`, `RouteFeature`, `ApiError`.
- `api.ts`: `class ApiError extends Error { status: number; field?: string }`; `getRegions(): Promise<Region[]>`, `getTrailheads(region): Promise<Trailhead[]>`, `getTrails(region): Promise<TrailFeatureCollection>`, `getFeatures(region, bbox: [w,s,e,n]): Promise<FeaturesInView>`, `generate(region, req: GenerateRequest, signal?: AbortSignal): Promise<Run>`, `getRun(id): Promise<Run>`, `gpxUrl(runId, n, day?: number): string`, `geojsonUrl(runId, n): string`. Every non-2xx response with a JSON `{error, field?}` body becomes `ApiError(error, status, field)`; a non-JSON body or network failure becomes `ApiError("Can't reach the Backcountry server at /api …", 0)`.

- [ ] **Step 1: Write `types.ts`**

```ts
// web/src/types.ts — the piece-1 API's JSON shapes, field names as the API sends them.
export interface Region {
  id: string; name: string; crs: string;
  bbox: { south: number; west: number; north: number; east: number };
  osm_retrieved_at: string | null; dem: boolean; usfs: boolean; trailheads: number;
}

export interface Trailhead {
  id: string; name: string | null; node: number | null; source: "yaml" | "data"; verified: boolean;
  in_graph: boolean; lat: number | null; lon: number | null; component: string | null;
  usfs?: string | null; note?: string | null; kinds?: string[]; snap_m?: number;
}

export interface TrailProps {
  name: string | null; highway: string; is_road: boolean; last_resort: boolean; source: string;
  length_m: number; component: string; key: string;
}
export interface TrailFeatureCollection {
  type: "FeatureCollection"; attribution: string;
  features: { type: "Feature"; geometry: { type: "LineString"; coordinates: [number, number][] }; properties: TrailProps }[];
}

export interface MapFeature {
  source: string; kind: string; name: string | null; lat: number; lon: number;
  tags: Record<string, string>; meta: { timestamp: string | null; version: number | null }; url: string | null;
}
export interface FeaturesInView {
  camp_sites: MapFeature[]; water_points: MapFeature[];
  streams: { source: string; kind: string; name: string | null; coords: [number, number][]; url: string | null }[];
}

export interface GenerateRequest {
  trailhead: string; total: string | [number, number]; nights?: number | null; daily?: string | [number, number] | null;
  max_gain_day?: number | null; type?: "loop" | "lollipop" | "any"; count?: number; no_roads?: boolean;
  road_weight?: number; repeat_weight?: number; camps?: string[]; camps_at_sites?: boolean;
  must_visit?: string[]; avoid?: string[]; direction?: "cw" | "ccw" | null;
}

export interface Segment { label: string; highway: string; is_road: boolean; last_resort: boolean; length_m: number; sources: string[] }
export interface Piece { name: string; points: [number, number, number | null][]; start_m: number; end_m: number; length_m: number; gain_ft: number | null; loss_ft: number | null; day: number | null }
export interface Waypoint { name: string; desc: string; lat: number; lon: number; dist_m: number; ele: number | null; kind: "start" | "end" | "camp" | "junction" }
export interface Day { index: number; start_m: number; end_m: number; length_m: number; gain_ft: number | null; loss_ft: number | null; start_label: string; end_label: string }
export interface NearbySite { source: string; kind: string; name: string | null; url?: string | null; distance_m: number }
export interface Camp {
  index: number; dist_m: number; lat: number; lon: number; on_source: string; on_name: string | null; nearby: NearbySite[];
  note: string; pinned: boolean; site: string | null;
  nearest_water: { source: string; kind: string; name: string | null; distance_m: number } | null;
}
export interface Plan {
  nights: number; daily_min_m: number; daily_max_m: number; max_gain_ft: number | null; days: Day[]; camps: Camp[];
  ok: boolean; violations: string[]; notes: string[]; penalty: number;
}
export interface RouteFeature extends MapFeature { dist_m: number; off_m: number; day?: number | null }
export interface Candidate {
  index: number; kind: "loop" | "lollipop"; length_m: number; gain_ft: number | null; loss_ft: number | null; road_m: number;
  last_resort_m: number; repeated_m: number; penalty: number; segments: Segment[];
  geometry: { type: "LineString"; coordinates: [number, number][] }; pieces: Piece[]; waypoints: Waypoint[];
  plan: Plan | null; features: RouteFeature[]; profile: { d_m: number[]; z_m: (number | null)[] } | null;
  title: string; description: string; notes: string[];
}
export interface Run {
  run_id: string; region: string; request: GenerateRequest & { total: [number, number]; daily: [number, number] | null };
  generated_at: string; start: { node: number; how: string; snap_m: number };
  search: { loops_found: number; lollipops_found: number; stems_tried: number; via_pairs_tried: number; scored: number;
    seconds: number; widened: boolean; truncated: boolean; avoided_edges: number; notes: string[]; must_visit_notes: string[] };
  candidates: Candidate[];
  data_sources: { name: string; retrieved_at: string | null; attribution: string }[];
  known_gaps: string[]; diff: string | null;
}
export interface ApiErrorBody { error: string; field?: string }
```

- [ ] **Step 2: Write the failing tests**

`web/src/api.test.ts`:
```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, generate, getRegions, gpxUrl } from "./api";

function respond(status: number, body: unknown, json = true) {
  return vi.fn(async () => new Response(json ? JSON.stringify(body) : String(body), {
    status, headers: { "content-type": json ? "application/json" : "text/plain" },
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("returns regions", async () => {
    vi.stubGlobal("fetch", respond(200, { regions: [{ id: "dolly_sods", name: "Dolly Sods" }] }));
    expect((await getRegions())[0]?.id).toBe("dolly_sods");
  });

  it("turns {error, field} into ApiError with the field", async () => {
    vi.stubGlobal("fetch", respond(400, { error: "total must look like 18-26", field: "total" }));
    await expect(generate("dolly_sods", { trailhead: "x", total: "5-2" })).rejects.toMatchObject({
      message: "total must look like 18-26", status: 400, field: "total",
    });
  });

  it("explains an unreachable server", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await getRegions().catch((e) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toMatch(/server/i);
  });

  it("explains a non-JSON error body", async () => {
    vi.stubGlobal("fetch", respond(502, "Bad Gateway", false));
    const err = await getRegions().catch((e) => e as ApiError);
    expect(err.status).toBe(502);
    expect(err.message).toMatch(/502/);
  });

  it("builds download urls", () => {
    expect(gpxUrl("r1", 2)).toBe("/api/runs/r1/candidates/2.gpx");
    expect(gpxUrl("r1", 2, 3)).toBe("/api/runs/r1/candidates/2.gpx?day=3");
  });
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd web && npm test`
Expected: FAIL, `./api` cannot be resolved.

- [ ] **Step 4: Write `api.ts`**

```ts
// web/src/api.ts — typed client for the piece-1 API. All errors become ApiError with the server's wording.
import type { ApiErrorBody, FeaturesInView, GenerateRequest, Region, Run, TrailFeatureCollection, Trailhead } from "./types";

export class ApiError extends Error {
  constructor(message: string, public status: number, public field?: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Can't reach the Backcountry server at /api. Is `backcountry serve` running on this network?", 0);
  }
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!res.ok) {
    const err = body as ApiErrorBody | null;
    throw new ApiError(err?.error ?? `The server answered ${res.status} without a reason.`, res.status, err?.field);
  }
  return body as T;
}

export async function getRegions(): Promise<Region[]> {
  return (await request<{ regions: Region[] }>("/api/regions")).regions;
}

export async function getTrailheads(region: string): Promise<Trailhead[]> {
  return (await request<{ trailheads: Trailhead[] }>(`/api/regions/${region}/trailheads`)).trailheads;
}

export function getTrails(region: string): Promise<TrailFeatureCollection> {
  return request(`/api/regions/${region}/trails`);
}

export function getFeatures(region: string, bbox: [number, number, number, number]): Promise<FeaturesInView> {
  return request(`/api/regions/${region}/features?bbox=${bbox.join(",")}`);
}

export function generate(region: string, req: GenerateRequest, signal?: AbortSignal): Promise<Run> {
  return request(`/api/regions/${region}/generate`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(req), signal,
  });
}

export function getRun(runId: string): Promise<Run> {
  return request(`/api/runs/${runId}`);
}

export function gpxUrl(runId: string, n: number, day?: number): string {
  return `/api/runs/${runId}/candidates/${n}.gpx${day ? `?day=${day}` : ""}`;
}

export function geojsonUrl(runId: string, n: number): string {
  return `/api/runs/${runId}/candidates/${n}.geojson`;
}
```

- [ ] **Step 5: Run the tests**

Run: `cd web && npm test`
Expected: 7 tests pass (2 format + 5 api).

- [ ] **Step 6: Commit**

```bash
git add web/src/types.ts web/src/api.ts web/src/api.test.ts
git commit -m "web: typed API client with the server's error wording"
```

---

### Task 3: Store (state, persistence, request mapping)

**Files:**
- Create: `web/src/store.ts`, `web/src/request.ts`
- Test: `web/src/store.test.ts`, `web/src/request.test.ts`

**Interfaces:**
- `request.ts`: `interface FormState { trailhead: string; total: string; nights: string; daily: string; maxGainDay: string; type: "loop"|"lollipop"|"any"; count: string; noRoads: boolean; direction: ""|"cw"|"ccw"; mustVisit: string[]; avoid: string[]; camps: string[]; campsAtSites: boolean }`; `DEFAULT_FORM`; `toRequest(form): { request: GenerateRequest } | { error: string; field: keyof FormState }` (the same rules as the API's `GenerateRequest.from_dict`, so the user hears about a typo before the round trip); `fromRun(run): FormState` (prefills Re-plan).
- `store.ts`: `type View = "plan" | "compare" | "trip"`; `interface State { regions: Region[]; region: string | null; trailheads: Trailhead[]; form: FormState; run: Run | null; selected: number; view: View; status: string; error: string | null; busy: boolean; basemap: "topo"|"hybrid"|"satellite"; sheet: "collapsed"|"half"|"full" }`; `createStore(initial?: Partial<State>)` returning `{ get(): State; set(patch: Partial<State> | ((s: State) => Partial<State>)): void; subscribe(fn: (s: State, prev: State) => void): () => void }`; `persist(store, storage = localStorage)` saves `region`, `form`, `basemap` under key `backcountry.v1` on every change and `loadPersisted(storage): Partial<State>` reads them back (bad JSON → `{}`).

- [ ] **Step 1: Write the failing tests**

`web/src/request.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_FORM, fromRun, toRequest } from "./request";
import type { Run } from "./types";

describe("toRequest", () => {
  it("maps a filled form to the API request", () => {
    const r = toRequest({ ...DEFAULT_FORM, trailhead: "bear_rocks", total: "18-26", nights: "2", daily: "6-11",
      maxGainDay: "2500", type: "loop", direction: "cw", mustVisit: ["Rocky Point Trail"], avoid: [], camps: ["8.5"] });
    expect("request" in r && r.request).toEqual({
      trailhead: "bear_rocks", total: "18-26", nights: 2, daily: "6-11", max_gain_day: 2500, type: "loop", count: 3,
      no_roads: false, camps: ["8.5"], camps_at_sites: false, must_visit: ["Rocky Point Trail"], avoid: [], direction: "cw",
    });
  });
  it("omits nights/daily/gain when blank", () => {
    const r = toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "18-26" });
    expect("request" in r && r.request.nights).toBeNull();
    expect("request" in r && r.request.daily).toBeNull();
  });
  it("names the field for a bad range, like the API does", () => {
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "26-18" })).toEqual({ error: expect.stringContaining("18-26"), field: "total" });
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "18-26", nights: "2" })).toMatchObject({ field: "daily" });
    expect(toRequest({ ...DEFAULT_FORM, total: "18-26" })).toMatchObject({ field: "trailhead" });
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "18-26", nights: "x", daily: "6-11" })).toMatchObject({ field: "nights" });
  });
});

describe("fromRun", () => {
  it("prefills the form from a run's request", () => {
    const run = { request: { trailhead: "bear_rocks", total: [18, 26], nights: 2, daily: [6, 11], max_gain_day: 2500, type: "loop",
      count: 3, no_roads: false, road_weight: 1, repeat_weight: 0.5, camps: [], camps_at_sites: true, must_visit: ["A"], avoid: ["B"], direction: null } } as unknown as Run;
    expect(fromRun(run)).toMatchObject({ trailhead: "bear_rocks", total: "18-26", nights: "2", daily: "6-11", maxGainDay: "2500",
      type: "loop", campsAtSites: true, mustVisit: ["A"], avoid: ["B"], direction: "" });
  });
});
```

`web/src/store.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { createStore, loadPersisted, persist } from "./store";

class MemoryStorage implements Storage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); } key(i: number) { return [...this.m.keys()][i] ?? null; }
  getItem(k: string) { return this.m.get(k) ?? null; } setItem(k: string, v: string) { this.m.set(k, v); } removeItem(k: string) { this.m.delete(k); }
}

describe("store", () => {
  it("notifies subscribers with previous and next state", () => {
    const s = createStore();
    const seen: string[] = [];
    s.subscribe((next, prev) => seen.push(`${prev.view}->${next.view}`));
    s.set({ view: "compare" });
    s.set((st) => ({ status: `was ${st.view}` }));
    expect(seen).toEqual(["plan->compare", "compare->compare"]);
    expect(s.get().status).toBe("was compare");
  });
  it("persists region, form and basemap only, and survives bad JSON", () => {
    const storage = new MemoryStorage();
    const s = createStore();
    persist(s, storage);
    s.set({ region: "pemi", basemap: "satellite", status: "ignored", run: {} as never });
    const saved = JSON.parse(storage.getItem("backcountry.v1")!);
    expect(saved.region).toBe("pemi"); expect(saved.basemap).toBe("satellite");
    expect(saved.status).toBeUndefined(); expect(saved.run).toBeUndefined();
    expect(loadPersisted(storage)).toMatchObject({ region: "pemi", basemap: "satellite" });
    storage.setItem("backcountry.v1", "{not json");
    expect(loadPersisted(storage)).toEqual({});
  });
  it("unsubscribe stops notifications", () => {
    const s = createStore();
    let n = 0;
    const off = s.subscribe(() => n++);
    s.set({ status: "a" }); off(); s.set({ status: "b" });
    expect(n).toBe(1);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npm test`
Expected: FAIL resolving `./request` and `./store`.

- [ ] **Step 3: Write `request.ts` and `store.ts`**

`web/src/request.ts`:
```ts
import type { GenerateRequest, Run } from "./types";

export interface FormState {
  trailhead: string; total: string; nights: string; daily: string; maxGainDay: string;
  type: "loop" | "lollipop" | "any"; count: string; noRoads: boolean; direction: "" | "cw" | "ccw";
  mustVisit: string[]; avoid: string[]; camps: string[]; campsAtSites: boolean;
}

export const DEFAULT_FORM: FormState = {
  trailhead: "", total: "18-26", nights: "2", daily: "6-11", maxGainDay: "2500", type: "any", count: "3",
  noRoads: false, direction: "", mustVisit: [], avoid: [], camps: [], campsAtSites: false,
};

type Result = { request: GenerateRequest } | { error: string; field: keyof FormState };

function range(text: string, field: keyof FormState): [number, number] | { error: string; field: keyof FormState } {
  const parts = text.split("-").map((p) => Number(p.trim()));
  const [lo, hi] = parts;
  if (parts.length !== 2 || lo === undefined || hi === undefined || !Number.isFinite(lo) || !Number.isFinite(hi) || !(0 < lo && lo < hi)) {
    return { error: `${field} must look like 18-26 (miles, low-high); got "${text}"`, field };
  }
  return [lo, hi];
}

function whole(text: string, field: keyof FormState, min: number): number | { error: string; field: keyof FormState } {
  const n = Number(text.trim());
  if (!Number.isInteger(n) || n < min) return { error: `${field} must be a whole number of at least ${min}`, field };
  return n;
}

export function toRequest(form: FormState): Result {
  if (!form.trailhead.trim()) return { error: "Pick a trailhead first.", field: "trailhead" };
  const total = range(form.total, "total");
  if (!Array.isArray(total)) return total;
  let nights: number | null = null;
  if (form.nights.trim()) {
    const n = whole(form.nights, "nights", 1);
    if (typeof n !== "number") return n;
    nights = n;
  }
  let daily: string | null = null;
  if (form.daily.trim()) {
    const d = range(form.daily, "daily");
    if (!Array.isArray(d)) return d;
    daily = form.daily.trim();
  }
  if (nights !== null && daily === null) return { error: "nights needs daily (e.g. 6-11)", field: "daily" };
  let maxGain: number | null = null;
  if (form.maxGainDay.trim()) {
    maxGain = Number(form.maxGainDay);
    if (!Number.isFinite(maxGain)) return { error: "max_gain_day must be a number of feet", field: "maxGainDay" };
  }
  const count = whole(form.count, "count", 1);
  if (typeof count !== "number") return count;
  return {
    request: {
      trailhead: form.trailhead.trim(), total: form.total.trim(), nights, daily, max_gain_day: maxGain, type: form.type, count,
      no_roads: form.noRoads, camps: form.camps, camps_at_sites: form.campsAtSites, must_visit: form.mustVisit, avoid: form.avoid,
      direction: form.direction || null,
    },
  };
}

function rangeText(v: string | [number, number] | null | undefined): string {
  return Array.isArray(v) ? `${v[0]}-${v[1]}` : v ?? "";
}

export function fromRun(run: Run): FormState {
  const r = run.request;
  return {
    trailhead: r.trailhead, total: rangeText(r.total), nights: r.nights == null ? "" : String(r.nights), daily: rangeText(r.daily),
    maxGainDay: r.max_gain_day == null ? "" : String(r.max_gain_day), type: r.type ?? "any", count: String(r.count ?? 3),
    noRoads: !!r.no_roads, direction: r.direction ?? "", mustVisit: r.must_visit ?? [], avoid: r.avoid ?? [], camps: r.camps ?? [],
    campsAtSites: !!r.camps_at_sites,
  };
}
```

`web/src/store.ts`:
```ts
import { DEFAULT_FORM, type FormState } from "./request";
import type { Region, Run, Trailhead } from "./types";

export type View = "plan" | "compare" | "trip";
export type Basemap = "topo" | "hybrid" | "satellite";

export interface State {
  regions: Region[]; region: string | null; trailheads: Trailhead[]; form: FormState; run: Run | null; selected: number;
  view: View; status: string; error: string | null; busy: boolean; basemap: Basemap; sheet: "collapsed" | "half" | "full";
}

export const INITIAL: State = {
  regions: [], region: null, trailheads: [], form: DEFAULT_FORM, run: null, selected: 1, view: "plan", status: "",
  error: null, busy: false, basemap: "topo", sheet: "half",
};

type Listener = (next: State, prev: State) => void;

export function createStore(initial: Partial<State> = {}) {
  let state: State = { ...INITIAL, ...initial };
  const listeners = new Set<Listener>();
  return {
    get: () => state,
    set(patch: Partial<State> | ((s: State) => Partial<State>)) {
      const prev = state;
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      listeners.forEach((fn) => fn(state, prev));
    },
    subscribe(fn: Listener) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
  };
}
export type Store = ReturnType<typeof createStore>;

const KEY = "backcountry.v1";

export function persist(store: Store, storage: Storage = localStorage): () => void {
  return store.subscribe((s) => {
    try { storage.setItem(KEY, JSON.stringify({ region: s.region, form: s.form, basemap: s.basemap })); } catch { /* storage may be unavailable */ }
  });
}

export function loadPersisted(storage: Storage = localStorage): Partial<State> {
  try {
    const raw = storage.getItem(KEY);
    if (!raw) return {};
    const saved = JSON.parse(raw) as Partial<State>;
    const out: Partial<State> = {};
    if (typeof saved.region === "string") out.region = saved.region;
    if (saved.form && typeof saved.form === "object") out.form = { ...DEFAULT_FORM, ...saved.form };
    if (saved.basemap === "topo" || saved.basemap === "hybrid" || saved.basemap === "satellite") out.basemap = saved.basemap;
    return out;
  } catch { return {}; }
}
```

- [ ] **Step 4: Run the tests**

Run: `cd web && npm test`
Expected: all pass (format 2, api 5, request 5, store 3).

- [ ] **Step 5: Commit**

```bash
git add web/src/request.ts web/src/request.test.ts web/src/store.ts web/src/store.test.ts
git commit -m "web: store with persistence; form <-> API request mapping"
```

---

### Task 4: Map module (MapLibre, USGS base maps, trails, trailheads, route layers)

**Files:**
- Create: `web/src/basemaps.ts`, `web/src/map.ts`
- Test: `web/src/basemaps.test.ts`

**Interfaces:**
- `basemaps.ts` (pure, tested): `BASEMAPS: Record<Basemap, { name: string; url: string }>` with the three USGS tile templates (`https://basemap.nationalmap.gov/arcgis/rest/services/<USGSTopo|USGSImageryTopo|USGSImageryOnly>/MapServer/tile/{z}/{y}/{x}`), `ATTRIBUTION` ("Base map: USGS The National Map | Trail data © OpenStreetMap contributors, ODbL"), `baseStyle(active: Basemap): StyleSpecification` (one raster source + raster layer per basemap, only `active` visible, `maxzoom: 16`), `CANDIDATE_COLORS = ["#d32f2f", "#1976d2", "#388e3c", "#f57c00", "#7b1fa2"]`, `candidateColor(i)`, `dayColor(i)` (same palette).
- `map.ts`: `createMap(container: HTMLElement, basemap: Basemap, center: [lon, lat], zoom: number): TripMap` where `TripMap` has: `setBasemap(b)`, `fitBounds(bbox)`, `setTrails(fc | null)`, `setTrailheads(ths: Trailhead[], onPick: (th) => void)`, `onMapTap(fn: (lon, lat) => void)`, `setCandidates(cands: Candidate[], selected: number, onSelect: (i) => void)`, `clearCandidates()`, `setTrip(c: Candidate | null, onFeature?: (f: RouteFeature) => string)` (day lines, waypoint badges, feature dots with popups from `onFeature`'s HTML), `destroy()`. Layer ids are constants; every `set*` removes its own previous layers/sources first so re-rendering never duplicates (Review Focus 5).

- [ ] **Step 1: Write the failing test**

`web/src/basemaps.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { ATTRIBUTION, BASEMAPS, baseStyle, candidateColor } from "./basemaps";

describe("basemaps", () => {
  it("uses only USGS National Map tiles with attribution", () => {
    for (const b of Object.values(BASEMAPS)) {
      expect(b.url).toMatch(/^https:\/\/basemap\.nationalmap\.gov\//);
      expect(b.url).not.toMatch(/openstreetmap/);
    }
    expect(ATTRIBUTION).toMatch(/OpenStreetMap contributors/);
  });
  it("builds a style with one visible raster layer", () => {
    const style = baseStyle("satellite");
    const layers = style.layers as { id: string; layout?: { visibility?: string } }[];
    expect(layers.map((l) => l.id).sort()).toEqual(["base-hybrid", "base-satellite", "base-topo"]);
    expect(layers.find((l) => l.id === "base-satellite")?.layout?.visibility).toBe("visible");
    expect(layers.find((l) => l.id === "base-topo")?.layout?.visibility).toBe("none");
    const src = (style.sources as Record<string, { maxzoom?: number; attribution?: string }>)["topo"];
    expect(src?.maxzoom).toBe(16);
    expect(src?.attribution).toBe(ATTRIBUTION);
  });
  it("cycles candidate colors", () => {
    expect(candidateColor(1)).toBe("#d32f2f");
    expect(candidateColor(6)).toBe("#d32f2f");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npm test -- basemaps`
Expected: FAIL resolving `./basemaps`.

- [ ] **Step 3: Write `basemaps.ts`**

```ts
import type { StyleSpecification } from "maplibre-gl";
import type { Basemap } from "./store";

const USGS = "https://basemap.nationalmap.gov/arcgis/rest/services";
export const BASEMAPS: Record<Basemap, { name: string; url: string }> = {
  topo: { name: "USGS Topo", url: `${USGS}/USGSTopo/MapServer/tile/{z}/{y}/{x}` },
  hybrid: { name: "USGS Imagery + Topo", url: `${USGS}/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}` },
  satellite: { name: "USGS Imagery (satellite)", url: `${USGS}/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}` },
};
export const ATTRIBUTION = "Base map: USGS The National Map | Trail data © OpenStreetMap contributors, ODbL";
export const CANDIDATE_COLORS = ["#d32f2f", "#1976d2", "#388e3c", "#f57c00", "#7b1fa2"];

export function candidateColor(i: number): string {
  return CANDIDATE_COLORS[(i - 1) % CANDIDATE_COLORS.length]!;
}
export const dayColor = candidateColor;

export function baseStyle(active: Basemap): StyleSpecification {
  const sources: StyleSpecification["sources"] = {};
  const layers: StyleSpecification["layers"] = [];
  for (const [key, b] of Object.entries(BASEMAPS) as [Basemap, { url: string }][]) {
    sources[key] = { type: "raster", tiles: [b.url], tileSize: 256, maxzoom: 16, attribution: ATTRIBUTION };
    layers.push({ id: `base-${key}`, type: "raster", source: key, layout: { visibility: key === active ? "visible" : "none" } });
  }
  return { version: 8, sources, layers };
}
```

- [ ] **Step 4: Write `map.ts`**

```ts
// web/src/map.ts — owns the MapLibre instance. Draws only what the API sends.
import maplibregl, { type LngLatBoundsLike, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { BASEMAPS, baseStyle, candidateColor, dayColor } from "./basemaps";
import type { Basemap } from "./store";
import type { Candidate, RouteFeature, TrailFeatureCollection, Trailhead } from "./types";

const TRAILS = "trails", TRAILHEADS = "trailheads", CANDS = "candidates", TRIP = "trip", WPS = "waypoints", FEATS = "features";

export interface TripMap {
  setBasemap(b: Basemap): void;
  fitBounds(bbox: [number, number, number, number]): void;
  setTrails(fc: TrailFeatureCollection | null): void;
  setTrailheads(ths: Trailhead[], onPick: (th: Trailhead) => void): void;
  onMapTap(fn: (lon: number, lat: number) => void): void;
  setCandidates(cands: Candidate[], selected: number, onSelect: (i: number) => void): void;
  clearCandidates(): void;
  setTrip(c: Candidate | null, popupHtml?: (f: RouteFeature) => string): void;
  destroy(): void;
}

function removeAll(map: MLMap, prefix: string) {
  for (const l of map.getStyle().layers ?? []) if (l.id.startsWith(prefix)) map.removeLayer(l.id);
  for (const id of Object.keys(map.getStyle().sources ?? {})) if (id.startsWith(prefix)) map.removeSource(id);
}

export function createMap(container: HTMLElement, basemap: Basemap, center: [number, number], zoom: number): TripMap {
  const map = new maplibregl.Map({ container, style: baseStyle(basemap), center, zoom, attributionControl: { compact: false } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  const ready = new Promise<void>((resolve) => map.once("load", () => resolve()));
  const markers: maplibregl.Marker[] = [];
  const popups: maplibregl.Popup[] = [];
  let tapHandler: ((lon: number, lat: number) => void) | null = null;
  map.on("click", (e) => {
    if ((e.originalEvent.target as HTMLElement)?.closest(".marker")) return;
    tapHandler?.(e.lngLat.lng, e.lngLat.lat);
  });

  function clearMarkers() { markers.splice(0).forEach((m) => m.remove()); popups.splice(0).forEach((p) => p.remove()); }

  function badge(text: string, color: string, title: string, onClick?: () => void): HTMLElement {
    const el = document.createElement("div");
    el.className = "marker";
    el.textContent = text;
    el.title = title;
    el.style.cssText = `background:${color};color:#fff;font:700 11px/18px sans-serif;padding:0 6px;border-radius:4px;border:1px solid #fff;box-shadow:0 0 2px rgba(0,0,0,.6);cursor:pointer`;
    if (onClick) el.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(); });
    return el;
  }

  const api: TripMap = {
    setBasemap(b) {
      for (const key of Object.keys(BASEMAPS)) map.setLayoutProperty(`base-${key}`, "visibility", key === b ? "visible" : "none");
    },
    fitBounds(bbox) { map.fitBounds(bbox as LngLatBoundsLike, { padding: 40, duration: 0 }); },
    setTrails(fc) {
      ready.then(() => {
        removeAll(map, TRAILS);
        if (!fc) return;
        map.addSource(TRAILS, { type: "geojson", data: fc });
        map.addLayer({ id: `${TRAILS}-road`, type: "line", source: TRAILS, filter: ["==", ["get", "is_road"], true],
          paint: { "line-color": "#6d4c41", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.7 } });
        map.addLayer({ id: `${TRAILS}-trail`, type: "line", source: TRAILS, filter: ["==", ["get", "is_road"], false],
          paint: { "line-color": "#5d4037", "line-width": 1.5, "line-opacity": 0.8 } });
      });
    },
    setTrailheads(ths, onPick) {
      ready.then(() => {
        removeAll(map, TRAILHEADS);
        // Trailheads are DOM markers (tappable badges); keep them in `markers` so setTrip/clear can remove them.
        for (const th of ths) {
          if (th.lat == null || th.lon == null || !th.in_graph) continue;
          const el = badge(th.source === "yaml" ? "TH" : "th", th.source === "yaml" ? "#1b5e20" : "#78909c",
            `${th.name ?? th.id}${th.verified ? "" : " (unverified)"}`, () => onPick(th));
          el.classList.add("marker-th");
          markers.push(new maplibregl.Marker({ element: el }).setLngLat([th.lon, th.lat]).addTo(map));
        }
      });
    },
    onMapTap(fn) { tapHandler = fn; },
    setCandidates(cands, selected, onSelect) {
      ready.then(() => {
        removeAll(map, CANDS);
        if (!cands.length) return;
        map.addSource(CANDS, { type: "geojson", data: { type: "FeatureCollection", features: cands.map((c) => ({
          type: "Feature", geometry: c.geometry, properties: { index: c.index, color: candidateColor(c.index) } })) } });
        map.addLayer({ id: `${CANDS}-dim`, type: "line", source: CANDS, filter: ["!=", ["get", "index"], selected],
          paint: { "line-color": ["get", "color"], "line-width": 4, "line-opacity": 0.35 } });
        map.addLayer({ id: `${CANDS}-sel`, type: "line", source: CANDS, filter: ["==", ["get", "index"], selected],
          paint: { "line-color": ["get", "color"], "line-width": 6, "line-opacity": 0.95 } });
        map.on("click", `${CANDS}-dim`, (e) => { const i = e.features?.[0]?.properties?.index; if (typeof i === "number") onSelect(i); });
      });
    },
    clearCandidates() { ready.then(() => removeAll(map, CANDS)); },
    setTrip(c, popupHtml) {
      ready.then(() => {
        removeAll(map, TRIP); removeAll(map, WPS); removeAll(map, FEATS);
        markers.filter((m) => !m.getElement().classList.contains("marker-th")).forEach((m) => m.remove());
        popups.splice(0).forEach((p) => p.remove());
        if (!c) return;
        map.addSource(TRIP, { type: "geojson", data: { type: "FeatureCollection", features: c.pieces.map((p, i) => ({
          type: "Feature", geometry: { type: "LineString", coordinates: p.points.map(([lon, lat]) => [lon, lat]) },
          properties: { color: dayColor(i + 1), name: p.name } })) } });
        map.addLayer({ id: `${TRIP}-line`, type: "line", source: TRIP, paint: { "line-color": ["get", "color"], "line-width": 5, "line-opacity": 0.9 } });
        const isLoop = c.waypoints[0] && c.waypoints[1] && c.waypoints[0].lat === c.waypoints[1].lat && c.waypoints[0].lon === c.waypoints[1].lon;
        for (const w of c.waypoints) {
          if (w.kind === "junction" || (isLoop && w.kind === "end")) continue;
          const text = w.kind === "start" ? (isLoop ? "S/E" : "S") : w.kind === "end" ? "E" : `C${w.name.split(" ")[1]}`;
          markers.push(new maplibregl.Marker({ element: badge(text, "#1b5e20", `${w.name}: ${w.desc}`) }).setLngLat([w.lon, w.lat]).addTo(map));
        }
        if (c.features.length) {
          map.addSource(FEATS, { type: "geojson", data: { type: "FeatureCollection", features: c.features.map((f, i) => ({
            type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] },
            properties: { i, camp: f.kind === "camp_site" || f.kind === "camp_pitch" || f.kind.startsWith("usfs") } })) } });
          map.addLayer({ id: `${FEATS}-dots`, type: "circle", source: FEATS, paint: {
            "circle-radius": 5, "circle-color": ["case", ["get", "camp"], "#8d5524", "#1565c0"], "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
          map.on("click", `${FEATS}-dots`, (e) => {
            const i = e.features?.[0]?.properties?.i; const f = typeof i === "number" ? c.features[i] : undefined;
            if (!f || !popupHtml) return;
            popups.push(new maplibregl.Popup({ maxWidth: "340px" }).setLngLat([f.lon, f.lat]).setHTML(popupHtml(f)).addTo(map));
          });
        }
      });
    },
    destroy() { clearMarkers(); map.remove(); },
  };
  return api;
}
```

- [ ] **Step 5: Run the tests and the type check**

Run: `cd web && npm test && npx tsc --noEmit`
Expected: tests pass (basemaps 3 more); `tsc` reports no errors. (`map.ts` has no unit test: it needs WebGL. The Playwright smoke in Task 8 exercises it.)

- [ ] **Step 6: Commit**

```bash
git add web/src/basemaps.ts web/src/basemaps.test.ts web/src/map.ts
git commit -m "web: MapLibre map with USGS base maps, trails, trailheads, candidate and trip layers"
```

---

### Task 5: Plan panel and app wiring (`main.ts`)

**Files:**
- Create: `web/src/panels/plan.ts`, `web/src/panels/common.ts`, `web/src/app.ts`
- Modify: `web/src/main.ts`
- Test: `web/src/panels/plan.test.ts`

**Interfaces:**
- `panels/common.ts`: `esc(s: unknown): string` (HTML-escape), `notesHtml(lines: string[], cls = "note"): string`, `knownGapsHtml(gaps: string[]): string` (the non-dismissable block: `<div class="warn"><b>Not checked by this tool:</b> …</div>`).
- `panels/plan.ts`: `renderPlan(s: State): string` (region select if >1, trailhead select listing every trailhead; entries with `!in_graph || lat == null` are `disabled` with "(not on the trail network)"; `data` ones say "(from the data, unverified)"; the form fields bound by `name=`; the Generate button `id="generate"`; status/error lines; the "tap the map to use a point" hint) and `bindPlan(root: HTMLElement, store, actions)` which reads inputs on change into `store.form` and calls `actions.generate()` on submit.
- `app.ts`: `startApp(root: Document, mapContainer: HTMLElement, panel: HTMLElement, handle: HTMLElement)` builds the store (with `loadPersisted`), the map, loads regions → trailheads → trails, renders the current view's panel on every store change, and implements `actions`: `pickRegion(id)`, `pickTrailhead(th)`, `tapPoint(lon, lat)` (sets `form.trailhead = "lat,lon"` with 5 decimals), `generate()` (toRequest → `api.generate` with a 120 s AbortController; `busy`/`status` text "Searching… this takes 5–20 s"; on success `run`, `selected = 1`, `view = "compare"`; on `ApiError` show its message and keep the previous `run`), `select(i)`, `openTrip(i)`, `replan()`, `setBasemap(b)`, `toggleSheet()`.
- `main.ts`: `startApp(document, #map, #panel, #sheet-handle)`.

- [ ] **Step 1: Write the failing tests**

`web/src/panels/plan.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { Trailhead } from "../types";
import { renderPlan } from "./plan";

const th = (over: Partial<Trailhead>): Trailhead => ({ id: "x", name: "X", node: 1, source: "yaml", verified: false, in_graph: true,
  lat: 39, lon: -79, component: "C0", ...over });

describe("renderPlan", () => {
  it("lists trailheads and disables ones not on the network", () => {
    const html = renderPlan({ ...INITIAL, region: "dolly_sods", trailheads: [
      th({ id: "bear_rocks", name: "Bear Rocks Trailhead" }),
      th({ id: "gone", name: "Gone", in_graph: false, lat: null, lon: null }),
      th({ id: "155984740", name: "19 View Trail @ Dolly Sods Road", source: "data" }),
    ] });
    expect(html).toContain('<option value="bear_rocks"');
    expect(html).toMatch(/<option value="gone"[^>]*disabled[^>]*>Gone \(not on the trail network\)/);
    expect(html).toContain("(from the data, unverified)");
    expect(html).toContain('id="generate"');
    expect(html).not.toMatch(/\bsafe\b/i);
  });
  it("shows the error and status lines verbatim", () => {
    const html = renderPlan({ ...INITIAL, status: "Searching…", error: "total must look like 18-26" });
    expect(html).toContain("Searching…");
    expect(html).toContain("total must look like 18-26");
  });
  it("escapes HTML in names", () => {
    const html = renderPlan({ ...INITIAL, trailheads: [th({ id: "a", name: "<b>bold</b>" })] });
    expect(html).toContain("&lt;b&gt;bold&lt;/b&gt;");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npm test -- plan`
Expected: FAIL resolving `./plan`.

- [ ] **Step 3: Write `common.ts`, `plan.ts`, `app.ts`, `main.ts`**

`web/src/panels/common.ts`:
```ts
export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function notesHtml(lines: string[], cls = "note"): string {
  return lines.length ? `<ul class="${cls}">${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "";
}

export function knownGapsHtml(gaps: string[]): string {
  return `<div class="warn" id="known-gaps"><b>Not checked by this tool:</b> ${gaps.map(esc).join("; ")}.</div>`;
}
```

`web/src/panels/plan.ts`:
```ts
import type { State, Store } from "../store";
import type { Trailhead } from "../types";
import { esc } from "./common";

function option(th: Trailhead, current: string): string {
  const usable = th.in_graph && th.lat != null && th.lon != null;
  const label = `${th.name ?? th.id}${usable ? "" : " (not on the trail network)"}${th.source === "data" && usable ? " (from the data, unverified)" : ""}`;
  return `<option value="${esc(th.id)}"${usable ? "" : " disabled"}${th.id === current ? " selected" : ""}>${esc(label)}</option>`;
}

export function renderPlan(s: State): string {
  const f = s.form;
  const regions = s.regions.length > 1
    ? `<label>Region<select name="region">${s.regions.map((r) => `<option value="${esc(r.id)}"${r.id === s.region ? " selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>`
    : "";
  const yaml = s.trailheads.filter((t) => t.source === "yaml");
  const data = s.trailheads.filter((t) => t.source === "data");
  const custom = f.trailhead && !s.trailheads.some((t) => t.id === f.trailhead) ? `<option value="${esc(f.trailhead)}" selected>${esc(f.trailhead)} (map point)</option>` : "";
  return `
<h1>Plan a trip</h1>
${regions}
<label>Trailhead<select name="trailhead">
  <option value=""${f.trailhead ? "" : " selected"}>Pick one, or tap the map</option>
  ${custom}
  ${yaml.length ? `<optgroup label="Trailheads">${yaml.map((t) => option(t, f.trailhead)).join("")}</optgroup>` : ""}
  ${data.length ? `<optgroup label="Other start points from the data">${data.map((t) => option(t, f.trailhead)).join("")}</optgroup>` : ""}
</select></label>
<p class="note">Tap a TH badge on the map, pick from the list, or tap anywhere on a trail to start there (snapped within 650 ft).</p>
<div class="row">
  <label>Nights<input name="nights" inputmode="numeric" value="${esc(f.nights)}" placeholder="2"></label>
  <label>Total miles<input name="total" value="${esc(f.total)}" placeholder="18-26"></label>
</div>
<div class="row">
  <label>Miles per day<input name="daily" value="${esc(f.daily)}" placeholder="6-11"></label>
  <label>Max gain per day (ft)<input name="maxGainDay" inputmode="numeric" value="${esc(f.maxGainDay)}" placeholder="2500"></label>
</div>
<div class="row">
  <label>Shape<select name="type">${["any", "loop", "lollipop"].map((t) => `<option value="${t}"${f.type === t ? " selected" : ""}>${t}</option>`).join("")}</select></label>
  <label>Direction<select name="direction"><option value=""${f.direction ? "" : " selected"}>either</option><option value="cw"${f.direction === "cw" ? " selected" : ""}>clockwise</option><option value="ccw"${f.direction === "ccw" ? " selected" : ""}>counterclockwise</option></select></label>
</div>
<label><input type="checkbox" name="noRoads"${f.noRoads ? " checked" : ""}> No roads at all</label>
<label><input type="checkbox" name="campsAtSites"${f.campsAtSites ? " checked" : ""}> Camps only at known campsites</label>
<label>Must visit (trail names or trailhead ids, one per line)<textarea name="mustVisit" rows="2">${esc(f.mustVisit.join("\n"))}</textarea></label>
<label>Avoid (trail names, one per line)<textarea name="avoid" rows="2">${esc(f.avoid.join("\n"))}</textarea></label>
<label>Pin camps (miles in, or lat,lon; one per line)<textarea name="camps" rows="2">${esc(f.camps.join("\n"))}</textarea></label>
<button class="primary" id="generate"${s.busy ? " disabled" : ""}>${s.busy ? "Searching…" : "Generate loops"}</button>
<div class="status${s.error ? " error" : ""}" id="status">${esc(s.error ?? s.status)}</div>
${s.run ? `<button class="link" id="back-to-compare">Back to the last results</button>` : ""}`;
}

export interface PlanActions { generate(): void; pickRegion(id: string): void; backToCompare(): void }

export function bindPlan(root: HTMLElement, store: Store, actions: PlanActions): void {
  const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("[name]").forEach((el) => {
    el.addEventListener("change", () => {
      const f = { ...store.get().form };
      const name = el.getAttribute("name")!;
      if (name === "region") return actions.pickRegion(el.value);
      if (el instanceof HTMLInputElement && el.type === "checkbox") (f as unknown as Record<string, unknown>)[name] = el.checked;
      else if (name === "mustVisit" || name === "avoid" || name === "camps") (f as unknown as Record<string, unknown>)[name] = lines(el.value);
      else (f as unknown as Record<string, unknown>)[name] = el.value;
      store.set({ form: f, error: null });
    });
  });
  root.querySelector("#generate")?.addEventListener("click", () => actions.generate());
  root.querySelector("#back-to-compare")?.addEventListener("click", () => actions.backToCompare());
}
```

`web/src/app.ts`:
```ts
import { ApiError, generate, getRegions, getTrailheads, getTrails } from "./api";
import { createMap, type TripMap } from "./map";
import { bindPlan, renderPlan } from "./panels/plan";
import { fromRun, toRequest } from "./request";
import { createStore, loadPersisted, persist, type Basemap, type Store } from "./store";
import type { Trailhead } from "./types";

export function startApp(doc: Document, mapEl: HTMLElement, panel: HTMLElement, handle: HTMLElement, storage: Storage = localStorage) {
  const store: Store = createStore(loadPersisted(storage));
  persist(store, storage);
  let map: TripMap | null = null;
  let abort: AbortController | null = null;

  const actions = {
    async pickRegion(id: string) {
      const region = store.get().regions.find((r) => r.id === id);
      if (!region) return;
      store.set({ region: id, trailheads: [], run: null, view: "plan", status: "Loading trails…", error: null });
      const b = region.bbox;
      map?.fitBounds([b.west, b.south, b.east, b.north]);
      try {
        const [ths, trails] = await Promise.all([getTrailheads(id), getTrails(id)]);
        store.set({ trailheads: ths, status: "" });
        map?.setTrails(trails);
        map?.setTrailheads(ths, (th) => actions.pickTrailhead(th));
      } catch (e) { store.set({ status: "", error: (e as Error).message }); }
    },
    pickTrailhead(th: Trailhead) { store.set((s) => ({ form: { ...s.form, trailhead: th.id }, error: null, view: "plan" })); },
    tapPoint(lon: number, lat: number) {
      if (store.get().view !== "plan") return;
      store.set((s) => ({ form: { ...s.form, trailhead: `${lat.toFixed(5)},${lon.toFixed(5)}` }, error: null }));
    },
    async generate() {
      const s = store.get();
      const built = toRequest(s.form);
      if ("error" in built) return store.set({ error: built.error });
      if (!s.region) return store.set({ error: "Pick a region first." });
      abort?.abort();
      abort = new AbortController();
      const timer = setTimeout(() => abort?.abort(), 120_000);
      store.set({ busy: true, error: null, status: "Searching… this takes 5–20 s on a big network." });
      try {
        const run = await generate(s.region, built.request, abort.signal);
        store.set({ run, selected: 1, view: "compare", busy: false, status: "" });
      } catch (e) {
        const msg = (e as Error).name === "AbortError" ? "The search took longer than 2 minutes and was stopped. Try a narrower range or fewer must-visits."
          : e instanceof ApiError ? e.message : `Unexpected error: ${(e as Error).message}`;
        store.set({ busy: false, status: "", error: msg });  // the previous run (if any) stays
      } finally { clearTimeout(timer); }
    },
    select(i: number) { store.set({ selected: i }); },
    openTrip(i: number) { store.set({ selected: i, view: "trip", sheet: "half" }); },
    backToCompare() { store.set({ view: "compare" }); },
    replan() { const run = store.get().run; store.set({ view: "plan", form: run ? fromRun(run) : store.get().form }); },
    setBasemap(b: Basemap) { store.set({ basemap: b }); map?.setBasemap(b); },
    toggleSheet() { store.set((s) => ({ sheet: s.sheet === "collapsed" ? "half" : s.sheet === "half" ? "full" : "collapsed" })); },
  };

  // Rendering: the view decides the panel; the map layers follow the run/selection. Later tasks add compare/trip here.
  const renderers: Record<string, (s: ReturnType<Store["get"]>) => string> = { plan: renderPlan };
  const binders: Record<string, (root: HTMLElement) => void> = { plan: (root) => bindPlan(root, store, actions) };
  function render() {
    const s = store.get();
    panel.innerHTML = renderers[s.view]?.(s) ?? "";
    binders[s.view]?.(panel);
    handle.parentElement!.className = `sheet sheet-${s.sheet}`;
  }
  store.subscribe((s, prev) => {
    if (s.view !== prev.view || s.form !== prev.form || s.status !== prev.status || s.error !== prev.error || s.busy !== prev.busy
      || s.run !== prev.run || s.selected !== prev.selected || s.trailheads !== prev.trailheads || s.sheet !== prev.sheet) render();
  });
  handle.addEventListener("click", () => actions.toggleSheet());

  map = createMap(mapEl, store.get().basemap, [-79.35, 39.02], 11);
  map.onMapTap((lon, lat) => actions.tapPoint(lon, lat));
  render();
  getRegions().then((regions) => {
    store.set({ regions });
    const id = store.get().region && regions.some((r) => r.id === store.get().region) ? store.get().region! : regions[0]?.id;
    if (id) actions.pickRegion(id);
    else store.set({ error: "No region has cached data yet. Run `backcountry ingest` and `backcountry dem` first." });
  }).catch((e) => store.set({ error: (e as Error).message }));

  return { store, actions, map };
}
```

`web/src/main.ts`:
```ts
import "./styles.css";
import { startApp } from "./app";

startApp(document, document.getElementById("map")!, document.getElementById("panel")!, document.getElementById("sheet-handle")!);
```

- [ ] **Step 4: Run tests, type check, and look at it**

Run: `cd web && npm test && npx tsc --noEmit`
Expected: all pass, no type errors. Then, with `uv run backcountry serve` running in another terminal: `cd web && npm run dev`, open http://localhost:5173 on the laptop: the map shows Dolly Sods with trails and TH badges, the form fills from defaults, Generate returns to the (still empty) compare view after ~15 s with no error. Stop both.

- [ ] **Step 5: Commit**

```bash
git add web/src/app.ts web/src/main.ts web/src/panels
git commit -m "web: plan panel, app wiring, region/trailhead loading, generate with timeout and error display"
```

---

### Task 6: Compare panel and candidate layers

**Files:**
- Create: `web/src/panels/compare.ts`
- Modify: `web/src/app.ts` (register the view; drive `map.setCandidates`)
- Test: `web/src/panels/compare.test.ts`

**Interfaces:**
- `compare.ts`: `cardHtml(c: Candidate, selected: boolean): string` (color swatch, "Candidate n: loop", distance, `signedFeet`, road miles, "days: OK"/"days: NOT all limits met"/"days: not planned", penalty, trail names in order via `segmentNames(c)`), `segmentNames(c): string[]` (consecutive duplicates merged), `renderCompare(s: State): string` (heading with the request summary, `search` notes + `must_visit_notes` verbatim, widened/truncated sentences, the card strip, a "What changed since last time" `<details>` with the `diff` text when present, buttons `#open-trip`, `#replan`), `bindCompare(root, store, actions)`.
- `app.ts`: whenever `run` or `selected` changes and `view !== "trip"`, call `map.setCandidates(run.candidates, selected, actions.select)`; when `run` becomes null call `clearCandidates()`.

- [ ] **Step 1: Write the failing tests**

`web/src/panels/compare.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { Candidate, Run } from "../types";
import { cardHtml, renderCompare, segmentNames } from "./compare";

const cand = (over: Partial<Candidate>): Candidate => ({
  index: 1, kind: "loop", length_m: 1609.344 * 19.02, gain_ft: 2023, loss_ft: 2023, road_m: 1609.344 * 0.81, last_resort_m: 0,
  repeated_m: 0, penalty: 0.81, segments: [{ label: "Bear Rocks Trail", highway: "path", is_road: false, last_resort: false, length_m: 1, sources: [] },
  { label: "Bear Rocks Trail", highway: "path", is_road: false, last_resort: false, length_m: 1, sources: [] },
  { label: "Raven Ridge Trail", highway: "path", is_road: false, last_resort: false, length_m: 1, sources: [] }],
  geometry: { type: "LineString", coordinates: [] }, pieces: [], waypoints: [], plan: null, features: [], profile: null,
  title: "t", description: "d", notes: [], ...over });

describe("compare", () => {
  it("merges consecutive trail names", () => {
    expect(segmentNames(cand({}))).toEqual(["Bear Rocks Trail", "Raven Ridge Trail"]);
  });
  it("renders a card in miles and feet with the day status", () => {
    const html = cardHtml(cand({ plan: { ok: false } as never }), true);
    expect(html).toContain("19.02 mi"); expect(html).toContain("+2,023 / −2,023 ft"); expect(html).toContain("0.81 mi");
    expect(html).toContain("NOT all limits met"); expect(html).toContain('class="card selected"');
    expect(cardHtml(cand({ gain_ft: null, loss_ft: null }), false)).toContain("unknown");
  });
  it("shows engine notes verbatim and the diff when present", () => {
    const run = { candidates: [cand({})], search: { notes: ["With these settings the start sits on a dead-end spur"], must_visit_notes: ["must-visit X: 3 of 9 loops"], widened: true, truncated: false, scored: 9, via_pairs_tried: 100, seconds: 1.2 },
      diff: "# What changed\n- Candidate 1: unchanged", request: { trailhead: "bear_rocks", total: [18, 26] }, known_gaps: [] } as unknown as Run;
    const html = renderCompare({ ...INITIAL, run, view: "compare" });
    expect(html).toContain("dead-end spur"); expect(html).toContain("must-visit X"); expect(html).toMatch(/wider/i);
    expect(html).toContain("What changed"); expect(html).toContain("Candidate 1: unchanged");
    expect(html).toContain('id="open-trip"');
  });
  it("explains an empty run", () => {
    const run = { candidates: [], search: { notes: ["No loop or lollipop fits"], must_visit_notes: [], widened: true, truncated: false, scored: 0, via_pairs_tried: 0, seconds: 0 }, diff: null, request: { trailhead: "x", total: [1, 2] }, known_gaps: [] } as unknown as Run;
    expect(renderCompare({ ...INITIAL, run })).toContain("No loop or lollipop fits");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npm test -- compare`
Expected: FAIL resolving `./compare`.

- [ ] **Step 3: Write `compare.ts` and wire it**

`web/src/panels/compare.ts`:
```ts
import { candidateColor } from "../basemaps";
import { miles, signedFeet } from "../format";
import type { State, Store } from "../store";
import type { Candidate } from "../types";
import { esc, notesHtml } from "./common";

export function segmentNames(c: Candidate): string[] {
  const out: string[] = [];
  for (const s of c.segments) if (out[out.length - 1] !== s.label) out.push(s.label);
  return out;
}

export function cardHtml(c: Candidate, selected: boolean): string {
  const days = c.plan ? (c.plan.ok ? "days: OK" : "days: NOT all limits met") : "days: not planned";
  return `<div class="card${selected ? " selected" : ""}" data-index="${c.index}">
  <div><span class="swatch" style="background:${candidateColor(c.index)}"></span><b>Candidate ${c.index}</b>: ${esc(c.kind)}</div>
  <div>${miles(c.length_m)}, ${signedFeet(c.gain_ft, c.loss_ft)}</div>
  <div>road ${miles(c.road_m)}${c.repeated_m ? `, walked twice ${miles(c.repeated_m)}` : ""} · ${days} · penalty ${c.penalty.toFixed(2)}</div>
  <div class="note">${esc(segmentNames(c).join(" › "))}</div>
</div>`;
}

export function renderCompare(s: State): string {
  const run = s.run;
  if (!run) return `<h1>Results</h1><p class="note">Nothing generated yet.</p><button class="link" id="replan">Back to the plan</button>`;
  const r = run.request;
  const total = Array.isArray(r.total) ? `${r.total[0]}–${r.total[1]}` : r.total;
  const lines = [...run.search.notes, ...run.search.must_visit_notes];
  if (run.search.widened) lines.push("Nothing fit the requested range, so the search looked 25% wider; the distance deviation is included in each penalty.");
  if (run.search.truncated) lines.push("The search hit its time budget, so some loops were never examined.");
  return `
<h1>${esc(r.trailhead)}, ${esc(total)} mi</h1>
<p class="note">${run.search.scored} loops scored from ${run.search.via_pairs_tried.toLocaleString("en-US")} waypoint pairs in ${run.search.seconds.toFixed(1)} s. Tap a card or a line on the map to focus it.</p>
${notesHtml(lines)}
${run.candidates.length ? `<div class="cards">${run.candidates.map((c) => cardHtml(c, c.index === s.selected)).join("")}</div>` : `<p class="note">No candidates. Change the settings and try again.</p>`}
${run.candidates.length ? `<button class="primary" id="open-trip">Open candidate ${s.selected}</button>` : ""}
${run.diff ? `<details><summary>What changed since last time</summary><pre class="note" style="white-space:pre-wrap">${esc(run.diff)}</pre></details>` : ""}
<button class="link" id="replan">Change the settings</button>`;
}

export interface CompareActions { select(i: number): void; openTrip(i: number): void; replan(): void }

export function bindCompare(root: HTMLElement, store: Store, actions: CompareActions): void {
  root.querySelectorAll<HTMLElement>(".card").forEach((el) => el.addEventListener("click", () => actions.select(Number(el.dataset.index))));
  root.querySelector("#open-trip")?.addEventListener("click", () => actions.openTrip(store.get().selected));
  root.querySelector("#replan")?.addEventListener("click", () => actions.replan());
}
```

In `web/src/app.ts`: import `{ bindCompare, renderCompare }` from `./panels/compare`; add `compare: renderCompare` to `renderers` and `compare: (root) => bindCompare(root, store, actions)` to `binders`; and extend the subscriber: after `render()` logic add

```ts
    if (s.run !== prev.run || s.selected !== prev.selected || s.view !== prev.view) {
      if (!s.run) map?.clearCandidates();
      else if (s.view !== "trip") { map?.setTrip(null); map?.setCandidates(s.run.candidates, s.selected, (i) => actions.select(i)); }
    }
```

- [ ] **Step 4: Run tests and type check**

Run: `cd web && npm test && npx tsc --noEmit`
Expected: all pass. With the API running, `npm run dev`: Generate shows three colored loops and cards; tapping a card highlights its loop.

- [ ] **Step 5: Commit**

```bash
git add web/src/panels/compare.ts web/src/panels/compare.test.ts web/src/app.ts
git commit -m "web: compare view with candidate cards, notes, diff, and map highlighting"
```

---

### Task 7: Trip panel, elevation profile, feature popups, downloads

**Files:**
- Create: `web/src/profile.ts`, `web/src/evidence.ts`, `web/src/panels/trip.ts`
- Modify: `web/src/app.ts`
- Test: `web/src/profile.test.ts`, `web/src/evidence.test.ts`, `web/src/panels/trip.test.ts`

**Interfaces:**
- `profile.ts`: `profileSvg(profile: {d_m: number[]; z_m: (number|null)[]} | null, days: Day[] | null, width = 600, height = 140): string`: an inline `<svg class="profile" viewBox="0 0 W H">` with the area path, day-boundary vertical lines labeled "Camp n", the x axis in miles and the y axis in feet (3 ticks); returns `<p class="note">Elevation unknown for this route.</p>` when `profile` is null or any `z_m` is null (Review Focus 2). `profilePath(d_m, z_m, W, H, pad)` returns the `d` attribute string (pure; tested).
- `evidence.ts`: `kindLabel(kind)` (same mapping as Python's `kind_label`: camp_site→"Campsite", camp_pitch→"Tent pitch", spring→"Spring", drinking_water→"Drinking water", water→"Lake/pond", "stream crossing"→"Stream crossing", "usfs campground"→"USFS campground"), `featureTitle(f)`, `evidenceHtml(f: RouteFeature): string` (title; mile and off-route feet; `details:` tags minus name/tourism/natural/waterway/amenity; "OpenStreetMap: last edited YYYY-MM-DD, version N" or "edit history unknown"; links: satellite view (`https://www.google.com/maps/@lat,lon,300m/data=!3m1!1e3`), OpenStreetMap entry, edit history; closing italic "Existence, status, and (for water) presence are unknown. Check before relying on it."). No raw `osm:` ids in the output.
- `panels/trip.ts`: `renderTrip(s: State): string` for `s.run.candidates[selected-1]`: heading, totals line, the plan's notes/violations verbatim (violations prefixed "Constraint not met:"), the day table (Day n, from, to, miles, gain, loss) or "Days weren't planned (no nights given)" when `plan` is null, the camps list (location, mile, trail, "pinned by you"/"at a known campsite"/"placed for mileage", "Location unverified", nearby campsites count, nearest water with "(presence unknown)"), the profile SVG, a features table (mile, day, feature, off route, source link) with the water warning sentence above it, download links (`gpxUrl(run, n)` whole trip, one per day when planned, `geojsonUrl`), buttons `#back-compare`, `#replan`, a disabled "Save for offline (coming)" button, and `knownGapsHtml(run.known_gaps)` last. `bindTrip(root, store, actions)`.
- `app.ts`: when `view === "trip"`, call `map.setTrip(candidate, evidenceHtml)` and `map.clearCandidates()`; when leaving trip, `map.setTrip(null)`.

- [ ] **Step 1: Write the failing tests**

`web/src/profile.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { profilePath, profileSvg } from "./profile";

describe("profile", () => {
  it("builds a path across the width with the high point at the top", () => {
    const d = profilePath([0, 100, 200], [1000, 1100, 1000], 200, 100, 10);
    expect(d.startsWith("M10,")).toBe(true);
    const ys = [...d.matchAll(/[ML](\d+(?:\.\d+)?),(\d+(?:\.\d+)?)/g)].map((m) => Number(m[2]));
    expect(Math.min(...ys)).toBeCloseTo(10, 0);   // the 1100 m point touches the top padding
    expect(Math.max(...ys)).toBeCloseTo(90, 0);   // the 1000 m points touch the bottom padding
  });
  it("marks camps and labels axes in miles and feet", () => {
    const svg = profileSvg({ d_m: [0, 1609.344, 3218.688], z_m: [1000, 1100, 1000] },
      [{ index: 1, start_m: 0, end_m: 1609.344, length_m: 1609.344, gain_ft: 1, loss_ft: 0, start_label: "Start", end_label: "Camp 1" },
       { index: 2, start_m: 1609.344, end_m: 3218.688, length_m: 1609.344, gain_ft: 0, loss_ft: 1, start_label: "Camp 1", end_label: "End" }]);
    expect(svg).toContain("<svg"); expect(svg).toContain("Camp 1"); expect(svg).toContain("mi"); expect(svg).toContain("ft");
    expect(svg).not.toMatch(/\bm\b/);
  });
  it("says unknown instead of drawing when elevation is missing", () => {
    expect(profileSvg(null, null)).toContain("unknown");
    expect(profileSvg({ d_m: [0, 10], z_m: [1, null] }, null)).toContain("unknown");
  });
});
```

`web/src/evidence.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { evidenceHtml, kindLabel } from "./evidence";
import type { RouteFeature } from "./types";

describe("evidence", () => {
  it("labels kinds like the reports do", () => {
    expect(kindLabel("camp_site")).toBe("Campsite"); expect(kindLabel("stream crossing")).toBe("Stream crossing");
    expect(kindLabel("usfs campground")).toBe("USFS campground"); expect(kindLabel("water")).toBe("Lake/pond");
  });
  it("shows tags, edit history, links, and the unknown warning without raw ids", () => {
    const f: RouteFeature = { source: "osm:node/12830535560", kind: "camp_site", name: null, lat: 39.04, lon: -79.38, dist_m: 1609.344 * 21.3,
      off_m: 1.8, tags: { tourism: "camp_site", description: "no water" }, meta: { timestamp: "2025-05-12T15:00:02Z", version: 1 },
      url: "https://www.openstreetmap.org/node/12830535560" };
    const html = evidenceHtml(f);
    expect(html).toContain("<b>Campsite</b>"); expect(html).toContain("mile 21.3"); expect(html).toContain("6 ft off route");
    expect(html).toContain("description=no water"); expect(html).toContain("last edited 2025-05-12, version 1");
    expect(html).toContain("google.com/maps/@39.040000,-79.380000"); expect(html).toContain("openstreetmap.org/node/12830535560/history");
    expect(html).toContain("presence) are unknown"); expect(html).not.toContain("osm:node/");
  });
});
```

`web/src/panels/trip.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { Candidate, Run } from "../types";
import { renderTrip } from "./trip";

function run(c: Partial<Candidate>): Run {
  const cand: Candidate = { index: 1, kind: "loop", length_m: 30000, gain_ft: 2023, loss_ft: 2023, road_m: 0, last_resort_m: 0, repeated_m: 0,
    penalty: 0, segments: [{ label: "Bear Rocks Trail", highway: "path", is_road: false, last_resort: false, length_m: 30000, sources: ["osm:way/1"] }],
    geometry: { type: "LineString", coordinates: [] }, pieces: [{ name: "Route", points: [], start_m: 0, end_m: 30000, length_m: 30000, gain_ft: 2023, loss_ft: 2023, day: null }],
    waypoints: [], plan: null, features: [], profile: { d_m: [0, 30000], z_m: [1000, 1100] }, title: "t", description: "d", notes: [], ...c };
  return { run_id: "r1", region: "dolly_sods", request: { trailhead: "bear_rocks", total: [18, 26], daily: null }, generated_at: "", start: { node: 1, how: "trailhead bear_rocks", snap_m: 0 },
    search: { loops_found: 1, lollipops_found: 0, stems_tried: 0, via_pairs_tried: 1, scored: 1, seconds: 1, widened: false, truncated: false, avoided_edges: 0, notes: [], must_visit_notes: [] },
    candidates: [cand], data_sources: [{ name: "OpenStreetMap via Overpass API", retrieved_at: "2026-10-13", attribution: "© OSM" }],
    known_gaps: ["water: map features only; whether water is present is unknown", "permits: not checked"], diff: null };
}

describe("renderTrip", () => {
  it("works without a day plan and always shows known gaps and downloads", () => {
    const html = renderTrip({ ...INITIAL, run: run({}), selected: 1, view: "trip" });
    expect(html).toContain("Days weren't planned"); expect(html).toContain('id="known-gaps"'); expect(html).toContain("permits: not checked");
    expect(html).toContain('href="/api/runs/r1/candidates/1.gpx"'); expect(html).toContain("18.64 mi"); expect(html).not.toMatch(/\bsafe\b/i);
  });
  it("renders days, camps and violations verbatim", () => {
    const html = renderTrip({ ...INITIAL, selected: 1, view: "trip", run: run({ plan: {
      nights: 1, daily_min_m: 1, daily_max_m: 2, max_gain_ft: 2500, ok: false, penalty: 1, notes: ["note A"],
      violations: ["Day 2: 12.4 mi is over the 11.00 mi daily max"],
      days: [{ index: 1, start_m: 0, end_m: 15000, length_m: 15000, gain_ft: 1000, loss_ft: 100, start_label: "Start", end_label: "Camp 1" },
             { index: 2, start_m: 15000, end_m: 30000, length_m: 15000, gain_ft: null, loss_ft: null, start_label: "Camp 1", end_label: "End" }],
      camps: [{ index: 1, dist_m: 15000, lat: 39, lon: -79, on_source: "osm:way/1", on_name: "Red Creek Trail", nearby: [], note: "camp_location: unverified",
        pinned: true, site: null, nearest_water: { source: "osm:way/2", kind: "stream crossing", name: "Red Creek", distance_m: 61 } }] } }) });
    expect(html).toContain("Day 1"); expect(html).toContain("9.32 mi"); expect(html).toContain("1,000 ft"); expect(html).toContain("unknown");
    expect(html).toContain("Constraint not met: Day 2: 12.4 mi is over the 11.00 mi daily max"); expect(html).toContain("note A");
    expect(html).toContain("Camp 1"); expect(html).toContain("pinned by you"); expect(html).toContain("Red Creek Trail"); expect(html).toContain("Location unverified");
    expect(html).toContain("200 ft"); expect(html).toContain("presence unknown"); expect(html).toContain('href="/api/runs/r1/candidates/1.gpx?day=2"');
    expect(html).not.toContain("osm:way/");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npm test -- "profile|evidence|trip"`
Expected: FAIL resolving the three modules.

- [ ] **Step 3: Write `profile.ts`, `evidence.ts`, `trip.ts`; wire the view**

`web/src/profile.ts`:
```ts
import { FT_PER_M, M_PER_MI } from "./format";
import type { Day } from "./types";

export function profilePath(d_m: number[], z_m: number[], W: number, H: number, pad: number): string {
  const dMax = d_m[d_m.length - 1] || 1;
  const zMin = Math.min(...z_m), zMax = Math.max(...z_m);
  const span = zMax - zMin || 1;
  const x = (d: number) => pad + ((W - 2 * pad) * d) / dMax;
  const y = (z: number) => H - pad - ((H - 2 * pad) * (z - zMin)) / span;
  return d_m.map((d, i) => `${i ? "L" : "M"}${x(d).toFixed(1)},${y(z_m[i]!).toFixed(1)}`).join(" ");
}

export function profileSvg(profile: { d_m: number[]; z_m: (number | null)[] } | null, days: Day[] | null, W = 600, H = 140): string {
  if (!profile || profile.z_m.some((z) => z == null) || profile.d_m.length < 2) return `<p class="note">Elevation unknown for this route.</p>`;
  const z = profile.z_m as number[];
  const pad = 22;
  const path = profilePath(profile.d_m, z, W, H, pad);
  const dMax = profile.d_m[profile.d_m.length - 1]!;
  const zMin = Math.min(...z), zMax = Math.max(...z);
  const x = (d: number) => pad + ((W - 2 * pad) * d) / dMax;
  const area = `${path} L${(W - pad).toFixed(1)},${H - pad} L${pad},${H - pad} Z`;
  const camps = (days ?? []).slice(0, -1).map((d) => `<line x1="${x(d.end_m).toFixed(1)}" y1="${pad}" x2="${x(d.end_m).toFixed(1)}" y2="${H - pad}" stroke="#1b5e20" stroke-dasharray="3 3"/>` +
    `<text x="${(x(d.end_m) + 3).toFixed(1)}" y="${pad + 10}" font-size="10" fill="#1b5e20">${d.end_label}</text>`).join("");
  const ticks = [0, 0.5, 1].map((t) => { const mi = (dMax * t) / M_PER_MI; return `<text x="${x(dMax * t).toFixed(1)}" y="${H - 6}" font-size="10" text-anchor="${t === 0 ? "start" : t === 1 ? "end" : "middle"}" fill="#666">${mi.toFixed(1)} mi</text>`; }).join("");
  const yl = `<text x="2" y="${pad + 4}" font-size="10" fill="#666">${Math.round(zMax * FT_PER_M).toLocaleString("en-US")} ft</text><text x="2" y="${H - pad}" font-size="10" fill="#666">${Math.round(zMin * FT_PER_M).toLocaleString("en-US")} ft</text>`;
  return `<svg class="profile" viewBox="0 0 ${W} ${H}" role="img" aria-label="Elevation profile"><path d="${area}" fill="#c8e6c9"/><path d="${path}" fill="none" stroke="#2e7d32" stroke-width="1.5"/>${camps}${ticks}${yl}</svg>`;
}
```

`web/src/evidence.ts`:
```ts
import { feet, M_PER_MI, FT_PER_M } from "./format";
import { esc } from "./panels/common";
import type { RouteFeature } from "./types";

const LABELS: Record<string, string> = { camp_site: "Campsite", camp_pitch: "Tent pitch", spring: "Spring", drinking_water: "Drinking water",
  water: "Lake/pond", "stream crossing": "Stream crossing", "river crossing": "River crossing" };
const HIDDEN = new Set(["name", "tourism", "natural", "waterway", "amenity"]);

export function kindLabel(kind: string): string {
  if (LABELS[kind]) return LABELS[kind]!;
  if (kind.startsWith("usfs")) return "USFS " + kind.slice(5).trim().replace(/_/g, " ").toLowerCase();
  return kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function featureTitle(f: { kind: string; name: string | null }): string {
  return kindLabel(f.kind) + (f.name ? `: ${f.name}` : "");
}

export function evidenceHtml(f: RouteFeature): string {
  const when = (f.meta?.timestamp ?? "").slice(0, 10);
  const tags = Object.entries(f.tags ?? {}).filter(([k]) => !HIDDEN.has(k)).sort().map(([k, v]) => `${k}=${v}`).join("; ") || "no other tags";
  const edit = when ? `last edited ${when}, version ${f.meta?.version}` : "edit history unknown";
  const src = f.url ? "OpenStreetMap" : f.source.startsWith("usfs") ? "USFS" : "source on file";
  const links = [`<a href="https://www.google.com/maps/@${f.lat.toFixed(6)},${f.lon.toFixed(6)},300m/data=!3m1!1e3" target="_blank" rel="noopener">satellite view</a>`];
  if (f.url) links.push(`<a href="${esc(f.url)}" target="_blank" rel="noopener">OpenStreetMap entry</a>`, `<a href="${esc(f.url)}/history" target="_blank" rel="noopener">edit history</a>`);
  return `<b>${esc(featureTitle(f))}</b><br>mile ${(f.dist_m / M_PER_MI).toFixed(1)}, ${feet(f.off_m * FT_PER_M)} off route<br>details: ${esc(tags)}<br>${src}: ${esc(edit)}<br>${links.join(" · ")}` +
    `<br><i>Existence, status, and (for water) presence are unknown. Check before relying on it.</i>`;
}
```

`web/src/panels/trip.ts`:
```ts
import { geojsonUrl, gpxUrl } from "../api";
import { dayColor } from "../basemaps";
import { featureTitle } from "../evidence";
import { feet, FT_PER_M, M_PER_MI, miles, signedFeet } from "../format";
import { profileSvg } from "../profile";
import type { State, Store } from "../store";
import type { Candidate, Run } from "../types";
import { esc, knownGapsHtml, notesHtml } from "./common";
import { segmentNames } from "./compare";

function daysHtml(c: Candidate): string {
  const p = c.plan;
  if (!p) return `<p class="note">Days weren't planned (no nights given). Change the settings to split this loop into days.</p>`;
  const rows = p.days.map((d) => `<tr><td><span class="swatch" style="background:${dayColor(d.index)}"></span>Day ${d.index}</td><td>${esc(d.start_label)} → ${esc(d.end_label)}</td><td>${miles(d.length_m)}</td><td>${feet(d.gain_ft)}</td><td>${feet(d.loss_ft)}</td></tr>`).join("");
  const camps = p.camps.map((cp) => {
    const how = cp.pinned ? "pinned by you" : cp.site ? "at a known campsite" : "placed for mileage";
    const water = cp.nearest_water ? `nearest water: ${featureTitle(cp.nearest_water).toLowerCase()}, ${feet(cp.nearest_water.distance_m * FT_PER_M)} away (presence unknown)` : "nearest water: none listed";
    const near = cp.nearby.length ? `${cp.nearby.length} known campsite${cp.nearby.length === 1 ? "" : "s"} within ${feet(500 * FT_PER_M)}` : `no known campsite within ${feet(500 * FT_PER_M)}`;
    return `<li><b>Camp ${cp.index}</b> at ${cp.lat.toFixed(5)}, ${cp.lon.toFixed(5)}, ${miles(cp.dist_m)} in, on ${esc(cp.on_name ?? "an unnamed trail")}, ${how}. Location unverified. ${near}. ${esc(water)}.</li>`;
  }).join("");
  return `${notesHtml(p.notes)}${notesHtml(p.violations.map((v) => `Constraint not met: ${v}`), "note error")}
<table><thead><tr><th>day</th><th>from → to</th><th>distance</th><th>gain</th><th>loss</th></tr></thead><tbody>${rows}</tbody></table>
<h2>Camps</h2><p class="note">Camps are points on the route chosen for mileage, not known campsites. Legality and water: unknown.</p><ul>${camps}</ul>`;
}

function featuresHtml(c: Candidate): string {
  if (!c.features.length) return `<p class="note">No campsites or water features in the data within 492 ft of this route.</p>`;
  const rows = c.features.map((f) => `<tr><td>${(f.dist_m / M_PER_MI).toFixed(1)}</td><td>${f.day ?? "—"}</td><td>${esc(featureTitle(f))}</td><td>${feet(f.off_m * FT_PER_M)}</td><td>${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">OSM</a>` : f.source.startsWith("usfs") ? "USFS" : "—"}</td></tr>`).join("");
  return `<p class="warn">Water features come from OpenStreetMap. Whether water is actually there, flowing, or drinkable is unknown; this tool has not checked. Tap a dot on the map for details and a satellite view.</p>
<table><thead><tr><th>mile</th><th>day</th><th>feature</th><th>off route</th><th>source</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function downloadsHtml(run: Run, c: Candidate): string {
  const days = c.plan ? c.plan.days.map((d) => `<a class="link" href="${gpxUrl(run.run_id, c.index, d.index)}" download>day ${d.index}</a>`).join(" · ") : "";
  return `<p><a class="link" href="${gpxUrl(run.run_id, c.index)}" download>Download GPX (whole trip)</a>${days ? ` · GPX for ${days}` : ""} · <a class="link" href="${geojsonUrl(run.run_id, c.index)}" download>GeoJSON</a></p>`;
}

export function renderTrip(s: State): string {
  const run = s.run;
  const c = run?.candidates[s.selected - 1];
  if (!run || !c) return `<h1>Trip</h1><p class="note">Pick a candidate first.</p><button class="link" id="back-compare">Back</button>`;
  const sources = run.data_sources.map((d) => `${esc(d.name)} (retrieved ${esc(d.retrieved_at ?? "unknown")})`).join("; ");
  return `
<h1>Candidate ${c.index}: ${esc(c.kind)}, ${miles(c.length_m)}</h1>
<p>${signedFeet(c.gain_ft, c.loss_ft)} · road ${miles(c.road_m)}${c.repeated_m ? ` · walked twice ${miles(c.repeated_m)}` : ""}</p>
<p class="note">${esc(segmentNames(c).join(" › "))}</p>
${notesHtml(c.notes)}
<h2>Days</h2>${daysHtml(c)}
<h2>Elevation</h2>${profileSvg(c.profile, c.plan?.days ?? null)}
<h2>Campsites and water along the route</h2>${featuresHtml(c)}
<h2>Files</h2>${downloadsHtml(run, c)}
<div class="row"><button class="primary" id="replan">Re-plan with these settings</button><button class="primary" disabled title="Coming in a later piece">Save for offline (coming)</button></div>
<button class="link" id="back-compare">Back to all candidates</button>
<p class="note">Data: ${sources}.</p>
${knownGapsHtml(run.known_gaps)}`;
}

export interface TripActions { backToCompare(): void; replan(): void }

export function bindTrip(root: HTMLElement, store: Store, actions: TripActions): void {
  root.querySelector("#back-compare")?.addEventListener("click", () => actions.backToCompare());
  root.querySelector("#replan")?.addEventListener("click", () => actions.replan());
}
```

In `web/src/app.ts`: import `{ bindTrip, renderTrip }` and `{ evidenceHtml }`; add `trip: renderTrip` / `trip: (root) => bindTrip(root, store, actions)`; and in the subscriber's map section, when `s.view === "trip"`: `map?.clearCandidates(); map?.setTrip(s.run?.candidates[s.selected - 1] ?? null, evidenceHtml);` (the existing branch handles the non-trip case and calls `setTrip(null)`).

- [ ] **Step 4: Run tests and type check**

Run: `cd web && npm test && npx tsc --noEmit`
Expected: all pass. With the API running, `npm run dev`: open a candidate: days in colors, camp badges, dots with popups, the profile, downloads that save files.

- [ ] **Step 5: Commit**

```bash
git add web/src/profile.ts web/src/profile.test.ts web/src/evidence.ts web/src/evidence.test.ts web/src/panels/trip.ts web/src/panels/trip.test.ts web/src/app.ts
git commit -m "web: trip view with days, camps, elevation profile, feature evidence popups, downloads"
```

---

### Task 8: Phone layout, base-map switcher, test API server, Playwright smoke test

**Files:**
- Create: `scripts/test_api_server.py`, `web/playwright.config.ts`, `web/e2e/smoke.spec.ts`
- Modify: `web/src/app.ts` (base-map control, sheet drag), `web/src/styles.css` (nothing new unless the control needs it), `tests/test_api.py` (fixture `web_dist` isolation, see deferred minor)
- Test: `web/e2e/smoke.spec.ts` (Playwright), `web/src/app.test.ts` (sheet state cycling without a map)

**Interfaces:**
- `scripts/test_api_server.py`: writes the synthetic grid's Overpass cache into a temp dir, builds `Settings(raw_dir, runs_dir, regions_dir=None)` and runs uvicorn on `BACKCOUNTRY_TEST_PORT` (default 8765), with `web_dist` pointed at a missing dir. Region id `dolly_sods` (the real yaml; its trailheads will show as "not on the trail network", the data-derived junctions are the usable starts).
- `app.ts`: a small base-map `<select>` injected top-left of the map (`#basemap`) that calls `actions.setBasemap`; the sheet handle supports a vertical drag (pointer events: up → full, down → collapsed) in addition to tap-cycling; `applySheet(s)` is the only place that sets the sheet class.
- `tests/test_api.py`: the `client` fixture passes `web_dist=tmp_path / "no-web"` so a built `web/dist` never changes test results.

- [ ] **Step 1: Write the test API server script**

`scripts/test_api_server.py`:
```python
"""Run the API on the synthetic grid network, for the web app's end-to-end test.

    uv run python scripts/test_api_server.py   # listens on $BACKCOUNTRY_TEST_PORT or 8765
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import build_grid  # noqa: E402

from backcountry.api.app import create_app  # noqa: E402
from backcountry.api.store import Settings  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="backcountry-e2e-"))
(tmp / "raw" / "dolly_sods").mkdir(parents=True)
(tmp / "raw" / "dolly_sods" / "overpass_20260101T000000Z.json").write_text(json.dumps(build_grid().doc()))
app = create_app(Settings(raw_dir=tmp / "raw", runs_dir=tmp / "runs", regions_dir=None), web_dist=tmp / "no-web")
uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("BACKCOUNTRY_TEST_PORT", "8765")), log_level="warning")
```

Check it: `uv run python scripts/test_api_server.py & sleep 3; curl -s localhost:8765/api/regions | head -c 120; kill %1`
Expected: JSON with `dolly_sods` and `osm_retrieved_at: "2026-01-01T00:00:00Z"`.

- [ ] **Step 2: Write the failing tests**

`web/src/app.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { nextSheet } from "./app";

describe("sheet", () => {
  it("cycles collapsed -> half -> full -> collapsed on tap and follows drags", () => {
    expect(nextSheet("collapsed", "tap")).toBe("half");
    expect(nextSheet("half", "tap")).toBe("full");
    expect(nextSheet("full", "tap")).toBe("collapsed");
    expect(nextSheet("half", "up")).toBe("full");
    expect(nextSheet("full", "down")).toBe("half");
    expect(nextSheet("half", "down")).toBe("collapsed");
    expect(nextSheet("collapsed", "down")).toBe("collapsed");
  });
});
```

`web/playwright.config.ts`:
```ts
import { defineConfig } from "@playwright/test";

const API = "http://127.0.0.1:8765";
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:4173", viewport: { width: 390, height: 844 } },  // a phone-sized viewport
  webServer: [
    { command: "cd .. && uv run python scripts/test_api_server.py", url: `${API}/api/regions`, reuseExistingServer: false, timeout: 60_000 },
    { command: `BACKCOUNTRY_API=${API} npm run build && BACKCOUNTRY_API=${API} npm run preview -- --port 4173`, url: "http://127.0.0.1:4173", reuseExistingServer: false, timeout: 120_000 },
  ],
});
```

`web/e2e/smoke.spec.ts`:
```ts
import { expect, test } from "@playwright/test";

test("plan, compare, open a trip, download its GPX", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#map canvas")).toBeVisible();
  const trailhead = page.locator('select[name="trailhead"]');
  await expect(trailhead.locator("optgroup[label='Other start points from the data'] option")).not.toHaveCount(0);
  const first = await trailhead.locator("optgroup[label='Other start points from the data'] option").first().getAttribute("value");
  await trailhead.selectOption(first!);
  await page.fill('input[name="total"]', "2.4-5.1");
  await page.fill('input[name="nights"]', "1");
  await page.fill('input[name="daily"]', "0.5-4");
  await page.fill('input[name="maxGainDay"]', "");
  await page.selectOption('select[name="type"]', "loop");
  await page.click("#generate");
  await expect(page.locator(".card")).toHaveCount(3, { timeout: 30_000 });
  await expect(page.locator("#status")).not.toContainText("error");
  await page.locator(".card").nth(1).click();
  await expect(page.locator(".card.selected")).toContainText("Candidate 2");
  await page.click("#open-trip");
  await expect(page.locator("h1")).toContainText("Candidate 2");
  await expect(page.locator("#known-gaps")).toContainText("Not checked by this tool");
  await expect(page.locator("table")).toContainText("Day 1");
  const [download] = await Promise.all([page.waitForEvent("download"), page.click('a[href$="/candidates/2.gpx"]')]);
  expect(download.suggestedFilename()).toMatch(/candidate_2\.gpx$/);
  const text = await (await download.createReadStream())?.toArray().then((b) => Buffer.concat(b).toString());
  expect(text).toContain("<trk>");
});

test("a bad range is refused with the field named, and the map stays", async ({ page }) => {
  await page.goto("/");
  await page.fill('input[name="total"]', "26-18");
  await page.click("#generate");
  await expect(page.locator("#status")).toContainText("total must look like 18-26");
  await expect(page.locator("#map canvas")).toBeVisible();
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd web && npm test -- app`
Expected: FAIL, `nextSheet` is not exported. (Playwright needs browsers: `cd web && npx playwright install chromium` once.)

- [ ] **Step 4: Implement**

In `web/src/app.ts` add, exported, above `startApp`:

```ts
export type SheetState = "collapsed" | "half" | "full";
export function nextSheet(current: SheetState, gesture: "tap" | "up" | "down"): SheetState {
  const order: SheetState[] = ["collapsed", "half", "full"];
  const i = order.indexOf(current);
  if (gesture === "tap") return order[(i + 1) % 3]!;
  if (gesture === "up") return order[Math.min(i + 1, 2)]!;
  return order[Math.max(i - 1, 0)]!;
}
```

Replace `toggleSheet()` with `sheet(gesture: "tap" | "up" | "down") { store.set((s) => ({ sheet: nextSheet(s.sheet, gesture) })); }` and the handle listener with:

```ts
  let dragStart: number | null = null;
  handle.addEventListener("pointerdown", (e) => { dragStart = e.clientY; });
  handle.addEventListener("pointerup", (e) => {
    const dy = dragStart == null ? 0 : e.clientY - dragStart;
    dragStart = null;
    actions.sheet(dy < -30 ? "up" : dy > 30 ? "down" : "tap");
  });
```

Add the base-map control after `createMap(...)`:

```ts
  const basemapEl = doc.createElement("select");
  basemapEl.id = "basemap";
  basemapEl.setAttribute("aria-label", "Base map");
  basemapEl.style.cssText = "position:fixed;top:10px;left:10px;z-index:5;width:auto;padding:6px;background:#fff";
  basemapEl.innerHTML = Object.entries(BASEMAPS).map(([k, b]) => `<option value="${k}"${k === store.get().basemap ? " selected" : ""}>${b.name}</option>`).join("");
  basemapEl.addEventListener("change", () => actions.setBasemap(basemapEl.value as Basemap));
  mapEl.parentElement!.appendChild(basemapEl);
```

(import `BASEMAPS` from `./basemaps`). In `tests/test_api.py`, change the `client` fixture to `TestClient(create_app(env, web_dist=env.runs_dir / "no-web"))`.

- [ ] **Step 5: Run everything**

Run: `cd web && npm test && npx tsc --noEmit && npm run test:e2e` and from the repo root `uv run pytest -q`.
Expected: Vitest green; Playwright 2 passed (it builds the app, starts the synthetic API on 8765, serves the build on 4173); pytest green. On your phone, with `uv run backcountry serve --host 0.0.0.0` after `npm run build`, open `http://<laptop-ip>:8000`: the sheet drags, the base-map switcher works.

- [ ] **Step 6: Commit**

```bash
git add scripts/test_api_server.py web/playwright.config.ts web/e2e web/src/app.ts web/src/app.test.ts tests/test_api.py
git commit -m "web: phone sheet gestures, base-map switcher, synthetic test API server, Playwright smoke test"
```

---

### Task 9: Docs and progress

**Files:**
- Modify: `README.md`, `PROGRESS.md`

- [ ] **Step 1: README**

Add under "Running it", after the API section:

```markdown
### Web app (phone or laptop)

One-time: `cd web && npm install && npm run build`. Then `uv run backcountry serve --host 0.0.0.0`
and open `http://<your-laptop-ip>:8000` on your phone (same Wi-Fi) or `http://localhost:8000` on the
laptop. Pick a trailhead on the map, set the limits, Generate, compare the candidates, open one,
download its GPX. Add it to your home screen for an app-style window.

Development: `uv run backcountry serve` in one terminal, `cd web && npm run dev` in another, open
http://localhost:5173. Tests: `npm test` (unit), `npm run test:e2e` (one end-to-end run against the API
on synthetic data; needs `npx playwright install chromium` once).
```

- [ ] **Step 2: PROGRESS**

Add a `## Phase 2 piece 2 — Web app (done <date>)` section: what was built (one page, three states, map layers, profile, evidence popups, downloads, persistence, sheet), the phone check result (your own, over Wi-Fi), test counts (Vitest, Playwright, pytest), what's deferred (Save for offline is a disabled button; no accounts; the API's deferred minors), and anything found wrong.

- [ ] **Step 3: Commit**

```bash
git add README.md PROGRESS.md
git commit -m "docs: web app usage and progress"
```

---

## Self-review notes

- Spec coverage: Plan/Compare/Trip states (Tasks 5–7); map with USGS bases, trails, trailheads, candidate colors, day colors, S/E and camp badges, junctions off by default (Task 4: junction dots aren't drawn; the spec lists a junction toggle: **gap**, handled by adding a `#junctions` checkbox in Task 7's trip panel that calls a `map.setJunctions(show)` drawing `kind === "junction"` waypoints as 4 px circles: add both to Task 7's Step 3 (`TripMap.setJunctions(c: Candidate | null, show: boolean)` in `map.ts`, default off)); profile with day boundaries (Task 7); feature popups with evidence (Task 7); downloads (Task 7); Re-plan with prefilled form and the diff (Tasks 6–7); "Save for offline" disabled (Task 7); Known gaps always visible (Task 7); persistence (Task 3); errors verbatim and last good state kept (Task 5); bottom sheet / side panel (Tasks 1, 8); Vitest + Playwright + phone check (Tasks 1–8); `serve` serving `dist` (piece 1, verified in Task 1 Step 4).
- Types: `FormState` field names match between `request.ts`, `plan.ts` (`name=` attributes) and `app.ts`; `TripMap` methods used by `app.ts` all exist in Task 4 (plus `setJunctions` per the gap above); `nextSheet`/`SheetState` match between Task 8's test and code.
- Review Focus: 1 → Task 2 (`ApiError` for unreachable/non-JSON) and Task 5 (timeout text, previous run kept); 2 → Tasks 6–7 (`unknown`, profile placeholder); 3 → Task 7 test "works without a day plan"; 4 → Task 5 test (disabled option) and `toRequest` refusing an empty trailhead; 5 → Task 3 (persistence) and Task 8 (`nextSheet`, `removeAll` before every layer set).
