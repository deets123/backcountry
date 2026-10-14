import type { StyleSpecification } from "maplibre-gl";
import type { Basemap } from "./store";

const USGS = "https://basemap.nationalmap.gov/arcgis/rest/services";
export const BASEMAPS: Record<Basemap, { name: string; url: string }> = {
  topo: { name: "USGS Topo", url: `${USGS}/USGSTopo/MapServer/tile/{z}/{y}/{x}` },
  hybrid: { name: "USGS Imagery + Topo", url: `${USGS}/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}` },
  satellite: { name: "USGS Imagery (satellite)", url: `${USGS}/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}` },
};
export const ATTRIBUTION = "Base map: USGS The National Map | Trail data © OpenStreetMap contributors, ODbL";
export const CANDIDATE_COLORS = ["#d32f2f", "#1976d2", "#388e3c", "#f57c00", "#7b1fa2"];

export function candidateColor(i: number): string {
  return CANDIDATE_COLORS[(i - 1) % CANDIDATE_COLORS.length]!;
}
export const dayColor = candidateColor;

export function baseStyle(active: Basemap): StyleSpecification {
  const sources: StyleSpecification["sources"] = {};
  const layers: StyleSpecification["layers"] = [];
  for (const [key, b] of Object.entries(BASEMAPS) as [Basemap, { url: string }][]) {
    sources[key] = { type: "raster", tiles: [b.url], tileSize: 256, maxzoom: 16, attribution: ATTRIBUTION };
    layers.push({ id: `base-${key}`, type: "raster", source: key, layout: { visibility: key === active ? "visible" : "none" } });
  }
  return { version: 8, sources, layers };
}
