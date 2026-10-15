# Offline Trips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A trip saved on the phone works with no signal: map tiles, route, days, camps, profile, evidence, GPX, the user's GPS position on the route, and camps that can be moved along the route.

**Architecture:** A hand-written service worker caches the app shell and answers USGS tile requests from per-trip caches. "Save for offline" downloads the trip box's tiles (Cache Storage), GPX files and nearby trail lines, and writes the trip record (IndexedDB). Pure TypeScript modules port the engine's elevation and day-splitting rules so GPS position and moved camps are computed on the phone. The server does not change.

**Tech Stack:** TypeScript 5, Vite 6, maplibre-gl 5, Vitest 3 (jsdom), Playwright; new dev-only dependency `fake-indexeddb`. Python 3.12 / pytest only for one parity fixture.

**Spec:** `docs/superpowers/specs/2026-10-14-phase2-offline-design.md`

## Global Constraints

- No new runtime dependencies. One new dev dependency, `fake-indexeddb` (IndexedDB for Vitest); its commit message says why.
- The server and CLI do not change. Python changes are test-only: `tests/resplit_fixture.py`, `tests/test_resplit_fixture.py`.
- Never invent geometry: camps move only along the saved route line, GPS snaps only to it, unsaved map areas stay blank.
- Never invent facts: moved camps say "Location unverified"; water says "presence unknown"; anything unknown says "unknown". Never call a route "safe". No raw OSM ids (`osm:node/…`, `osm:way/…`) in user-facing text.
- Miles and feet in everything the user reads; meters internally.
- Tiles come only from `basemap.nationalmap.gov`, the trip box plus 0.5 mi (805 m), zoom 12–16, USGS Topo + USGS Imagery+Topo, at most 4 requests at a time, one retry per tile.
- The app never reloads itself; an update shows a banner and waits for a tap.
- `/api/*` is never cached by the service worker.
- Code style: plain TypeScript, no framework, HTML-string panels with every API/user string through `esc`, small modules.

## Review Focus

1. The phone locks or the app is closed in the middle of a save: a `trip-*` tile cache exists with no trip record. Expected: cleaned up at the next start, never shown as a trip. Test: Task 5 `cleanOrphans`.
2. The browser evicts a saved trip's tile cache while the record survives. Expected: opening it says the offline map was cleared and to save again, instead of silently showing a blank map. Test: Task 5 `tilesPresent`, used by Task 9's `openSaved`.
3. A GPS fix far from the route, on a loop's start/end, or on a stem walked twice. Expected: "Off route: … mi from the nearest point", or the right pass chosen from the last position with "(the route passes here twice)". Tests: Task 1 (`nearest`), Task 3 (`locate`).
4. A camp dragged off the route line or past its neighbour. Expected: refused with the reason, marker returns, days unchanged. Tests: Task 2 (`moveCamp`), Task 9 (`moveCampTo` redraw).
5. A save that hits the storage limit or loses some tiles. Expected: kept as "incomplete" with the reason (count saved, missing per zoom) on the card and in the trip view. Tests: Task 7, Task 8.

---

## File structure

| File | Responsibility |
|---|---|
| `web/src/elevation.ts` (new) | Engine elevation rules: `smooth`, `cumulativeGainLoss`, `interp`, `cumulativeFeet` |
| `web/src/routeline.ts` (new) | Route polyline: snap a point (`nearest`), `pointAt`, `slice`, `metersBetween` |
| `web/src/resplit.ts` (new) | Day split at given camps (`resplitDays`), moved-camp candidate (`withCamps`), `moveCamp`, `campHereIndex`, `campsOf` |
| `web/src/gps.ts` (new) | `locate` (where am I on the route) and `whereText` |
| `web/src/offline/tiles.ts` (new) | `tripBox`, `tileXY`, `tileList`, `estimate`, `megabytes`, `coverageNote` |
| `web/src/offline/db.ts` (new) | IndexedDB trip records: `putTrip`, `getTrip`, `listTrips`, `deleteTrip`, `cleanOrphans`, `tilesPresent` |
| `web/src/offline/save.ts` (new) | `saveTrip`: tiles, GPX, trails, record |
| `web/src/offline/register.ts` (new) | Service worker registration and update offer |
| `web/src/swbuild.ts` (new) | Builds `sw.js` from the template at `vite build` |
| `web/sw/sw.js` (new) | Service worker template |
| `web/src/selectors.ts` (new) | `currentCandidate(s)`: the trip with moved camps applied (memoized) |
| `web/src/panels/saved.ts` (new) | Saved trips view |
| `web/src/panels/trip.ts`, `plan.ts`, `profile.ts`, `format.ts`, `store.ts`, `types.ts`, `map.ts`, `app.ts`, `main.ts`, `styles.css`, `vite.config.ts`, `tsconfig.json`, `package.json` | Modified |
| `web/src/fixtures/resplit-parity.json` (generated, committed) | Engine day splits for fixed camps |
| `tests/resplit_fixture.py`, `tests/test_resplit_fixture.py` (new) | Generate / keep the fixture current |
| `web/e2e/offline.spec.ts` (new) | Save → offline → reopen → GPS → move camp → delete |
| `README.md`, `PROGRESS.md` | Tailscale setup and usage; progress |

---

### Task 1: Elevation rules and the route line

**Files:**
- Create: `web/src/elevation.ts`, `web/src/routeline.ts`
- Test: `web/src/elevation.test.ts`, `web/src/routeline.test.ts`

**Interfaces:**
- Consumes: `FT_PER_M` from `web/src/format.ts`; `Candidate` from `web/src/types.ts`.
- Produces:
  - `smooth(z: number[], n?: number): number[]`
  - `cumulativeGainLoss(z: number[], threshold?: number): { G: number[]; L: number[] }` (meters)
  - `interp(x: number, xs: number[], ys: number[]): number` (numpy.interp: clamps at the ends)
  - `cumulativeFeet(profile: Candidate["profile"]): { d: number[]; G: number[]; L: number[] } | null` (G, L in feet; null if no profile, fewer than 2 points, or any null elevation)
  - `interface Snap { alongM: number; offM: number; lon: number; lat: number }`
  - `class RouteLine { constructor(coords: [number, number][], lengthM: number); static of(c: Candidate): RouteLine; readonly length: number; nearest(lon: number, lat: number, hintM: number | null): { snap: Snap; ambiguous: boolean }; pointAt(alongM: number): [number, number]; slice(a: number, b: number): [number, number, number][] /* [lon, lat, alongM] */ }`
  - `metersBetween(lon1: number, lat1: number, lon2: number, lat2: number): number`

- [ ] **Step 1: Write the failing tests**

`web/src/elevation.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { cumulativeFeet, cumulativeGainLoss, interp, smooth } from "./elevation";

describe("elevation rules (ported from backcountry/elevation.py)", () => {
  it("smooths with a centered window that shrinks at the ends", () => {
    expect(smooth([0, 3, 6, 3, 0])).toEqual([0, 3, 4, 3, 0]);
  });
  it("counts a change only past the 2 m threshold, and always counts the tail", () => {
    const { G, L } = cumulativeGainLoss([100, 101, 100.5, 104, 103, 99, 100]);
    expect(G).toEqual([0, 0, 0, 4, 4, 4, 5]);
    expect(L).toEqual([0, 0, 0, 0, 0, 5, 5]);
  });
  it("interpolates like numpy.interp, clamping outside the range", () => {
    expect(interp(5, [0, 10], [0, 100])).toBe(50);
    expect(interp(-1, [0, 10], [0, 100])).toBe(0);
    expect(interp(11, [0, 10], [0, 100])).toBe(100);
    expect(interp(15, [0, 10, 20], [0, 100, 300])).toBe(200);
  });
  it("gives cumulative feet, or null when any elevation is unknown", () => {
    const cum = cumulativeFeet({ d_m: [0, 100, 200], z_m: [100, 110, 120] })!;
    expect(cum.d).toEqual([0, 100, 200]);
    expect(cum.G[2]).toBeCloseTo(20 * 3.28084, 6);
    expect(cumulativeFeet({ d_m: [0, 100], z_m: [100, null] })).toBeNull();
    expect(cumulativeFeet(null)).toBeNull();
  });
});
```

`web/src/routeline.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { metersBetween, RouteLine } from "./routeline";

const M_PER_DEG = 111195.08;
const LON_M = M_PER_DEG * Math.cos((39 * Math.PI) / 180); // meters per degree of longitude at 39°N

describe("RouteLine", () => {
  const north: [number, number][] = [[-79.4, 39.0], [-79.4, 39.01]];
  const len = 0.01 * M_PER_DEG;

  it("snaps a point to the nearest place on the line, in meters along and off it", () => {
    const line = new RouteLine(north, len);
    const { snap, ambiguous } = line.nearest(-79.4 + 50 / LON_M, 39.005, null);
    expect(snap.alongM).toBeCloseTo(len / 2, 0);
    expect(snap.offM).toBeCloseTo(50, 0);
    expect(ambiguous).toBe(false);
  });

  it("scales distances so the line's length equals the engine's length", () => {
    const line = new RouteLine(north, 2 * len);
    expect(line.length).toBeCloseTo(2 * len, 6);
    expect(line.nearest(-79.4, 39.01, null).snap.alongM).toBeCloseTo(2 * len, 3);
    const [lon, lat] = line.pointAt(len);
    expect(lon).toBeCloseTo(-79.4, 9); expect(lat).toBeCloseTo(39.005, 9);
  });

  it("slices between two distances with interpolated ends and the vertices in between", () => {
    const line = new RouteLine([[-79.4, 39.0], [-79.4, 39.005], [-79.4, 39.01]], len);
    const pts = line.slice(len * 0.25, len * 0.75);
    expect(pts).toHaveLength(3);
    expect(pts[0]![1]).toBeCloseTo(39.0025, 9); expect(pts[1]![1]).toBeCloseTo(39.005, 9); expect(pts[2]![1]).toBeCloseTo(39.0075, 9);
    expect(pts[1]![2]).toBeCloseTo(len / 2, 3);
  });

  it("flags a loop's start/end as ambiguous and lets the last position choose", () => {
    const dLat = 0.009, dLon = 1000 / LON_M;
    const sq: [number, number][] = [[-79.4, 39], [-79.4, 39 + dLat], [-79.4 + dLon, 39 + dLat], [-79.4 + dLon, 39], [-79.4, 39]];
    const line = new RouteLine(sq, 4001.6);
    const p = [-79.4 + 3 / LON_M, 39 + 3 / M_PER_DEG] as const;
    expect(line.nearest(p[0], p[1], null).ambiguous).toBe(true);
    expect(line.nearest(p[0], p[1], 3900).snap.alongM).toBeGreaterThan(3900);
    expect(line.nearest(p[0], p[1], 50).snap.alongM).toBeLessThan(100);
  });

  it("tells the two passes of a stem apart by the last position", () => {
    // stem S->P, loop P->Q->R->P, back P->S
    const S: [number, number] = [-79.4, 39], P: [number, number] = [-79.4, 39.005];
    const Q: [number, number] = [-79.39, 39.01], R: [number, number] = [-79.41, 39.01];
    const line = new RouteLine([S, P, Q, R, P, S], 0); // 0: no scaling
    const mid: [number, number] = [-79.4, 39.0025];
    const out = line.nearest(mid[0], mid[1], 100);
    const back = line.nearest(mid[0], mid[1], line.length - 100);
    expect(out.ambiguous).toBe(true);
    expect(out.snap.alongM).toBeLessThan(line.length / 2);
    expect(back.snap.alongM).toBeGreaterThan(line.length / 2);
  });

  it("measures short distances in meters", () => {
    expect(metersBetween(-79.4, 39, -79.4, 39.001)).toBeCloseTo(111.2, 1);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/elevation.test.ts src/routeline.test.ts`
Expected: FAIL, `Failed to resolve import "./elevation"` and `"./routeline"`.

- [ ] **Step 3: Implement**

`web/src/elevation.ts`:
```ts
// The engine's elevation rules (src/backcountry/elevation.py), ported line for line so the phone's
// gain numbers match the days table: 3-sample centered smoothing, then gain/loss with 2 m hysteresis.
import { FT_PER_M } from "./format";
import type { Candidate } from "./types";

export const SMOOTH_SAMPLES = 3;
export const HYSTERESIS_M = 2.0;

export function smooth(z: number[], n = SMOOTH_SAMPLES): number[] {
  const cs = [0];
  for (const v of z) cs.push(cs[cs.length - 1]! + v);
  return z.map((_, i) => {
    const k = Math.min(Math.floor(n / 2), i, z.length - 1 - i);
    return (cs[i + k + 1]! - cs[i - k]!) / (2 * k + 1);
  });
}

export function cumulativeGainLoss(z: number[], threshold = HYSTERESIS_M): { G: number[]; L: number[] } {
  const G = new Array<number>(z.length).fill(0), L = new Array<number>(z.length).fill(0);
  if (!z.length) return { G, L };
  let gain = 0, loss = 0, ref = z[0]!;
  for (let i = 1; i < z.length; i++) {
    const diff = z[i]! - ref;
    if (diff >= threshold) { gain += diff; ref = z[i]!; }
    else if (diff <= -threshold) { loss -= diff; ref = z[i]!; }
    G[i] = gain; L[i] = loss;
  }
  const tail = z[z.length - 1]! - ref;
  if (tail > 0) G[z.length - 1]! += tail; else L[z.length - 1]! -= tail;
  return { G, L };
}

/** numpy.interp: linear between the two neighbours, clamped to the first/last value outside the range. */
export function interp(x: number, xs: number[], ys: number[]): number {
  if (x <= xs[0]!) return ys[0]!;
  if (x >= xs[xs.length - 1]!) return ys[ys.length - 1]!;
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid]! <= x) lo = mid; else hi = mid; }
  const t = (x - xs[lo]!) / (xs[hi]! - xs[lo]!);
  return ys[lo]! + t * (ys[hi]! - ys[lo]!);
}

/** Running gain and loss in feet along the profile, or null when elevation is unknown anywhere. */
export function cumulativeFeet(profile: Candidate["profile"]): { d: number[]; G: number[]; L: number[] } | null {
  if (!profile || profile.d_m.length < 2 || profile.z_m.some((z) => z == null)) return null;
  const { G, L } = cumulativeGainLoss(smooth(profile.z_m as number[]));
  return { d: profile.d_m, G: G.map((g) => g * FT_PER_M), L: L.map((l) => l * FT_PER_M) };
}
```

`web/src/routeline.ts`:
```ts
// The saved route as a polyline with distances along it: snap a GPS fix or a dragged camp to it, find
// the point at a given mile, cut it into days. Never creates geometry: every point is on the saved line.
// Distances use a local equirectangular projection around the route (well under a foot of error over a
// trip box), then are scaled so the line's total equals the engine's length_m, which is measured in UTM.
import type { Candidate } from "./types";

const R = 6371008.8;
const RAD = Math.PI / 180;
const TIE_M = 15;     // fixes within ~50 ft of two passes of the route are ambiguous
const SAME_PASS_M = 100;

export interface Snap { alongM: number; offM: number; lon: number; lat: number }

export function metersBetween(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const k = Math.cos(((lat1 + lat2) / 2) * RAD);
  return Math.hypot((lon2 - lon1) * RAD * R * k, (lat2 - lat1) * RAD * R);
}

export class RouteLine {
  private readonly xs: number[] = [];
  private readonly ys: number[] = [];
  private readonly cum: number[] = [0];
  private readonly lon0: number;
  private readonly lat0: number;
  private readonly kx: number;
  private readonly scale: number;

  /** `lengthM` is the engine's length for this line; 0 keeps the projected length unscaled. */
  constructor(coords: [number, number][], lengthM: number) {
    this.lon0 = coords[0]?.[0] ?? 0;
    this.lat0 = coords[0]?.[1] ?? 0;
    this.kx = Math.cos(this.lat0 * RAD) * RAD * R;
    for (const [lon, lat] of coords) { this.xs.push((lon - this.lon0) * this.kx); this.ys.push((lat - this.lat0) * RAD * R); }
    for (let i = 1; i < this.xs.length; i++) {
      this.cum.push(this.cum[i - 1]! + Math.hypot(this.xs[i]! - this.xs[i - 1]!, this.ys[i]! - this.ys[i - 1]!));
    }
    const raw = this.cum[this.cum.length - 1]!;
    this.scale = lengthM > 0 && raw > 0 ? lengthM / raw : 1;
  }

  static of(c: Candidate): RouteLine { return new RouteLine(c.geometry.coordinates, c.length_m); }

  get length(): number { return this.cum[this.cum.length - 1]! * this.scale; }

  private toLonLat(x: number, y: number): [number, number] { return [this.lon0 + x / this.kx, this.lat0 + y / (RAD * R)]; }

  /** The nearest point on the line. Where the line passes the same place twice, `hintM` (the last known
   * distance along) picks the pass, and `ambiguous` says there was a choice. */
  nearest(lon: number, lat: number, hintM: number | null): { snap: Snap; ambiguous: boolean } {
    const px = (lon - this.lon0) * this.kx, py = (lat - this.lat0) * RAD * R;
    const cands: Snap[] = [];
    for (let i = 1; i < this.xs.length; i++) {
      const ax = this.xs[i - 1]!, ay = this.ys[i - 1]!, dx = this.xs[i]! - ax, dy = this.ys[i]! - ay;
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
      const x = ax + t * dx, y = ay + t * dy;
      const [slon, slat] = this.toLonLat(x, y);
      cands.push({ alongM: (this.cum[i - 1]! + t * Math.sqrt(len2)) * this.scale, offM: Math.hypot(px - x, py - y), lon: slon, lat: slat });
    }
    if (!cands.length) return { snap: { alongM: 0, offM: metersBetween(lon, lat, this.lon0, this.lat0), lon: this.lon0, lat: this.lat0 }, ambiguous: false };
    const best = Math.min(...cands.map((c) => c.offM));
    // One representative (the closest) per pass: candidates close in distance-along are the same pass.
    const close = cands.filter((c) => c.offM <= best + TIE_M).sort((a, b) => a.alongM - b.alongM);
    const passes: Snap[] = [];
    for (const c of close) {
      const last = passes[passes.length - 1];
      if (last && c.alongM - last.alongM < SAME_PASS_M) { if (c.offM < last.offM) passes[passes.length - 1] = c; }
      else passes.push(c);
    }
    const pick = hintM === null
      ? passes.reduce((a, b) => (b.offM < a.offM ? b : a))
      : passes.reduce((a, b) => (Math.abs(b.alongM - hintM) < Math.abs(a.alongM - hintM) ? b : a));
    return { snap: pick, ambiguous: passes.length > 1 };
  }

  pointAt(alongM: number): [number, number] {
    const raw = Math.max(0, Math.min(this.cum[this.cum.length - 1]!, alongM / this.scale));
    let i = 1;
    while (i < this.cum.length - 1 && this.cum[i]! < raw) i++;
    const seg = this.cum[i]! - this.cum[i - 1]!;
    const t = seg ? (raw - this.cum[i - 1]!) / seg : 0;
    return this.toLonLat(this.xs[i - 1]! + t * (this.xs[i]! - this.xs[i - 1]!), this.ys[i - 1]! + t * (this.ys[i]! - this.ys[i - 1]!));
  }

  /** [lon, lat, alongM] from a to b: the two ends, plus every vertex strictly between them. */
  slice(a: number, b: number): [number, number, number][] {
    const out: [number, number, number][] = [[...this.pointAt(a), a]];
    for (let i = 0; i < this.cum.length; i++) {
      const m = this.cum[i]! * this.scale;
      if (m > a + 1e-6 && m < b - 1e-6) out.push([...this.toLonLat(this.xs[i]!, this.ys[i]!), m]);
    }
    out.push([...this.pointAt(b), b]);
    return out;
  }
}
```

- [ ] **Step 4: Run them to verify they pass**

Run: `cd web && npx vitest run src/elevation.test.ts src/routeline.test.ts && npx tsc --noEmit`
Expected: PASS (10 tests), tsc clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/elevation.ts web/src/elevation.test.ts web/src/routeline.ts web/src/routeline.test.ts
git commit -m "web: engine elevation rules and a route line for snapping and slicing"
```

---

### Task 2: Day split at moved camps, with engine parity

**Files:**
- Create: `web/src/resplit.ts`, `tests/resplit_fixture.py`, `tests/test_resplit_fixture.py`, `web/src/fixtures/resplit-parity.json` (generated)
- Modify: `web/src/types.ts` (Camp gains `moved?: boolean`), `web/tsconfig.json` (`resolveJsonModule`)
- Test: `web/src/resplit.test.ts`

**Interfaces:**
- Consumes: Task 1 `cumulativeFeet`, `interp`, `RouteLine`, `metersBetween`; `M_PER_MI` from `format.ts`.
- Produces:
  - `interface Limits { daily_min_m: number; daily_max_m: number; max_gain_ft: number | null }`
  - `resplitDays(profile: Candidate["profile"], lengthM: number, campsM: number[], limits: Limits): { days: Day[]; violations: string[] }` (throws if camps are out of order or outside the route)
  - `withCamps(c: Candidate, campsM: number[], line?: RouteLine): Candidate` (new plan/pieces/waypoints/feature days; original untouched)
  - `campsOf(c: Candidate): number[]`
  - `moveCamp(current: number[], index: number, alongM: number, endM: number): { campsM: number[] } | { error: string }` (index is 1-based)
  - `campHereIndex(day: number | null, nights: number): number | null`
  - `NEARBY_CAMP_M = 500`

- [ ] **Step 1: Write the fixture generator and its freshness test**

`tests/resplit_fixture.py`:
```python
"""The engine's day split for fixed camp positions, as a fixture for the phone's resplit.ts.

    uv run python tests/resplit_fixture.py   # rewrites web/src/fixtures/resplit-parity.json

tests/test_resplit_fixture.py fails when the committed file no longer matches what the engine produces,
so the phone's numbers and the engine's can't drift apart silently.
"""

import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import CRS, build_network  # noqa: E402

from backcountry.elevation import densify  # noqa: E402
from backcountry.graph import build_graph  # noqa: E402
from backcountry.route import shortest_route  # noqa: E402
from backcountry.split import split_route  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "web" / "src" / "fixtures" / "resplit-parity.json"
M = 1609.344
LIMITS = {"daily_min_m": 0.5 * M, "daily_max_m": 1.0 * M, "max_gain_ft": 150.0}
CASES = [  # (name, has elevation, camp distances in meters along the 3,500 m test route)
    ("one night, day 2 too long", True, [1400.0]),
    ("one night, day 1 too short", True, [600.0]),
    ("two nights", True, [700.0, 2600.0]),
    ("no elevation", False, [1750.0]),
]


def _route(elevation: bool):
    net = build_network()
    tg = build_graph(net.doc(), crs=CRS)
    route = shortest_route(tg, net.ids["A"], net.ids["F"])
    d, _ = densify(route.line_utm)
    if elevation:  # rolling terrain: some ups and downs under the 2 m hysteresis, some well over it
        z = [1000 + 12 * math.sin(x / 150) + 3 * math.sin(x / 23) + 0.01 * x for x in d]
        route.profile, route.gain_ft = {"d_m": d, "z_m": z}, 0.0  # split_route only needs gain_ft to be known
    else:
        route.profile, route.gain_ft = {"d_m": d, "z_m": [None] * len(d)}, None
    return route


def build() -> dict:
    cases = []
    for name, elevation, camps in CASES:
        route = _route(elevation)
        plan = split_route(route, len(camps), LIMITS["daily_min_m"], LIMITS["daily_max_m"], LIMITS["max_gain_ft"],
                           pins_m=camps)
        days = [{k: (float(v) if isinstance(v, float) else v) for k, v in asdict(day).items()} for day in plan.days]
        cases.append({"name": name, "profile": route.profile, "camps_m": camps, "days": days,
                      "violations": list(plan.violations)})
    return {"limits": LIMITS, "cases": cases}


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {FIXTURE}")
```

`tests/test_resplit_fixture.py`:
```python
import json

from resplit_fixture import FIXTURE, build


def test_resplit_fixture_matches_the_engine():
    assert FIXTURE.exists(), "run `uv run python tests/resplit_fixture.py`"
    assert json.loads(FIXTURE.read_text()) == json.loads(json.dumps(build()))
```

Run: `uv run pytest tests/test_resplit_fixture.py -q`
Expected: FAIL, "run `uv run python tests/resplit_fixture.py`".

Then: `uv run python tests/resplit_fixture.py && uv run pytest tests/test_resplit_fixture.py -q`
Expected: "wrote …/resplit-parity.json", then 1 passed. Check by eye that the first case lists a "daily max" violation and at least one case lists a "daily gain cap" violation; if no case has a gain violation, lower `max_gain_ft` and regenerate (ledger it).

- [ ] **Step 2: Write the failing TypeScript tests**

Add `"resolveJsonModule": true` to `compilerOptions` in `web/tsconfig.json`.

`web/src/resplit.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import fixture from "./fixtures/resplit-parity.json";
import { campHereIndex, campsOf, moveCamp, resplitDays, withCamps } from "./resplit";
import type { Candidate, Plan } from "./types";

describe("resplitDays matches the engine's split_route", () => {
  for (const k of fixture.cases) {
    it(k.name, () => {
      const profile = k.profile as Candidate["profile"];
      const end = profile!.d_m[profile!.d_m.length - 1]!;
      const { days, violations } = resplitDays(profile, end, k.camps_m, fixture.limits);
      expect(days).toHaveLength(k.days.length);
      days.forEach((d, i) => {
        const e = k.days[i]!;
        expect(d.index).toBe(e.index); expect(d.start_label).toBe(e.start_label); expect(d.end_label).toBe(e.end_label);
        expect(d.start_m).toBeCloseTo(e.start_m, 6); expect(d.end_m).toBeCloseTo(e.end_m, 6); expect(d.length_m).toBeCloseTo(e.length_m, 6);
        if (e.gain_ft === null) { expect(d.gain_ft).toBeNull(); expect(d.loss_ft).toBeNull(); }
        else { expect(d.gain_ft!).toBeCloseTo(e.gain_ft, 1); expect(d.loss_ft!).toBeCloseTo(e.loss_ft as number, 1); }
      });
      expect(violations).toEqual(k.violations);
    });
  }
  it("refuses camps out of order or outside the route", () => {
    const p = fixture.cases[0]!.profile as Candidate["profile"];
    expect(() => resplitDays(p, 3500, [2000, 1000], fixture.limits)).toThrow(/in order/);
    expect(() => resplitDays(p, 3500, [0], fixture.limits)).toThrow(/in order/);
  });
});

// A 3,000 m route due north at 79.4°W, climbing steadily 1000 -> 1300 m, split into two days at 1,500 m.
const M_PER_DEG = 111195.08;
const LON_M = M_PER_DEG * Math.cos((39 * Math.PI) / 180);
function candidate(): Candidate {
  const d = Array.from({ length: 31 }, (_, i) => i * 100);
  const plan: Plan = { nights: 1, daily_min_m: 1000, daily_max_m: 2000, max_gain_ft: null, ok: true, penalty: 0, notes: ["engine note"], violations: [],
    days: [{ index: 1, start_m: 0, end_m: 1500, length_m: 1500, gain_ft: 492, loss_ft: 0, start_label: "Start", end_label: "Camp 1" },
           { index: 2, start_m: 1500, end_m: 3000, length_m: 1500, gain_ft: 492, loss_ft: 0, start_label: "Camp 1", end_label: "End" }],
    camps: [{ index: 1, dist_m: 1500, lat: 39 + 1500 / M_PER_DEG, lon: -79.4, on_source: "osm:way/1", on_name: "North Trail", nearby: [],
      note: "camp_location: unverified", pinned: false, site: null, nearest_water: null }] };
  return { index: 1, kind: "loop", length_m: 3000, gain_ft: 984, loss_ft: 0, road_m: 0, last_resort_m: 0, repeated_m: 0, penalty: 0,
    segments: [{ label: "North Trail", highway: "path", is_road: false, last_resort: false, length_m: 2000, sources: ["osm:way/1"] },
               { label: "Upper Trail", highway: "path", is_road: false, last_resort: false, length_m: 1000, sources: ["osm:way/2"] }],
    geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39 + 3000 / M_PER_DEG]] },
    pieces: [], plan, profile: { d_m: d, z_m: d.map((x) => 1000 + x / 10) }, title: "t", description: "d", notes: [],
    waypoints: [{ name: "Start", desc: "s", lat: 39, lon: -79.4, dist_m: 0, ele: 1000, kind: "start" },
                { name: "End", desc: "e", lat: 39 + 3000 / M_PER_DEG, lon: -79.4, dist_m: 3000, ele: 1300, kind: "end" },
                { name: "Camp 1", desc: "c", lat: 39 + 1500 / M_PER_DEG, lon: -79.4, dist_m: 1500, ele: 1150, kind: "camp" },
                { name: "Junction", desc: "j", lat: 39 + 2000 / M_PER_DEG, lon: -79.4, dist_m: 2000, ele: 1200, kind: "junction" }],
    features: [
      { source: "osm:node/9", kind: "camp_site", name: "Knob Camp", lat: 39 + 2400 / M_PER_DEG, lon: -79.4 + 60 / LON_M, dist_m: 2400, off_m: 60, tags: {}, meta: { timestamp: null, version: null }, url: null, day: 2 },
      { source: "osm:node/8", kind: "spring", name: "Cold Spring", lat: 39 + 2600 / M_PER_DEG, lon: -79.4, dist_m: 2600, off_m: 0, tags: {}, meta: { timestamp: null, version: null }, url: null, day: 2 },
      { source: "osm:node/7", kind: "spring", name: "Low Spring", lat: 39 + 200 / M_PER_DEG, lon: -79.4, dist_m: 200, off_m: 0, tags: {}, meta: { timestamp: null, version: null }, url: null, day: 1 }] };
}

describe("withCamps", () => {
  it("moves the camp along the route and recomputes days, pieces, waypoints and feature days", () => {
    const c = candidate();
    const out = withCamps(c, [2500]);
    expect(out.plan!.days.map((d) => d.length_m)).toEqual([2500, 500]);
    expect(out.plan!.days[0]!.gain_ft!).toBeCloseTo(250 * 3.28084, 0);
    const camp = out.plan!.camps[0]!;
    expect(camp.lat).toBeCloseTo(39 + 2500 / M_PER_DEG, 7); expect(camp.lon).toBeCloseTo(-79.4, 9);
    expect(camp.moved).toBe(true); expect(camp.on_name).toBe("Upper Trail"); expect(camp.note).toBe("camp_location: unverified");
    expect(camp.nearby.map((n) => n.name)).toEqual(["Knob Camp"]);
    expect(camp.nearest_water!.name).toBe("Cold Spring"); expect(camp.nearest_water!.distance_m).toBeCloseTo(100, 0);
    expect(out.pieces).toHaveLength(2);
    expect(out.pieces[0]!.points[0]!.slice(0, 2)).toEqual([-79.4, 39]);
    expect(out.pieces[0]!.points.at(-1)![1]).toBeCloseTo(39 + 2500 / M_PER_DEG, 7);
    expect(out.pieces[0]!.points.at(-1)![2]).toBeCloseTo(1250, 6);
    expect(out.waypoints.map((w) => w.kind)).toEqual(["start", "end", "camp", "junction"]);
    expect(out.features.find((f) => f.name === "Knob Camp")!.day).toBe(1);
    expect(out.plan!.notes).toContain("Camps moved by you on this phone; the route itself is unchanged.");
    expect(c.plan!.camps[0]!.dist_m).toBe(1500); // the saved candidate is untouched
  });
  it("names constraint violations like the engine", () => {
    const out = withCamps(candidate(), [500]);
    expect(out.plan!.violations).toEqual(["Day 1: 0.31 mi is under the 0.62 mi daily minimum", "Day 2: 1.55 mi is over the 1.24 mi daily max"]);
    expect(out.plan!.ok).toBe(false);
  });
  it("needs a day plan and the right number of camps", () => {
    expect(() => withCamps({ ...candidate(), plan: null }, [1000])).toThrow(/no day plan/);
    expect(() => withCamps(candidate(), [1000, 2000])).toThrow(/1 camp/);
  });
});

describe("moveCamp and campHereIndex", () => {
  it("keeps camps in order and inside the route", () => {
    expect(moveCamp([1000, 2000], 1, 1500, 3000)).toEqual({ campsM: [1500, 2000] });
    expect(moveCamp([1000, 2000], 1, 2100, 3000)).toEqual({ error: "Camp 1 has to stay between the start and Camp 2." });
    expect(moveCamp([1000, 2000], 2, 3000, 3000)).toEqual({ error: "Camp 2 has to stay between Camp 1 and the end." });
  });
  it("moves tonight's camp, never one behind you, and nothing on the last day", () => {
    expect(campHereIndex(1, 2)).toBe(1); expect(campHereIndex(2, 2)).toBe(2);
    expect(campHereIndex(3, 2)).toBeNull(); expect(campHereIndex(null, 2)).toBeNull();
  });
  it("reads the current camps", () => { expect(campsOf(candidate())).toEqual([1500]); });
});
```

Add to `Camp` in `web/src/types.ts`: `moved?: boolean;` (after `nearest_water`).

- [ ] **Step 3: Run to verify they fail**

Run: `cd web && npx vitest run src/resplit.test.ts`
Expected: FAIL, `Failed to resolve import "./resplit"`.

- [ ] **Step 4: Implement**

`web/src/resplit.ts`:
```ts
// Re-cut a saved trip's days at camps the user moved, on the phone, with the engine's rules
// (split.py: day lengths, interpolated gain/loss, violation wording). The route never changes.
import { cumulativeFeet, interp } from "./elevation";
import { M_PER_MI } from "./format";
import { metersBetween, RouteLine } from "./routeline";
import type { Camp, Candidate, Day, NearbySite, Piece, Segment, Waypoint } from "./types";

export const NEARBY_CAMP_M = 500; // split.py NEARBY_CAMP_M
const CAMP_KINDS = new Set(["camp_site", "camp_pitch"]);

export interface Limits { daily_min_m: number; daily_max_m: number; max_gain_ft: number | null }

const grouped = (n: number) => Math.round(n).toLocaleString("en-US");
const mi2 = (m: number) => (m / M_PER_MI).toFixed(2);

export function resplitDays(profile: Candidate["profile"], lengthM: number, campsM: number[], limits: Limits): { days: Day[]; violations: string[] } {
  const end = profile && profile.d_m.length ? profile.d_m[profile.d_m.length - 1]! : lengthM;
  const bounds = [0, ...campsM, end];
  for (let i = 1; i < bounds.length; i++) {
    if (!(bounds[i]! > bounds[i - 1]!)) throw new Error("Camps must stay in order and strictly inside the route.");
  }
  const cum = cumulativeFeet(profile);
  const n = bounds.length - 1;
  const days: Day[] = [], violations: string[] = [];
  for (let k = 0; k < n; k++) {
    const a = bounds[k]!, b = bounds[k + 1]!, length = b - a;
    const gain = cum ? interp(b, cum.d, cum.G) - interp(a, cum.d, cum.G) : null;
    const loss = cum ? interp(b, cum.d, cum.L) - interp(a, cum.d, cum.L) : null;
    days.push({ index: k + 1, start_m: a, end_m: b, length_m: length, gain_ft: gain, loss_ft: loss,
      start_label: k === 0 ? "Start" : `Camp ${k}`, end_label: k === n - 1 ? "End" : `Camp ${k + 1}` });
    if (length < limits.daily_min_m) violations.push(`Day ${k + 1}: ${mi2(length)} mi is under the ${mi2(limits.daily_min_m)} mi daily minimum`);
    if (length > limits.daily_max_m) violations.push(`Day ${k + 1}: ${mi2(length)} mi is over the ${mi2(limits.daily_max_m)} mi daily max`);
    if (gain !== null && limits.max_gain_ft !== null && gain > limits.max_gain_ft) {
      violations.push(`Day ${k + 1}: ${grouped(gain)} ft of climbing is over the ${grouped(limits.max_gain_ft)} ft daily gain cap`);
    }
  }
  return { days, violations };
}

function segmentAt(c: Candidate, m: number): Segment | undefined {
  let cum = 0;
  for (const s of c.segments) { cum += s.length_m; if (m <= cum + 1e-6) return s; }
  return c.segments[c.segments.length - 1];
}

/** The candidate with its camps at `campsM` (meters along, in order). Everything else is copied. */
export function withCamps(c: Candidate, campsM: number[], line: RouteLine = RouteLine.of(c)): Candidate {
  const p = c.plan;
  if (!p) throw new Error("This trip has no day plan, so there are no camps to move.");
  if (campsM.length !== p.camps.length) throw new Error(`This trip has ${p.camps.length} camp(s); got ${campsM.length}.`);
  const { days, violations } = resplitDays(c.profile, c.length_m, campsM, p);
  const prof = c.profile && !c.profile.z_m.some((z) => z == null) ? c.profile : null;
  const ele = (m: number) => (prof ? interp(m, prof.d_m, prof.z_m as number[]) : null);
  const sites = c.features.filter((f) => CAMP_KINDS.has(f.kind));
  const water = c.features.filter((f) => !CAMP_KINDS.has(f.kind) && !f.kind.startsWith("usfs"));
  const camps: Camp[] = campsM.map((m, i) => {
    const [lon, lat] = line.pointAt(m);
    const seg = segmentAt(c, m);
    const nearby: NearbySite[] = sites
      .map((s) => ({ source: s.source, kind: s.kind, name: s.name, url: s.url, distance_m: metersBetween(lon, lat, s.lon, s.lat) }))
      .filter((s) => s.distance_m <= NEARBY_CAMP_M)
      .sort((a, b) => a.distance_m - b.distance_m);
    let nearest_water: Camp["nearest_water"] = null;
    for (const w of water) {
      const d = metersBetween(lon, lat, w.lon, w.lat);
      if (!nearest_water || d < nearest_water.distance_m) nearest_water = { source: w.source, kind: w.kind, name: w.name, distance_m: d };
    }
    return { index: i + 1, dist_m: m, lat, lon, on_source: seg?.sources[0] ?? "", on_name: seg?.label ?? null, nearby,
      note: "camp_location: unverified", pinned: false, site: null, nearest_water, moved: true };
  });
  const pieces: Piece[] = days.map((d) => ({
    name: `Day ${d.index}: ${d.start_label} to ${d.end_label}`,
    points: line.slice(d.start_m, d.end_m).map(([lon, lat, m]) => [lon, lat, ele(m)] as [number, number, number | null]),
    start_m: d.start_m, end_m: d.end_m, length_m: d.length_m, gain_ft: d.gain_ft, loss_ft: d.loss_ft, day: d.index,
  }));
  const campWps: Waypoint[] = camps.map((cp) => ({
    name: `Camp ${cp.index}`, kind: "camp", lat: cp.lat, lon: cp.lon, dist_m: cp.dist_m, ele: ele(cp.dist_m),
    desc: `Camp ${cp.index}, mile ${(cp.dist_m / M_PER_MI).toFixed(1)}, on ${cp.on_name ?? "an unnamed trail"}, moved by you. Location unverified.`,
  }));
  const others = c.waypoints.filter((w) => w.kind !== "camp");
  const waypoints = [...others.filter((w) => w.kind !== "junction"), ...campWps, ...others.filter((w) => w.kind === "junction")];
  const features = c.features.map((f) => ({ ...f, day: days.find((d) => d.start_m <= f.dist_m && f.dist_m <= d.end_m)?.index ?? null }));
  const notes = [...p.notes, "Camps moved by you on this phone; the route itself is unchanged."];
  return { ...c, pieces, waypoints, features, plan: { ...p, days, camps, violations, ok: violations.length === 0, notes } };
}

export function campsOf(c: Candidate): number[] {
  return c.plan?.camps.map((cp) => cp.dist_m) ?? [];
}

/** Move camp `index` (1-based) to `alongM`, keeping every camp strictly between its neighbours. */
export function moveCamp(current: number[], index: number, alongM: number, endM: number): { campsM: number[] } | { error: string } {
  const prev = index > 1 ? current[index - 2]! : 0;
  const next = index < current.length ? current[index]! : endM;
  if (!(alongM > prev && alongM < next)) {
    return { error: `Camp ${index} has to stay between ${index > 1 ? `Camp ${index - 1}` : "the start"} and ${index < current.length ? `Camp ${index + 1}` : "the end"}.` };
  }
  const out = [...current];
  out[index - 1] = alongM;
  return { campsM: out };
}

/** "Camp here" moves tonight's camp: the end of the day you're on. None on the last day. */
export function campHereIndex(day: number | null, nights: number): number | null {
  return day !== null && day >= 1 && day <= nights ? day : null;
}
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd web && npx vitest run src/resplit.test.ts && npx tsc --noEmit && cd .. && uv run pytest tests/test_resplit_fixture.py -q`
Expected: all PASS; tsc clean.

- [ ] **Step 6: Commit**

```bash
git add web/src/resplit.ts web/src/resplit.test.ts web/src/fixtures/resplit-parity.json web/src/types.ts web/tsconfig.json tests/resplit_fixture.py tests/test_resplit_fixture.py
git commit -m "web: re-split days at moved camps with the engine's rules, checked against split_route"
```

---

### Task 3: Where am I on the route

**Files:**
- Create: `web/src/gps.ts`
- Test: `web/src/gps.test.ts`

**Interfaces:**
- Consumes: Task 1 `RouteLine`, `cumulativeFeet`, `interp`; `FT_PER_M`, `M_PER_MI`.
- Produces:
  - `interface Fix { lon: number; lat: number; accuracyM: number }`
  - `type Where = { onRoute: true; alongM: number; offM: number; day: number | null; next: { label: string; distM: number; gainFt: number | null }; ambiguous: boolean; accuracyM: number } | { onRoute: false; offM: number; accuracyM: number }`
  - `locate(c: Candidate, fix: Fix, lastAlongM: number | null, line?: RouteLine): Where`
  - `whereText(w: Where, lengthM: number): string`
  - `OFF_ROUTE_M = 400`, `ROUGH_M = 91.44`

- [ ] **Step 1: Write the failing tests**

`web/src/gps.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { locate, whereText, type Where } from "./gps";
import type { Candidate } from "./types";

const M = 1609.344;
const M_PER_DEG = 111195.08;
const LON_M = M_PER_DEG * Math.cos((39 * Math.PI) / 180);

function cand(over: Partial<Candidate> = {}): Candidate {
  const d = Array.from({ length: 31 }, (_, i) => i * 100);
  return { index: 1, kind: "loop", length_m: 3000, gain_ft: 984, loss_ft: 0, road_m: 0, last_resort_m: 0, repeated_m: 0, penalty: 0, segments: [],
    geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39 + 3000 / M_PER_DEG]] }, pieces: [], waypoints: [], features: [],
    profile: { d_m: d, z_m: d.map((x) => 1000 + x / 10) }, title: "t", description: "d", notes: [],
    plan: { nights: 1, daily_min_m: 0, daily_max_m: 1e9, max_gain_ft: null, ok: true, penalty: 0, notes: [], violations: [], camps: [],
      days: [{ index: 1, start_m: 0, end_m: 1500, length_m: 1500, gain_ft: null, loss_ft: null, start_label: "Start", end_label: "Camp 1" },
             { index: 2, start_m: 1500, end_m: 3000, length_m: 1500, gain_ft: null, loss_ft: null, start_label: "Camp 1", end_label: "End" }] },
    ...over };
}
const fixAt = (alongM: number, eastM: number, accuracyM = 5) => ({ lon: -79.4 + eastM / LON_M, lat: 39 + alongM / M_PER_DEG, accuracyM });

describe("locate", () => {
  it("gives mile, day, distance and climb to tonight's camp, and distance off the route", () => {
    const w = locate(cand(), fixAt(600, 50), null);
    if (!w.onRoute) throw new Error("expected on route");
    expect(w.alongM).toBeCloseTo(600, 0); expect(w.offM).toBeCloseTo(50, 0); expect(w.day).toBe(1);
    expect(w.next.label).toBe("Camp 1"); expect(w.next.distM).toBeCloseTo(900, 0);
    expect(w.next.gainFt!).toBeCloseTo(90 * 3.28084, 0);
  });
  it("says off route beyond 0.25 mi and gives no mile numbers", () => {
    const w = locate(cand(), fixAt(600, 500), null);
    expect(w.onRoute).toBe(false);
    expect(whereText(w, 3000)).toBe("Off route: 0.3 mi from the nearest point on the route · ±16 ft");
  });
  it("says gain unknown without elevation, and heads to the end without a day plan", () => {
    const w = locate(cand({ plan: null, profile: { d_m: [0, 3000], z_m: [null, null] } }), fixAt(600, 0), null);
    if (!w.onRoute) throw new Error("expected on route");
    expect(w.day).toBeNull(); expect(w.next.label).toBe("End"); expect(w.next.gainFt).toBeNull();
    expect(whereText(w, 3000)).toContain("1.5 mi (gain unknown) to End");
  });
});

describe("whereText", () => {
  it("reads like the design", () => {
    const w: Where = { onRoute: true, alongM: 7.2 * M, offM: 33.528, day: 2, next: { label: "Camp 2", distM: 2.1 * M, gainFt: 540 }, ambiguous: false, accuracyM: 4.572 };
    expect(whereText(w, 19 * M)).toBe("mile 7.2 of 19.0 · Day 2 · 2.1 mi and +540 ft to Camp 2 · 110 ft off route · ±15 ft");
  });
  it("says when the position is rough or the route passes twice", () => {
    const w: Where = { onRoute: true, alongM: 0, offM: 0, day: 1, next: { label: "Camp 1", distM: M, gainFt: 0 }, ambiguous: true, accuracyM: 137.16 };
    const t = whereText(w, 3 * M);
    expect(t).toContain("±450 ft, position rough"); expect(t).toContain("(the route passes here twice)");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npx vitest run src/gps.test.ts`
Expected: FAIL, `Failed to resolve import "./gps"`.

- [ ] **Step 3: Implement**

`web/src/gps.ts`:
```ts
// Where am I on the saved route? Pure math on the phone; nothing here needs the network.
import { cumulativeFeet, interp } from "./elevation";
import { FT_PER_M, M_PER_MI } from "./format";
import { RouteLine } from "./routeline";
import type { Candidate } from "./types";

export const OFF_ROUTE_M = 400; // ~0.25 mi
export const ROUGH_M = 91.44;   // 300 ft

export interface Fix { lon: number; lat: number; accuracyM: number }
export type Where =
  | { onRoute: true; alongM: number; offM: number; day: number | null; next: { label: string; distM: number; gainFt: number | null }; ambiguous: boolean; accuracyM: number }
  | { onRoute: false; offM: number; accuracyM: number };

export function locate(c: Candidate, fix: Fix, lastAlongM: number | null, line: RouteLine = RouteLine.of(c)): Where {
  const { snap, ambiguous } = line.nearest(fix.lon, fix.lat, lastAlongM);
  if (snap.offM > OFF_ROUTE_M) return { onRoute: false, offM: snap.offM, accuracyM: fix.accuracyM };
  const along = snap.alongM;
  const day = c.plan?.days.find((d) => d.start_m <= along && along <= d.end_m) ?? null;
  const target = day ? { m: day.end_m, label: day.end_label } : { m: c.length_m, label: "End" };
  const cum = cumulativeFeet(c.profile);
  const gainFt = cum ? Math.max(0, interp(target.m, cum.d, cum.G) - interp(along, cum.d, cum.G)) : null;
  return { onRoute: true, alongM: along, offM: snap.offM, day: day?.index ?? null,
    next: { label: target.label, distM: Math.max(0, target.m - along), gainFt }, ambiguous, accuracyM: fix.accuracyM };
}

const ft = (m: number) => `${Math.round(m * FT_PER_M).toLocaleString("en-US")} ft`;
const mi1 = (m: number) => (m / M_PER_MI).toFixed(1);

export function whereText(w: Where, lengthM: number): string {
  const acc = `±${ft(w.accuracyM)}${w.accuracyM > ROUGH_M ? ", position rough" : ""}`;
  if (!w.onRoute) return `Off route: ${mi1(w.offM)} mi from the nearest point on the route · ${acc}`;
  const parts = [`mile ${mi1(w.alongM)} of ${mi1(lengthM)}`];
  if (w.day !== null) parts.push(`Day ${w.day}`);
  const climb = w.next.gainFt === null ? " (gain unknown)" : ` and +${Math.round(w.next.gainFt).toLocaleString("en-US")} ft`;
  parts.push(`${mi1(w.next.distM)} mi${climb} to ${w.next.label}`, `${ft(w.offM)} off route`, acc);
  return parts.join(" · ") + (w.ambiguous ? " (the route passes here twice)" : "");
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd web && npx vitest run src/gps.test.ts && npx tsc --noEmit`
Expected: PASS (5 tests); tsc clean. (The off-route accuracy reads "±16 ft" because 5 m is 16.4 ft.)

- [ ] **Step 5: Commit**

```bash
git add web/src/gps.ts web/src/gps.test.ts
git commit -m "web: where-am-I on the saved route (mile, day, climb to camp, off route, accuracy)"
```

---

### Task 4: Tile math

**Files:**
- Create: `web/src/offline/tiles.ts`
- Modify: `web/src/format.ts` (add `shortDate`)
- Test: `web/src/offline/tiles.test.ts`, `web/src/format.test.ts` (add one test)

**Interfaces:**
- Consumes: `BASEMAPS` from `web/src/basemaps.ts`; `Basemap` from `store.ts`.
- Produces:
  - `type Box = [number, number, number, number]` (west, south, east, north)
  - `ZOOMS = [12, 13, 14, 15, 16]`, `SAVED_BASEMAPS: Basemap[] = ["topo", "hybrid"]`, `MARGIN_M = 805`
  - `interface Tile { url: string; z: number; basemap: Basemap }`
  - `tripBox(c: Candidate, marginM?: number): Box | null` (null for an empty geometry)
  - `tileXY(lon: number, lat: number, z: number): [number, number]`
  - `tileList(box: Box, zooms?: number[], basemaps?: Basemap[]): Tile[]`
  - `estimate(tiles: Tile[]): { count: number; bytes: number }`
  - `megabytes(bytes: number): string`
  - `coverageNote(box: Box, view: Box, zoom: number): string | null`
  - in `format.ts`: `shortDate(iso: string | null): string` ("Oct 14, 2026", or "unknown")

- [ ] **Step 1: Write the failing tests**

`web/src/offline/tiles.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import type { Candidate } from "../types";
import { coverageNote, estimate, megabytes, tileList, tileXY, tripBox, type Box } from "./tiles";

describe("tiles", () => {
  it("finds the Web Mercator tile for a point", () => {
    expect(tileXY(-79.37, 39.03, 12)).toEqual([1144, 1564]);
    expect(tileXY(-79.37, 39.03, 14)).toEqual([4579, 6259]);
    expect(tileXY(-79.37, 39.03, 16)).toEqual([18319, 25039]);
  });
  it("lists every tile in the box at zoom 12-16 for Topo and Imagery+Topo", () => {
    const box: Box = [-79.40, 38.99, -79.33, 39.07];
    expect(tileList(box, undefined, ["topo"])).toHaveLength(382);
    const all = tileList(box);
    expect(all).toHaveLength(764);
    expect(all[0]!.url).toMatch(/^https:\/\/basemap\.nationalmap\.gov\/arcgis\/rest\/services\/USGSTopo\/MapServer\/tile\/12\/\d+\/\d+$/);
    expect(all.some((t) => t.url.includes("/USGSImageryTopo/"))).toBe(true);
    expect(estimate(all)).toEqual({ count: 764, bytes: 382 * 10_000 + 382 * 31_000 });
    expect(megabytes(15_662_000)).toBe("16 MB"); expect(megabytes(20_000)).toBe("1 MB");
  });
  it("pads the route's bounding box by half a mile", () => {
    const c = { geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39.01]] } } as unknown as Candidate;
    const [w, s, e, n] = tripBox(c)!;
    expect(s).toBeCloseTo(39 - 805 / 111195.08, 6); expect(n).toBeCloseTo(39.01 + 805 / 111195.08, 6);
    expect(e - w).toBeCloseTo((2 * 805) / (111195.08 * Math.cos((39.005 * Math.PI) / 180)), 6);
    expect(tripBox({ geometry: { type: "LineString", coordinates: [] } } as unknown as Candidate)).toBeNull();
  });
  it("says when the view leaves what was saved", () => {
    const box: Box = [-79.4, 39.0, -79.3, 39.1];
    expect(coverageNote(box, [-79.38, 39.02, -79.32, 39.08], 13)).toBeNull();
    expect(coverageNote(box, [-79.38, 39.02, -79.32, 39.08], 11)).toBe("Map not saved at this zoom: zoom in to see the saved map.");
    expect(coverageNote(box, [-79.5, 39.02, -79.32, 39.08], 13)).toBe("Part of this view is outside the saved map area; it shows blank offline.");
  });
});
```

Append to `web/src/format.test.ts` (inside a new `describe`):
```ts
import { shortDate } from "./format";
describe("shortDate", () => {
  it("formats an ISO time as a short US date, or unknown", () => {
    expect(shortDate("2026-10-14T16:21:38Z")).toBe("Oct 14, 2026");
    expect(shortDate(null)).toBe("unknown");
  });
});
```
(Move the `import` to the top of the file next to the existing imports.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npx vitest run src/offline/tiles.test.ts src/format.test.ts`
Expected: FAIL, `Failed to resolve import "./tiles"`, and `shortDate` is not exported.

- [ ] **Step 3: Implement**

Append to `web/src/format.ts`:
```ts
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown" : d.toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });
}
```

`web/src/offline/tiles.ts`:
```ts
// Which USGS tiles a saved trip needs, and whether the map view is inside what was saved.
// USGS National Map tiles are public domain and the services allow tile export (checked 2026-10-14).
import { BASEMAPS } from "../basemaps";
import type { Basemap } from "../store";
import type { Candidate } from "../types";

export type Box = [number, number, number, number]; // west, south, east, north
export interface Tile { url: string; z: number; basemap: Basemap }

export const ZOOMS = [12, 13, 14, 15, 16];
export const SAVED_BASEMAPS: Basemap[] = ["topo", "hybrid"];
export const MARGIN_M = 805; // 0.5 mi
const M_PER_DEG = 111195.08;
// Average tile sizes measured on Dolly Sods at z12-16 (2026-10-14); only used for the "about" estimate.
const BYTES_PER_TILE: Record<Basemap, number> = { topo: 10_000, hybrid: 31_000, satellite: 31_000 };

export function tripBox(c: Candidate, marginM = MARGIN_M): Box | null {
  const co = c.geometry.coordinates;
  if (!co.length) return null;
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [lon, lat] of co) { w = Math.min(w, lon); e = Math.max(e, lon); s = Math.min(s, lat); n = Math.max(n, lat); }
  const dLat = marginM / M_PER_DEG;
  const dLon = marginM / (M_PER_DEG * Math.cos((((s + n) / 2) * Math.PI) / 180));
  return [w - dLon, s - dLat, e + dLon, n + dLat];
}

export function tileXY(lon: number, lat: number, z: number): [number, number] {
  const n = 2 ** z, r = (lat * Math.PI) / 180;
  return [Math.floor(((lon + 180) / 360) * n), Math.floor(((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * n)];
}

export function tileList(box: Box, zooms: number[] = ZOOMS, basemaps: Basemap[] = SAVED_BASEMAPS): Tile[] {
  const out: Tile[] = [];
  for (const basemap of basemaps) {
    for (const z of zooms) {
      const [x0, y1] = tileXY(box[0], box[1], z), [x1, y0] = tileXY(box[2], box[3], z);
      for (let x = x0; x <= x1; x++) {
        for (let y = y0; y <= y1; y++) {
          out.push({ url: BASEMAPS[basemap].url.replace("{z}", String(z)).replace("{y}", String(y)).replace("{x}", String(x)), z, basemap });
        }
      }
    }
  }
  return out;
}

export function estimate(tiles: Tile[]): { count: number; bytes: number } {
  return { count: tiles.length, bytes: tiles.reduce((sum, t) => sum + BYTES_PER_TILE[t.basemap], 0) };
}

export function megabytes(bytes: number): string {
  return `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

export function coverageNote(box: Box, view: Box, zoom: number): string | null {
  if (zoom < ZOOMS[0]!) return "Map not saved at this zoom: zoom in to see the saved map.";
  const inside = view[0] >= box[0] && view[1] >= box[1] && view[2] <= box[2] && view[3] <= box[3];
  return inside ? null : "Part of this view is outside the saved map area; it shows blank offline.";
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd web && npx vitest run src/offline/tiles.test.ts src/format.test.ts && npx tsc --noEmit`
Expected: PASS; tsc clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/offline/tiles.ts web/src/offline/tiles.test.ts web/src/format.ts web/src/format.test.ts
git commit -m "web: tile list, size estimate and saved-area check for offline trips"
```

---

### Task 5: Saved-trip records in IndexedDB

**Files:**
- Create: `web/src/offline/db.ts`
- Modify: `web/package.json` (dev dependency `fake-indexeddb`)
- Test: `web/src/offline/db.test.ts`

**Interfaces:**
- Consumes: `Box` (Task 4); `Run`, `TrailFeatureCollection` types.
- Produces:
  - `interface SavedTrip { id: string; savedAt: string; startName: string; run: Run; index: number; campsM: number[] | null; gpx: { whole: string | null; days: (string | null)[] }; trails: TrailFeatureCollection; box: Box; tiles: { cache: string; total: number; saved: number; missingByZoom: Record<string, number> }; bytes: number; persisted: boolean; complete: boolean; problems: string[] }`
  - `putTrip(rec: SavedTrip): Promise<void>`, `getTrip(id: string): Promise<SavedTrip | undefined>`, `listTrips(): Promise<SavedTrip[]>` (newest first), `deleteTrip(id: string, cacheStorage?: CacheStorage): Promise<void>`
  - `cleanOrphans(cacheStorage?: CacheStorage): Promise<string[]>` (deletes `trip-*` caches with no record; returns their names)
  - `tilesPresent(rec: SavedTrip, cacheStorage?: CacheStorage): Promise<boolean>`

- [ ] **Step 1: Add the dev dependency**

Run: `cd web && npm install --save-dev fake-indexeddb@^6`
Expected: `package.json` devDependencies gains `fake-indexeddb`. (Why: jsdom has no IndexedDB; this is an in-memory implementation used only by Vitest.)

- [ ] **Step 2: Write the failing tests**

`web/src/offline/db.test.ts`:
```ts
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";
import { cleanOrphans, deleteTrip, getTrip, listTrips, putTrip, tilesPresent, type SavedTrip } from "./db";

function rec(id: string, savedAt: string): SavedTrip {
  return { id, savedAt, startName: "Bear Rocks Trailhead", run: { run_id: id } as SavedTrip["run"], index: 1, campsM: null,
    gpx: { whole: "<gpx/>", days: [] }, trails: { type: "FeatureCollection", attribution: "", features: [] }, box: [0, 0, 1, 1],
    tiles: { cache: `trip-${id}`, total: 2, saved: 2, missingByZoom: {} }, bytes: 10, persisted: true, complete: true, problems: [] };
}
function fakeCaches(names: string[]) {
  const set = new Set(names);
  return { set, cs: { keys: async () => [...set], delete: async (n: string) => set.delete(n), has: async (n: string) => set.has(n) } as unknown as CacheStorage };
}

beforeEach(() => { globalThis.indexedDB = new IDBFactory(); });

describe("saved trips", () => {
  it("stores and reads back a trip, newest first in the list", async () => {
    await putTrip(rec("a", "2026-10-14T10:00:00Z"));
    await putTrip(rec("b", "2026-10-15T10:00:00Z"));
    expect((await getTrip("a"))!.startName).toBe("Bear Rocks Trailhead");
    expect((await listTrips()).map((t) => t.id)).toEqual(["b", "a"]);
    expect(await getTrip("missing")).toBeUndefined();
  });
  it("deletes the record and its tile cache", async () => {
    const { set, cs } = fakeCaches(["trip-a", "shell-1"]);
    await putTrip(rec("a", "2026-10-14T10:00:00Z"));
    await deleteTrip("a", cs);
    expect(await getTrip("a")).toBeUndefined();
    expect([...set]).toEqual(["shell-1"]);
  });
  it("cleans tile caches left by an interrupted save", async () => {
    const { set, cs } = fakeCaches(["trip-a", "trip-orphan", "shell-1"]);
    await putTrip(rec("a", "2026-10-14T10:00:00Z"));
    expect(await cleanOrphans(cs)).toEqual(["trip-orphan"]);
    expect([...set].sort()).toEqual(["shell-1", "trip-a"]);
  });
  it("notices when the browser cleared a trip's tiles", async () => {
    expect(await tilesPresent(rec("a", "x"), fakeCaches(["trip-a"]).cs)).toBe(true);
    expect(await tilesPresent(rec("a", "x"), fakeCaches([]).cs)).toBe(false);
  });
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd web && npx vitest run src/offline/db.test.ts`
Expected: FAIL, `Failed to resolve import "./db"`.

- [ ] **Step 4: Implement**

`web/src/offline/db.ts`:
```ts
// Saved trips in IndexedDB (the browser's on-device database), written without a library.
// Tiles live in Cache Storage under `trip-<id>`; this record says which cache is the trip's.
import type { Run, TrailFeatureCollection } from "../types";
import type { Box } from "./tiles";

export interface SavedTrip {
  id: string; savedAt: string; startName: string; run: Run; index: number; campsM: number[] | null;
  gpx: { whole: string | null; days: (string | null)[] }; trails: TrailFeatureCollection; box: Box;
  tiles: { cache: string; total: number; saved: number; missingByZoom: Record<string, number> };
  bytes: number; persisted: boolean; complete: boolean; problems: string[];
}

const DB = "backcountry", STORE = "trips", VERSION = 1;

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, VERSION);
    req.onupgradeneeded = () => { req.result.createObjectStore(STORE, { keyPath: "id" }); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function run<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const req = fn(t.objectStore(STORE));
      t.oncomplete = () => resolve(req.result);
      t.onerror = () => reject(t.error ?? req.error);
      t.onabort = () => reject(t.error ?? new Error("The saved-trip database refused the change."));
    });
  } finally { db.close(); }
}

export async function putTrip(rec: SavedTrip): Promise<void> { await run("readwrite", (s) => s.put(rec)); }
export function getTrip(id: string): Promise<SavedTrip | undefined> { return run<SavedTrip | undefined>("readonly", (s) => s.get(id)); }

export async function listTrips(): Promise<SavedTrip[]> {
  const all = await run<SavedTrip[]>("readonly", (s) => s.getAll());
  return all.sort((a, b) => b.savedAt.localeCompare(a.savedAt));
}

export async function deleteTrip(id: string, cacheStorage: CacheStorage | undefined = globalThis.caches): Promise<void> {
  const rec = await getTrip(id);
  await run("readwrite", (s) => s.delete(id));
  if (rec && cacheStorage) await cacheStorage.delete(rec.tiles.cache);
}

/** Tile caches with no trip record (a save that was interrupted) are deleted. */
export async function cleanOrphans(cacheStorage: CacheStorage | undefined = globalThis.caches): Promise<string[]> {
  if (!cacheStorage) return [];
  const kept = new Set((await listTrips()).map((t) => t.tiles.cache));
  const orphans = (await cacheStorage.keys()).filter((n) => n.startsWith("trip-") && !kept.has(n));
  for (const n of orphans) await cacheStorage.delete(n);
  return orphans;
}

export async function tilesPresent(rec: SavedTrip, cacheStorage: CacheStorage | undefined = globalThis.caches): Promise<boolean> {
  return cacheStorage ? cacheStorage.has(rec.tiles.cache) : false;
}
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd web && npx vitest run src/offline/db.test.ts && npx tsc --noEmit`
Expected: PASS (4 tests); tsc clean.

- [ ] **Step 6: Commit**

```bash
git add web/package.json web/package-lock.json web/src/offline/db.ts web/src/offline/db.test.ts
git commit -m "web: saved-trip records in IndexedDB, orphan tile caches cleaned

Adds fake-indexeddb as a dev dependency: jsdom has no IndexedDB, and Vitest
needs one to test the saved-trip store. It is not shipped in the app."
```

---

### Task 6: Service worker

**Files:**
- Create: `web/sw/sw.js`, `web/src/swbuild.ts`, `web/src/offline/register.ts`
- Modify: `web/vite.config.ts`, `web/src/main.ts`
- Test: `web/src/swbuild.test.ts`

**Interfaces:**
- Produces:
  - `shellList(bundleFiles: string[]): string[]`, `versionOf(list: string[]): string`, `swSource(template: string, bundleFiles: string[]): string`
  - `registerWorker(onUpdateReady: (apply: () => void) => void, sw?: ServiceWorkerContainer): void`
  - `dist/sw.js` at `vite build`; served at `/sw.js` by `backcountry serve` (static files) and `vite preview`.
- Consumed later: Task 9 calls `app.actions.updateReady(apply)` from `main.ts`.

- [ ] **Step 1: Write the failing test**

`web/src/swbuild.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { shellList, swSource, versionOf } from "./swbuild";

describe("service worker build", () => {
  const T = "const VERSION = \"__VERSION__\"; const SHELL = __SHELL__;";
  it("lists the app shell: page, built assets, public files; no source maps or the worker itself", () => {
    expect(shellList(["assets/index-1.js", "assets/index-1.js.map", "assets/index-1.css", "sw.js"]))
      .toEqual(["/", "/assets/index-1.css", "/assets/index-1.js", "/icon.svg", "/index.html", "/manifest.webmanifest"]);
  });
  it("fills in the shell and a version that changes when the files change", () => {
    const a = swSource(T, ["assets/a.js"]), b = swSource(T, ["assets/b.js"]);
    expect(a).toContain('"/assets/a.js"'); expect(a).not.toContain("__SHELL__"); expect(a).toMatch(/VERSION = "[0-9a-f]{8}"/);
    expect(a).not.toBe(b); expect(swSource(T, ["assets/a.js"])).toBe(a);
    expect(versionOf(["/x"])).toMatch(/^[0-9a-f]{8}$/);
  });
  it("refuses a template without its placeholders", () => {
    expect(() => swSource("nothing here", [])).toThrow(/__VERSION__/);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run src/swbuild.test.ts`
Expected: FAIL, `Failed to resolve import "./swbuild"`.

- [ ] **Step 3: Implement**

`web/src/swbuild.ts`:
```ts
// Builds the service worker from web/sw/sw.js during `vite build` (see vite.config.ts): fills in the
// app-shell file list and a version derived from it, so a new build installs a new worker.
export const ALWAYS = ["index.html", "icon.svg", "manifest.webmanifest"];

export function shellList(bundleFiles: string[]): string[] {
  const files = [...bundleFiles, ...ALWAYS].filter((f) => !f.endsWith(".map") && f !== "sw.js");
  return [...new Set(["/", ...files.map((f) => `/${f}`)])].sort();
}

export function versionOf(list: string[]): string {
  let h = 0x811c9dc5; // FNV-1a, 32-bit
  for (const ch of list.join("\n")) { h ^= ch.charCodeAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, "0");
}

export function swSource(template: string, bundleFiles: string[]): string {
  if (!template.includes("__VERSION__") || !template.includes("__SHELL__")) throw new Error("sw.js template is missing __VERSION__ or __SHELL__");
  const shell = shellList(bundleFiles);
  return template.replace("__VERSION__", versionOf(shell)).replace("__SHELL__", JSON.stringify(shell));
}
```

`web/sw/sw.js`:
```js
// Backcountry service worker. vite.config.ts fills in __VERSION__ and __SHELL__ at build time.
// - App shell: cached at install, served cache-first, so the app opens with no signal.
// - USGS tiles: served from saved-trip caches (trip-*) first, then the network. Only the app's
//   save code writes tiles; this worker never caches them on its own.
// - /api/*: always the network. Never cached here.
const VERSION = "__VERSION__";
const SHELL = __SHELL__;
const SHELL_CACHE = `shell-${VERSION}`;
const TILE = /^https:\/\/basemap\.nationalmap\.gov\/arcgis\/rest\/services\/[^/]+\/MapServer\/tile\/\d+\/\d+\/\d+$/;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n.startsWith("shell-") && n !== SHELL_CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

// The page asks for this only after the user taps "Reload" on the update banner.
self.addEventListener("message", (event) => { if (event.data === "skipWaiting") self.skipWaiting(); });

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  if (TILE.test(req.url)) { event.respondWith(fromTrips(req)); return; }
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") {
    event.respondWith(caches.match("/index.html", { cacheName: SHELL_CACHE }).then((hit) => hit || fetch(req)));
    return;
  }
  event.respondWith(caches.match(req, { cacheName: SHELL_CACHE }).then((hit) => hit || fetch(req)));
});

async function fromTrips(req) {
  for (const name of await caches.keys()) {
    if (!name.startsWith("trip-")) continue;
    const hit = await (await caches.open(name)).match(req.url);
    if (hit) return hit;
  }
  return fetch(req);
}
```

`web/vite.config.ts`:
```ts
import { readFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import { swSource } from "./src/swbuild";

const api = process.env.BACKCOUNTRY_API ?? "http://127.0.0.1:8000";

// Emits dist/sw.js from sw/sw.js with the built file list (see src/swbuild.ts).
function serviceWorker(): Plugin {
  return {
    name: "backcountry-sw",
    apply: "build",
    enforce: "post",
    generateBundle(_options, bundle) {
      const template = readFileSync(new URL("./sw/sw.js", import.meta.url), "utf8");
      this.emitFile({ type: "asset", fileName: "sw.js", source: swSource(template, Object.keys(bundle)) });
    },
  };
}

export default defineConfig({
  plugins: [serviceWorker()],
  server: { proxy: { "/api": api }, host: true },
  preview: { proxy: { "/api": api }, host: true },
  build: { outDir: "dist", sourcemap: true },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
```

`web/src/offline/register.ts`:
```ts
// Registers the service worker (production builds only; see main.ts) and offers updates.
// A new version never takes over on its own: the app shows a banner and waits for a tap.
export function registerWorker(onUpdateReady: (apply: () => void) => void, sw: ServiceWorkerContainer | undefined = navigator.serviceWorker): void {
  if (!sw) return;
  sw.register("/sw.js").then((reg) => {
    const offer = (w: ServiceWorker | null) => {
      if (!w || !sw.controller) return; // first install: nothing to replace
      onUpdateReady(() => {
        sw.addEventListener("controllerchange", () => location.reload());
        w.postMessage("skipWaiting");
      });
    };
    offer(reg.waiting);
    reg.addEventListener("updatefound", () => {
      const w = reg.installing;
      w?.addEventListener("statechange", () => { if (w.state === "installed") offer(w); });
    });
  }).catch(() => { /* no service worker (e.g. plain http on a phone): the app still works online */ });
}
```

`web/src/main.ts` (whole file; `updateReady` is added to the app's actions in Task 9, so for now register with a no-op that Task 9 replaces):
```ts
import "./styles.css";
import { startApp } from "./app";
import { registerWorker } from "./offline/register";

const app = startApp(document, document.getElementById("map")!, document.getElementById("panel")!, document.getElementById("sheet-handle")!);
(window as unknown as { backcountry: typeof app }).backcountry = app;  // for debugging a route by hand from the console
if (import.meta.env.PROD) registerWorker(() => { /* Task 9 shows an update banner */ });
```

- [ ] **Step 4: Run to verify**

Run: `cd web && npx vitest run src/swbuild.test.ts && npx tsc --noEmit && npm run build && grep -o '"/assets/index-[^"]*\.js"' dist/sw.js && grep -c 'shell-' dist/sw.js`
Expected: 3 tests pass; tsc clean; the build prints the `/assets/index-….js` entry from `dist/sw.js` and a count ≥ 1.

Run the existing suites: `npm test && npm run test:e2e`
Expected: all pass (the worker is now active in the e2e browser; `/api` is untouched).

- [ ] **Step 5: Commit**

```bash
git add web/sw/sw.js web/src/swbuild.ts web/src/swbuild.test.ts web/src/offline/register.ts web/vite.config.ts web/src/main.ts
git commit -m "web: service worker for the app shell and saved-trip tiles; update waits for a tap"
```

---

### Task 7: Saving a trip

**Files:**
- Create: `web/src/offline/save.ts`
- Test: `web/src/offline/save.test.ts`

**Interfaces:**
- Consumes: Task 4 `tripBox`, `tileList`, `Box`, `Tile`; Task 5 `putTrip`, `SavedTrip`; `getTrails`, `gpxUrl` from `api.ts`.
- Produces:
  - `interface SaveProgress { done: number; total: number; failed: number }`
  - `interface SaveDeps { fetch: (url: string, init?: RequestInit) => Promise<Response>; caches: CacheStorage | undefined; persist: () => Promise<boolean>; now: () => Date; trails: (region: string) => Promise<TrailFeatureCollection> }`
  - `defaultDeps(): SaveDeps`, `tripId(run: Run, n: number): string` (`<run_id>-<n>`), `trailsInBox(fc, box): TrailFeatureCollection`
  - `saveTrip(run: Run, n: number, startName: string, onProgress: (p: SaveProgress) => void, signal: AbortSignal, deps?: SaveDeps): Promise<SavedTrip>`; rejects with `name === "AbortError"` on cancel (cache deleted, no record).
  - `CONCURRENCY = 4`

- [ ] **Step 1: Write the failing tests**

`web/src/offline/save.test.ts`:
```ts
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";
import type { Candidate, Run, TrailFeatureCollection } from "../types";
import { getTrip } from "./db";
import { saveTrip, trailsInBox, type SaveDeps, type SaveProgress } from "./save";
import { tileList, tripBox } from "./tiles";

const cand = { index: 1, length_m: 1000, geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39.004]] },
  plan: { days: [{ index: 1 }, { index: 2 }] } } as unknown as Candidate;
const run = { run_id: "r1", region: "dolly_sods", candidates: [cand] } as unknown as Run;
const tiles = tileList(tripBox(cand)!);
const trails: TrailFeatureCollection = { type: "FeatureCollection", attribution: "© OSM", features: [
  { type: "Feature", geometry: { type: "LineString", coordinates: [[-79.4, 39.001], [-79.401, 39.002]] }, properties: { name: "Near Trail" } as never },
  { type: "Feature", geometry: { type: "LineString", coordinates: [[-80.5, 38], [-80.6, 38.1]] }, properties: { name: "Far Trail" } as never }] };

function deps(opts: { failUrl?: string; quotaAfter?: number } = {}) {
  const stored = new Map<string, Map<string, Response>>();
  let puts = 0;
  const caches = {
    open: async (name: string) => {
      if (!stored.has(name)) stored.set(name, new Map());
      const m = stored.get(name)!;
      return { put: async (k: string, r: Response) => {
        if (++puts > (opts.quotaAfter ?? Infinity)) throw new DOMException("full", "QuotaExceededError");
        m.set(k, r);
      } } as unknown as Cache;
    },
    delete: async (name: string) => stored.delete(name),
  } as unknown as CacheStorage;
  const d: SaveDeps = {
    fetch: async (url) => url === opts.failUrl ? new Response("", { status: 500 })
      : url.includes("/tile/") ? new Response(new Uint8Array(100), { headers: { "content-type": "image/jpeg" } })
      : url.includes(".gpx") ? new Response(`<gpx>${url}</gpx>`) : new Response("", { status: 404 }),
    caches, persist: async () => true, now: () => new Date("2026-10-14T12:00:00Z"), trails: async () => trails,
  };
  return { d, stored };
}

beforeEach(() => { globalThis.indexedDB = new IDBFactory(); });

describe("saveTrip", () => {
  it("saves every tile, the GPX files and the nearby trails, and records the trip", async () => {
    const { d, stored } = deps();
    const progress: SaveProgress[] = [];
    const rec = await saveTrip(run, 1, "Bear Rocks Trailhead", (p) => progress.push(p), new AbortController().signal, d);
    expect(rec.complete).toBe(true); expect(rec.problems).toEqual([]);
    expect(rec.tiles).toEqual({ cache: "trip-r1-1", total: tiles.length, saved: tiles.length, missingByZoom: {} });
    expect(stored.get("trip-r1-1")!.size).toBe(tiles.length);
    expect(rec.bytes).toBe(100 * tiles.length);
    expect(rec.gpx.whole).toContain("/api/runs/r1/candidates/1.gpx"); expect(rec.gpx.days).toHaveLength(2);
    expect(rec.trails.features.map((f) => f.properties.name)).toEqual(["Near Trail"]);
    expect(progress.at(-1)).toEqual({ done: tiles.length, total: tiles.length, failed: 0 });
    expect((await getTrip("r1-1"))!.startName).toBe("Bear Rocks Trailhead");
  });
  it("keeps a trip with missing tiles as incomplete, saying which zoom", async () => {
    const z16 = tiles.find((t) => t.z === 16)!.url;
    const { d } = deps({ failUrl: z16 });
    const rec = await saveTrip(run, 1, "x", () => {}, new AbortController().signal, d);
    expect(rec.complete).toBe(false);
    expect(rec.tiles.missingByZoom).toEqual({ z16: 1 });
    expect(rec.problems).toEqual(["Map tiles not saved: z16: 1 missing."]);
  });
  it("stops at the storage limit and says how much was saved", async () => {
    const { d } = deps({ quotaAfter: 5 });
    const rec = await saveTrip(run, 1, "x", () => {}, new AbortController().signal, d);
    expect(rec.complete).toBe(false); expect(rec.tiles.saved).toBe(5);
    expect(rec.problems[0]).toBe(`Storage full: saved 5 of ${tiles.length} tiles; the trip is incomplete.`);
  });
  it("cancels cleanly: no record, no tile cache", async () => {
    const { d, stored } = deps();
    const ctl = new AbortController();
    const p = saveTrip(run, 1, "x", (pr) => { if (pr.done === 3) ctl.abort(); }, ctl.signal, d);
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
    expect(stored.has("trip-r1-1")).toBe(false);
    expect(await getTrip("r1-1")).toBeUndefined();
  });
  it("keeps trail lines whose extent overlaps the box", () => {
    expect(trailsInBox(trails, [-79.5, 38.9, -79.3, 39.1]).features).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npx vitest run src/offline/save.test.ts`
Expected: FAIL, `Failed to resolve import "./save"`.

- [ ] **Step 3: Implement**

`web/src/offline/save.ts`:
```ts
// Saves one candidate for offline use: its map tiles (Cache Storage, at most 4 downloads at a time, one
// retry each), its GPX files exactly as the server built them, nearby trail lines, and the run (IndexedDB).
// Failures are kept and reported ("incomplete"), never hidden.
import { getTrails, gpxUrl } from "../api";
import type { Run, TrailFeatureCollection } from "../types";
import { putTrip, type SavedTrip } from "./db";
import { tileList, tripBox, type Box, type Tile } from "./tiles";

export const CONCURRENCY = 4;
export interface SaveProgress { done: number; total: number; failed: number }
export interface SaveDeps {
  fetch: (url: string, init?: RequestInit) => Promise<Response>;
  caches: CacheStorage | undefined;
  persist: () => Promise<boolean>;
  now: () => Date;
  trails: (region: string) => Promise<TrailFeatureCollection>;
}

export function defaultDeps(): SaveDeps {
  return {
    fetch: (url, init) => fetch(url, init),
    caches: globalThis.caches,
    persist: () => navigator.storage?.persist?.() ?? Promise.resolve(false),
    now: () => new Date(),
    trails: getTrails,
  };
}

export function tripId(run: Run, n: number): string { return `${run.run_id}-${n}`; }

export function trailsInBox(fc: TrailFeatureCollection, box: Box): TrailFeatureCollection {
  const overlaps = (co: [number, number][]) => {
    const lons = co.map((p) => p[0]), lats = co.map((p) => p[1]);
    return Math.min(...lons) <= box[2] && Math.max(...lons) >= box[0] && Math.min(...lats) <= box[3] && Math.max(...lats) >= box[1];
  };
  return { ...fc, features: fc.features.filter((f) => f.geometry.coordinates.length && overlaps(f.geometry.coordinates)) };
}

const cancelled = () => new DOMException("The save was cancelled.", "AbortError");

export async function saveTrip(run: Run, n: number, startName: string, onProgress: (p: SaveProgress) => void,
  signal: AbortSignal, deps: SaveDeps = defaultDeps()): Promise<SavedTrip> {
  const c = run.candidates[n - 1];
  if (!c) throw new Error(`This run has no candidate ${n}.`);
  const box = tripBox(c);
  if (!box) throw new Error("This candidate has no route line to save.");
  const cs = deps.caches;
  if (!cs) throw new Error("This browser can't store map tiles. Offline saving needs a secure (https) address; see the README's Tailscale step.");
  const tiles = tileList(box), cacheName = `trip-${tripId(run, n)}`;
  const missing: Record<string, number> = {};
  const problems: string[] = [];
  let done = 0, failed = 0, bytes = 0, full = false;
  const miss = (t: Tile) => { missing[`z${t.z}`] = (missing[`z${t.z}`] ?? 0) + 1; };

  try {
    const cache = await cs.open(cacheName);
    const queue = [...tiles];
    const one = async (t: Tile): Promise<boolean> => {
      for (let attempt = 0; attempt < 2 && !full; attempt++) {
        if (signal.aborted) throw cancelled();
        try {
          const res = await deps.fetch(t.url, { signal });
          if (!res.ok) continue;
          const buf = await res.arrayBuffer();
          await cache.put(t.url, new Response(buf, { headers: { "content-type": res.headers.get("content-type") ?? "image/jpeg" } }));
          bytes += buf.byteLength;
          return true;
        } catch (e) {
          const name = (e as Error).name;
          if (name === "AbortError") throw cancelled();
          if (name === "QuotaExceededError") full = true;
        }
      }
      return false;
    };
    const worker = async () => {
      while (queue.length && !full) {
        if (signal.aborted) throw cancelled();
        const t = queue.shift()!;
        if (!(await one(t))) { failed++; miss(t); }
        done++;
        onProgress({ done, total: tiles.length, failed });
      }
    };
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    if (signal.aborted) throw cancelled();
    queue.forEach(miss); // never tried because storage filled up
    const saved = tiles.length - failed - queue.length;
    if (full) problems.push(`Storage full: saved ${saved} of ${tiles.length} tiles; the trip is incomplete.`);
    else if (failed) problems.push(`Map tiles not saved: ${Object.entries(missing).map(([z, k]) => `${z}: ${k} missing`).join(", ")}.`);

    const gpxText = async (day?: number): Promise<string | null> => {
      try { const r = await deps.fetch(gpxUrl(run.run_id, n, day), { signal }); return r.ok ? await r.text() : null; }
      catch (e) { if ((e as Error).name === "AbortError") throw cancelled(); return null; }
    };
    const whole = await gpxText();
    const days = c.plan ? await Promise.all(c.plan.days.map((d) => gpxText(d.index))) : [];
    if (whole === null || days.some((g) => g === null)) problems.push("Some GPX files weren't saved; those downloads won't work offline.");

    let trails: TrailFeatureCollection = { type: "FeatureCollection", attribution: "", features: [] };
    try { trails = trailsInBox(await deps.trails(run.region), box); }
    catch { problems.push("Trail lines near the route weren't saved; offline, only the route itself is drawn."); }
    if (signal.aborted) throw cancelled();

    const persisted = await deps.persist().catch(() => false);
    const rec: SavedTrip = {
      id: tripId(run, n), savedAt: deps.now().toISOString(), startName, run, index: n, campsM: null,
      gpx: { whole, days }, trails, box, tiles: { cache: cacheName, total: tiles.length, saved, missingByZoom: missing },
      bytes, persisted, complete: problems.length === 0, problems,
    };
    await putTrip(rec);
    return rec;
  } catch (e) {
    await cs.delete(cacheName);
    throw e;
  }
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd web && npx vitest run src/offline/save.test.ts && npx tsc --noEmit`
Expected: PASS (5 tests); tsc clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/offline/save.ts web/src/offline/save.test.ts
git commit -m "web: save a trip for offline (tiles, GPX, nearby trails, record), incomplete saves reported"
```

---

### Task 8: State, saved-trips view and the trip view's offline parts

**Files:**
- Create: `web/src/selectors.ts`, `web/src/panels/saved.ts`, `web/src/panels/saved.test.ts`
- Modify: `web/src/store.ts`, `web/src/profile.ts`, `web/src/panels/trip.ts`, `web/src/panels/trip.test.ts`, `web/src/panels/plan.ts`, `web/src/styles.css`, `web/src/profile.test.ts`

**Interfaces:**
- Consumes: Tasks 2–5 (`withCamps`, `whereText`, `Where`, `Fix`, `estimate`, `tileList`, `tripBox`, `megabytes`, `SavedTrip`, `SaveProgress`); `shortDate`.
- Produces:
  - `State` gains `saved: SavedTrip[]; savedId: string | null; campsM: number[] | null; gps: Gps; saving: SaveProgress | null; offline: boolean; updateReady: boolean; mapNote: string | null`; `View` gains `"saved"`.
  - `type Gps = { mode: "off" } | { mode: "waiting" } | { mode: "on"; fix: Fix; where: Where | null } | { mode: "error"; message: string }`
  - `currentCandidate(s: State): Candidate | null`
  - `profileSvg(profile, days, W = 600, H = 140, markerM: number | null = null)`
  - `renderSaved(s)`, `bindSaved(root, actions: SavedActions)`, `interface SavedActions { openSaved(id: string): void; deleteSaved(id: string): void; toPlan(): void }`
  - `TripActions` gains `save(); cancelSave(); toggleGps(); campHere(); resetCamps(); downloadSaved(which: string); showSaved()`
  - `PlanActions` gains `showSaved()`

- [ ] **Step 1: Write the failing tests**

Append to `web/src/profile.test.ts` (inside the existing `describe("profile", …)`):
```ts
  it("marks the user's position when given", () => {
    const svg = profileSvg({ d_m: [0, 1000, 2000], z_m: [1000, 1100, 1000] }, null, 600, 140, 500);
    expect(svg).toContain('class="you"'); expect(svg).toContain(">you<");
    expect(profileSvg({ d_m: [0, 1000], z_m: [1, 2] }, null)).not.toContain('class="you"');
  });
```

Append to `web/src/panels/trip.test.ts` (below the existing `describe`; it reuses the file's `run()` helper):
```ts
import type { SavedTrip } from "../offline/db";

function saved(over: Partial<SavedTrip> = {}): SavedTrip {
  const r = run({ geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39.01]] } });
  return { id: "r1-1", savedAt: "2026-10-14T12:00:00Z", startName: "Bear Rocks Trailhead", run: r, index: 1, campsM: null,
    gpx: { whole: "<gpx/>", days: [] }, trails: { type: "FeatureCollection", attribution: "", features: [] }, box: [-79.41, 38.99, -79.39, 39.02],
    tiles: { cache: "trip-r1-1", total: 760, saved: 760, missingByZoom: {} }, bytes: 16_000_000, persisted: true, complete: true, problems: [], ...over };
}

describe("renderTrip, offline parts", () => {
  it("offers to save with an estimate, and shows progress while saving", () => {
    const r = run({ geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39.01]] } });
    const html = renderTrip({ ...INITIAL, run: r, view: "trip" });
    expect(html).toMatch(/id="save-offline">Save for offline \(about \d+ map tiles, \d+ MB\)/);
    const busy = renderTrip({ ...INITIAL, run: r, view: "trip", saving: { done: 312, total: 760, failed: 2 } });
    expect(busy).toContain("Saving map tiles: 312 / 760 · 2 failed"); expect(busy).toContain('id="save-cancel"');
  });
  it("shows a saved trip's dates, saved GPX links and no save button", () => {
    const t = saved();
    const html = renderTrip({ ...INITIAL, run: t.run, view: "trip", saved: [t], savedId: t.id });
    expect(html).toContain("Saved for offline Oct 14, 2026"); expect(html).toContain("trail data retrieved Oct 13, 2026");
    expect(html).toContain('data-gpx="whole"'); expect(html).not.toContain('id="save-offline"');
    expect(html).not.toContain("/api/runs/r1/candidates/1.gpx"); expect(html).toContain('id="known-gaps"');
  });
  it("labels an incomplete save and one the browser may clear", () => {
    const t = saved({ complete: false, persisted: false, problems: ["Map tiles not saved: z16: 38 missing."] });
    const html = renderTrip({ ...INITIAL, run: t.run, view: "trip", saved: [t], savedId: t.id });
    expect(html).toContain("Incomplete:"); expect(html).toContain("z16: 38 missing");
    expect(html).toContain("the browser may clear it if the phone runs low on storage");
  });
  it("shows the GPS line, its errors, and the profile marker", () => {
    const t = saved();
    const where = { onRoute: true as const, alongM: 500, offM: 10, day: null, next: { label: "End", distM: 612, gainFt: 50 }, ambiguous: false, accuracyM: 5 };
    const on = renderTrip({ ...INITIAL, run: t.run, view: "trip", gps: { mode: "on", fix: { lon: -79.4, lat: 39.0045, accuracyM: 5 }, where } });
    expect(on).toContain("mile 0.3 of 18.6"); expect(on).toContain("Stop showing my location"); expect(on).toContain('class="you"');
    const err = renderTrip({ ...INITIAL, run: t.run, view: "trip", gps: { mode: "error", message: "User denied Geolocation" } });
    expect(err).toContain("Location unavailable: User denied Geolocation");
  });
});
```
(Move the new `import` line to the top of the file.)

`web/src/panels/saved.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import type { SavedTrip } from "../offline/db";
import { INITIAL } from "../store";
import type { Run } from "../types";
import { renderSaved } from "./saved";

const t = (over: Partial<SavedTrip> = {}): SavedTrip => ({ id: "r1-1", savedAt: "2026-10-14T12:00:00Z", startName: "Bear Rocks Trailhead",
  run: { candidates: [{ kind: "loop", length_m: 1609.344 * 19.02 }] } as unknown as Run, index: 1, campsM: null, gpx: { whole: null, days: [] },
  trails: { type: "FeatureCollection", attribution: "", features: [] }, box: [0, 0, 1, 1], tiles: { cache: "trip-r1-1", total: 1, saved: 1, missingByZoom: {} },
  bytes: 16_000_000, persisted: true, complete: true, problems: [], ...over });

describe("renderSaved", () => {
  it("lists saved trips with size, completeness and storage status", () => {
    const html = renderSaved({ ...INITIAL, view: "saved", saved: [t(), t({ id: "r2-1", complete: false, persisted: false, campsM: [100] })] });
    expect(html).toContain("Bear Rocks Trailhead: loop, 19.02 mi"); expect(html).toContain("Saved Oct 14, 2026 · 16 MB · complete · storage kept");
    expect(html).toContain("<b>incomplete</b>"); expect(html).toContain("may be cleared if the phone runs low on storage");
    expect(html).toContain("camps moved by you"); expect(html).toContain('data-open="r2-1"'); expect(html).toContain('data-delete="r1-1"');
    expect(html).toContain("Map tiles for saved trips: 32 MB");
  });
  it("says when it's offline and when nothing is saved", () => {
    const html = renderSaved({ ...INITIAL, view: "saved", offline: true });
    expect(html).toContain("Offline: showing saved trips. Planning needs the server.");
    expect(html).toContain("No saved trips yet"); expect(html).not.toContain('id="to-plan"');
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npx vitest run src/profile.test.ts src/panels/trip.test.ts src/panels/saved.test.ts`
Expected: FAIL: `./saved` unresolved; `saving`/`saved`/`gps` not in `State` (type errors are fine at this step; Vitest reports assertion failures for the trip and profile tests).

- [ ] **Step 3: Implement**

`web/src/store.ts`: replace the `View` type, `State` and `INITIAL` with:
```ts
import type { Fix, Where } from "./gps";
import type { SavedTrip } from "./offline/db";
import type { SaveProgress } from "./offline/save";

export type View = "plan" | "compare" | "trip" | "saved";
export type Basemap = "topo" | "hybrid" | "satellite";
export type Gps = { mode: "off" } | { mode: "waiting" } | { mode: "on"; fix: Fix; where: Where | null } | { mode: "error"; message: string };

export interface State {
  regions: Region[]; region: string | null; trailheads: Trailhead[]; form: FormState; run: Run | null; selected: number;
  view: View; status: string; error: string | null; busy: boolean; basemap: Basemap; sheet: "collapsed" | "half" | "full";
  saved: SavedTrip[]; savedId: string | null; campsM: number[] | null; gps: Gps; saving: SaveProgress | null;
  offline: boolean; updateReady: boolean; mapNote: string | null;
}

export const INITIAL: State = {
  regions: [], region: null, trailheads: [], form: DEFAULT_FORM, run: null, selected: 1, view: "plan", status: "",
  error: null, busy: false, basemap: "topo", sheet: "half",
  saved: [], savedId: null, campsM: null, gps: { mode: "off" }, saving: null, offline: false, updateReady: false, mapNote: null,
};
```
(The new fields are not persisted to localStorage; `persist`/`loadPersisted` stay as they are.)

`web/src/selectors.ts`:
```ts
// The trip as shown: the selected candidate, with the user's moved camps applied (memoized, since
// withCamps re-cuts the whole route and the trip view re-renders on every GPS fix).
import { withCamps } from "./resplit";
import type { State } from "./store";
import type { Candidate } from "./types";

let memo: { run: State["run"]; selected: number; campsM: number[]; out: Candidate } | null = null;

export function currentCandidate(s: State): Candidate | null {
  const c = s.run?.candidates[s.selected - 1];
  if (!c) return null;
  if (!s.campsM || !c.plan) return c;
  if (memo && memo.run === s.run && memo.selected === s.selected && memo.campsM === s.campsM) return memo.out;
  const out = withCamps(c, s.campsM);
  memo = { run: s.run, selected: s.selected, campsM: s.campsM, out };
  return out;
}
```

`web/src/profile.ts`: change the `profileSvg` signature to `export function profileSvg(profile: { d_m: number[]; z_m: (number | null)[] } | null, days: Day[] | null, W = 600, H = 140, markerM: number | null = null): string` and, before the final `return`, add:
```ts
  const you = markerM === null ? "" : `<g class="you"><line x1="${x(markerM).toFixed(1)}" y1="${pad}" x2="${x(markerM).toFixed(1)}" y2="${H - pad}" stroke="#1565c0" stroke-width="2"/><text x="${(x(markerM) + 3).toFixed(1)}" y="${H - pad - 4}" font-size="10" fill="#1565c0">you</text></g>`;
```
and insert `${you}` after `${camps}` in the returned SVG string.

`web/src/panels/trip.ts`: replace the whole file with:
```ts
import { geojsonUrl, gpxUrl } from "../api";
import { dayColor } from "../basemaps";
import { featureTitle } from "../evidence";
import { feet, FT_PER_M, M_PER_MI, miles, shortDate, signedFeet } from "../format";
import { whereText } from "../gps";
import type { SavedTrip } from "../offline/db";
import { estimate, megabytes, tileList, tripBox } from "../offline/tiles";
import { profileSvg } from "../profile";
import { currentCandidate } from "../selectors";
import type { State, Store } from "../store";
import type { Candidate, Run } from "../types";
import { esc, knownGapsHtml, notesHtml } from "./common";
import { segmentNames } from "./compare";

const NEAR = feet(500 * FT_PER_M);

function daysHtml(c: Candidate, editable: boolean): string {
  const p = c.plan;
  if (!p) return `<p class="note">Days weren't planned (no nights given). Change the settings to split this loop into days.</p>`;
  const rows = p.days.map((d) => `<tr><td><span class="swatch" style="background:${dayColor(d.index)}"></span>Day ${d.index}</td><td>${esc(d.start_label)} → ${esc(d.end_label)}</td><td>${miles(d.length_m)}</td><td>${feet(d.gain_ft)}</td><td>${feet(d.loss_ft)}</td></tr>`).join("");
  const moved = p.camps.some((cp) => cp.moved);
  const camps = p.camps.map((cp) => {
    const how = cp.moved ? "moved by you" : cp.pinned ? "pinned by you" : cp.site ? "at a known campsite" : "placed for mileage";
    const water = cp.nearest_water
      ? `nearest water: ${featureTitle(cp.nearest_water).toLowerCase()}, ${feet(cp.nearest_water.distance_m * FT_PER_M)} away (presence unknown)`
      : "nearest water: none listed";
    const where = cp.moved ? "listed along the route " : "";
    const near = cp.nearby.length ? `${cp.nearby.length} known campsite${cp.nearby.length === 1 ? "" : "s"} ${where}within ${NEAR}` : `no known campsite ${where}within ${NEAR}`;
    return `<li><b>Camp ${cp.index}</b> at ${cp.lat.toFixed(5)}, ${cp.lon.toFixed(5)}, ${miles(cp.dist_m)} in, on ${esc(cp.on_name ?? "an unnamed trail")}, ${how}. Location unverified. ${near}. ${esc(water)}.</li>`;
  }).join("");
  const edit = editable && p.camps.length
    ? `<p class="note">Drag a camp marker along the route to move it, or tap Camp here at your location.</p>${moved ? `<button class="link" id="reset-camps">Reset camps to the engine's plan</button>` : ""}`
    : "";
  return `${notesHtml(p.notes)}${notesHtml(p.violations.map((v) => `Constraint not met: ${v}`), "note error")}
<table><thead><tr><th>day</th><th>from → to</th><th>distance</th><th>gain</th><th>loss</th></tr></thead><tbody>${rows}</tbody></table>
<h2>Camps</h2><p class="note">Camps are points on the route chosen for mileage, not known campsites. Legality and water: unknown.</p>${edit}<ul>${camps}</ul>`;
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

function savedDownloadsHtml(t: SavedTrip, moved: boolean): string {
  const link = (which: string, text: string, ok: boolean) => ok ? `<a class="link" href="#" data-gpx="${which}">${text}</a>` : `${text} (not saved)`;
  const days = t.gpx.days.map((g, i) => link(String(i + 1), `day ${i + 1}`, g !== null)).join(" · ");
  return `<p>${link("whole", "Download GPX (whole trip)", t.gpx.whole !== null)}${days ? ` · GPX for ${days}` : ""}</p>`
    + (moved ? `<p class="note">These GPX files were saved with the trip: they show the original camps, not the ones you moved.</p>` : "");
}

function savedLineHtml(t: SavedTrip, run: Run): string {
  const osm = run.data_sources.find((d) => d.name.startsWith("OpenStreetMap"))?.retrieved_at ?? null;
  return `<p class="note" id="saved-line">Saved for offline ${shortDate(t.savedAt)} · trail data retrieved ${shortDate(osm)} · map tiles from USGS The National Map, ${shortDate(t.savedAt)} · ${megabytes(t.bytes)}${t.persisted ? "" : " · the browser may clear it if the phone runs low on storage"}</p>`
    + (t.complete ? "" : `<div class="warn" id="incomplete"><b>Incomplete:</b> ${t.problems.map(esc).join(" ")}</div>`);
}

function gpsHtml(s: State, c: Candidate, saved: SavedTrip | null): string {
  const g = s.gps;
  const line = g.mode === "waiting" ? "Finding your location…" : g.mode === "error" ? `Location unavailable: ${g.message}`
    : g.mode === "on" && g.where ? whereText(g.where, c.length_m) : "";
  const canCamp = g.mode === "on" && g.where?.onRoute === true;
  const campBtn = saved && c.plan?.camps.length ? `<button class="primary" id="camp-here"${canCamp ? "" : " disabled"}>Camp here</button>` : "";
  return `<div class="row"><button class="primary" id="locate">${g.mode === "off" ? "Show my location" : "Stop showing my location"}</button>${campBtn}</div>
<p class="status${g.mode === "error" ? " error" : ""}" id="gps-line">${esc(line)}</p>`;
}

function saveHtml(s: State, c: Candidate, saved: SavedTrip | null): string {
  if (saved) return "";
  if (s.saving) {
    return `<p class="status" id="save-progress">Saving map tiles: ${s.saving.done} / ${s.saving.total}${s.saving.failed ? ` · ${s.saving.failed} failed` : ""} <button class="link" id="save-cancel">Cancel</button></p>`;
  }
  const box = tripBox(c);
  if (s.offline || !box) return "";
  const est = estimate(tileList(box));
  return `<button class="primary" id="save-offline">Save for offline (about ${est.count} map tiles, ${megabytes(est.bytes)})</button>`;
}

export function renderTrip(s: State): string {
  const run = s.run;
  const c = currentCandidate(s);
  if (!run || !c) return `<h1>Trip</h1><p class="note">Pick a candidate first.</p><button class="link" id="back-compare">Back</button>`;
  const saved = s.savedId ? s.saved.find((t) => t.id === s.savedId) ?? null : null;
  const sources = run.data_sources.map((d) => `${esc(d.name)} (retrieved ${esc(d.retrieved_at ?? "unknown")})`).join("; ");
  const hasJunctions = c.waypoints.some((w) => w.kind === "junction");
  const where = s.gps.mode === "on" ? s.gps.where : null;
  const status = [s.status, s.error].filter(Boolean).join(" ");
  return `
<h1>Candidate ${c.index}: ${esc(c.kind)}, ${miles(c.length_m)}</h1>
${saved ? savedLineHtml(saved, run) : ""}
<p>${signedFeet(c.gain_ft, c.loss_ft)} · road ${miles(c.road_m)}${c.repeated_m ? ` · walked twice ${miles(c.repeated_m)}` : ""}</p>
<p class="note">${esc(segmentNames(c).join(" › "))}</p>
${notesHtml(c.notes)}
${gpsHtml(s, c, saved)}
${status ? `<p class="status${s.error ? " error" : ""}" id="status">${esc(status)}</p>` : ""}
${s.mapNote ? `<p class="note" id="map-note">${esc(s.mapNote)}</p>` : ""}
${hasJunctions ? `<label><input type="checkbox" id="junctions"> Show trail junctions on the map</label>` : ""}
<h2>Days</h2>${daysHtml(c, saved !== null)}
<h2>Elevation</h2>${profileSvg(c.profile, c.plan?.days ?? null, 600, 140, where && where.onRoute ? where.alongM : null)}
<h2>Campsites and water along the route</h2>${featuresHtml(c)}
<h2>Files</h2>${saved ? savedDownloadsHtml(saved, s.campsM !== null) : downloadsHtml(run, c)}
${saveHtml(s, c, saved)}
<div class="row"><button class="primary" id="replan"${s.offline ? " disabled" : ""}>Re-plan with these settings</button></div>
${s.offline ? "" : `<button class="link" id="back-compare">Back to all candidates</button>`}
${s.saved.length ? `<button class="link" id="back-saved">Saved trips</button>` : ""}
<p class="note">Data: ${sources}.</p>
${knownGapsHtml(run.known_gaps)}`;
}

export interface TripActions {
  backToCompare(): void; replan(): void; showJunctions(show: boolean): void; save(): void; cancelSave(): void;
  toggleGps(): void; campHere(): void; resetCamps(): void; downloadSaved(which: string): void; showSaved(): void;
}

export function bindTrip(root: HTMLElement, _store: Store, actions: TripActions): void {
  const on = (sel: string, fn: () => void) => root.querySelector(sel)?.addEventListener("click", fn);
  on("#back-compare", () => actions.backToCompare());
  on("#back-saved", () => actions.showSaved());
  on("#replan", () => actions.replan());
  on("#save-offline", () => actions.save());
  on("#save-cancel", () => actions.cancelSave());
  on("#locate", () => actions.toggleGps());
  on("#camp-here", () => actions.campHere());
  on("#reset-camps", () => actions.resetCamps());
  root.querySelector<HTMLInputElement>("#junctions")?.addEventListener("change", (e) => actions.showJunctions((e.target as HTMLInputElement).checked));
  root.querySelectorAll<HTMLAnchorElement>("[data-gpx]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); actions.downloadSaved(a.dataset.gpx!); }));
}
```

`web/src/panels/saved.ts`:
```ts
import { miles, shortDate } from "../format";
import { megabytes } from "../offline/tiles";
import type { State } from "../store";
import { esc } from "./common";

export function renderSaved(s: State): string {
  const status = [s.status, s.error].filter(Boolean).join(" ");
  const head = `<h1>Saved trips</h1>${s.offline ? `<p class="warn" id="offline-note">Offline: showing saved trips. Planning needs the server.</p>` : ""}`
    + (status ? `<p class="status${s.error ? " error" : ""}" id="status">${esc(status)}</p>` : "");
  const plan = s.offline ? "" : `<button class="link" id="to-plan">Plan a trip</button>`;
  if (!s.saved.length) return `${head}<p class="note">No saved trips yet. Open a trip and tap Save for offline.</p>${plan}`;
  const total = s.saved.reduce((sum, t) => sum + t.bytes, 0);
  const cards = s.saved.map((t) => {
    const c = t.run.candidates[t.index - 1];
    return `<div class="card saved-trip"><b>${esc(t.startName)}: ${esc(c?.kind ?? "trip")}, ${miles(c?.length_m)}</b>
<div class="note">Saved ${shortDate(t.savedAt)} · ${megabytes(t.bytes)} · ${t.complete ? "complete" : "<b>incomplete</b>"} · ${t.persisted ? "storage kept" : "may be cleared if the phone runs low on storage"}${t.campsM ? " · camps moved by you" : ""}</div>
<div class="row"><button class="primary" data-open="${esc(t.id)}">Open</button><button class="link" data-delete="${esc(t.id)}">Delete</button></div></div>`;
  }).join("");
  return `${head}<p class="note">Map tiles for saved trips: ${megabytes(total)}.</p><div class="saved-list">${cards}</div>${plan}`;
}

export interface SavedActions { openSaved(id: string): void; deleteSaved(id: string): void; toPlan(): void }

export function bindSaved(root: HTMLElement, actions: SavedActions): void {
  root.querySelectorAll<HTMLElement>("[data-open]").forEach((b) => b.addEventListener("click", () => actions.openSaved(b.dataset.open!)));
  root.querySelectorAll<HTMLElement>("[data-delete]").forEach((b) => b.addEventListener("click", () => actions.deleteSaved(b.dataset.delete!)));
  root.querySelector("#to-plan")?.addEventListener("click", () => actions.toPlan());
}
```

`web/src/panels/plan.ts`:
- In `renderPlan`, directly after the `<h1>` line, add: `${s.saved.length ? `<button class="link" id="to-saved">Saved trips (${s.saved.length})</button>` : ""}`
- Extend `PlanActions` with `showSaved(): void`.
- In `bindPlan`, add: `root.querySelector("#to-saved")?.addEventListener("click", () => actions.showSaved());`

`web/src/styles.css`: append
```css
.saved-list .card { margin: 0 0 10px; }
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd web && npm test && npx tsc --noEmit`
Expected: `tsc` reports errors only in `src/app.ts` (missing `showSaved` and the new trip actions); that file is rewritten in Task 9. All Vitest tests pass. (If `tsc` reports errors anywhere else, fix them before continuing.)

- [ ] **Step 5: Commit**

```bash
git add web/src/store.ts web/src/selectors.ts web/src/profile.ts web/src/profile.test.ts web/src/panels/trip.ts web/src/panels/trip.test.ts web/src/panels/saved.ts web/src/panels/saved.test.ts web/src/panels/plan.ts web/src/styles.css
git commit -m "web: saved-trips view; trip view gains save, GPS line, moved camps and saved files"
```

---

### Task 9: Map and app wiring

**Files:**
- Modify: `web/src/map.ts`, `web/src/app.ts`, `web/src/main.ts`

**Interfaces:**
- Consumes: everything above; `currentCandidate`; `saveTrip`, `tripId`; `listTrips`, `getTrip`, `putTrip`, `deleteTrip`, `cleanOrphans`, `tilesPresent`; `coverageNote`; `locate`; `RouteLine`; `moveCamp`, `campsOf`, `campHereIndex`; `startLabel`.
- Produces:
  - `TripMap` gains `setPosition(fix: Fix | null): void`, `setCampDrag(onDrop: ((index: number, lon: number, lat: number) => void) | null): void`, `onMove(fn: (view: Box, zoom: number) => void): void`
  - app actions gain `save, cancelSave, showSaved, toPlan, openSaved, deleteSaved, toggleGps, campHere, moveCampTo, resetCamps, downloadSaved, updateReady(apply), applyUpdate`

- [ ] **Step 1: Map additions**

In `web/src/map.ts`:

Add imports: `import type { GeoJSONSource } from "maplibre-gl";`, `import type { Fix } from "./gps";`, `import type { Box } from "./offline/tiles";`. Add `POS = "position"` to the layer-id constants line.

Extend `TripMap` with:
```ts
  setPosition(fix: Fix | null): void;
  setCampDrag(onDrop: ((index: number, lon: number, lat: number) => void) | null): void;
  onMove(fn: (view: Box, zoom: number) => void): void;
```

Inside `createMap`, next to the other closure state:
```ts
  let campDrop: ((index: number, lon: number, lat: number) => void) | null = null;
  let moveHandler: ((view: Box, zoom: number) => void) | null = null;
  map.on("moveend", () => {
    const b = map.getBounds();
    moveHandler?.([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], map.getZoom());
  });
```

Add a helper above `createMap`:
```ts
// The accuracy circle around a GPS fix, as a polygon (48 points, local flat approximation).
function circle(lon: number, lat: number, radiusM: number): [number, number][] {
  const dLat = radiusM / 111195.08, dLon = dLat / Math.cos((lat * Math.PI) / 180);
  return Array.from({ length: 49 }, (_, i) => {
    const a = (i / 48) * 2 * Math.PI;
    return [lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)] as [number, number];
  });
}
```

In `setTrip`, replace the waypoint-marker loop with:
```ts
        for (const w of c.waypoints) {
          if (w.kind === "junction" || (isLoop && w.kind === "end")) continue;
          const text = w.kind === "start" ? (isLoop ? "S/E" : "S") : w.kind === "end" ? "E" : `C${w.name.split(" ")[1]}`;
          const draggable = w.kind === "camp" && campDrop !== null;
          const m = new maplibregl.Marker({ element: badge(text, "#1b5e20", `${w.name}: ${w.desc}`), draggable }).setLngLat([w.lon, w.lat]).addTo(map);
          if (draggable) {
            const index = Number(w.name.split(" ")[1]);
            m.on("dragend", () => { const p = m.getLngLat(); campDrop?.(index, p.lng, p.lat); });
          }
          tripMarkers.push(m);
        }
```

Add to the `api` object:
```ts
    setPosition(fix) {
      ready.then(() => {
        if (!fix) { removeAll(map, POS); return; }
        const data: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [
          { type: "Feature", geometry: { type: "Polygon", coordinates: [circle(fix.lon, fix.lat, fix.accuracyM)] }, properties: {} },
          { type: "Feature", geometry: { type: "Point", coordinates: [fix.lon, fix.lat] }, properties: {} }] };
        const src = map.getSource(POS) as GeoJSONSource | undefined;
        if (src) { src.setData(data); return; }
        map.addSource(POS, { type: "geojson", data });
        map.addLayer({ id: `${POS}-accuracy`, type: "fill", source: POS, filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#1565c0", "fill-opacity": 0.15 } });
        map.addLayer({ id: `${POS}-dot`, type: "circle", source: POS, filter: ["==", ["geometry-type"], "Point"],
          paint: { "circle-radius": 7, "circle-color": "#1565c0", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      });
    },
    setCampDrag(onDrop) { campDrop = onDrop; },
    onMove(fn) { moveHandler = fn; },
```

Run: `cd web && npx tsc --noEmit 2>&1 | grep -v "src/app.ts" | head`
Expected: no errors outside `src/app.ts`.

- [ ] **Step 2: App wiring**

Replace `web/src/app.ts` with:
```ts
import { ApiError, generate, getRegions, getTrailheads, getTrails } from "./api";
import { BASEMAPS } from "./basemaps";
import { evidenceHtml } from "./evidence";
import { locate, type Fix } from "./gps";
import { createMap, type TripMap } from "./map";
import { cleanOrphans, deleteTrip, getTrip, listTrips, putTrip, tilesPresent } from "./offline/db";
import { saveTrip, tripId } from "./offline/save";
import { coverageNote } from "./offline/tiles";
import { bindCompare, renderCompare, startLabel } from "./panels/compare";
import { esc } from "./panels/common";
import { bindPlan, renderPlan, tapMessage } from "./panels/plan";
import { bindSaved, renderSaved } from "./panels/saved";
import { bindTrip, renderTrip } from "./panels/trip";
import { fromRun, toRequest, type FormState } from "./request";
import { campHereIndex, campsOf, moveCamp } from "./resplit";
import { RouteLine } from "./routeline";
import { currentCandidate } from "./selectors";
import { nextSheet } from "./sheet";
import { createStore, loadPersisted, persist, type Basemap, type State, type Store } from "./store";
import type { Trailhead } from "./types";

const DRAG_SNAP_M = 150; // a dropped camp must land this close to the route line

// Changes to these re-render the panel. (Form edits typed in the panel don't; see editForm.)
const RENDER_KEYS: (keyof State)[] = ["view", "status", "error", "busy", "run", "selected", "trailheads", "sheet", "regions",
  "saved", "savedId", "campsM", "gps", "saving", "offline", "updateReady", "mapNote"];

export function startApp(doc: Document, mapEl: HTMLElement, panel: HTMLElement, handle: HTMLElement, storage: Storage = localStorage) {
  const store: Store = createStore(loadPersisted(storage));
  persist(store, storage);
  const win = doc.defaultView;
  const geo = win?.navigator.geolocation;
  let map: TripMap | null = null;
  let abort: AbortController | null = null;
  let saveAbort: AbortController | null = null;
  let editingInPanel = false;
  let watchId: number | null = null;
  let lastAlongM: number | null = null;
  let pendingUpdate: (() => void) | null = null;

  async function refreshSaved() {
    try { store.set({ saved: await listTrips() }); } catch { /* no IndexedDB here: no saved trips */ }
  }

  async function setCamps(campsM: number[] | null) {
    const id = store.get().savedId;
    store.set({ campsM, error: null });
    if (!id) return;
    const rec = await getTrip(id);
    if (rec) { await putTrip({ ...rec, campsM }); await refreshSaved(); }
  }

  function onFix(p: GeolocationPosition) {
    const fix: Fix = { lon: p.coords.longitude, lat: p.coords.latitude, accuracyM: p.coords.accuracy };
    const c = currentCandidate(store.get());
    const where = c ? locate(c, fix, lastAlongM) : null;
    if (where?.onRoute) lastAlongM = where.alongM;
    store.set({ gps: { mode: "on", fix, where } });
    map?.setPosition(fix);
  }

  const actions = {
    async pickRegion(id: string) {
      const region = store.get().regions.find((r) => r.id === id);
      if (!region) return;
      store.set({ region: id, trailheads: [], run: null, savedId: null, campsM: null, view: "plan", status: "Loading trails…", error: null });
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
    tapPoint(lon: number, lat: number, trailName: string | null) {
      if (store.get().view !== "plan") return;
      store.set((s) => ({ form: { ...s.form, trailhead: `${lat.toFixed(5)},${lon.toFixed(5)}` }, error: null, status: tapMessage(trailName) }));
    },
    async generate() {
      const s = store.get();
      const built = toRequest(s.form, s.trailheads);
      if ("error" in built) return store.set({ error: built.error });
      if (!s.region) return store.set({ error: "Pick a region first." });
      abort?.abort();
      abort = new AbortController();
      const timer = setTimeout(() => abort?.abort(), 120_000);
      store.set({ busy: true, error: null, status: "Searching… this takes 5–20 s on a big network." });
      try {
        const run = await generate(s.region, built.request, abort.signal);
        store.set({ run, selected: 1, view: "compare", busy: false, status: "", savedId: null, campsM: null });
      } catch (e) {
        const msg = (e as Error).name === "AbortError"
          ? "The search took longer than 2 minutes and was stopped. Try a narrower range or fewer must-visits."
          : e instanceof ApiError ? e.message : `Unexpected error: ${(e as Error).message}`;
        store.set({ busy: false, status: "", error: msg }); // the previous run (if any) stays
      } finally { clearTimeout(timer); }
    },
    select(i: number) { store.set({ selected: i }); },
    openTrip(i: number) {
      const s = store.get();
      const rec = s.run ? s.saved.find((t) => t.id === tripId(s.run!, i)) : undefined;
      lastAlongM = null;
      store.set({ selected: i, view: "trip", sheet: "half", savedId: rec?.id ?? null, campsM: rec?.campsM ?? null, status: "", error: null });
    },
    backToCompare() { store.set({ view: "compare", status: "", error: null }); },
    replan() { const run = store.get().run; store.set({ view: "plan", form: run ? fromRun(run) : store.get().form }); },
    setBasemap(b: Basemap) { store.set({ basemap: b }); map?.setBasemap(b); },
    showJunctions(show: boolean) { map?.setJunctions(currentCandidate(store.get()), show); },
    sheet(gesture: "tap" | "up" | "down") { store.set((s) => ({ sheet: nextSheet(s.sheet, gesture) })); },
    // Edits typed into the plan panel: record them without re-rendering the panel (a re-render on blur
    // would replace the Generate button under the user's finger and lose the tap).
    editForm(form: FormState) { editingInPanel = true; try { store.set({ form }); } finally { editingInPanel = false; } },

    async save() {
      const s = store.get();
      if (!s.run || s.saving) return;
      saveAbort = new AbortController();
      store.set({ saving: { done: 0, total: 0, failed: 0 }, error: null, status: "" });
      try {
        const rec = await saveTrip(s.run, s.selected, startLabel(s.run.request.trailhead, s.trailheads), (p) => store.set({ saving: p }), saveAbort.signal);
        await refreshSaved();
        store.set({ saving: null, savedId: rec.id, campsM: null, status: rec.complete ? "Saved for offline." : "" });
      } catch (e) {
        store.set((e as Error).name === "AbortError"
          ? { saving: null, status: "Save cancelled; nothing was kept." }
          : { saving: null, error: `Couldn't save this trip: ${(e as Error).message}` });
      } finally { saveAbort = null; }
    },
    cancelSave() { saveAbort?.abort(); },
    async showSaved() { await refreshSaved(); store.set({ view: "saved", status: "", error: null }); },
    toPlan() { store.set({ view: "plan", status: "", error: null }); },
    async openSaved(id: string) {
      const rec = await getTrip(id);
      if (!rec) return store.set({ error: "That saved trip is gone." });
      lastAlongM = null;
      const tilesGone = !(await tilesPresent(rec));
      store.set({ run: rec.run, selected: rec.index, savedId: rec.id, campsM: rec.campsM, view: "trip", sheet: "half", status: "",
        error: tilesGone ? "This trip's offline map was cleared by the browser. Save the trip again while online to get the map back." : null });
      map?.setTrails(rec.trails);
      map?.fitBounds(rec.box);
    },
    async deleteSaved(id: string) {
      if (!win?.confirm("Delete this saved trip and its offline map?")) return;
      await deleteTrip(id);
      await refreshSaved();
      if (store.get().savedId === id) store.set({ savedId: null, campsM: null });
    },
    toggleGps() {
      if (store.get().gps.mode !== "off") {
        if (watchId !== null) geo?.clearWatch(watchId);
        watchId = null; lastAlongM = null;
        store.set({ gps: { mode: "off" } });
        map?.setPosition(null);
        return;
      }
      if (!win?.isSecureContext) return store.set({ gps: { mode: "error", message: "location needs a secure (https) address; see the README's Tailscale step" } });
      if (!geo) return store.set({ gps: { mode: "error", message: "this browser has no location service" } });
      store.set({ gps: { mode: "waiting" } });
      watchId = geo.watchPosition(onFix, (err) => store.set({ gps: { mode: "error", message: err.message || "no position fix yet" } }),
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 30_000 });
    },
    async campHere() {
      const s = store.get();
      const c = currentCandidate(s);
      if (s.gps.mode !== "on" || !s.gps.where?.onRoute || !c?.plan) return;
      const index = campHereIndex(s.gps.where.day, c.plan.camps.length);
      if (index === null) return store.set({ error: "You're on the last day; there's no camp ahead to move." });
      const res = moveCamp(campsOf(c), index, s.gps.where.alongM, c.length_m);
      if ("error" in res) return store.set({ error: res.error });
      await setCamps(res.campsM);
    },
    async moveCampTo(index: number, lon: number, lat: number) {
      const s = store.get();
      const base = s.run?.candidates[s.selected - 1];
      const c = currentCandidate(s);
      if (!base || !c?.plan) return;
      const current = campsOf(c);
      const { snap } = RouteLine.of(base).nearest(lon, lat, current[index - 1] ?? null);
      const res = snap.offM > DRAG_SNAP_M
        ? { error: "Drop the camp on the route line; camps can't leave the route." }
        : moveCamp(current, index, snap.alongM, c.length_m);
      if ("error" in res) { store.set({ error: res.error }); map?.setTrip(c, evidenceHtml); return; } // the marker goes back
      await setCamps(res.campsM);
    },
    resetCamps() { void setCamps(null); },
    downloadSaved(which: string) {
      const s = store.get();
      const rec = s.saved.find((t) => t.id === s.savedId);
      if (!rec) return;
      const text = which === "whole" ? rec.gpx.whole : rec.gpx.days[Number(which) - 1] ?? null;
      if (!text) return store.set({ error: "That GPX file wasn't saved with this trip." });
      const url = URL.createObjectURL(new Blob([text], { type: "application/gpx+xml" }));
      const a = doc.createElement("a");
      a.href = url;
      a.download = `${rec.run.run_id}_candidate_${rec.index}${which === "whole" ? "" : `_day_${which}`}.gpx`;
      doc.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    },
    updateReady(apply: () => void) { pendingUpdate = apply; store.set({ updateReady: true }); },
    applyUpdate() { pendingUpdate?.(); },
  };

  const renderers: Record<string, (s: State) => string> = { plan: renderPlan, compare: renderCompare, trip: renderTrip, saved: renderSaved };
  const binders: Record<string, (root: HTMLElement) => void> = {
    plan: (root) => bindPlan(root, store, actions),
    compare: (root) => bindCompare(root, store, actions),
    trip: (root) => bindTrip(root, store, actions),
    saved: (root) => bindSaved(root, actions),
  };
  let shownView: string | null = null;
  function render() {
    const s = store.get();
    const banner = s.updateReady ? `<div class="warn" id="update">A new version of the app is ready. <button class="link" id="apply-update">Reload</button></div>` : "";
    panel.innerHTML = banner + (renderers[s.view]?.(s) ?? "");
    binders[s.view]?.(panel);
    panel.querySelector("#apply-update")?.addEventListener("click", () => actions.applyUpdate());
    handle.parentElement!.className = `sheet sheet-${s.sheet}`;
    if (s.view !== shownView) panel.scrollTop = 0; // a new view starts at its heading, not where the last one was scrolled
    shownView = s.view;
  }
  function syncMap(s: State) {
    if (!s.run || s.view === "saved") { map?.clearCandidates(); map?.setTrip(null); map?.setJunctions(null, false); return; }
    if (s.view === "trip") {
      const c = currentCandidate(s);
      map?.clearCandidates(); map?.setJunctions(null, false);
      map?.setCampDrag(s.savedId && c?.plan ? (i, lon, lat) => void actions.moveCampTo(i, lon, lat) : null);
      map?.setTrip(c, evidenceHtml);
    } else {
      map?.setTrip(null); map?.setJunctions(null, false);
      map?.setCandidates(s.run.candidates, s.selected, (i) => actions.select(i));
    }
  }
  store.subscribe((s, prev) => {
    if (RENDER_KEYS.some((k) => s[k] !== prev[k]) || (s.form !== prev.form && !editingInPanel)) render();
    if (s.run !== prev.run || s.selected !== prev.selected || s.view !== prev.view || s.campsM !== prev.campsM || s.savedId !== prev.savedId) syncMap(s);
  });
  // Tap cycles the sheet; a vertical drag of more than 30px on the handle moves it one step up or down.
  let dragStart: number | null = null;
  handle.addEventListener("pointerdown", (e) => { dragStart = e.clientY; });
  handle.addEventListener("pointerup", (e) => {
    const dy = dragStart == null ? 0 : e.clientY - dragStart;
    dragStart = null;
    actions.sheet(dy < -30 ? "up" : dy > 30 ? "down" : "tap");
  });

  const basemapEl = doc.createElement("select");
  basemapEl.id = "basemap";
  basemapEl.setAttribute("aria-label", "Base map");
  basemapEl.innerHTML = Object.entries(BASEMAPS).map(([k, b]) => `<option value="${k}"${k === store.get().basemap ? " selected" : ""}>${esc(b.name)}</option>`).join("");
  basemapEl.addEventListener("change", () => actions.setBasemap(basemapEl.value as Basemap));
  map = createMap(mapEl, store.get().basemap, [-79.35, 39.02], 11, basemapEl);
  map.onMapTap((lon, lat, name) => actions.tapPoint(lon, lat, name));
  map.onMove((view, zoom) => {
    const s = store.get();
    const rec = s.savedId ? s.saved.find((t) => t.id === s.savedId) : undefined;
    const offline = s.offline || win?.navigator.onLine === false;
    const note = rec && s.view === "trip" && offline ? coverageNote(rec.box, view, zoom) : null;
    if (note !== s.mapNote) store.set({ mapNote: note });
  });
  render();
  void (async () => {
    await cleanOrphans().catch(() => []);
    await refreshSaved();
    try {
      const regions = await getRegions();
      store.set({ regions, offline: false });
      const saved = store.get().region;
      const id = saved && regions.some((r) => r.id === saved) ? saved : regions[0]?.id;
      if (id) await actions.pickRegion(id);
      else store.set({ error: "No region has cached data yet. Run `backcountry ingest` and `backcountry dem` first." });
    } catch (e) {
      const unreachable = e instanceof ApiError && e.status === 0;
      if (unreachable && store.get().saved.length) store.set({ offline: true, view: "saved", status: "", error: null });
      else store.set({ offline: unreachable, error: (e as Error).message });
    }
  })();

  return { store, actions, map };
}
```

In `web/src/main.ts`, replace the registration line with:
```ts
if (import.meta.env.PROD) registerWorker((apply) => app.actions.updateReady(apply));
```

- [ ] **Step 3: Verify**

Run: `cd web && npx tsc --noEmit && npm test && npm run build && npm run test:e2e`
Expected: tsc clean; all Vitest tests pass; build succeeds; the 6 existing Playwright tests pass.

- [ ] **Step 4: Commit**

```bash
git add web/src/map.ts web/src/app.ts web/src/main.ts
git commit -m "web: wire saving, saved trips, offline start, GPS position and camp moves into the app"
```

---

### Task 10: End-to-end offline test

**Files:**
- Create: `web/e2e/offline.spec.ts`

**Interfaces:**
- Consumes: the built app with its service worker (Playwright's `vite preview` on 127.0.0.1, which browsers treat as a secure context), the synthetic API (`scripts/test_api_server.py`), `window.backcountry` (main.ts).

- [ ] **Step 1: Write the test**

`web/e2e/offline.spec.ts`:
```ts
import { expect, test } from "@playwright/test";

// A 1x1 PNG stands in for every USGS tile: the test never contacts basemap.nationalmap.gov.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const TILES = /basemap\.nationalmap\.gov/;

test("save a trip, go offline, reopen it, see where I am, move a camp, delete it", async ({ page, context }) => {
  await context.route(TILES, (route) => route.fulfill({ status: 200, contentType: "image/png", body: PNG, headers: { "access-control-allow-origin": "*" } }));
  await context.grantPermissions(["geolocation"]);
  page.on("dialog", (d) => d.accept());

  // Plan and open a 1-night trip on the synthetic grid.
  await page.goto("/");
  const options = page.locator("select[name='trailhead'] optgroup[label='Other start points from the data'] option");
  await expect(options).not.toHaveCount(0);
  await page.selectOption('select[name="trailhead"]', (await options.first().getAttribute("value"))!);
  await page.fill('input[name="total"]', "2.4-5.1");
  await page.fill('input[name="nights"]', "1");
  await page.fill('input[name="daily"]', "0.5-4");
  await page.fill('input[name="maxGainDay"]', "");
  await page.click("#generate");
  await expect(page.locator(".card")).toHaveCount(3, { timeout: 30_000 });
  await page.click("#open-trip");

  // Save it.
  await expect(page.locator("#save-offline")).toContainText(/about \d+ map tiles/);
  await page.click("#save-offline");
  await expect(page.locator("#saved-line")).toContainText("Saved for offline", { timeout: 60_000 });
  await expect(page.locator("#incomplete")).toHaveCount(0);
  await page.waitForFunction(() => navigator.serviceWorker?.controller != null);
  const tileUrl = await page.evaluate(async () => {
    const name = (await caches.keys()).find((n) => n.startsWith("trip-"))!;
    return (await (await caches.open(name)).keys())[0]!.url;
  });

  // Offline: the app opens from the service worker on Saved trips, and the saved tiles come from the cache.
  await context.unroute(TILES);
  await context.setOffline(true);
  await page.reload();
  await expect(page.locator("#offline-note")).toContainText("Offline: showing saved trips");
  expect(await page.evaluate(async (u) => (await fetch(u)).ok, tileUrl)).toBe(true);
  await page.click("[data-open]");
  await expect(page.locator("h1")).toContainText("Candidate 1");
  await expect(page.locator("#known-gaps")).toBeVisible();

  // GPS: a fix on day 1 of the route.
  const [lon, lat] = await page.evaluate(() => {
    const s = (window as unknown as { backcountry: { store: { get(): { run: { candidates: { pieces: { points: number[][] }[] }[] }; selected: number } } } }).backcountry.store.get();
    const p = s.run.candidates[s.selected - 1]!.pieces[0]!.points;
    return [(p[0]![0]! + p[1]![0]!) / 2, (p[0]![1]! + p[1]![1]!) / 2];
  });
  await context.setGeolocation({ longitude: lon!, latitude: lat!, accuracy: 10 });
  await page.click("#locate");
  await expect(page.locator("#gps-line")).toContainText(/mile \d+\.\d of \d+\.\d · Day 1 · .* to Camp 1/);

  // Move tonight's camp to here; the days table and the saved record follow.
  const before = await page.locator("table").first().innerText();
  await page.click("#camp-here");
  await expect(page.locator("#reset-camps")).toBeVisible();
  expect(await page.locator("table").first().innerText()).not.toBe(before);
  await expect(page.locator(".note").filter({ hasText: "Camps moved by you on this phone" })).toHaveCount(1);
  await page.reload();
  await expect(page.locator(".saved-trip")).toContainText("camps moved by you");

  // Delete it: record and tiles are gone.
  await page.click("[data-delete]");
  await expect(page.locator("#panel")).toContainText("No saved trips yet");
  expect(await page.evaluate(async () => (await caches.keys()).filter((n) => n.startsWith("trip-")).length)).toBe(0);
});
```

- [ ] **Step 2: Run it**

Run: `cd web && npm run test:e2e`
Expected: 7 passed. If the service worker's own tile requests escape `context.route` in this Playwright/Chromium version, the save step reports missing tiles (`#incomplete` visible); then route with `page.route` as well, or serve tiles from the test API (ledger the choice). Do not weaken the offline assertions.

- [ ] **Step 3: Commit**

```bash
git add web/e2e/offline.spec.ts
git commit -m "web: end-to-end offline test (save, offline reopen, GPS line, camp move, delete)"
```

---

### Task 11: Docs and progress

**Files:**
- Modify: `README.md`, `PROGRESS.md`, `docs/ROADMAP.md`

- [ ] **Step 1: README**

Add after the "Web app (phone or laptop)" section:
```markdown
### Offline trips (phone)

Saving trips and showing your position need a secure (https) address. Plain `http://<laptop-ip>:8000`
works for planning only. The simplest secure address is Tailscale (free):

1. Install Tailscale on the laptop and the phone; sign in to the same account on both.
2. In the Tailscale admin console, turn on HTTPS certificates (DNS → HTTPS Certificates). Once.
3. On the laptop: `uv run backcountry serve`, then `tailscale serve --bg 8000`.
4. On the phone, with Tailscale on, open `https://<laptop-name>.<tailnet>.ts.net` (the address
   `tailscale serve` prints). Add it to your home screen.

Then: open a trip and tap **Save for offline** (on Wi-Fi; a Dolly Sods trip is about 16 MB of map).
With no signal, the app opens on **Saved trips**. In a saved trip, **Show my location** gives your mile,
day, and distance and climb to the next camp; drag a camp marker along the route (or tap **Camp here**)
to move a camp, and the days are recomputed on the phone. Saved trips can be deleted from the list.
Map tiles: USGS The National Map (public domain).
```

- [ ] **Step 2: PROGRESS and ROADMAP**

Add a `## Phase 2 piece 3 — Offline trips (built <date>; awaiting your phone check)` section to `PROGRESS.md`: what was built (service worker, save with tiles/GPX/trails, saved-trips view, offline start, GPS line and profile marker, moved camps with engine parity, update banner), test counts (Vitest, Playwright, pytest), the parity fixture and how to regenerate it, what's deferred (background tracking, offline re-planning, whole-region saves, GPX rebuilt on the phone), known limits (iOS may still evict storage when `persist()` isn't granted; nearby-campsite lists for moved camps only include sites already listed along the route), and anything found wrong.

In `docs/ROADMAP.md`, mark piece 3 as built with a pointer to the spec.

- [ ] **Step 3: Commit**

```bash
git add README.md PROGRESS.md docs/ROADMAP.md
git commit -m "docs: offline trips setup (Tailscale) and progress"
```

---

## Self-review notes

- Spec coverage: Tailscale (Task 11 README); trip box + margin, z12–16, Topo + Imagery+Topo (Task 4); service worker shell/tiles/api rules and update banner (Tasks 6, 9); save flow with estimate, progress, retry, cancel, persist, incomplete reporting, GPX bytes, trails subset (Tasks 7, 8, 9); IndexedDB record (Task 5); offline start on Saved (Task 9, e2e Task 10); "map not saved here" (Tasks 4, 9); GPS dot + line + profile marker + ambiguity + off route + rough (Tasks 1, 3, 8, 9); camp moves by drag and Camp here with engine wording and parity (Tasks 2, 9); saved GPX labelled as original camps (Task 8); delete (Tasks 5, 9, 10); storage used (Task 8 total). Python parity test (Task 2). Server unchanged.
- Deviation from the spec, decided here: a moved camp's "known campsites nearby" can only use sites already listed along the route (within ~490 ft of it), since the phone doesn't store the region's full campsite list; the trip view says "listed along the route" for moved camps (Task 8), and PROGRESS notes it.
- Types: `Fix`/`Where` (Task 3) used by `store.ts` (Task 8) and `app.ts` (Task 9); `SavedTrip` (Task 5) used by Tasks 7–9; `SaveProgress` (Task 7) used by `store.ts`; `Box` (Task 4) used by db/save/map; `TripActions`/`SavedActions`/`PlanActions` match the `actions` object in Task 9.
- Review Focus 1 → Task 5 `cleanOrphans` test + Task 9 boot; 2 → Task 5 `tilesPresent` + Task 9 `openSaved`; 3 → Tasks 1, 3; 4 → Task 2 `moveCamp` tests + Task 9 `moveCampTo`; 5 → Tasks 7, 8.
