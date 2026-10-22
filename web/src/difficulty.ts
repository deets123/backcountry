// Difficulty levels on the phone, ported from the engine (src/backcountry/difficulty.py) so a camp moved on
// the phone re-rates its days exactly as the engine would: effort sqrt(2 x gain ft x miles), a sustained uphill
// tagged "steep" (and raising one level, never into strenuous, when very steep), OSM terrain tags making a day at
// least hard. difficulty.test.ts holds it to the engine with fixtures/difficulty-parity.json.
import { cumulativeFeet, interp, SMOOTH_SAMPLES, smooth } from "./elevation";
import { M_PER_MI } from "./format";
import type { Candidate, DifficultyRules, Level, Rating, Steep, TerrainInterval } from "./types";

export const LEVEL_ORDER: Level[] = ["easy", "moderate", "hard", "strenuous"];
export type Scale = "day_hike" | "backpacking";
type Profile = NonNullable<Candidate["profile"]>;

/** numpy.searchsorted on a sorted list: the first index whose value is >= x ("left") or > x ("right"). */
function searchsorted(xs: number[], x: number, side: "left" | "right"): number {
  let lo = 0, hi = xs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (side === "left" ? xs[mid]! < x : xs[mid]! <= x) lo = mid + 1; else hi = mid;
  }
  return lo;
}

function stretch(p: Profile, startM: number, endM: number): { d: number[]; z: (number | null)[] } {
  const i = searchsorted(p.d_m, startM - 1e-6, "left"), j = searchsorted(p.d_m, endM + 1e-6, "right");
  return { d: p.d_m.slice(i, j), z: p.z_m.slice(i, j) };
}

/** The steepest uphill grade over `windowM` inside the stretch, on the whole route's smoothed profile. */
export function steepestClimb(p: Profile, startM: number, endM: number, windowM: number): Steep | null {
  const s = stretch(p, startM, endM);
  if (s.d.length < 2 || s.z.some((v) => v == null) || s.d[s.d.length - 1]! - s.d[0]! < windowM - 1e-6) return null;
  const d = p.d_m;
  const zs = smooth(p.z_m.map((v) => (v == null ? NaN : v)), SMOOTH_SAMPLES);
  let best: Steep | null = null;
  for (let i = 0; i < d.length; i++) {
    const j = searchsorted(d, d[i]! + windowM - 1e-6, "left");
    if (j >= d.length || d[i]! < startM - 1e-6 || d[j]! > endM + 1e-6) continue;
    const grade = ((zs[j]! - zs[i]!) / (d[j]! - d[i]!)) * 100;
    if (Number.isNaN(grade)) continue;
    if (best === null || grade > best.grade_pct) best = { grade_pct: grade, start_m: d[i]!, end_m: d[j]! };
  }
  return best && best.grade_pct > 0 ? best : null;
}

const overlap = (t: TerrainInterval, a: number, b: number) => Math.max(0, Math.min(t.end_m, b) - Math.max(t.start_m, a));

export function rateStretch(p: Profile, terrain: TerrainInterval[], startM: number, endM: number, gainFt: number | null,
  scale: Scale, rules: DifficultyRules): Rating {
  const length = Math.max(endM - startM, 0);
  const tagged = terrain.filter((t) => t.reason && overlap(t, startM, endM) >= rules.terrain.min_m);
  const covered = terrain.reduce((sum, t) => sum + overlap(t, startM, endM), 0);
  const untagged = length > 0 ? 1 - Math.min(covered / length, 1) : 1;
  const z = stretch(p, startM, endM).z;
  if (gainFt === null || !z.length || z.some((v) => v == null)) {
    return { level: null, reasons: [], effort: null, steep: null, tagged, untagged_share: untagged };
  }
  const effort = Math.sqrt(Math.max(gainFt, 0) * 2 * Math.max(length / M_PER_MI, 0));
  let level = rules.scales[scale].cutoffs.filter((c) => c <= effort).length;  // bisect_right
  const reasons: string[] = [];
  const steep = steepestClimb(p, startM, endM, rules.steep.window_m);
  const hard = LEVEL_ORDER.indexOf("hard");
  if (steep && steep.grade_pct >= rules.steep.tag_pct) {
    reasons.push("steep");
    if (steep.grade_pct >= rules.steep.raise_pct && level < hard) level += 1;
  }
  if (tagged.length) {
    level = Math.max(level, hard);
    for (const t of tagged) if (!reasons.includes(t.reason!)) reasons.push(t.reason!);
  }
  return { level: LEVEL_ORDER[level]!, reasons, effort, steep, tagged, untagged_share: untagged };
}

export function tripLevel(ratings: Rating[]): Level | null {
  if (!ratings.length || ratings.some((r) => r.level === null)) return null;
  return LEVEL_ORDER[Math.max(...ratings.map((r) => LEVEL_ORDER.indexOf(r.level!)))]!;
}

export interface SavedRating { days: { start_m: number; end_m: number; gain_ft: number | null; rating: Rating }[]; level: Level | null; rating: Rating | null }

/** Days and levels for a route with these camps, as the engine's rate_saved (POST /api/difficulty) gives them. */
export function rateSaved(p: Profile, terrain: TerrainInterval[], nights: number | null, campsM: number[] | null, rules: DifficultyRules): SavedRating {
  const cum = cumulativeFeet(p);
  const gain = (a: number, b: number) => (cum ? interp(b, cum.d, cum.G) - interp(a, cum.d, cum.G) : null);
  const end = p.d_m[p.d_m.length - 1]!;
  if (!nights) {
    const r = rateStretch(p, terrain, 0, end, gain(0, end), "day_hike", rules);
    return { days: [], level: r.level, rating: r };
  }
  const bounds = [0, ...(campsM ?? []), end];
  const days = bounds.slice(1).map((b, k) => {
    const a = bounds[k]!;
    return { start_m: a, end_m: b, gain_ft: gain(a, b), rating: rateStretch(p, terrain, a, b, gain(a, b), "backpacking", rules) };
  });
  return { days, level: tripLevel(days.map((d) => d.rating)), rating: null };
}
