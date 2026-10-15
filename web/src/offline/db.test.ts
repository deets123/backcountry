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
