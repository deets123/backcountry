import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { Candidate, Run } from "../types";
import { cardHtml, renderCompare, segmentNames } from "./compare";

const cand = (over: Partial<Candidate>): Candidate => ({
  index: 1, kind: "loop", length_m: 1609.344 * 19.02, gain_ft: 2023, loss_ft: 2023, road_m: 1609.344 * 0.81, last_resort_m: 0,
  repeated_m: 0, penalty: 0.81, segments: [{ label: "Bear Rocks Trail", highway: "path", is_road: false, last_resort: false, length_m: 1, sources: [] },
  { label: "Bear Rocks Trail", highway: "path", is_road: false, last_resort: false, length_m: 1, sources: [] },
  { label: "Raven Ridge Trail", highway: "path", is_road: false, last_resort: false, length_m: 1, sources: [] }],
  geometry: { type: "LineString", coordinates: [] }, pieces: [], waypoints: [], plan: null, features: [], profile: null,
  title: "t", description: "d", notes: [], ...over });

describe("compare", () => {
  it("merges consecutive trail names", () => {
    expect(segmentNames(cand({}))).toEqual(["Bear Rocks Trail", "Raven Ridge Trail"]);
  });
  it("renders a card in miles and feet with the day status", () => {
    const html = cardHtml(cand({ plan: { ok: false } as never }), true);
    expect(html).toContain("19.02 mi"); expect(html).toContain("+2,023 / −2,023 ft"); expect(html).toContain("0.81 mi");
    expect(html).toContain("NOT all limits met"); expect(html).toContain('class="card selected"');
    expect(cardHtml(cand({ gain_ft: null, loss_ft: null }), false)).toContain("unknown");
  });
  it("shows engine notes verbatim and the diff when present", () => {
    const run = { candidates: [cand({})], search: { notes: ["With these settings the start sits on a dead-end spur"], must_visit_notes: ["must-visit X: 3 of 9 loops"], widened: true, truncated: false, scored: 9, via_pairs_tried: 100, seconds: 1.2 },
      diff: "# What changed\n- Candidate 1: unchanged", request: { trailhead: "bear_rocks", total: [18, 26] }, known_gaps: [] } as unknown as Run;
    const html = renderCompare({ ...INITIAL, run, view: "compare" });
    expect(html).toContain("dead-end spur"); expect(html).toContain("must-visit X"); expect(html).toMatch(/wider/i);
    expect(html).toContain("What changed"); expect(html).toContain("Candidate 1: unchanged");
    expect(html).toContain('id="open-trip"');
  });
  it("explains an empty run", () => {
    const run = { candidates: [], search: { notes: ["No loop or lollipop fits"], must_visit_notes: [], widened: true, truncated: false, scored: 0, via_pairs_tried: 0, seconds: 0 }, diff: null, request: { trailhead: "x", total: [1, 2] }, known_gaps: [] } as unknown as Run;
    expect(renderCompare({ ...INITIAL, run })).toContain("No loop or lollipop fits");
  });
});

describe("compare heading", () => {
  const base = { candidates: [], search: { notes: [], must_visit_notes: [], widened: false, truncated: false, scored: 0, via_pairs_tried: 0, seconds: 0 }, diff: null, known_gaps: [] };
  const th = { id: "402290253", name: "Wilderness Way Trailhead", node: 402290253, source: "data" as const, verified: false, in_graph: true, lat: 39, lon: -79, component: "C0" };
  it("names the start instead of showing a bare node id", () => {
    const run = { ...base, request: { trailhead: "402290253", total: [18, 26] } } as unknown as Run;
    const html = renderCompare({ ...INITIAL, run, trailheads: [th] });
    expect(html).toContain("Wilderness Way Trailhead, 18–26 mi"); expect(html).not.toContain("<h1>402290253");
  });
  it("calls a lat,lon start a map point", () => {
    const run = { ...base, request: { trailhead: "39.04000,-79.38000", total: [18, 26] } } as unknown as Run;
    expect(renderCompare({ ...INITIAL, run, trailheads: [th] })).toContain("Map point 39.04000,-79.38000, 18–26 mi");
  });
});
