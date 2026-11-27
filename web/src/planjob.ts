// Planning runs as a job on the server: start it, poll it, show the first trips while the search goes on.
import { ApiError } from "./api";
import type { GenerateRequest, PlanJob, Run } from "./types";

export const POLL_MS = 1500;
export const POLL_TIMEOUT_MS = 10_000;  // a poll that takes longer than this on a weak signal is tried again
export const MAX_POLL_FAILURES = 5;  // dropped polls in a row before the plan is given up on

export interface PlanJobDeps {
  startPlan(region: string, req: GenerateRequest, signal?: AbortSignal): Promise<{ job_id: string }>;
  getPlan(jobId: string, signal?: AbortSignal): Promise<PlanJob>;
  sleep(ms: number): Promise<void>;
  onPartial(run: Run): void;
  /** Called when the phone gives up on a plan (a new one, the time limit, or a dead signal), to free its slot on the server. */
  cancelPlan?(jobId: string): Promise<unknown>;
  pollMs?: number;
  pollTimeoutMs?: number;
}

function aborted(): Error {
  const e = new Error("aborted");
  e.name = "AbortError";
  return e;
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new ApiError("The server didn't answer in time.", 0)), ms);
    p.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

/** The final run. A partial run (the first trips found) goes to onPartial; a failed job throws its words.
 * Dropped or slow polls are retried; when the phone gives up, the server is told so the plan's slot is freed. */
export async function runPlanJob(deps: PlanJobDeps, region: string, req: GenerateRequest, signal?: AbortSignal): Promise<Run> {
  const { job_id } = await deps.startPlan(region, req, signal);
  let shown: string | null = null;
  let failures = 0;
  const giveUp = (why: Error) => {
    void deps.cancelPlan?.(job_id).catch(() => { /* it expires on its own */ });
    return why;
  };
  for (;;) {
    if (signal?.aborted) throw giveUp(aborted());
    let job: PlanJob | null = null;
    try {
      job = await withTimeout(deps.getPlan(job_id, signal), deps.pollTimeoutMs ?? POLL_TIMEOUT_MS);
      failures = 0;
    } catch (e) {
      if ((e as Error).name === "AbortError" || signal?.aborted) throw giveUp(aborted());
      const status = e instanceof ApiError ? e.status : 0;
      if (status === 404 || status === 400) throw giveUp(e as Error);  // the server no longer has this plan
      if (++failures >= MAX_POLL_FAILURES) {
        throw giveUp(e instanceof ApiError ? e : new ApiError("Can't reach the Backcountry server to see how the plan is going.", 0));
      }
    }
    if (job) {
      if (job.status === "done" && job.run) return job.run;
      if (job.status === "failed") throw new ApiError(job.message ?? "Couldn't plan this trip. Try again.", 400, job.field ?? undefined);
      if (job.status !== "searching") throw new ApiError("This plan was stopped before it finished. Try again.", 400);
      if (job.run && job.run.generated_at !== shown) {
        shown = job.run.generated_at;
        deps.onPartial(job.run);
      }
    }
    await deps.sleep(deps.pollMs ?? POLL_MS);
  }
}

/** What to show after a plan fails. If the first trips were on screen (a partial run), go back to the plan with
 * the error and the run from before; a partial run left showing would say "still looking" for ever. */
export function afterFailedPlan(current: Run | null, before: Run | null, error: string):
  { run?: Run | null; view?: "plan"; busy: false; status: ""; error: string } {
  if (current?.partial) return { run: before, view: "plan", busy: false, status: "", error };
  return { busy: false, status: "", error };
}
