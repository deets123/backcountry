// Trips saved before difficulty levels existed (or under older rules) get their levels from the server when the
// phone is online: POST /api/difficulty rebuilds the route's terrain from the trail data and rates the engine's
// days. From then on the phone re-rates moved camps itself (resplit.ts). Until the refresh, such a trip shows
// no levels at all, never levels computed without its terrain.
import levelsJson from "../levels.json";
import type { DifficultyRules, Level, Rating, Run, TerrainInterval } from "../types";
import type { SavedTrip } from "./db";

const CURRENT = (levelsJson.rules as unknown as DifficultyRules).version;

export interface DifficultyBody {
  region: string; nights: number | null; camps_m: number[] | null;
  profile: { d_m: number[]; z_m: (number | null)[] }; segments: Run["candidates"][number]["segments"];
  geometry: [number, number][];  // the route's line: places terrain tags inside a trail whose ways are tagged differently
}
export interface DifficultyAnswer {
  terrain: TerrainInterval[]; days: { start_m: number; end_m: number; gain_ft: number | null; rating: Rating }[];
  level: Level | null; rating: Rating | null; difficulty_rules: DifficultyRules;
}
export interface LevelDeps { difficulty(body: DifficultyBody): Promise<DifficultyAnswer>; put(t: SavedTrip): Promise<void>; now(): string }

export function needsLevels(t: SavedTrip): boolean {
  const c = t.run.candidates[t.index - 1];
  if (!c || !c.profile) return false;  // nothing to rate
  return !t.run.difficulty_rules || t.run.difficulty_rules.version < CURRENT || !c.terrain;
}

function withLevels(t: SavedTrip, a: DifficultyAnswer, now: string): SavedTrip {
  const candidates = t.run.candidates.map((c, i) => {
    if (i !== t.index - 1) return c;
    const plan = c.plan ? { ...c.plan, days: c.plan.days.map((d, k) => ({ ...d, rating: a.days[k]?.rating ?? null })) } : null;
    return { ...c, terrain: a.terrain, level: a.level, rating: a.rating, plan };
  });
  return { ...t, levelsRefreshedAt: now, run: { ...t.run, candidates, difficulty_rules: a.difficulty_rules } };
}

/** Refresh every saved trip that needs levels; returns the ids refreshed. Failures wait for the next boot. */
export async function refreshLevels(trips: SavedTrip[], deps: LevelDeps): Promise<string[]> {
  const done: string[] = [];
  for (const t of trips.filter(needsLevels)) {
    const c = t.run.candidates[t.index - 1]!;
    try {
      const a = await deps.difficulty({ region: t.run.region, nights: c.plan?.nights ?? null,
        camps_m: c.plan ? c.plan.camps.map((cp) => cp.dist_m) : null, profile: c.profile!, segments: c.segments,
        geometry: c.geometry.coordinates });
      await deps.put(withLevels(t, a, deps.now()));
      done.push(t.id);
    } catch { /* offline, or the region is gone from the server: try again next time */ }
  }
  return done;
}
