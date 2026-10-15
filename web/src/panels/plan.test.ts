import { describe, expect, it } from "vitest";
import type { FormState } from "../request";
import { createStore, INITIAL } from "../store";
import type { Trailhead } from "../types";
import { bindPlan, renderPlan, tapMessage } from "./plan";

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

describe("bindPlan", () => {
  it("hands field edits to editForm instead of writing the store itself", () => {
    const root = document.createElement("div");
    root.innerHTML = renderPlan({ ...INITIAL, region: "dolly_sods" });
    const edits: FormState[] = [];
    const store = createStore(INITIAL);
    bindPlan(root, store, { generate() {}, pickRegion() {}, backToCompare() {}, showSaved() {}, editForm: (f) => edits.push(f) });
    const total = root.querySelector<HTMLInputElement>('input[name="total"]')!;
    total.value = "26-18";
    total.dispatchEvent(new Event("change"));
    expect(edits.map((f) => f.total)).toEqual(["26-18"]);
    expect(store.get().form.total).toBe(INITIAL.form.total);
  });
});

describe("tapMessage", () => {
  it("tells the user which trail the tapped start is on so they can type its name", () => {
    expect(tapMessage("Bear Rocks Trail")).toContain("Bear Rocks Trail");
    expect(tapMessage(null)).toContain("unnamed");
  });
});
