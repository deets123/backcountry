import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { Candidate, Run } from "../types";
import { renderTrip } from "./trip";

function run(c: Partial<Candidate>): Run {
  const cand: Candidate = { index: 1, kind: "loop", length_m: 30000, gain_ft: 2023, loss_ft: 2023, road_m: 0, last_resort_m: 0, repeated_m: 0,
    penalty: 0, segments: [{ label: "Bear Rocks Trail", highway: "path", is_road: false, last_resort: false, length_m: 30000, sources: ["osm:way/1"] }],
    geometry: { type: "LineString", coordinates: [] }, pieces: [{ name: "Route", points: [], start_m: 0, end_m: 30000, length_m: 30000, gain_ft: 2023, loss_ft: 2023, day: null }],
    waypoints: [], plan: null, features: [], profile: { d_m: [0, 30000], z_m: [1000, 1100] }, title: "t", description: "d", notes: [], ...c };
  return { run_id: "r1", region: "dolly_sods", request: { trailhead: "bear_rocks", total: [18, 26], daily: null }, generated_at: "", start: { node: 1, how: "trailhead bear_rocks", snap_m: 0 },
    search: { loops_found: 1, lollipops_found: 0, stems_tried: 0, via_pairs_tried: 1, scored: 1, seconds: 1, widened: false, truncated: false, avoided_edges: 0, notes: [], must_visit_notes: [] },
    candidates: [cand], data_sources: [{ name: "OpenStreetMap via Overpass API", retrieved_at: "2026-10-13", attribution: "© OSM" }],
    known_gaps: ["water: map features only; whether water is present is unknown", "permits: not checked"], diff: null };
}

describe("renderTrip", () => {
  it("works without a day plan and always shows known gaps and downloads", () => {
    const html = renderTrip({ ...INITIAL, run: run({}), selected: 1, view: "trip" });
    expect(html).toContain("Days weren't planned"); expect(html).toContain('id="known-gaps"'); expect(html).toContain("permits: not checked");
    expect(html).toContain('href="/api/runs/r1/candidates/1.gpx"'); expect(html).toContain("18.64 mi"); expect(html).not.toMatch(/\bsafe\b/i);
  });
  it("renders days, camps and violations verbatim", () => {
    const html = renderTrip({ ...INITIAL, selected: 1, view: "trip", run: run({ plan: {
      nights: 1, daily_min_m: 1, daily_max_m: 2, max_gain_ft: 2500, ok: false, penalty: 1, notes: ["note A"],
      violations: ["Day 2: 12.4 mi is over the 11.00 mi daily max"],
      days: [{ index: 1, start_m: 0, end_m: 15000, length_m: 15000, gain_ft: 1000, loss_ft: 100, start_label: "Start", end_label: "Camp 1" },
             { index: 2, start_m: 15000, end_m: 30000, length_m: 15000, gain_ft: null, loss_ft: null, start_label: "Camp 1", end_label: "End" }],
      camps: [{ index: 1, dist_m: 15000, lat: 39, lon: -79, on_source: "osm:way/1", on_name: "Red Creek Trail", nearby: [], note: "camp_location: unverified",
        pinned: true, site: null, nearest_water: { source: "osm:way/2", kind: "stream crossing", name: "Red Creek", distance_m: 61 } }] } }) });
    expect(html).toContain("Day 1"); expect(html).toContain("9.32 mi"); expect(html).toContain("1,000 ft"); expect(html).toContain("unknown");
    expect(html).toContain("Constraint not met: Day 2: 12.4 mi is over the 11.00 mi daily max"); expect(html).toContain("note A");
    expect(html).toContain("Camp 1"); expect(html).toContain("pinned by you"); expect(html).toContain("Red Creek Trail"); expect(html).toContain("Location unverified");
    expect(html).toContain("200 ft"); expect(html).toContain("presence unknown"); expect(html).toContain('href="/api/runs/r1/candidates/1.gpx?day=2"');
    expect(html).not.toContain("osm:way/");
  });
});
