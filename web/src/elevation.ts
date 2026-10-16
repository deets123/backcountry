// The engine's elevation rules (src/backcountry/elevation.py), ported line for line so the phone's
// gain numbers match the days table: 3-sample centered smoothing, then gain/loss with 2 m hysteresis.
import { FT_PER_M } from "./format";
import type { Candidate } from "./types";

export const SMOOTH_SAMPLES = 3;
export const HYSTERESIS_M = 2.0;

export function smooth(z: number[], n = SMOOTH_SAMPLES): number[] {
  const cs = [0];
  for (const v of z) cs.push(cs[cs.length - 1]! + v);
  return z.map((_, i) => {
    const k = Math.min(Math.floor(n / 2), i, z.length - 1 - i);
    return (cs[i + k + 1]! - cs[i - k]!) / (2 * k + 1);
  });
}

export function cumulativeGainLoss(z: number[], threshold = HYSTERESIS_M): { G: number[]; L: number[] } {
  const G = new Array<number>(z.length).fill(0), L = new Array<number>(z.length).fill(0);
  if (!z.length) return { G, L };
  let gain = 0, loss = 0, ref = z[0]!;
  for (let i = 1; i < z.length; i++) {
    const diff = z[i]! - ref;
    if (diff >= threshold) { gain += diff; ref = z[i]!; }
    else if (diff <= -threshold) { loss -= diff; ref = z[i]!; }
    G[i] = gain; L[i] = loss;
  }
  const tail = z[z.length - 1]! - ref;
  if (tail > 0) G[z.length - 1]! += tail; else L[z.length - 1]! -= tail;
  return { G, L };
}

/** numpy.interp: linear between the two neighbours, clamped to the first/last value outside the range. */
export function interp(x: number, xs: number[], ys: number[]): number {
  if (x <= xs[0]!) return ys[0]!;
  if (x >= xs[xs.length - 1]!) return ys[ys.length - 1]!;
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid]! <= x) lo = mid; else hi = mid; }
  const t = (x - xs[lo]!) / (xs[hi]! - xs[lo]!);
  return ys[lo]! + t * (ys[hi]! - ys[lo]!);
}

/** Running gain and loss in feet along the profile, or null when elevation is unknown anywhere. */
export function cumulativeFeet(profile: Candidate["profile"]): { d: number[]; G: number[]; L: number[] } | null {
  if (!profile || profile.d_m.length < 2 || profile.z_m.some((z) => z == null)) return null;
  const { G, L } = cumulativeGainLoss(smooth(profile.z_m as number[]));
  return { d: profile.d_m, G: G.map((g) => g * FT_PER_M), L: L.map((l) => l * FT_PER_M) };
}
