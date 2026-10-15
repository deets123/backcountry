import type { SavedTrip } from "../offline/db";
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

function saved(over: Partial<SavedTrip> = {}): SavedTrip {
  const r = run({ geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39.01]] } });
  return { id: "r1-1", savedAt: "2026-10-14T12:00:00Z", startName: "Bear Rocks Trailhead", run: r, index: 1, campsM: null,
    gpx: { whole: "<gpx/>", days: [] }, trails: { type: "FeatureCollection", attribution: "", features: [] }, box: [-79.41, 38.99, -79.39, 39.02],
    tiles: { cache: "trip-r1-1", total: 760, saved: 760, missingByZoom: {} }, bytes: 16_000_000, persisted: true, complete: true, problems: [], ...over };
}

describe("renderTrip, offline parts", () => {
  it("offers to save with an estimate, and shows progress while saving", () => {
    const r = run({ geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39.01]] } });
    const html = renderTrip({ ...INITIAL, run: r, view: "trip" });
    expect(html).toMatch(/id="save-offline">Save for offline \(about \d+ map tiles, \d+ MB\)/);
    const busy = renderTrip({ ...INITIAL, run: r, view: "trip", saving: { done: 312, total: 760, failed: 2 } });
    expect(busy).toContain("Saving map tiles: 312 / 760 · 2 failed"); expect(busy).toContain('id="save-cancel"');
  });
  it("shows a saved trip's dates, saved GPX links and no save button", () => {
    const t = saved();
    const html = renderTrip({ ...INITIAL, run: t.run, view: "trip", saved: [t], savedId: t.id });
    expect(html).toContain("Saved for offline Oct 14, 2026"); expect(html).toContain("trail data retrieved Oct 13, 2026");
    expect(html).toContain('data-gpx="whole"'); expect(html).not.toContain('id="save-offline"');
    expect(html).not.toContain("/api/runs/r1/candidates/1.gpx"); expect(html).toContain('id="known-gaps"');
  });
  it("labels an incomplete save and one the browser may clear", () => {
    const t = saved({ complete: false, persisted: false, problems: ["Map tiles not saved: z16: 38 missing."] });
    const html = renderTrip({ ...INITIAL, run: t.run, view: "trip", saved: [t], savedId: t.id });
    expect(html).toContain("Incomplete:"); expect(html).toContain("z16: 38 missing");
    expect(html).toContain("the browser may clear it if the phone runs low on storage");
  });
  it("shows the GPS line, its errors, and the profile marker", () => {
    const t = saved();
    const where = { onRoute: true as const, alongM: 500, offM: 10, day: null, next: { label: "End", distM: 612, gainFt: 50 }, ambiguous: false, accuracyM: 5 };
    const on = renderTrip({ ...INITIAL, run: t.run, view: "trip", gps: { mode: "on", fix: { lon: -79.4, lat: 39.0045, accuracyM: 5 }, where } });
    expect(on).toContain("mile 0.3 of 18.6"); expect(on).toContain("Stop showing my location"); expect(on).toContain('class="you"');
    const err = renderTrip({ ...INITIAL, run: t.run, view: "trip", gps: { mode: "error", message: "User denied Geolocation" } });
    expect(err).toContain("Location unavailable: User denied Geolocation");
  });
});
