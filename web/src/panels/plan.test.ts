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

describe("the day strip", () => {
  const form = (over: Partial<FormState> = {}): FormState => ({ ...INITIAL.form, nights: "3", daily: "6-11", perDay: true,
    days: [{ level: "easy", miles: "", maxGainFt: "", maxGradePct: "" }], ...over });
  const mount = (f: FormState) => {
    const root = document.createElement("div");
    root.innerHTML = renderPlan({ ...INITIAL, form: f });
    const edits: FormState[] = [];
    bindPlan(root, createStore({ ...INITIAL, form: f }), { generate() {}, pickRegion() {}, backToCompare() {}, showSaved() {}, editForm: (x) => edits.push(x) });
    return { root, edits };
  };

  it("the day strip has one block per day, labeled with its level", () => {
    const { root } = mount(form());
    const blocks = [...root.querySelectorAll<HTMLElement>(".day-block")];
    expect(blocks.map((b) => b.textContent!.trim())).toEqual(["D1 Easy", "D2 Any", "D3 Any", "D4 Any"]);
    expect(blocks[0]!.className).toContain("lvl-easy");
    expect(Number(blocks[0]!.style.flexGrow)).toBeLessThan(Number(blocks[1]!.style.flexGrow));  // 2-5 mi vs the trip's 6-11
  });
  it("tapping a block opens its editor", () => {
    const { root, edits } = mount(form());
    expect(root.querySelector(".day-editor")).toBeNull();
    root.querySelectorAll<HTMLElement>(".day-block")[1]!.click();
    const editor = root.querySelector(".day-editor")!;
    expect([...editor.querySelectorAll("[data-level]")].map((b) => b.textContent)).toEqual(["Easy", "Moderate", "Hard", "Strenuous"]);
    expect(editor.querySelectorAll("input[data-day-field]").length).toBe(3);
    editor.querySelector<HTMLElement>('[data-level="hard"]')!.click();
    expect(edits.at(-1)!.days[1]!.level).toBe("hard");
    expect(root.querySelectorAll(".day-block")[1]!.textContent!.trim()).toBe("D2 Hard");
    const miles = root.querySelector<HTMLInputElement>('.day-editor input[data-day-field="miles"]')!;
    expect(miles.placeholder).toBe("7-12");  // the hard row's numbers, as hints
    miles.value = "8-10";
    miles.dispatchEvent(new Event("change"));
    expect(edits.at(-1)!.days[1]).toEqual({ level: "hard", miles: "8-10", maxGainFt: "", maxGradePct: "" });
    expect(root.querySelectorAll(".day-block")[1]!.textContent!.trim()).toBe("D2 Hard, custom");
  });
  it("the switch shows the strip, and the strip is hidden without nights", () => {
    const off = mount(form({ perDay: false }));
    expect(off.root.querySelector(".day-strip")).toBeNull();
    const sw = off.root.querySelector<HTMLInputElement>('input[name="perDay"]')!;
    sw.checked = true;
    sw.dispatchEvent(new Event("change"));
    expect(off.edits.at(-1)!.perDay).toBe(true);
    expect(off.root.querySelectorAll(".day-block").length).toBe(4);
    const hike = mount(form({ nights: "" }));
    expect(hike.root.querySelector('input[name="perDay"]')).toBeNull();
    expect(hike.root.querySelector(".day-strip")).toBeNull();
  });
  it("has a trip level choice", () => {
    const { root, edits } = mount(form({ tripLevel: "moderate" }));
    const sel = root.querySelector<HTMLSelectElement>('select[name="tripLevel"]')!;
    expect(sel.value).toBe("moderate");
    expect([...sel.options].map((o) => o.text)).toEqual(["Any", "Easy", "Moderate", "Hard", "Strenuous"]);
    sel.value = "hard";
    sel.dispatchEvent(new Event("change"));
    expect(edits.at(-1)!.tripLevel).toBe("hard");
  });
});

describe("bindPlan with outside changes", () => {
  it("an edit after a map tap keeps the tapped trailhead", () => {
    const root = document.createElement("div");
    const store = createStore({ ...INITIAL, form: { ...INITIAL.form, trailhead: "" } });
    root.innerHTML = renderPlan(store.get());
    const edits: FormState[] = [];
    bindPlan(root, store, { generate() {}, pickRegion() {}, backToCompare() {}, showSaved() {}, editForm: (f) => { edits.push(f); store.set({ form: f }); } });
    store.set({ form: { ...store.get().form, trailhead: "39.04,-79.38" } });  // the map tap, no re-render
    const total = root.querySelector<HTMLInputElement>('input[name="total"]')!;
    total.value = "20-30";
    total.dispatchEvent(new Event("change"));
    expect(edits.at(-1)).toMatchObject({ trailhead: "39.04,-79.38", total: "20-30" });
  });
});
