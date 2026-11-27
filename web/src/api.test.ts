import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, chat, generate, getRegions, gpxUrl } from "./api";

function respond(status: number, body: unknown, json = true) {
  return vi.fn(async () => new Response(json ? JSON.stringify(body) : String(body), {
    status, headers: { "content-type": json ? "application/json" : "text/plain" },
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("returns regions", async () => {
    vi.stubGlobal("fetch", respond(200, { regions: [{ id: "dolly_sods", name: "Dolly Sods" }] }));
    expect((await getRegions()).regions[0]?.id).toBe("dolly_sods");
  });

  it("turns {error, field} into ApiError with the field", async () => {
    vi.stubGlobal("fetch", respond(400, { error: "total must look like 18-26", field: "total" }));
    await expect(generate("dolly_sods", { trailhead: "x", total: "5-2" })).rejects.toMatchObject({
      message: "total must look like 18-26", status: 400, field: "total",
    });
  });

  it("explains an unreachable server", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = (await getRegions().catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toMatch(/server/i);
  });

  it("explains a non-JSON error body", async () => {
    vi.stubGlobal("fetch", respond(502, "Bad Gateway", false));
    const err = (await getRegions().catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(502);
    expect(err.message).toMatch(/502/);
  });

  it("builds download urls", () => {
    expect(gpxUrl("r1", 2)).toBe("/api/runs/r1/candidates/2.gpx");
    expect(gpxUrl("r1", 2, 3)).toBe("/api/runs/r1/candidates/2.gpx?day=3");
  });
});

describe("getRegions with a time limit", () => {
  it("gives up as unreachable when the server doesn't answer in time", async () => {
    vi.stubGlobal("fetch", vi.fn((_u: string, init?: RequestInit) => new Promise((_r, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    await expect(getRegions(20)).rejects.toMatchObject({ name: "Error", status: 0, message: expect.stringContaining("didn't answer within") });
  });
});

describe("chat", () => {
  it("posts the message with its run, candidate and history", async () => {
    const f = respond(200, { reply: "ok", kind: "explain", proposal: null, note: null, withheld: null, model: "fake" });
    vi.stubGlobal("fetch", f);
    const a = await chat({ run_id: "r1", candidate: 2, message: "hi", history: [{ role: "user", text: "a" }] });
    expect(a.reply).toBe("ok");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/chat"); expect(JSON.parse(init.body as string)).toEqual({ run_id: "r1", candidate: 2, message: "hi", history: [{ role: "user", text: "a" }] });
  });
});
