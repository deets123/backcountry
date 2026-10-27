import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { runPlanJob } from "./planjob";
import type { Run } from "./types";

const run = (id: string, partial: boolean) => ({ run_id: id, partial } as unknown as Run);

describe("runPlanJob", () => {
  it("shows a partial run, then returns the final one", async () => {
    const polls = [{ status: "searching", run: null }, { status: "searching", run: run("partial", true) }, { status: "done", run: run("r1", false) }];
    const partials: Run[] = [];
    const final = await runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: async () => polls.shift() as never,
      sleep: async () => {},
      onPartial: (r) => partials.push(r),
    }, "dolly_sods", { trailhead: "x", total: "18-26" });
    expect(partials.map((r) => r.run_id)).toEqual(["partial"]);
    expect(final.run_id).toBe("r1");
  });
  it("a failed job becomes an ApiError with the server's words and field", async () => {
    const p = runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: async () => ({ status: "failed", run: null, message: "No trailhead called X here", field: "trailhead" }) as never,
      sleep: async () => {}, onPartial: () => {},
    }, "dolly_sods", { trailhead: "x", total: "18-26" });
    await expect(p).rejects.toMatchObject({ message: "No trailhead called X here", field: "trailhead" });
    await expect(p).rejects.toBeInstanceOf(ApiError);
  });
  it("stops when aborted", async () => {
    const ctl = new AbortController();
    const p = runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: async () => { ctl.abort(); return { status: "searching", run: null } as never; },
      sleep: async () => {}, onPartial: () => {},
    }, "dolly_sods", { trailhead: "x", total: "18-26" }, ctl.signal);
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });
});
