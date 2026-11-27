import { describe, expect, it } from "vitest";
import type { Notice, NoticeKind, ParkNotices } from "../types";
import { chipsHtml, noticeKey, noticesHtml, parkLineHtml, refreshNotices } from "./notices";

const n = (kind: NoticeKind, text: string, matched = false, links: string[] = []): Notice => ({
  kind, text, applies_to: matched ? "trail: X" : "park", matched,
  source: { name: "National Park Service", ref: null, as_of: "2026-09-16", checked_at: "2026-11-26T10:04:00Z" }, links,
});

describe("notices", () => {
  it("escapes text and keeps only http links", () => {
    const html = noticesHtml([n("info", "<img src=x onerror=alert(1)> & more", false, ["javascript:x", "https://www.nps.gov/a"])]);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).toContain('href="https://www.nps.gov/a"');
    expect(html).not.toContain("javascript:");
  });
  it("marks new notices and shows attachment and dates", () => {
    const one = n("closure", "Watchman Trail closed", true);
    const html = noticesHtml([one], new Set([noticeKey(one)]));
    expect(html).toContain("new since you saved");
    expect(html).toContain("Attached to: trail: X");
    expect(html).toContain("2026-09-16");
    expect(html).toContain("National Park Service");
  });
  it("renders the unknown line plainly", () => {
    const html = noticesHtml([n("unknown", "Everything else: unknown")]);
    expect(html).toContain("Everything else: unknown");
    expect(html).not.toContain("Attached to");
  });
  it("chips only for matched permit, ticket and closure", () => {
    expect(chipsHtml([n("permit", "p", true), n("closure", "c", false), n("fee", "f", true)])).toBe('<span class="chip chip-permit">permit</span>');
    expect(chipsHtml([n("ticket", "t", true), n("closure", "c", true)])).toBe('<span class="chip chip-ticket">ticket</span><span class="chip chip-closure">closure</span>');
    expect(chipsHtml(undefined)).toBe("");
  });
  it("park line counts closures and says when it is checking", () => {
    const checking: ParkNotices = { configured: true, park: { code: "zion", name: "Zion National Park" }, pending: true, stale: false, error: null, checked_at: null, notices: [] };
    expect(parkLineHtml(checking, false)).toContain("Checking park notices…");
    const p: ParkNotices = { configured: true, park: { code: "zion", name: "Zion National Park" }, pending: false, stale: false, error: null,
      checked_at: "2026-11-26T10:04:00Z", notices: [n("closure", "a"), n("closure", "b"), n("fee", "c")] };
    const html = parkLineHtml(p, false);
    expect(html).toContain("2 closures, 1 more");
    expect(html).toContain("National Park Service");
    expect(html).toContain("<details");
    expect(parkLineHtml(null, false)).toBe("");
    expect(parkLineHtml({ ...p, park: null, pending: false, notices: [] }, false)).toBe("");
    expect(parkLineHtml({ ...p, stale: true, error: "HTTP 429" }, false)).toContain("could not refresh");
  });
});

describe("refreshNotices", () => {
  const unknown: Notice = { kind: "unknown", text: "Everything else: unknown", applies_to: null, matched: false, source: null, links: [] };
  const base = (notices: Notice[] | undefined) => ({
    run_id: "r1", region: "zion-x", request: { trailhead: "a", total: [5, 8] as [number, number], daily: null }, generated_at: "", start: { node: 1, how: "x", snap_m: 0 },
    search: { loops_found: 0, lollipops_found: 0, stems_tried: 0, via_pairs_tried: 0, scored: 0, seconds: 0, widened: false, truncated: false, avoided_edges: 0, notes: [], must_visit_notes: [], notices_pending: true },
    candidates: [{ index: 1, kind: "loop" as const, length_m: 1, gain_ft: null, loss_ft: null, road_m: 0, last_resort_m: 0, repeated_m: 0, penalty: 0, segments: [],
      geometry: { type: "LineString" as const, coordinates: [] }, pieces: [], waypoints: [], plan: null, features: [], profile: null, title: "", description: "", notes: [],
      ...(notices ? { notices } : {}) }],
    data_sources: [], known_gaps: [], diff: null,
  });
  it("replaces the candidate's notices, clears pending and marks what the saved copy lacked", async () => {
    const fresh = [n("closure", "Watchman Trail closed", true), n("fee", "Entrance fee"), unknown];
    const puts: unknown[] = [];
    const rec = { id: "r1-1", run: base([n("fee", "Entrance fee"), unknown]), index: 1 } as unknown as import("../offline/db").SavedTrip;
    const out = await refreshNotices(base([n("fee", "Entrance fee"), unknown]), 1, rec, {
      getRunNotices: async () => ({ notices: fresh, pending: false, checked_at: "2026-11-26T10:04:00Z" }),
      put: async (r) => { puts.push(r); },
    });
    expect(out?.run.candidates[0]?.notices).toEqual(fresh);
    expect(out?.run.search.notices_pending).toBe(false);
    expect(out?.noticesNew).toEqual([noticeKey(fresh[0]!)]);
    expect(puts).toHaveLength(1);
  });
  it("does nothing for a snapshot run and stores nothing for an unsaved trip", async () => {
    expect(await refreshNotices({ ...base(undefined), run_id: "partial" }, 1, null, { getRunNotices: async () => { throw new Error("not called"); } })).toBeNull();
    const puts: unknown[] = [];
    const out = await refreshNotices(base(undefined), 1, null, { getRunNotices: async () => ({ notices: [unknown], pending: false, checked_at: null }), put: async (r) => { puts.push(r); } });
    expect(out?.noticesNew).toEqual([]);
    expect(puts).toHaveLength(0);
  });
});
