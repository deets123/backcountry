// Typed client for the piece-1 API. All errors become ApiError with the server's wording.
import type { DifficultyAnswer, DifficultyBody } from "./offline/levels";
import type { ApiErrorBody, ChatAnswer, FeaturesInView, GenerateRequest, Region, Run, TrailFeatureCollection, Trailhead } from "./types";

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

/** With `timeoutMs`, a server that doesn't answer in time counts as unreachable (status 0). */
export async function getRegions(timeoutMs?: number): Promise<Region[]> {
  if (timeoutMs === undefined) return (await request<{ regions: Region[] }>("/api/regions")).regions;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    return (await request<{ regions: Region[] }>("/api/regions", { signal: ctl.signal })).regions;
  } catch (e) {
    if ((e as Error).name === "AbortError") throw new ApiError(`The Backcountry server didn't answer within ${Math.round(timeoutMs / 1000)} s.`, 0);
    throw e;
  } finally { clearTimeout(timer); }
}

export async function getTrailheads(region: string): Promise<Trailhead[]> {
  return (await request<{ trailheads: Trailhead[] }>(`/api/regions/${region}/trailheads`)).trailheads;
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
