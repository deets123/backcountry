// Planning runs as a job on the server: start it, poll it, show the first trips while the search goes on.
import { ApiError } from "./api";
import type { GenerateRequest, PlanJob, Run } from "./types";

export const POLL_MS = 1500;

export interface PlanJobDeps {
  startPlan(region: string, req: GenerateRequest, signal?: AbortSignal): Promise<{ job_id: string }>;
  getPlan(jobId: string, signal?: AbortSignal): Promise<PlanJob>;
  sleep(ms: number): Promise<void>;
  onPartial(run: Run): void;
  /** Called when the phone gives up on a plan (a new one, or the time limit), to free its slot on the server. */
  cancelPlan?(jobId: string): Promise<unknown>;
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
  const giveUp = () => {
    void deps.cancelPlan?.(job_id).catch(() => { /* it expires on its own */ });
    return aborted();
  };
  for (;;) {
    if (signal?.aborted) throw giveUp();
    let job: PlanJob;
    try {
      job = await deps.getPlan(job_id, signal);
    } catch (e) {
      if ((e as Error).name === "AbortError") throw giveUp();
      throw e;
    }
    if (signal?.aborted) throw giveUp();
    if (job.status === "done" && job.run) return job.run;
    if (job.status === "failed") throw new ApiError(job.message ?? "Couldn't plan this trip. Try again.", 400, job.field ?? undefined);
    if (job.run && job.run.generated_at !== shown) {
      shown = job.run.generated_at;
      deps.onPartial(job.run);
    }
    await deps.sleep(POLL_MS);
  }
}

/** What to show after a plan fails. If the first trips were on screen (a partial run), go back to the plan with
 * the error and the run from before; a partial run left showing would say "still looking" for ever. */
export function afterFailedPlan(current: Run | null, before: Run | null, error: string):
  { run?: Run | null; view?: "plan"; busy: false; status: ""; error: string } {
  if (current?.partial) return { run: before, view: "plan", busy: false, status: "", error };
  return { busy: false, status: "", error };
}
