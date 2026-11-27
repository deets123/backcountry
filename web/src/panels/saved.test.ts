import { describe, expect, it } from "vitest";
import type { SavedTrip } from "../offline/db";
import { INITIAL } from "../store";
import type { Run } from "../types";
import { renderSaved } from "./saved";

const t = (over: Partial<SavedTrip> = {}): SavedTrip => ({ id: "r1-1", savedAt: "2026-10-14T12:00:00Z", startName: "Bear Rocks Trailhead",
  run: { candidates: [{ kind: "loop", length_m: 1609.344 * 19.02 }] } as unknown as Run, index: 1, campsM: null, gpx: { whole: null, days: [] },
  trails: { type: "FeatureCollection", attribution: "", features: [] }, box: [0, 0, 1, 1], tiles: { cache: "trip-r1-1", total: 1, saved: 1, missingByZoom: {} },
  bytes: 16_000_000, persisted: true, complete: true, problems: [], ...over });

describe("renderSaved", () => {
  it("lists saved trips with size, completeness and storage status", () => {
    const html = renderSaved({ ...INITIAL, view: "saved", saved: [t(), t({ id: "r2-1", complete: false, persisted: false, campsM: [100] })] });
    expect(html).toContain("Bear Rocks Trailhead: loop, 19.02 mi"); expect(html).toContain("Saved Oct 14, 2026 · 16 MB · complete · storage kept");
    expect(html).toContain("<b>incomplete</b>"); expect(html).toContain("may be cleared if the phone runs low on storage");
    expect(html).toContain("camps moved by you"); expect(html).toContain('data-open="r2-1"'); expect(html).toContain('data-delete="r1-1"');
    expect(html).toContain("Map tiles for saved trips: 32 MB");
  });
  it("says when it's offline and when nothing is saved", () => {
    const html = renderSaved({ ...INITIAL, view: "saved", offline: true });
    expect(html).toContain("Offline: showing saved trips. Planning needs the server.");
    expect(html).toContain("No saved trips yet"); expect(html).not.toContain('id="to-plan"');
  });
});

describe("trip shapes in the list", () => {
  it("says \"out and back\", not out_and_back", () => {
    const html = renderSaved({ ...INITIAL, view: "saved", saved: [t({ run: { candidates: [{ kind: "out_and_back", length_m: 1609.344 }] } as unknown as Run })] });
    expect(html).toContain("out and back, 1.00 mi");
    expect(html).not.toContain("out_and_back");
  });
});
