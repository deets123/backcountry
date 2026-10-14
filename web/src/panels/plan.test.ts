import { describe, expect, it } from "vitest";
import { INITIAL } from "../store";
import type { Trailhead } from "../types";
import { renderPlan } from "./plan";

const th = (over: Partial<Trailhead>): Trailhead => ({ id: "x", name: "X", node: 1, source: "yaml", verified: false, in_graph: true,
  lat: 39, lon: -79, component: "C0", ...over });

describe("renderPlan", () => {
  it("lists trailheads and disables ones not on the network", () => {
    const html = renderPlan({ ...INITIAL, region: "dolly_sods", trailheads: [
      th({ id: "bear_rocks", name: "Bear Rocks Trailhead" }),
      th({ id: "gone", name: "Gone", in_graph: false, lat: null, lon: null }),
      th({ id: "155984740", name: "19 View Trail @ Dolly Sods Road", source: "data" }),
    ] });
    expect(html).toContain('<option value="bear_rocks"');
    expect(html).toMatch(/<option value="gone"[^>]*disabled[^>]*>Gone \(not on the trail network\)/);
    expect(html).toContain("(from the data, unverified)");
    expect(html).toContain('id="generate"');
    expect(html).not.toMatch(/\bsafe\b/i);
  });
  it("shows the error and status lines verbatim", () => {
    const html = renderPlan({ ...INITIAL, status: "Searching…", error: "total must look like 18-26" });
    expect(html).toContain("Searching…");
    expect(html).toContain("total must look like 18-26");
  });
  it("escapes HTML in names", () => {
    const html = renderPlan({ ...INITIAL, trailheads: [th({ id: "a", name: "<b>bold</b>" })] });
    expect(html).toContain("&lt;b&gt;bold&lt;/b&gt;");
  });
});
