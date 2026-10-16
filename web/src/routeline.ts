// The saved route as a polyline with distances along it: snap a GPS fix or a dragged camp to it, find
// the point at a given mile, cut it into days. Never creates geometry: every point is on the saved line.
// Distances use a local equirectangular projection around the route (well under a foot of error over a
// trip box), then are scaled so the line's total equals the engine's length_m, which is measured in UTM.
import type { Candidate } from "./types";

const R = 6371008.8;
const RAD = Math.PI / 180;
const TIE_M = 15;     // fixes within ~50 ft of two passes of the route are ambiguous
const SAME_PASS_M = 100;

export interface Snap { alongM: number; offM: number; lon: number; lat: number }

export function metersBetween(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const k = Math.cos(((lat1 + lat2) / 2) * RAD);
  return Math.hypot((lon2 - lon1) * RAD * R * k, (lat2 - lat1) * RAD * R);
}

export class RouteLine {
  private readonly xs: number[] = [];
  private readonly ys: number[] = [];
  private readonly cum: number[] = [0];
  private readonly lon0: number;
  private readonly lat0: number;
  private readonly kx: number;
  private readonly scale: number;

  /** `lengthM` is the engine's length for this line; 0 keeps the projected length unscaled. */
  constructor(coords: [number, number][], lengthM: number) {
    this.lon0 = coords[0]?.[0] ?? 0;
    this.lat0 = coords[0]?.[1] ?? 0;
    this.kx = Math.cos(this.lat0 * RAD) * RAD * R;
    for (const [lon, lat] of coords) { this.xs.push((lon - this.lon0) * this.kx); this.ys.push((lat - this.lat0) * RAD * R); }
    for (let i = 1; i < this.xs.length; i++) {
      this.cum.push(this.cum[i - 1]! + Math.hypot(this.xs[i]! - this.xs[i - 1]!, this.ys[i]! - this.ys[i - 1]!));
    }
    const raw = this.cum[this.cum.length - 1]!;
    this.scale = lengthM > 0 && raw > 0 ? lengthM / raw : 1;
  }

  static of(c: Candidate): RouteLine { return new RouteLine(c.geometry.coordinates, c.length_m); }

  get length(): number { return this.cum[this.cum.length - 1]! * this.scale; }

  private toLonLat(x: number, y: number): [number, number] { return [this.lon0 + x / this.kx, this.lat0 + y / (RAD * R)]; }

  /** The nearest point on the line. Where the line passes the same place twice, `hintM` (the last known
   * distance along) picks the pass, and `ambiguous` says there was a choice. */
  nearest(lon: number, lat: number, hintM: number | null): { snap: Snap; ambiguous: boolean } {
    const px = (lon - this.lon0) * this.kx, py = (lat - this.lat0) * RAD * R;
    const cands: Snap[] = [];
    for (let i = 1; i < this.xs.length; i++) {
      const ax = this.xs[i - 1]!, ay = this.ys[i - 1]!, dx = this.xs[i]! - ax, dy = this.ys[i]! - ay;
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
      const x = ax + t * dx, y = ay + t * dy;
      const [slon, slat] = this.toLonLat(x, y);
      cands.push({ alongM: (this.cum[i - 1]! + t * Math.sqrt(len2)) * this.scale, offM: Math.hypot(px - x, py - y), lon: slon, lat: slat });
    }
    if (!cands.length) return { snap: { alongM: 0, offM: metersBetween(lon, lat, this.lon0, this.lat0), lon: this.lon0, lat: this.lat0 }, ambiguous: false };
    const best = Math.min(...cands.map((c) => c.offM));
    // One representative (the closest) per pass: candidates close in distance-along are the same pass.
    const close = cands.filter((c) => c.offM <= best + TIE_M).sort((a, b) => a.alongM - b.alongM);
    const passes: Snap[] = [];
    for (const c of close) {
      const last = passes[passes.length - 1];
      if (last && c.alongM - last.alongM < SAME_PASS_M) { if (c.offM < last.offM) passes[passes.length - 1] = c; }
      else passes.push(c);
    }
    const pick = hintM === null
      ? passes.reduce((a, b) => (b.offM < a.offM ? b : a))
      : passes.reduce((a, b) => (Math.abs(b.alongM - hintM) < Math.abs(a.alongM - hintM) ? b : a));
    return { snap: pick, ambiguous: passes.length > 1 };
  }

  pointAt(alongM: number): [number, number] {
    const raw = Math.max(0, Math.min(this.cum[this.cum.length - 1]!, alongM / this.scale));
    let i = 1;
    while (i < this.cum.length - 1 && this.cum[i]! < raw) i++;
    const seg = this.cum[i]! - this.cum[i - 1]!;
    const t = seg ? (raw - this.cum[i - 1]!) / seg : 0;
    return this.toLonLat(this.xs[i - 1]! + t * (this.xs[i]! - this.xs[i - 1]!), this.ys[i - 1]! + t * (this.ys[i]! - this.ys[i - 1]!));
  }

  /** [lon, lat, alongM] from a to b: the two ends, plus every vertex strictly between them. */
  slice(a: number, b: number): [number, number, number][] {
    const out: [number, number, number][] = [[...this.pointAt(a), a]];
    for (let i = 0; i < this.cum.length; i++) {
      const m = this.cum[i]! * this.scale;
      if (m > a + 1e-6 && m < b - 1e-6) out.push([...this.toLonLat(this.xs[i]!, this.ys[i]!), m]);
    }
    out.push([...this.pointAt(b), b]);
    return out;
  }
}
