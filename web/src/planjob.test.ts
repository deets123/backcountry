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

describe("afterFailedPlan", () => {
  it("a failed plan after the first trips showed goes back to the plan with the error and the earlier run", async () => {
    const { afterFailedPlan } = await import("./planjob");
    const before = run("r0", false);
    expect(afterFailedPlan(run("partial", true), before, "Couldn't plan this trip. Try again.")).toEqual({
      run: before, view: "plan", busy: false, status: "", error: "Couldn't plan this trip. Try again." });
    expect(afterFailedPlan(run("partial", true), null, "x")).toMatchObject({ run: null, view: "plan" });
  });
  it("without a partial run on screen only the error changes", async () => {
    const { afterFailedPlan } = await import("./planjob");
    expect(afterFailedPlan(run("r0", false), null, "x")).toEqual({ busy: false, status: "", error: "x" });
  });
});

describe("giving up on a plan", () => {
  it("tells the server, so the plan's slot is free for the next one", async () => {
    const ctl = new AbortController();
    const cancelled: string[] = [];
    const p = runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: async () => { ctl.abort(); return { status: "searching", run: null } as never; },
      sleep: async () => {}, onPartial: () => {}, cancelPlan: async (id) => { cancelled.push(id); },
    }, "dolly_sods", { trailhead: "x", total: "18-26" }, ctl.signal);
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toEqual(["0123456789abcdef"]);
  });
});
