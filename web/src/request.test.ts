import { describe, expect, it } from "vitest";
import { DEFAULT_FORM, dayTargets, emptyDay, fromRun, pickLevel, setDayNumber, toRequest } from "./request";
import type { Run } from "./types";

describe("toRequest", () => {
  it("maps a filled form to the API request", () => {
    const r = toRequest({ ...DEFAULT_FORM, trailhead: "bear_rocks", total: "18-26", nights: "2", daily: "6-11",
      maxGainDay: "2500", type: "loop", direction: "cw", mustVisit: ["Rocky Point Trail"], avoid: [], camps: ["8.5"] });
    expect("request" in r && r.request).toEqual({
      trailhead: "bear_rocks", total: "18-26", nights: 2, daily: "6-11", max_gain_day: 2500, type: "loop", count: 3,
      no_roads: false, camps: ["8.5"], camps_at_sites: false, must_visit: ["Rocky Point Trail"], avoid: [], direction: "cw",
      days: [], level: null,
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

describe("toRequest with the trailhead list", () => {
  const th = { id: "old_th", name: "Old", node: 1, source: "yaml" as const, verified: true, in_graph: false, lat: 39, lon: -79, component: null };
  it("refuses a saved trailhead that is no longer on the trail network", () => {
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "old_th" }, [th])).toEqual({ error: expect.stringContaining("not on the trail network"), field: "trailhead" });
  });
  it("still accepts ids the list doesn't know (map points, typed ids)", () => {
    expect(toRequest({ ...DEFAULT_FORM, trailhead: "39.04,-79.38" }, [th])).toHaveProperty("request");
  });
});

describe("per-day targets", () => {
  const base = { ...DEFAULT_FORM, trailhead: "bear_rocks", nights: "3", daily: "6-11", total: "24-36" };
  it("per-day targets map to the request and back", () => {
    const form = { ...base, perDay: true, tripLevel: "hard" as const,
      days: [pickLevel(emptyDay(), "easy"), emptyDay(), { ...emptyDay(), miles: "8-10", maxGainFt: "2500" }, pickLevel(emptyDay(), "hard")] };
    const r = toRequest(form);
    expect("request" in r && r.request.days).toEqual([
      { day: 1, level: "easy" }, { day: 3, miles: [8, 10], max_gain_ft: 2500 }, { day: 4, level: "hard" }]);
    expect("request" in r && r.request.level).toBe("hard");
    const back = fromRun({ request: { ...("request" in r ? r.request : {}), total: [24, 36], daily: [6, 11] } } as unknown as Run);
    expect(back.perDay).toBe(true);
    expect(back.tripLevel).toBe("hard");
    expect(back.days.map((d) => d.level)).toEqual(["easy", "", "", "hard"]);
    expect(back.days[2]).toEqual({ level: "", miles: "8-10", maxGainFt: "2500", maxGradePct: "" });
  });
  it("a level fills the numbers; editing one makes it custom", () => {
    const easy = pickLevel({ ...emptyDay(), miles: "9-12" }, "easy");
    expect(easy).toEqual({ level: "easy", miles: "", maxGainFt: "", maxGradePct: "" });  // the level's own numbers, shown as hints
    expect(dayTargets(easy, "backpacking")).toEqual({ miles: [2, 5], max_gain_ft: 250, max_grade_pct: 10 });
    const edited = setDayNumber(easy, "miles", "3-6");
    expect(edited.level).toBe("easy");
    expect(dayTargets(edited, "backpacking").miles).toEqual([3, 6]);
    const r = toRequest({ ...base, perDay: true, days: [edited, emptyDay(), emptyDay(), emptyDay()] });
    expect("request" in r && r.request.days).toEqual([{ day: 1, level: "easy", miles: [3, 6] }]);
  });
  it("no day targets when the switch is off or there are no nights", () => {
    const days = [pickLevel(emptyDay(), "easy"), emptyDay(), emptyDay(), emptyDay()];
    const off = toRequest({ ...base, perDay: false, days });
    expect("request" in off && off.request.days).toEqual([]);
    const hike = toRequest({ ...base, nights: "", daily: "", perDay: true, days });
    expect("request" in hike && hike.request.days).toEqual([]);
  });
  it("only the trip's days count, and bad numbers name the days field", () => {
    const days = [emptyDay(), emptyDay(), pickLevel(emptyDay(), "easy")];
    const r = toRequest({ ...base, nights: "1", perDay: true, days });
    expect("request" in r && r.request.days).toEqual([]);
    expect(toRequest({ ...base, perDay: true, days: [{ ...emptyDay(), miles: "9-3" }] })).toEqual({
      error: "Day 1: miles must look like 8-12 (low-high); got \"9-3\"", field: "days" });
    expect(toRequest({ ...base, perDay: true, days: [{ ...emptyDay(), maxGainFt: "-5" }] })).toMatchObject({ field: "days" });
  });
});
