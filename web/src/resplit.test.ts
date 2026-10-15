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
