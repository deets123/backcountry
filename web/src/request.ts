import type { GenerateRequest, Run, Trailhead } from "./types";

export interface FormState {
  trailhead: string; total: string; nights: string; daily: string; maxGainDay: string;
  type: "loop" | "lollipop" | "any"; count: string; noRoads: boolean; direction: "" | "cw" | "ccw";
  mustVisit: string[]; avoid: string[]; camps: string[]; campsAtSites: boolean;
}

export const DEFAULT_FORM: FormState = {
  trailhead: "", total: "18-26", nights: "2", daily: "6-11", maxGainDay: "2500", type: "any", count: "3",
  noRoads: false, direction: "", mustVisit: [], avoid: [], camps: [], campsAtSites: false,
};

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

export function toRequest(form: FormState, trailheads: Trailhead[] = []): Result {
  if (!form.trailhead.trim()) return { error: "Pick a trailhead first.", field: "trailhead" };
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
  return {
    request: {
      trailhead: form.trailhead.trim(), total: form.total.trim(), nights, daily, max_gain_day: maxGain, type: form.type, count,
      no_roads: form.noRoads, camps: form.camps, camps_at_sites: form.campsAtSites, must_visit: form.mustVisit, avoid: form.avoid,
      direction: form.direction || null,
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
  };
}
