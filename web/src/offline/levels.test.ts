import { describe, expect, it } from "vitest";
import levelsJson from "../levels.json";
import type { DifficultyRules, Rating, Run } from "../types";
import type { SavedTrip } from "./db";
import { needsLevels, refreshLevels, type DifficultyAnswer } from "./levels";

const rules = levelsJson.rules as unknown as DifficultyRules;
const rating = (level: Rating["level"]): Rating => ({ level, reasons: [], effort: 50, steep: null, tagged: [], untagged_share: 1 });

function oldTrip(id = "r1-1"): SavedTrip {
  const cand = { index: 1, length_m: 3000, profile: { d_m: [0, 1500, 3000], z_m: [1, 2, 3] },
    segments: [{ label: "A Trail", highway: "path", is_road: false, last_resort: false, length_m: 3000, sources: ["osm:way/1"] }],
    plan: { nights: 1, days: [{ index: 1, start_m: 0, end_m: 1500 }, { index: 2, start_m: 1500, end_m: 3000 }],
      camps: [{ index: 1, dist_m: 1500 }] } };
  const run = { run_id: "r1", region: "dolly_sods", candidates: [cand] } as unknown as Run;  // saved before levels existed
  return { id, savedAt: "2026-10-14T12:00:00Z", startName: "S", run, index: 1, campsM: [1000], gpx: { whole: null, days: [] },
    trails: { type: "FeatureCollection", attribution: "", features: [] }, box: [0, 0, 0, 0] as never,
    tiles: { cache: "trip-r1-1", total: 0, saved: 0, missingByZoom: {} }, bytes: 0, persisted: true, complete: true, problems: [] };
}

const answer: DifficultyAnswer = { terrain: [{ start_m: 0, end_m: 200, reason: null, sources: ["osm:way/1"] }],
  days: [{ start_m: 0, end_m: 1500, gain_ft: 3, rating: rating("easy") }, { start_m: 1500, end_m: 3000, gain_ft: 3, rating: rating("hard") }],
  level: "hard", rating: null, difficulty_rules: rules };

describe("levels for saved trips", () => {
  it("old saved trip shows no levels until refreshed", () => {
    const t = oldTrip();
    expect(needsLevels(t)).toBe(true);
    expect(t.run.candidates[0]!.level).toBeUndefined();
    expect(t.run.candidates[0]!.plan!.days[0]!.rating).toBeUndefined();
  });
  it("online boot refreshes saved trips missing levels and stores them", async () => {
    const sent: unknown[] = [];
    const stored: SavedTrip[] = [];
    const done = await refreshLevels([oldTrip(), { ...oldTrip("r2-1"), run: { ...oldTrip().run, difficulty_rules: rules,
      candidates: [{ ...oldTrip().run.candidates[0]!, terrain: [] }] } }],
      { difficulty: async (body) => { sent.push(body); return answer; }, put: async (t) => { stored.push(t); }, now: () => "2026-10-22T00:00:00Z" });
    expect(done).toEqual(["r1-1"]);  // the second already has levels from these rules
    expect(sent).toEqual([{ region: "dolly_sods", nights: 1, camps_m: [1500], profile: { d_m: [0, 1500, 3000], z_m: [1, 2, 3] },
      segments: oldTrip().run.candidates[0]!.segments }]);  // the engine's camps: moved ones are re-rated on the phone
    const c = stored[0]!.run.candidates[0]!;
    expect(c.terrain).toEqual(answer.terrain);
    expect(c.level).toBe("hard");
    expect(c.plan!.days.map((d) => d.rating!.level)).toEqual(["easy", "hard"]);
    expect(stored[0]!.run.difficulty_rules).toEqual(rules);
    expect(stored[0]!.levelsRefreshedAt).toBe("2026-10-22T00:00:00Z");
    expect(stored[0]!.campsM).toEqual([1000]);
    expect(needsLevels(stored[0]!)).toBe(false);
  });
  it("a failed refresh is left for the next boot", async () => {
    const stored: SavedTrip[] = [];
    const done = await refreshLevels([oldTrip()], { difficulty: async () => { throw new Error("offline"); }, put: async (t) => { stored.push(t); }, now: () => "x" });
    expect(done).toEqual([]);
    expect(stored).toEqual([]);
  });
  it("older rules are refreshed too", () => {
    const t = oldTrip();
    t.run = { ...t.run, difficulty_rules: { ...rules, version: rules.version - 1 }, candidates: [{ ...t.run.candidates[0]!, terrain: [] }] };
    expect(needsLevels(t)).toBe(true);
  });
});
