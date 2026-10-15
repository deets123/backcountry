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
