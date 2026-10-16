import { describe, expect, it } from "vitest";
import { cumulativeFeet, cumulativeGainLoss, interp, smooth } from "./elevation";

describe("elevation rules (ported from backcountry/elevation.py)", () => {
  it("smooths with a centered window that shrinks at the ends", () => {
    expect(smooth([0, 3, 6, 3, 0])).toEqual([0, 3, 4, 3, 0]);
  });
  it("counts a change only past the 2 m threshold, and always counts the tail", () => {
    const { G, L } = cumulativeGainLoss([100, 101, 100.5, 104, 103, 99, 100]);
    expect(G).toEqual([0, 0, 0, 4, 4, 4, 5]);
    expect(L).toEqual([0, 0, 0, 0, 0, 5, 5]);
  });
  it("interpolates like numpy.interp, clamping outside the range", () => {
    expect(interp(5, [0, 10], [0, 100])).toBe(50);
    expect(interp(-1, [0, 10], [0, 100])).toBe(0);
    expect(interp(11, [0, 10], [0, 100])).toBe(100);
    expect(interp(15, [0, 10, 20], [0, 100, 300])).toBe(200);
  });
  it("gives cumulative feet, or null when any elevation is unknown", () => {
    const cum = cumulativeFeet({ d_m: [0, 100, 200], z_m: [100, 110, 120] })!;
    expect(cum.d).toEqual([0, 100, 200]);
    expect(cum.G[2]).toBeCloseTo(20 * 3.28084, 6);
    expect(cumulativeFeet({ d_m: [0, 100], z_m: [100, null] })).toBeNull();
    expect(cumulativeFeet(null)).toBeNull();
  });
});
