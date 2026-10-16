// Where am I on the saved route? Pure math on the phone; nothing here needs the network.
import { cumulativeFeet, interp } from "./elevation";
import { FT_PER_M, M_PER_MI } from "./format";
import { RouteLine } from "./routeline";
import type { Candidate } from "./types";

export const OFF_ROUTE_M = 400; // ~0.25 mi
export const ROUGH_M = 91.44;   // 300 ft

export interface Fix { lon: number; lat: number; accuracyM: number }
export type Where =
  | { onRoute: true; alongM: number; offM: number; day: number | null; next: { label: string; distM: number; gainFt: number | null }; ambiguous: boolean; accuracyM: number }
  | { onRoute: false; offM: number; accuracyM: number };

export function locate(c: Candidate, fix: Fix, lastAlongM: number | null, line: RouteLine = RouteLine.of(c)): Where {
  const { snap, ambiguous } = line.nearest(fix.lon, fix.lat, lastAlongM);
  if (snap.offM > OFF_ROUTE_M) return { onRoute: false, offM: snap.offM, accuracyM: fix.accuracyM };
  const along = snap.alongM;
  const day = c.plan?.days.find((d) => d.start_m <= along && along <= d.end_m) ?? null;
  const target = day ? { m: day.end_m, label: day.end_label } : { m: c.length_m, label: "End" };
  const cum = cumulativeFeet(c.profile);
  const gainFt = cum ? Math.max(0, interp(target.m, cum.d, cum.G) - interp(along, cum.d, cum.G)) : null;
  return { onRoute: true, alongM: along, offM: snap.offM, day: day?.index ?? null,
    next: { label: target.label, distM: Math.max(0, target.m - along), gainFt }, ambiguous, accuracyM: fix.accuracyM };
}

const ft = (m: number) => `${Math.round(m * FT_PER_M).toLocaleString("en-US")} ft`;
const mi1 = (m: number) => (m / M_PER_MI).toFixed(1);

export function whereText(w: Where, lengthM: number): string {
  const acc = `±${ft(w.accuracyM)}${w.accuracyM > ROUGH_M ? ", position rough" : ""}`;
  if (!w.onRoute) return `Off route: ${mi1(w.offM)} mi from the nearest point on the route · ${acc}`;
  const parts = [`mile ${mi1(w.alongM)} of ${mi1(lengthM)}`];
  if (w.day !== null) parts.push(`Day ${w.day}`);
  const climb = w.next.gainFt === null ? " (gain unknown)" : ` and +${Math.round(w.next.gainFt).toLocaleString("en-US")} ft`;
  parts.push(`${mi1(w.next.distM)} mi${climb} to ${w.next.label}`, `${ft(w.offM)} off route`, acc);
  return parts.join(" · ") + (w.ambiguous ? " (the route passes here twice)" : "");
}
