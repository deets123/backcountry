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
