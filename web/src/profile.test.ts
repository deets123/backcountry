import { describe, expect, it } from "vitest";
import { profilePath, profileSvg } from "./profile";

describe("profile", () => {
  it("builds a path across the width with the high point at the top", () => {
    const d = profilePath([0, 100, 200], [1000, 1100, 1000], 200, 100, 10);
    expect(d.startsWith("M10.0,")).toBe(true);
    const ys = [...d.matchAll(/[ML](\d+(?:\.\d+)?),(\d+(?:\.\d+)?)/g)].map((m) => Number(m[2]));
    expect(Math.min(...ys)).toBeCloseTo(10, 0);
    expect(Math.max(...ys)).toBeCloseTo(90, 0);
  });
  it("marks camps and labels axes in miles and feet", () => {
    const svg = profileSvg({ d_m: [0, 1609.344, 3218.688], z_m: [1000, 1100, 1000] },
      [{ index: 1, start_m: 0, end_m: 1609.344, length_m: 1609.344, gain_ft: 1, loss_ft: 0, start_label: "Start", end_label: "Camp 1" },
       { index: 2, start_m: 1609.344, end_m: 3218.688, length_m: 1609.344, gain_ft: 0, loss_ft: 1, start_label: "Camp 1", end_label: "End" }]);
    expect(svg).toContain("<svg"); expect(svg).toContain("Camp 1"); expect(svg).toContain("mi"); expect(svg).toContain("ft");
    expect(svg).not.toMatch(/\bm\b/);
  });
  it("says unknown instead of drawing when elevation is missing", () => {
    expect(profileSvg(null, null)).toContain("unknown");
    expect(profileSvg({ d_m: [0, 10], z_m: [1, null] }, null)).toContain("unknown");
  });
});
