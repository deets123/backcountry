// Which USGS tiles a saved trip needs, and whether the map view is inside what was saved.
// USGS National Map tiles are public domain and the services allow tile export (checked 2026-10-14).
import { BASEMAPS } from "../basemaps";
import type { Basemap } from "../store";
import type { Candidate } from "../types";

export type Box = [number, number, number, number]; // west, south, east, north
export interface Tile { url: string; z: number; basemap: Basemap }

export const ZOOMS = [12, 13, 14, 15, 16];
export const SAVED_BASEMAPS: Basemap[] = ["topo", "hybrid"];
export const MARGIN_M = 805; // 0.5 mi
const M_PER_DEG = 111195.08;
// Average tile sizes measured on Dolly Sods at z12-16 (2026-10-14); only used for the "about" estimate.
const BYTES_PER_TILE: Record<Basemap, number> = { topo: 10_000, hybrid: 31_000, satellite: 31_000 };

export function tripBox(c: Candidate, marginM = MARGIN_M): Box | null {
  const co = c.geometry.coordinates;
  if (!co.length) return null;
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [lon, lat] of co) { w = Math.min(w, lon); e = Math.max(e, lon); s = Math.min(s, lat); n = Math.max(n, lat); }
  const dLat = marginM / M_PER_DEG;
  const dLon = marginM / (M_PER_DEG * Math.cos((((s + n) / 2) * Math.PI) / 180));
  return [w - dLon, s - dLat, e + dLon, n + dLat];
}

export function tileXY(lon: number, lat: number, z: number): [number, number] {
  const n = 2 ** z, r = (lat * Math.PI) / 180;
  return [Math.floor(((lon + 180) / 360) * n), Math.floor(((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * n)];
}

export function tileList(box: Box, zooms: number[] = ZOOMS, basemaps: Basemap[] = SAVED_BASEMAPS): Tile[] {
  const out: Tile[] = [];
  for (const basemap of basemaps) {
    for (const z of zooms) {
      const [x0, y1] = tileXY(box[0], box[1], z), [x1, y0] = tileXY(box[2], box[3], z);
      for (let x = x0; x <= x1; x++) {
        for (let y = y0; y <= y1; y++) {
          out.push({ url: BASEMAPS[basemap].url.replace("{z}", String(z)).replace("{y}", String(y)).replace("{x}", String(x)), z, basemap });
        }
      }
    }
  }
  return out;
}

export function estimate(tiles: Tile[]): { count: number; bytes: number } {
  return { count: tiles.length, bytes: tiles.reduce((sum, t) => sum + BYTES_PER_TILE[t.basemap], 0) };
}

export function megabytes(bytes: number): string {
  return `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

/** What a saved trip's map can't show in this view, judged from the tiles that were saved. MapLibre draws
 * 256 px raster tiles at tile zoom round(zoom + 1), and above the highest saved zoom it stretches those. */
export function coverageNote(box: Box, view: Box, zoom: number, basemap: Basemap): string | null {
  if (!SAVED_BASEMAPS.includes(basemap)) return "Satellite imagery isn't saved for offline use: switch the base map to USGS Topo or Imagery + Topo.";
  const tz = Math.round(zoom + 1);
  if (tz < ZOOMS[0]!) return "Map not saved at this zoom: zoom in to see the saved map.";
  const z = Math.min(tz, ZOOMS[ZOOMS.length - 1]!);
  const [bx0, by1] = tileXY(box[0], box[1], z), [bx1, by0] = tileXY(box[2], box[3], z);
  const [vx0, vy1] = tileXY(view[0], view[1], z), [vx1, vy0] = tileXY(view[2], view[3], z);
  const inside = vx0 >= bx0 && vx1 <= bx1 && vy0 >= by0 && vy1 <= by1;
  return inside ? null : "Part of this view is outside the saved map area; it shows blank offline.";
}
