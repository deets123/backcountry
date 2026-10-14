// Typed client for the piece-1 API. All errors become ApiError with the server's wording.
import type { ApiErrorBody, FeaturesInView, GenerateRequest, Region, Run, TrailFeatureCollection, Trailhead } from "./types";

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

export async function getRegions(): Promise<Region[]> {
  return (await request<{ regions: Region[] }>("/api/regions")).regions;
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
