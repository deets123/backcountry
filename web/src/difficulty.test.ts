import { describe, expect, it } from "vitest";
import { rateSaved, tripLevel } from "./difficulty";
import fixture from "./fixtures/difficulty-parity.json";
import type { DifficultyRules, Rating, TerrainInterval } from "./types";

const rules = fixture.rules as unknown as DifficultyRules;

function same(got: Rating | null, want: Rating | null) {
  if (want === null) return expect(got).toBeNull();
  expect(got!.level).toBe(want.level);
  expect(got!.reasons).toEqual(want.reasons);
  if (want.effort === null) expect(got!.effort).toBeNull(); else expect(got!.effort!).toBeCloseTo(want.effort, 2);
  if (want.steep === null) expect(got!.steep).toBeNull();
  else { expect(got!.steep!.grade_pct).toBeCloseTo(want.steep.grade_pct, 1); expect(got!.steep!.start_m).toBeCloseTo(want.steep.start_m, 3); }
  expect(got!.tagged.map((t) => t.reason)).toEqual(want.tagged.map((t) => t.reason));
  expect(got!.untagged_share).toBeCloseTo(want.untagged_share, 6);
}

describe("difficulty.ts matches the engine's rate_saved", () => {
  for (const k of fixture.cases) {
    it(`matches the engine on every parity case: ${k.name}`, () => {
      const got = rateSaved(k.profile as never, k.terrain as TerrainInterval[], k.nights, k.camps_m, rules);
      const want = k.expected as unknown as { days: { start_m: number; end_m: number; gain_ft: number | null; rating: Rating }[]; level: string | null; rating: Rating | null };
      expect(got.level).toBe(want.level);
      same(got.rating, want.rating);
      expect(got.days).toHaveLength(want.days.length);
      got.days.forEach((d, i) => {
        const w = want.days[i]!;
        expect(d.start_m).toBeCloseTo(w.start_m, 6); expect(d.end_m).toBeCloseTo(w.end_m, 6);
        if (w.gain_ft === null) expect(d.gain_ft).toBeNull(); else expect(d.gain_ft!).toBeCloseTo(w.gain_ft, 1);
        same(d.rating, w.rating);
      });
    });
  }
  it("a trip is as hard as its hardest day, and unknown if any day is", () => {
    const r = (level: Rating["level"]): Rating => ({ level, reasons: [], effort: null, steep: null, tagged: [], untagged_share: 1 });
    expect(tripLevel([r("easy"), r("hard"), r("moderate")])).toBe("hard");
    expect(tripLevel([r("easy"), r(null)])).toBeNull();
    expect(tripLevel([])).toBeNull();
  });
});
