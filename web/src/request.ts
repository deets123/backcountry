import levels from "./levels.json";
import type { DayTarget, GenerateRequest, Level, Run, Trailhead, TripType } from "./types";

export type Scale = "day_hike" | "backpacking";
interface LevelRow { miles: [number, number]; max_gain_ft: number | null; max_grade_pct: number | null }
/** The engine's level table (difficulty.LEVEL_TARGETS), generated into levels.json by tests/levels_json.py. */
export const LEVEL_TARGETS = levels.level_targets as unknown as Record<Scale, Record<Level, LevelRow>>;
export const LEVELS = levels.levels as Level[];

/** One day's targets in the form. The numbers are the user's own; blank ones come from the level (or the trip). */
export interface FormDay { level: Level | ""; miles: string; maxGainFt: string; maxGradePct: string }
export type DayNumber = "miles" | "maxGainFt" | "maxGradePct";

export interface FormState {
  trailhead: string; total: string; nights: string; daily: string; maxGainDay: string;
  type: TripType; count: string; noRoads: boolean; direction: "" | "cw" | "ccw";
  mustVisit: string[]; avoid: string[]; camps: string[]; campsAtSites: boolean;
  perDay: boolean; days: FormDay[]; tripLevel: Level | "";
  /** One way: the finishing trailhead's id (or node id, or lat,lon). */
  finish: string;
  /** A day hike: no nights, no miles per day (a blank nights field means the same). */
  dayHike: boolean;
}

export const DEFAULT_FORM: FormState = {
  trailhead: "", total: "18-26", nights: "2", daily: "6-11", maxGainDay: "2500", type: "any", count: "3",
  noRoads: false, direction: "", mustVisit: [], avoid: [], camps: [], campsAtSites: false,
  perDay: false, days: [], tripLevel: "", finish: "", dayHike: false,
};

export function emptyDay(): FormDay {
  return { level: "", miles: "", maxGainFt: "", maxGradePct: "" };
}

/** Picking a level replaces the day's own numbers with the level's (shown as hints, sent as the level). */
export function pickLevel(_day: FormDay, level: Level | ""): FormDay {
  return { ...emptyDay(), level };
}

/** Typing a number makes it the user's own: it wins over the level's, and the day reads "custom". */
export function setDayNumber(day: FormDay, field: DayNumber, value: string): FormDay {
  return { ...day, [field]: value.trim() };
}

export function isCustom(day: FormDay): boolean {
  return !!(day.miles || day.maxGainFt || day.maxGradePct);
}

function parseRange(text: string): [number, number] | null {
  const parts = text.split("-").map((p) => Number(p.trim()));
  const [lo, hi] = parts;
  return parts.length === 2 && lo !== undefined && hi !== undefined && Number.isFinite(lo) && Number.isFinite(hi) && 0 < lo && lo < hi ? [lo, hi] : null;
}

/** The numbers a day works to: its own, else its level's; null where neither sets one. */
export function dayTargets(day: FormDay, scale: Scale): { miles: [number, number] | null; max_gain_ft: number | null; max_grade_pct: number | null } {
  const row = day.level ? LEVEL_TARGETS[scale][day.level] : null;
  const num = (v: string) => (v && Number.isFinite(Number(v)) ? Number(v) : null);
  return {
    miles: (day.miles && parseRange(day.miles)) || (row ? [row.miles[0], row.miles[1]] : null),
    max_gain_ft: num(day.maxGainFt) ?? row?.max_gain_ft ?? null,
    max_grade_pct: num(day.maxGradePct) ?? row?.max_grade_pct ?? null,
  };
}

/** The form's days for a trip with this many nights: one per day, extra ones kept aside, missing ones blank. */
export function daysFor(form: FormState, nights: number): FormDay[] {
  return Array.from({ length: nights + 1 }, (_, i) => form.days[i] ?? emptyDay());
}

type Failure = { error: string; field: keyof FormState };
type Result = { request: GenerateRequest } | Failure;

function range(text: string, field: keyof FormState): [number, number] | Failure {
  const parts = text.split("-").map((p) => Number(p.trim()));
  const [lo, hi] = parts;
  if (parts.length !== 2 || lo === undefined || hi === undefined || !Number.isFinite(lo) || !Number.isFinite(hi) || !(0 < lo && lo < hi)) {
    return { error: `${field} must look like 18-26 (miles, low-high); got "${text}"`, field };
  }
  return [lo, hi];
}

function whole(text: string, field: keyof FormState, min: number): number | Failure {
  const n = Number(text.trim());
  if (!Number.isInteger(n) || n < min) return { error: `${field} must be a whole number of at least ${min}`, field };
  return n;
}

export function toRequest(form: FormState, trailheads: Trailhead[] = [], destination: string | null = null): Result {
  if (!form.trailhead.trim()) return { error: "Pick a trailhead first.", field: "trailhead" };
  if (form.type === "one_way" && !form.finish.trim()) return { error: "Pick where the one-way trip finishes.", field: "finish" };
  if (form.dayHike) form = { ...form, nights: "", daily: "", perDay: false };
  const known = trailheads.find((t) => t.id === form.trailhead);
  if (known && !(known.in_graph && known.lat != null && known.lon != null)) {
    return { error: `${known.name ?? known.id} is not on the trail network; pick another start.`, field: "trailhead" };
  }
  const total = range(form.total, "total");
  if (!Array.isArray(total)) return total;
  let nights: number | null = null;
  if (form.nights.trim()) {
    const n = whole(form.nights, "nights", 1);
    if (typeof n !== "number") return n;
    nights = n;
  }
  let daily: string | null = null;
  if (form.daily.trim()) {
    const d = range(form.daily, "daily");
    if (!Array.isArray(d)) return d;
    daily = form.daily.trim();
  }
  if (nights !== null && daily === null) return { error: "nights needs daily (e.g. 6-11)", field: "daily" };
  let maxGain: number | null = null;
  if (form.maxGainDay.trim()) {
    maxGain = Number(form.maxGainDay);
    if (!Number.isFinite(maxGain)) return { error: "max_gain_day must be a number of feet", field: "maxGainDay" };
  }
  const count = whole(form.count, "count", 1);
  if (typeof count !== "number") return count;
  const days: DayTarget[] = [];
  if (form.perDay && nights !== null) {
    for (const [i, d] of daysFor(form, nights).entries()) {
      const t: DayTarget = { day: i + 1 };
      if (d.level) t.level = d.level;
      if (d.miles) {
        const m = parseRange(d.miles);
        if (!m) return { error: `Day ${i + 1}: miles must look like 8-12 (low-high); got "${d.miles}"`, field: "days" };
        t.miles = m;
      }
      for (const [key, field, label] of [["maxGainFt", "max_gain_ft", "climbing (ft)"], ["maxGradePct", "max_grade_pct", "steepness (%)"]] as const) {
        if (!d[key]) continue;
        const v = Number(d[key]);
        if (!Number.isFinite(v) || v <= 0) return { error: `Day ${i + 1}: ${label} must be a number above 0; got "${d[key]}"`, field: "days" };
        t[field] = v;
      }
      if (Object.keys(t).length > 1) days.push(t);
    }
  }
  return {
    request: {
      trailhead: form.trailhead.trim(), total: form.total.trim(), nights, daily, max_gain_day: maxGain, type: form.type, count,
      no_roads: form.noRoads, camps: form.camps, camps_at_sites: form.campsAtSites, must_visit: form.mustVisit, avoid: form.avoid,
      direction: form.direction || null, days, level: form.tripLevel || null, destination,
      finish: form.type === "one_way" ? form.finish.trim() : null,
    },
  };
}

function rangeText(v: string | [number, number] | null | undefined): string {
  return Array.isArray(v) ? `${v[0]}-${v[1]}` : v ?? "";
}

export function fromRun(run: Run): FormState {
  const r = run.request;
  return {
    trailhead: r.trailhead, total: rangeText(r.total), nights: r.nights == null ? "" : String(r.nights), daily: rangeText(r.daily),
    maxGainDay: r.max_gain_day == null ? "" : String(r.max_gain_day), type: r.type ?? "any", count: String(r.count ?? 3),
    noRoads: !!r.no_roads, direction: r.direction ?? "", mustVisit: r.must_visit ?? [], avoid: r.avoid ?? [], camps: r.camps ?? [],
    campsAtSites: !!r.camps_at_sites,
    perDay: (r.days ?? []).length > 0, tripLevel: r.level ?? "", finish: r.finish ?? "", dayHike: r.nights == null,
    days: Array.from({ length: r.nights == null ? 0 : r.nights + 1 }, (_, i) => {
      const t = (r.days ?? []).find((d) => d.day === i + 1);
      return t ? { level: t.level ?? "", miles: t.miles ? `${t.miles[0]}-${t.miles[1]}` : "",
        maxGainFt: t.max_gain_ft == null ? "" : String(t.max_gain_ft), maxGradePct: t.max_grade_pct == null ? "" : String(t.max_grade_pct) } : emptyDay();
    }),
  };
}
