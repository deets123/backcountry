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
