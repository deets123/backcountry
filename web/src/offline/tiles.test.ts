import { describe, expect, it } from "vitest";
import type { Candidate } from "../types";
import { coverageNote, estimate, megabytes, tileList, tileXY, tripBox, type Box } from "./tiles";

describe("tiles", () => {
  it("finds the Web Mercator tile for a point", () => {
    expect(tileXY(-79.37, 39.03, 12)).toEqual([1144, 1564]);
    expect(tileXY(-79.37, 39.03, 14)).toEqual([4579, 6259]);
    expect(tileXY(-79.37, 39.03, 16)).toEqual([18319, 25039]);
  });
  it("lists every tile in the box at zoom 12-16 for Topo and Imagery+Topo", () => {
    const box: Box = [-79.40, 38.99, -79.33, 39.07];
    expect(tileList(box, undefined, ["topo"])).toHaveLength(382);
    const all = tileList(box);
    expect(all).toHaveLength(764);
    expect(all[0]!.url).toMatch(/^https:\/\/basemap\.nationalmap\.gov\/arcgis\/rest\/services\/USGSTopo\/MapServer\/tile\/12\/\d+\/\d+$/);
    expect(all.some((t) => t.url.includes("/USGSImageryTopo/"))).toBe(true);
    expect(estimate(all)).toEqual({ count: 764, bytes: 382 * 10_000 + 382 * 31_000 });
    expect(megabytes(15_662_000)).toBe("16 MB"); expect(megabytes(20_000)).toBe("1 MB");
  });
  it("pads the route's bounding box by half a mile", () => {
    const c = { geometry: { type: "LineString", coordinates: [[-79.4, 39], [-79.4, 39.01]] } } as unknown as Candidate;
    const [w, s, e, n] = tripBox(c)!;
    expect(s).toBeCloseTo(39 - 805 / 111195.08, 6); expect(n).toBeCloseTo(39.01 + 805 / 111195.08, 6);
    expect(e - w).toBeCloseTo((2 * 805) / (111195.08 * Math.cos((39.005 * Math.PI) / 180)), 6);
    expect(tripBox({ geometry: { type: "LineString", coordinates: [] } } as unknown as Candidate)).toBeNull();
  });
  it("says when the view leaves what was saved", () => {
    const box: Box = [-79.4, 39.0, -79.3, 39.1];
    expect(coverageNote(box, [-79.38, 39.02, -79.32, 39.08], 13)).toBeNull();
    expect(coverageNote(box, [-79.38, 39.02, -79.32, 39.08], 11)).toBe("Map not saved at this zoom: zoom in to see the saved map.");
    expect(coverageNote(box, [-79.5, 39.02, -79.32, 39.08], 13)).toBe("Part of this view is outside the saved map area; it shows blank offline.");
  });
});
