// Re-cut a saved trip's days at camps the user moved, on the phone, with the engine's rules
// (split.py: day lengths, interpolated gain/loss, violation wording). The route never changes.
import { cumulativeFeet, interp } from "./elevation";
import { M_PER_MI } from "./format";
import { metersBetween, RouteLine } from "./routeline";
import type { Camp, Candidate, Day, NearbySite, Piece, Segment, Waypoint } from "./types";

export const NEARBY_CAMP_M = 500; // split.py NEARBY_CAMP_M
const CAMP_KINDS = new Set(["camp_site", "camp_pitch"]);

export interface Limits { daily_min_m: number; daily_max_m: number; max_gain_ft: number | null }

const grouped = (n: number) => Math.round(n).toLocaleString("en-US");
const mi2 = (m: number) => (m / M_PER_MI).toFixed(2);

export function resplitDays(profile: Candidate["profile"], lengthM: number, campsM: number[], limits: Limits): { days: Day[]; violations: string[] } {
  const end = profile && profile.d_m.length ? profile.d_m[profile.d_m.length - 1]! : lengthM;
  const bounds = [0, ...campsM, end];
  for (let i = 1; i < bounds.length; i++) {
    if (!(bounds[i]! > bounds[i - 1]!)) throw new Error("Camps must stay in order and strictly inside the route.");
  }
  const cum = cumulativeFeet(profile);
  const n = bounds.length - 1;
  const days: Day[] = [], violations: string[] = [];
  for (let k = 0; k < n; k++) {
    const a = bounds[k]!, b = bounds[k + 1]!, length = b - a;
    const gain = cum ? interp(b, cum.d, cum.G) - interp(a, cum.d, cum.G) : null;
    const loss = cum ? interp(b, cum.d, cum.L) - interp(a, cum.d, cum.L) : null;
    days.push({ index: k + 1, start_m: a, end_m: b, length_m: length, gain_ft: gain, loss_ft: loss,
      start_label: k === 0 ? "Start" : `Camp ${k}`, end_label: k === n - 1 ? "End" : `Camp ${k + 1}` });
    if (length < limits.daily_min_m) violations.push(`Day ${k + 1}: ${mi2(length)} mi is under the ${mi2(limits.daily_min_m)} mi daily minimum`);
    if (length > limits.daily_max_m) violations.push(`Day ${k + 1}: ${mi2(length)} mi is over the ${mi2(limits.daily_max_m)} mi daily max`);
    if (gain !== null && limits.max_gain_ft !== null && gain > limits.max_gain_ft) {
      violations.push(`Day ${k + 1}: ${grouped(gain)} ft of climbing is over the ${grouped(limits.max_gain_ft)} ft daily gain cap`);
    }
  }
  return { days, violations };
}

function segmentAt(c: Candidate, m: number): Segment | undefined {
  let cum = 0;
  for (const s of c.segments) { cum += s.length_m; if (m <= cum + 1e-6) return s; }
  return c.segments[c.segments.length - 1];
}

/** The candidate with its camps at `campsM` (meters along, in order). Everything else is copied. */
export function withCamps(c: Candidate, campsM: number[], line: RouteLine = RouteLine.of(c)): Candidate {
  const p = c.plan;
  if (!p) throw new Error("This trip has no day plan, so there are no camps to move.");
  if (campsM.length !== p.camps.length) throw new Error(`This trip has ${p.camps.length} camp(s); got ${campsM.length}.`);
  const { days, violations } = resplitDays(c.profile, c.length_m, campsM, p);
  const prof = c.profile && !c.profile.z_m.some((z) => z == null) ? c.profile : null;
  const ele = (m: number) => (prof ? interp(m, prof.d_m, prof.z_m as number[]) : null);
  const sites = c.features.filter((f) => CAMP_KINDS.has(f.kind));
  const water = c.features.filter((f) => !CAMP_KINDS.has(f.kind) && !f.kind.startsWith("usfs"));
  const camps: Camp[] = campsM.map((m, i) => {
    const before = p.camps[i]!;
    if (m === before.dist_m) return before; // not moved: keep the engine's camp and what it says about it
    const [lon, lat] = line.pointAt(m);
    const seg = segmentAt(c, m);
    const nearby: NearbySite[] = sites
      .map((s) => ({ source: s.source, kind: s.kind, name: s.name, url: s.url, distance_m: metersBetween(lon, lat, s.lon, s.lat) }))
      .filter((s) => s.distance_m <= NEARBY_CAMP_M)
      .sort((a, b) => a.distance_m - b.distance_m);
    let nearest_water: Camp["nearest_water"] = null;
    for (const w of water) {
      const d = metersBetween(lon, lat, w.lon, w.lat);
      if (!nearest_water || d < nearest_water.distance_m) nearest_water = { source: w.source, kind: w.kind, name: w.name, distance_m: d };
    }
    return { index: i + 1, dist_m: m, lat, lon, on_source: seg?.sources[0] ?? "", on_name: seg?.label ?? null, nearby,
      note: "camp_location: unverified", pinned: false, site: null, nearest_water, moved: true };
  });
  const pieces: Piece[] = days.map((d) => ({
    name: `Day ${d.index}: ${d.start_label} to ${d.end_label}`,
    points: line.slice(d.start_m, d.end_m).map(([lon, lat, m]) => [lon, lat, ele(m)] as [number, number, number | null]),
    start_m: d.start_m, end_m: d.end_m, length_m: d.length_m, gain_ft: d.gain_ft, loss_ft: d.loss_ft, day: d.index,
  }));
  const oldWps = c.waypoints.filter((w) => w.kind === "camp");
  const campWps: Waypoint[] = camps.map((cp, i) => (!cp.moved && oldWps[i] ? oldWps[i]! : {
    name: `Camp ${cp.index}`, kind: "camp", lat: cp.lat, lon: cp.lon, dist_m: cp.dist_m, ele: ele(cp.dist_m),
    desc: `Camp ${cp.index}, mile ${(cp.dist_m / M_PER_MI).toFixed(1)}, on ${cp.on_name ?? "an unnamed trail"}${cp.moved ? ", moved by you" : ""}. Location unverified.`,
  }));
  const others = c.waypoints.filter((w) => w.kind !== "camp");
  const waypoints = [...others.filter((w) => w.kind !== "junction"), ...campWps, ...others.filter((w) => w.kind === "junction")];
  const features = c.features.map((f) => ({ ...f, day: days.find((d) => d.start_m <= f.dist_m && f.dist_m <= d.end_m)?.index ?? null }));
  const notes = [...p.notes, "Camps moved by you on this phone; the route itself is unchanged."];
  return { ...c, pieces, waypoints, features, plan: { ...p, days, camps, violations, ok: violations.length === 0, notes } };
}

export function campsOf(c: Candidate): number[] {
  return c.plan?.camps.map((cp) => cp.dist_m) ?? [];
}

/** Move camp `index` (1-based) to `alongM`, keeping every camp strictly between its neighbours. */
export function moveCamp(current: number[], index: number, alongM: number, endM: number): { campsM: number[] } | { error: string } {
  const prev = index > 1 ? current[index - 2]! : 0;
  const next = index < current.length ? current[index]! : endM;
  if (!(alongM > prev && alongM < next)) {
    return { error: `Camp ${index} has to stay between ${index > 1 ? `Camp ${index - 1}` : "the start"} and ${index < current.length ? `Camp ${index + 1}` : "the end"}.` };
  }
  const out = [...current];
  out[index - 1] = alongM;
  return { campsM: out };
}

/** "Camp here" moves tonight's camp: the end of the day you're on. None on the last day. */
export function campHereIndex(day: number | null, nights: number): number | null {
  return day !== null && day >= 1 && day <= nights ? day : null;
}
