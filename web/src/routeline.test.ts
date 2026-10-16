import { describe, expect, it } from "vitest";
import { metersBetween, RouteLine } from "./routeline";

const M_PER_DEG = 111195.08;
const LON_M = M_PER_DEG * Math.cos((39 * Math.PI) / 180); // meters per degree of longitude at 39°N

describe("RouteLine", () => {
  const north: [number, number][] = [[-79.4, 39.0], [-79.4, 39.01]];
  const len = 0.01 * M_PER_DEG;

  it("snaps a point to the nearest place on the line, in meters along and off it", () => {
    const line = new RouteLine(north, len);
    const { snap, ambiguous } = line.nearest(-79.4 + 50 / LON_M, 39.005, null);
    expect(snap.alongM).toBeCloseTo(len / 2, 0);
    expect(snap.offM).toBeCloseTo(50, 0);
    expect(ambiguous).toBe(false);
  });

  it("scales distances so the line's length equals the engine's length", () => {
    const line = new RouteLine(north, 2 * len);
    expect(line.length).toBeCloseTo(2 * len, 6);
    expect(line.nearest(-79.4, 39.01, null).snap.alongM).toBeCloseTo(2 * len, 3);
    const [lon, lat] = line.pointAt(len);
    expect(lon).toBeCloseTo(-79.4, 9); expect(lat).toBeCloseTo(39.005, 9);
  });

  it("slices between two distances with interpolated ends and the vertices in between", () => {
    const line = new RouteLine([[-79.4, 39.0], [-79.4, 39.005], [-79.4, 39.01]], len);
    const pts = line.slice(len * 0.25, len * 0.75);
    expect(pts).toHaveLength(3);
    expect(pts[0]![1]).toBeCloseTo(39.0025, 9); expect(pts[1]![1]).toBeCloseTo(39.005, 9); expect(pts[2]![1]).toBeCloseTo(39.0075, 9);
    expect(pts[1]![2]).toBeCloseTo(len / 2, 3);
  });

  it("flags a loop's start/end as ambiguous and lets the last position choose", () => {
    const dLat = 0.009, dLon = 1000 / LON_M;
    const sq: [number, number][] = [[-79.4, 39], [-79.4, 39 + dLat], [-79.4 + dLon, 39 + dLat], [-79.4 + dLon, 39], [-79.4, 39]];
    const line = new RouteLine(sq, 4001.6);
    const p = [-79.4 + 3 / LON_M, 39 + 3 / M_PER_DEG] as const;
    expect(line.nearest(p[0], p[1], null).ambiguous).toBe(true);
    expect(line.nearest(p[0], p[1], 3900).snap.alongM).toBeGreaterThan(3900);
    expect(line.nearest(p[0], p[1], 50).snap.alongM).toBeLessThan(100);
  });

  it("tells the two passes of a stem apart by the last position", () => {
    // stem S->P, loop P->Q->R->P, back P->S
    const S: [number, number] = [-79.4, 39], P: [number, number] = [-79.4, 39.005];
    const Q: [number, number] = [-79.39, 39.01], R: [number, number] = [-79.41, 39.01];
    const line = new RouteLine([S, P, Q, R, P, S], 0); // 0: no scaling
    const mid: [number, number] = [-79.4, 39.0025];
    const out = line.nearest(mid[0], mid[1], 100);
    const back = line.nearest(mid[0], mid[1], line.length - 100);
    expect(out.ambiguous).toBe(true);
    expect(out.snap.alongM).toBeLessThan(line.length / 2);
    expect(back.snap.alongM).toBeGreaterThan(line.length / 2);
  });

  it("measures short distances in meters", () => {
    expect(metersBetween(-79.4, 39, -79.4, 39.001)).toBeCloseTo(111.2, 1);
  });
});
