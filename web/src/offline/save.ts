// Saves one candidate for offline use: its map tiles (Cache Storage, at most 4 downloads at a time, one
// retry each), its GPX files exactly as the server built them, nearby trail lines, and the run (IndexedDB).
// Failures are kept and reported ("incomplete"), never hidden.
import { getTrails, gpxUrl } from "../api";
import type { Run, TrailFeatureCollection } from "../types";
import { getTrip, putTrip, type SavedTrip } from "./db";
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
  signal: AbortSignal, deps: SaveDeps = defaultDeps(), campsM: number[] | null = null): Promise<SavedTrip> {
  const c = run.candidates[n - 1];
  if (!c) throw new Error(`This run has no candidate ${n}.`);
  const box = tripBox(c);
  if (!box) throw new Error("This candidate has no route line to save.");
  const cs = deps.caches;
  if (!cs) throw new Error("This browser can't store map tiles. Offline saving needs a secure (https) address; see the README's Tailscale step.");
  // each save fills its own cache and only then replaces the old one: a cancelled re-save keeps what was there
  const stamp = deps.now().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const tiles = tileList(box), cacheName = `trip-${tripId(run, n)}-${stamp}`;
  const before = await getTrip(tripId(run, n)).catch(() => undefined);
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
          const res = await deps.fetch(t.url, { signal, cache: "reload" }); // "reload": past any saved copy (see sw.js)
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
      id: tripId(run, n), savedAt: deps.now().toISOString(), startName, run, index: n, campsM,
      gpx: { whole, days }, trails, box, tiles: { cache: cacheName, total: tiles.length, saved, missingByZoom: missing },
      bytes, persisted, complete: problems.length === 0, problems,
    };
    await putTrip(rec);
    if (before && before.tiles.cache !== cacheName) await cs.delete(before.tiles.cache).catch(() => false);
    return rec;
  } catch (e) {
    await cs.delete(cacheName);
    throw e;
  }
}
