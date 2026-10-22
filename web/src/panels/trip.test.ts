import type { SavedTrip } from "../offline/db";
import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { Candidate, Run } from "../types";
import { patchTrip, renderTrip } from "./trip";

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
    const busy = renderTrip({ ...INITIAL, run: r, view: "trip", saving: { id: "r1-1", done: 312, total: 760, failed: 2 } });
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

describe("renderTrip, review fixes", () => {
  const geo = { geometry: { type: "LineString" as const, coordinates: [[-79.4, 39], [-79.4, 39.01]] as [number, number][] } };
  it("shows save progress and Cancel only on the trip being saved", () => {
    const r = run(geo);
    const mine = renderTrip({ ...INITIAL, run: r, view: "trip", saving: { id: "r1-1", done: 3, total: 10, failed: 0 } });
    expect(mine).toContain('id="save-cancel"');
    const other = renderTrip({ ...INITIAL, run: r, view: "trip", saving: { id: "r9-2", done: 3, total: 10, failed: 0 } });
    expect(other).not.toContain('id="save-cancel"'); expect(other).toContain("Another trip is being saved (3 / 10)");
  });
  it("offers Save again for an incomplete saved trip or one whose map was cleared", () => {
    const t = saved({ complete: false });
    expect(renderTrip({ ...INITIAL, run: t.run, view: "trip", saved: [t], savedId: t.id })).toContain('id="save-offline">Save again');
    const ok = saved();
    expect(renderTrip({ ...INITIAL, run: ok.run, view: "trip", saved: [ok], savedId: ok.id })).not.toContain('id="save-offline"');
    expect(renderTrip({ ...INITIAL, run: ok.run, view: "trip", saved: [ok], savedId: ok.id, tilesMissing: true })).toContain("Save again");
    expect(renderTrip({ ...INITIAL, run: t.run, view: "trip", saved: [t], savedId: t.id, offline: true })).not.toContain('id="save-offline"');
  });
  it("keeps the junction checkbox's state", () => {
    const r = run({ ...geo, waypoints: [{ name: "J", desc: "j", lat: 39, lon: -79.4, dist_m: 1, ele: null, kind: "junction" }] });
    expect(renderTrip({ ...INITIAL, run: r, view: "trip", junctions: true })).toContain('id="junctions" checked');
  });
  it("updates the GPS line and save progress in place, without rebuilding the panel", () => {
    const r = run(geo);
    const root = document.createElement("div");
    const where = (alongM: number) => ({ onRoute: true as const, alongM, offM: 3, day: null, next: { label: "End", distM: 100, gainFt: 1 }, ambiguous: false, accuracyM: 5 });
    const s1 = { ...INITIAL, run: r, view: "trip" as const, gps: { mode: "on" as const, fix: { lon: 0, lat: 0, accuracyM: 5 }, where: where(500) }, saving: { id: "r1-1", done: 1, total: 10, failed: 0 } };
    root.innerHTML = renderTrip(s1);
    const cancel = root.querySelector("#save-cancel");
    const s2 = { ...s1, gps: { ...s1.gps, where: where(1609.344) }, saving: { ...s1.saving, done: 2 } };
    expect(patchTrip(root, s1, s2)).toBe(true);
    expect(root.querySelector("#gps-line")!.textContent).toContain("mile 1.0 of 18.6");
    expect(root.querySelector("#save-progress")!.textContent).toContain("2 / 10");
    expect(root.querySelector("#save-cancel")).toBe(cancel);
    expect(patchTrip(root, s1, { ...s2, gps: { mode: "off" as const } })).toBe(false); // a mode change needs a full render
  });
});

describe("levels in the trip view", () => {
  const M = 1609.344;
  const rating = (over: Partial<import("../types").Rating> = {}) => ({ level: "easy" as const, reasons: [], effort: 40.2, steep: null,
    tagged: [], untagged_share: 0, ...over });
  const day = (index: number, start: number, end: number, r: unknown) => ({ index, start_m: start, end_m: end, length_m: end - start,
    gain_ft: 720, loss_ft: 300, start_label: index === 1 ? "Start" : "Camp 1", end_label: index === 1 ? "Camp 1" : "End", rating: r as never });
  const withDays = (days: unknown[], extra: Partial<Candidate> = {}) => renderTrip({ ...INITIAL, selected: 1, view: "trip", run: run({
    level: "hard", ...extra, plan: { nights: 1, daily_min_m: 1, daily_max_m: 2, max_gain_ft: null, ok: true, penalty: 0, notes: [], violations: [],
      days: days as never, camps: [] } }) });

  it("days show level, miles, gain and reasons", () => {
    const html = withDays([day(1, 0, 4.6 * M, rating()), day(2, 4.6 * M, 30000, rating({ level: "hard", reasons: ["steep", "scramble (tagged)"] }))]);
    expect(html).toContain('Day 1 · <span class="lvl lvl-easy">Easy</span> · 4.60 mi · +720 ft');
    expect(html).toContain('<span class="lvl lvl-hard">Hard</span> · 14.04 mi · +720 ft · steep, scramble (tagged)');
  });
  it("unknown level says Level unknown", () => {
    const html = withDays([day(1, 0, 15000, rating({ level: null, effort: null })), day(2, 15000, 30000, null)], { level: null });
    expect(html).toContain('Day 1 · <span class="lvl">Level unknown</span>');
    expect(html).toContain("Level unknown: no elevation data for this day.");
    expect(html).not.toContain("Day 2 · <span");  // no rating stored (a trip saved before levels): no level shown at all
  });
  it("details list the steep stretch and terrain unknown share", () => {
    const html = withDays([day(1, 0, 15000, rating({ level: "hard", reasons: ["steep"], effort: 151.6,
      steep: { grade_pct: 18.4, start_m: 14.2 * M, end_m: 14.6 * M }, untagged_share: 0.82,
      tagged: [{ start_m: 3.1 * M, end_m: 3.4 * M, reason: "scramble (tagged)", sources: ["osm:way/9"] }] })), day(2, 15000, 30000, rating())]);
    expect(html).toContain("<details");
    expect(html).toContain("Effort score 152");
    expect(html).toContain("Steepest climb: 18% for 0.4 mi at mile 14.2");
    expect(html).toContain("Tagged in OpenStreetMap: scramble from mile 3.1 to 3.4");
    expect(html).toContain("Terrain unknown for 82% of this day");
    expect(html).not.toContain("osm:way/9");
  });
  it("the suggestion shows under the days", () => {
    const html = withDays([day(1, 0, 15000, rating()), day(2, 15000, 30000, rating())],
      { suggestion: { text: "Allowing day 1 up to 6 mi would fix it.", days: [] } });
    expect(html).toContain("Allowing day 1 up to 6 mi would fix it.");
  });
  it("a day hike shows its level", () => {
    const html = renderTrip({ ...INITIAL, selected: 1, view: "trip", run: run({ level: "moderate", rating: rating({ level: "moderate", reasons: ["steep"] }) }) });
    expect(html).toContain('Level: <span class="lvl lvl-moderate">Moderate</span> · steep');
  });
});

describe("tagged stretches in a day's details", () => {
  it("are clipped to the day", () => {
    const M = 1609.344;
    const r = { level: "hard" as const, reasons: ["scramble (tagged)"], effort: 40, steep: null, untagged_share: 0,
      tagged: [{ start_m: 5 * M, end_m: 7 * M, reason: "scramble (tagged)", sources: [] }] };
    const html = renderTrip({ ...INITIAL, selected: 1, view: "trip", run: run({ level: "hard", plan: { nights: 1, daily_min_m: 1, daily_max_m: 2,
      max_gain_ft: null, ok: true, penalty: 0, notes: [], violations: [], camps: [], days: [
        { index: 1, start_m: 0, end_m: 6 * M, length_m: 6 * M, gain_ft: 1, loss_ft: 1, start_label: "Start", end_label: "Camp 1", rating: r },
        { index: 2, start_m: 6 * M, end_m: 30000, length_m: 30000 - 6 * M, gain_ft: 1, loss_ft: 1, start_label: "Camp 1", end_label: "End", rating: r }] } }) });
    expect(html).toContain("scramble from mile 5.0 to 6.0");
    expect(html).toContain("scramble from mile 6.0 to 7.0");
  });
});
