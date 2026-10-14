import { describe, expect, it } from "vitest";
import { DEFAULT_FORM, fromRun, toRequest } from "./request";
import type { Run } from "./types";

describe("toRequest", () => {
  it("maps a filled form to the API request", () => {
    const r = toRequest({ ...DEFAULT_FORM, trailhead: "bear_rocks", total: "18-26", nights: "2", daily: "6-11",
      maxGainDay: "2500", type: "loop", direction: "cw", mustVisit: ["Rocky Point Trail"], avoid: [], camps: ["8.5"] });
    expect("request" in r && r.request).toEqual({
      trailhead: "bear_rocks", total: "18-26", nights: 2, daily: "6-11", max_gain_day: 2500, type: "loop", count: 3,
      no_roads: false, camps: ["8.5"], camps_at_sites: false, must_visit: ["Rocky Point Trail"], avoid: [], direction: "cw",
    });
  });
  it("omits nights/daily/gain when blank", () => {
    const r = toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "18-26", nights: "", daily: "", maxGainDay: "" });
    expect("request" in r && r.request.nights).toBeNull();
    expect("request" in r && r.request.daily).toBeNull();
  });
  it("names the field for a bad range, like the API does", () => {
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "26-18" })).toEqual({ error: expect.stringContaining("18-26"), field: "total" });
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "18-26", nights: "2", daily: "" })).toMatchObject({ field: "daily" });
    expect(toRequest({ ...DEFAULT_FORM, total: "18-26" })).toMatchObject({ field: "trailhead" });
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "x", total: "18-26", nights: "x", daily: "6-11" })).toMatchObject({ field: "nights" });
  });
});

describe("fromRun", () => {
  it("prefills the form from a run's request", () => {
    const run = { request: { trailhead: "bear_rocks", total: [18, 26], nights: 2, daily: [6, 11], max_gain_day: 2500, type: "loop",
      count: 3, no_roads: false, road_weight: 1, repeat_weight: 0.5, camps: [], camps_at_sites: true, must_visit: ["A"], avoid: ["B"], direction: null } } as unknown as Run;
    expect(fromRun(run)).toMatchObject({ trailhead: "bear_rocks", total: "18-26", nights: "2", daily: "6-11", maxGainDay: "2500",
      type: "loop", campsAtSites: true, mustVisit: ["A"], avoid: ["B"], direction: "" });
  });
});
