import { describe, expect, it } from "vitest";
import { currentCandidate, openTripId } from "./selectors";
import { INITIAL } from "./store";
import type { Candidate, Run } from "./types";

const M_PER_DEG = 111195.08;
const cand = { index: 1, kind: "loop", length_m: 3000, gain_ft: null, loss_ft: null, road_m: 0, last_resort_m: 0, repeated_m: 0, penalty: 0,
  segments: [{ label: "T", highway: "path", is_road: false, last_resort: false, length_m: 3000, sources: ["osm:way/1"] }],
  geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39 + 3000 / M_PER_DEG]] }, pieces: [], waypoints: [], features: [],
  profile: { d_m: [0, 3000], z_m: [null, null] }, title: "t", description: "d", notes: [],
  plan: { nights: 1, daily_min_m: 0, daily_max_m: 1e9, max_gain_ft: null, ok: true, penalty: 0, notes: [], violations: [],
    days: [{ index: 1, start_m: 0, end_m: 1500, length_m: 1500, gain_ft: null, loss_ft: null, start_label: "Start", end_label: "Camp 1" },
           { index: 2, start_m: 1500, end_m: 3000, length_m: 1500, gain_ft: null, loss_ft: null, start_label: "Camp 1", end_label: "End" }],
    camps: [{ index: 1, dist_m: 1500, lat: 39, lon: -79.4, on_source: "osm:way/1", on_name: "T", nearby: [], note: "camp_location: unverified", pinned: false, site: null, nearest_water: null }] },
} as unknown as Candidate;
const run = { run_id: "r1", candidates: [cand, { ...cand, index: 2 }] } as unknown as Run;

describe("selectors", () => {
  it("names the trip that is open, or null outside the trip view", () => {
    expect(openTripId({ ...INITIAL, run, selected: 2, view: "trip" })).toBe("r1-2");
    expect(openTripId({ ...INITIAL, run, selected: 2, view: "compare" })).toBeNull();
    expect(openTripId({ ...INITIAL, view: "trip" })).toBeNull();
  });
  it("falls back to the engine's camps when stored camps don't fit this trip, instead of breaking the view", () => {
    expect(currentCandidate({ ...INITIAL, run, selected: 1, campsM: [500, 1000] })).toBe(cand);
  });
});
