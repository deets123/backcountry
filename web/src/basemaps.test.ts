import { describe, expect, it } from "vitest";
import { ATTRIBUTION, BASEMAPS, baseStyle, candidateColor } from "./basemaps";

describe("basemaps", () => {
  it("uses only USGS National Map tiles with attribution", () => {
    for (const b of Object.values(BASEMAPS)) {
      expect(b.url).toMatch(/^https:\/\/basemap\.nationalmap\.gov\//);
      expect(b.url).not.toMatch(/openstreetmap/);
    }
    expect(ATTRIBUTION).toMatch(/OpenStreetMap contributors/);
  });
  it("builds a style with one visible raster layer", () => {
    const style = baseStyle("satellite");
    const layers = style.layers as { id: string; layout?: { visibility?: string } }[];
    expect(layers.map((l) => l.id).sort()).toEqual(["base-hybrid", "base-satellite", "base-topo"]);
    expect(layers.find((l) => l.id === "base-satellite")?.layout?.visibility).toBe("visible");
    expect(layers.find((l) => l.id === "base-topo")?.layout?.visibility).toBe("none");
    const src = (style.sources as Record<string, { maxzoom?: number; attribution?: string }>)["topo"];
    expect(src?.maxzoom).toBe(16);
    expect(src?.attribution).toBe(ATTRIBUTION);
  });
  it("cycles candidate colors", () => {
    expect(candidateColor(1)).toBe("#d32f2f");
    expect(candidateColor(6)).toBe("#d32f2f");
  });
});
