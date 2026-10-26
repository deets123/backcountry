import { describe, expect, it, vi } from "vitest";
import type { SavedTrip } from "../offline/db";
import type { Run } from "../types";
import { acceptUpdate, bindUpdate, checkForUpdate, declineUpdate, renderUpdate, reviewUpdate, type TripUpdate } from "./update";

const MI = 1609.344;
function rec(over: Partial<SavedTrip> = {}): SavedTrip {
  const run = { run_id: "old", region: "zion-r1", data_cut: "2026-10-20", candidates: [{ index: 1, length_m: 20 * MI }] } as unknown as Run;
  return { id: "old-1", savedAt: "x", startName: "Rim Trailhead", run, index: 1, campsM: [9 * MI], gpx: { whole: null, days: [] },
    trails: { type: "FeatureCollection", attribution: "", features: [] }, box: [0, 0, 1, 1], tiles: { cache: "trip-old-1", total: 10, saved: 10, missingByZoom: {} },
    bytes: 2_400_000, persisted: true, complete: true, problems: [], ...over };
}
const newRun = { run_id: "new", region: "zion-r1", data_cut: "2026-11-20", candidates: [{ index: 1, length_m: 20.4 * MI }] } as unknown as Run;

describe("checkForUpdate", () => {
  it("offers newer data with its approximate size", async () => {
    const u = await checkForUpdate(rec(), { online: () => true, updateCheck: async () => ({ newer: true, cut: "2026-10-20", latest: "2026-11-20", approx_bytes: 50_000 }) });
    expect(u).toEqual({ id: "old-1", phase: "offer", latest: "2026-11-20", mb: 3 });
  });
  it("stays quiet when there is nothing newer, the user declined this version, or the phone is offline", async () => {
    const check = vi.fn(async () => ({ newer: true, cut: "a", latest: "2026-11-20", approx_bytes: 1 }));
    expect(await checkForUpdate(rec({ declinedCut: "2026-11-20" }), { online: () => true, updateCheck: check })).toBeNull();
    expect(await checkForUpdate(rec(), { online: () => false, updateCheck: check })).toBeNull();
    expect(check).toHaveBeenCalledTimes(1);  // offline: not even asked
    expect(await checkForUpdate(rec(), { online: () => true, updateCheck: async () => ({ newer: false, cut: "a", latest: "a", approx_bytes: 1 }) })).toBeNull();
    expect(await checkForUpdate(rec(), { online: () => true, updateCheck: async () => { throw new Error("gone"); } })).toBeNull();
  });
  it("a declined version is offered again when an even newer one appears", async () => {
    const u = await checkForUpdate(rec({ declinedCut: "2026-11-20" }), { online: () => true,
      updateCheck: async () => ({ newer: true, cut: "2026-10-20", latest: "2026-12-20", approx_bytes: 1 }) });
    expect(u?.phase).toBe("offer");
  });
});

describe("renderUpdate", () => {
  it("the offer names the data use and asks first", () => {
    const html = renderUpdate({ id: "old-1", phase: "offer", latest: "2026-11-20", mb: 3 });
    expect(html).toContain("Newer trail data for this area. Updating uses about 3 MB.");
    expect(html).toContain('id="update-review"');
    expect(html).toContain('id="update-later"');
  });
  it("the review lists every change, escaped, with Accept and Keep my trip", () => {
    const html = renderUpdate({ id: "old-1", phase: "review", latest: "2026-11-20", run: newRun, changes: ["Total: 20.0 mi → 20.4 mi", "<b>x</b>"] });
    expect(html).toContain("Total: 20.0 mi → 20.4 mi");
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
    expect(html).toContain('id="update-accept"');
    expect(html).toContain("Keep my trip");
  });
  it("a failure says the trip is unchanged", () => {
    expect(renderUpdate({ id: "old-1", phase: "failed", message: "The route no longer follows the same trails. Your trip is unchanged." }))
      .toContain("Your trip is unchanged.");
  });
});

describe("the update steps", () => {
  it("nothing is sent until the user taps", () => {
    const root = document.createElement("div");
    root.innerHTML = renderUpdate({ id: "old-1", phase: "offer", latest: "2026-11-20", mb: 3 });
    const actions = { reviewUpdate: vi.fn(), declineUpdate: vi.fn(), acceptUpdate: vi.fn() };
    bindUpdate(root, actions);
    expect(actions.reviewUpdate).not.toHaveBeenCalled();
    root.querySelector<HTMLButtonElement>("#update-review")!.click();
    expect(actions.reviewUpdate).toHaveBeenCalledTimes(1);
    root.querySelector<HTMLButtonElement>("#update-later")!.click();
    expect(actions.declineUpdate).toHaveBeenCalledTimes(1);
  });
  it("review fetches the re-planned trip and its changes", async () => {
    const u = await reviewUpdate(rec(), "2026-11-20", { updateTrip: async () => ({ ok: true, run: newRun, changes: ["No changes to this trip."] }) });
    expect(u).toEqual({ id: "old-1", phase: "review", latest: "2026-11-20", run: newRun, changes: ["No changes to this trip."] });
  });
  it("a route that can't be followed keeps the trip and says why", async () => {
    const u = await reviewUpdate(rec(), "2026-11-20", { updateTrip: async () => ({ ok: false, message: "missing: Canyon Trail. Your trip is unchanged." }) });
    expect(u).toEqual({ id: "old-1", phase: "failed", message: "missing: Canyon Trail. Your trip is unchanged." });
  });
  it("Keep my trip remembers the declined version on the phone", async () => {
    const put = vi.fn(async () => {});
    await declineUpdate(rec(), "2026-11-20", { put });
    expect(put).toHaveBeenCalledWith(expect.objectContaining({ id: "old-1", declinedCut: "2026-11-20", run: rec().run }));
  });
  it("Accept saves the new trip for offline, keeps moved camps that still fit, then removes the old one", async () => {
    const calls: string[] = [];
    const review: Extract<TripUpdate, { phase: "review" }> = { id: "old-1", phase: "review", latest: "2026-11-20", run: newRun, changes: [] };
    const saved = await acceptUpdate(rec(), review, {
      save: async (run, n, name, campsM) => { calls.push(`save ${run.run_id} ${n} ${name} ${JSON.stringify(campsM)}`); return { ...rec(), id: "new-1", run }; },
      remove: async (id) => { calls.push(`remove ${id}`); },
    });
    expect(calls).toEqual([`save new 1 Rim Trailhead ${JSON.stringify([9 * MI])}`, "remove old-1"]);
    expect(saved.id).toBe("new-1");
  });
  it("moved camps that no longer fit the new route are dropped (the engine's camps are used)", async () => {
    const calls: unknown[] = [];
    const review = { id: "old-1", phase: "review" as const, latest: "x", run: newRun, changes: [] };
    await acceptUpdate(rec({ campsM: [25 * MI] }), review, { save: async (run, n, name, campsM) => { calls.push(campsM); return rec(); }, remove: async () => {} });
    expect(calls).toEqual([null]);
  });
  it("a failed save keeps the old trip", async () => {
    const remove = vi.fn(async () => {});
    const review = { id: "old-1", phase: "review" as const, latest: "x", run: newRun, changes: [] };
    await expect(acceptUpdate(rec(), review, { save: async () => { throw new Error("quota"); }, remove })).rejects.toThrow("quota");
    expect(remove).not.toHaveBeenCalled();
  });
});
