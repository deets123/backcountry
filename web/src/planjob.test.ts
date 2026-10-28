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

describe("polling on a weak signal", () => {
  const req = { trailhead: "x", total: "18-26" };
  it("a dropped poll is retried, not fatal", async () => {
    const polls = [() => { throw new ApiError("Can't reach the Backcountry server", 0); }, () => ({ status: "searching", run: null }),
      () => { throw new ApiError("gateway", 502); }, () => ({ status: "done", run: run("r1", false) })];
    const slept: number[] = [];
    const final = await runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: async () => polls.shift()!() as never,
      sleep: async (ms) => { slept.push(ms); }, onPartial: () => {},
    }, "dolly_sods", req);
    expect(final.run_id).toBe("r1");
    expect(slept.length).toBeGreaterThanOrEqual(3);
  });
  it("a plan whose polls keep failing is cancelled on the server and fails with plain words", async () => {
    const cancelled: string[] = [];
    const p = runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: async () => { throw new ApiError("Can't reach the Backcountry server", 0); },
      sleep: async () => {}, onPartial: () => {}, cancelPlan: async (id) => { cancelled.push(id); },
    }, "dolly_sods", req);
    await expect(p).rejects.toMatchObject({ message: expect.stringMatching(/server/i) });
    expect(cancelled).toEqual(["0123456789abcdef"]);
  });
  it("a cancelled status fails the plan instead of polling for ever", async () => {
    const p = runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: async () => ({ status: "cancelled", run: null }) as never,
      sleep: async () => {}, onPartial: () => {},
    }, "dolly_sods", req);
    await expect(p).rejects.toBeInstanceOf(ApiError);
  });
  it("a stalled poll is given up on after its timeout and tried again", async () => {
    let calls = 0;
    const final = await runPlanJob({
      startPlan: async () => ({ job_id: "0123456789abcdef" }),
      getPlan: () => (++calls === 1 ? new Promise(() => {}) : Promise.resolve({ status: "done", run: run("r1", false) } as never)),
      sleep: (ms) => new Promise((r) => setTimeout(r, Math.min(ms, 5))), onPartial: () => {}, pollTimeoutMs: 20,
    }, "dolly_sods", req);
    expect(final.run_id).toBe("r1");
    expect(calls).toBe(2);
  });
});
