// Planning runs as a job on the server: start it, poll it, show the first trips while the search goes on.
import { ApiError } from "./api";
import type { GenerateRequest, PlanJob, Run } from "./types";

export const POLL_MS = 1500;

export interface PlanJobDeps {
  startPlan(region: string, req: GenerateRequest, signal?: AbortSignal): Promise<{ job_id: string }>;
  getPlan(jobId: string, signal?: AbortSignal): Promise<PlanJob>;
  sleep(ms: number): Promise<void>;
  onPartial(run: Run): void;
}

function aborted(): Error {
  const e = new Error("aborted");
  e.name = "AbortError";
  return e;
}

/** The final run. A partial run (the first trips found) goes to onPartial; a failed job throws its words. */
export async function runPlanJob(deps: PlanJobDeps, region: string, req: GenerateRequest, signal?: AbortSignal): Promise<Run> {
  const { job_id } = await deps.startPlan(region, req, signal);
  let shown: string | null = null;
  for (;;) {
    if (signal?.aborted) throw aborted();
    const job = await deps.getPlan(job_id, signal);
    if (signal?.aborted) throw aborted();
    if (job.status === "done" && job.run) return job.run;
    if (job.status === "failed") throw new ApiError(job.message ?? "Couldn't plan this trip. Try again.", 400, job.field ?? undefined);
    if (job.run && job.run.generated_at !== shown) {
      shown = job.run.generated_at;
      deps.onPartial(job.run);
    }
    await deps.sleep(POLL_MS);
  }
}
