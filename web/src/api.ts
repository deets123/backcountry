// Typed client for the piece-1 API. All errors become ApiError with the server's wording.
import type { DifficultyAnswer, DifficultyBody } from "./offline/levels";
import type { UpdateAnswer, UpdateCheck } from "./panels/update";
import type { ApiErrorBody, AreaOpened, AreaStatus, ChatAnswer, FeaturesInView, Finish, GenerateRequest, PlanJob, RecentArea, Region, Run, SearchResult,
  TrailFeatureCollection, Trailhead } from "./types";

export class ApiError extends Error {
  constructor(message: string, public status: number, public field?: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Can't reach the Backcountry server at /api. Is `backcountry serve` running on this network?", 0);
  }
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!res.ok) {
    const err = body as ApiErrorBody | null;
    throw new ApiError(err?.error ?? `The server answered ${res.status} without a reason.`, res.status, err?.field);
  }
  return body as T;
}

export interface RegionList { regions: Region[]; recent_areas: RecentArea[] }

/** Hand-made regions and recently opened areas. With `timeoutMs`, a server that doesn't answer in time counts as
 * unreachable (status 0). */
export async function getRegions(timeoutMs?: number): Promise<RegionList> {
  const list = (r: Partial<RegionList>): RegionList => ({ regions: r.regions ?? [], recent_areas: r.recent_areas ?? [] });
  if (timeoutMs === undefined) return list(await request<RegionList>("/api/regions"));
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    return list(await request<RegionList>("/api/regions", { signal: ctl.signal }));
  } catch (e) {
    if ((e as Error).name === "AbortError") throw new ApiError(`The Backcountry server didn't answer within ${Math.round(timeoutMs / 1000)} s.`, 0);
    throw e;
  } finally { clearTimeout(timer); }
}

export async function search(q: string): Promise<SearchResult[]> {
  return (await request<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}`)).results;
}

export function openArea(body: { ref: string } | { box: [number, number, number, number] } | { point: [number, number] }): Promise<AreaOpened> {
  return request("/api/areas", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
}

export function updateCheck(runId: string, n: number): Promise<UpdateCheck> {
  return request(`/api/runs/${encodeURIComponent(runId)}/update?candidate=${n}`);
}

export function updateTrip(runId: string, n: number): Promise<UpdateAnswer> {
  return request(`/api/runs/${encodeURIComponent(runId)}/update`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ candidate: n }),
  });
}

export function areaStatus(id: string): Promise<AreaStatus> {
  return request(`/api/areas/${encodeURIComponent(id)}/status`);
}

/** With `destination` (a search result's ref), each trailhead carries its trail distance to it, nearest first. */
export async function getTrailheads(region: string, destination?: string | null): Promise<Trailhead[]> {
  const q = destination ? `?destination=${encodeURIComponent(destination)}` : "";
  return (await request<{ trailheads: Trailhead[] }>(`/api/regions/${region}/trailheads${q}`)).trailheads;
}

/** Trailheads a one-way trip from `trailhead` can finish at within `total` miles (via `destination`). */
export async function getFinishes(region: string, trailhead: string, total: string, destination?: string | null): Promise<Finish[]> {
  const q = new URLSearchParams({ trailhead, total });
  if (destination) q.set("destination", destination);
  return (await request<{ finishes: Finish[] }>(`/api/regions/${region}/finishes?${q}`)).finishes;
}

export function getTrails(region: string): Promise<TrailFeatureCollection> {
  return request(`/api/regions/${region}/trails`);
}

export function getFeatures(region: string, bbox: [number, number, number, number]): Promise<FeaturesInView> {
  return request(`/api/regions/${region}/features?bbox=${bbox.join(",")}`);
}

export function generate(region: string, req: GenerateRequest, signal?: AbortSignal): Promise<Run> {
  return request(`/api/regions/${region}/generate`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(req), signal,
  });
}

export function startPlan(region: string, req: GenerateRequest, signal?: AbortSignal): Promise<{ job_id: string }> {
  return request(`/api/regions/${region}/plans`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(req), signal,
  });
}

export function getPlan(jobId: string, signal?: AbortSignal): Promise<PlanJob> {
  return request(`/api/plans/${encodeURIComponent(jobId)}`, { signal });
}

export function getRun(runId: string): Promise<Run> {
  return request(`/api/runs/${runId}`);
}

export function gpxUrl(runId: string, n: number, day?: number): string {
  return `/api/runs/${runId}/candidates/${n}.gpx${day ? `?day=${day}` : ""}`;
}

export function geojsonUrl(runId: string, n: number): string {
  return `/api/runs/${runId}/candidates/${n}.geojson`;
}

export function chat(body: { run_id: string; candidate: number; message: string; history: { role: string; text: string }[] }): Promise<ChatAnswer> {
  return request("/api/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
}

/** Levels for a saved trip's route (offline/levels.ts). */
export function difficulty(body: DifficultyBody): Promise<DifficultyAnswer> {
  return request("/api/difficulty", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
}
