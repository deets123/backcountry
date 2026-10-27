import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import type { AreaStatus, SearchResult } from "../types";
import { createStore, INITIAL as START } from "../store";
import { bindSearch, openSearchResult, renderSearch, waitForArea, type OpenDeps } from "./search";

const INITIAL = START;
const park: SearchResult = { kind: "area", ref: "relation/3003", name: "Testpark National Park", type_label: "National Park",
  state: "Statea", bbox: [-149.95, 10.1, -149.6, 10.5], center: [-149.8, 10.3] };
const region: SearchResult = { kind: "region", id: "dolly_sods", name: "Dolly Sods Wilderness, WV", type_label: "Region", state: null };
const BANNED = /download|\bMB\b|\bGB\b|geofabrik|mirror/i;

describe("renderSearch", () => {
  it("shows each result with its type and state on a second line", () => {
    const html = renderSearch({ ...INITIAL, searchQuery: "test", searchResults: [park, { ...park, ref: "node/9", name: "<b>x</b>", kind: "place", type_label: "Peak" }] });
    expect(html).toContain("Testpark National Park");
    expect(html).toMatch(/National Park · Statea/);
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
    expect(html).toContain('id="plan-here"');
    expect(html).not.toMatch(/name="/);  // the plan form binds every [name]; search must not be one of them
  });
  it("lists recent areas when the box is empty", () => {
    const html = renderSearch({ ...INITIAL, searchQuery: "", recentAreas: [{ id: "zion-r1", name: "Zion National Park", type_label: "National Park", state: "Utah", bbox: { south: 1, west: 1, north: 2, east: 2 } }] });
    expect(html).toContain("Recent");
    expect(html).toContain("Zion National Park");
  });
  it("never mentions downloads, sizes or mirrors", () => {
    for (const s of [{ ...INITIAL, searchQuery: "z", searchResults: [park, region] }, { ...INITIAL, searchQuery: "", status: "Finding trails in Zion National Park…" },
      { ...INITIAL, searchQuery: "nothing", searchResults: [] }]) {
      expect(renderSearch(s)).not.toMatch(BANNED);
    }
  });
  it("says so when nothing matches", () => {
    expect(renderSearch({ ...INITIAL, searchQuery: "qqq", searchResults: [] })).toContain("No places match");
  });
});

const status = (s: AreaStatus["status"], extra: Partial<AreaStatus> = {}): AreaStatus =>
  ({ status: s, message: s === "failed" ? "No trails were found there." : null, name: "Testpark National Park",
     bbox: { south: 10.1, west: -149.95, north: 10.5, east: -149.6 }, ...extra });

describe("waitForArea", () => {
  it("polls every 2 s until the area is ready", async () => {
    const answers = [status("queued"), status("preparing"), status("ready")];
    const sleep = vi.fn(async () => {});
    const got = await waitForArea("a", { areaStatus: async () => answers.shift()!, sleep });
    expect(got.status).toBe("ready");
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledWith(2000);
  });
  it("gives up after ten minutes with a plain message", async () => {
    let t = 0;
    const got = await waitForArea("a", { areaStatus: async () => status("preparing"), sleep: async (ms) => { t += ms; }, now: () => t });
    expect(got.status).toBe("failed");
    expect(got.message).toMatch(/taking longer than expected/);
  });
  it("an area the server forgot (restarted) counts as not ready", async () => {
    const got = await waitForArea("a", { areaStatus: async () => ({ status: "unknown", message: null }), sleep: async () => {} });
    expect(got.status).toBe("unknown");
  });
});

function deps(over: Partial<OpenDeps> = {}): OpenDeps & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    openArea: async (body) => { calls.push(`open ${JSON.stringify(body)}`); return { area_id: "testpark-national-park-r3003", name: "Testpark National Park", status: "queued", message: "Finding trails in Testpark National Park…", bbox: status("ready").bbox! }; },
    areaStatus: async () => status("ready"),
    sleep: async () => {},
    pickRegion: async (id) => { calls.push(`pick ${id}`); },
    setStatus: (m) => calls.push(`status ${m}`),
    setError: (m) => calls.push(`error ${m}`),
    addRecent: (a) => calls.push(`recent ${a.id}`),
    setElevationPending: (on) => calls.push(`elevation ${on ? "pending" : "ready"}`),
    ...over,
  };
}

describe("openSearchResult", () => {
  it("a hand-made region opens directly, with no area request", async () => {
    const d = deps();
    await openSearchResult(region, d);
    expect(d.calls).toEqual(["pick dolly_sods"]);
  });
  it("an area is prepared quietly, then opened and remembered", async () => {
    const d = deps();
    await openSearchResult(park, d);
    expect(d.calls).toEqual(['open {"ref":"relation/3003"}', "status Finding trails in Testpark National Park…",
      "recent testpark-national-park-r3003", "pick testpark-national-park-r3003"]);
  });
  it("a failed preparation shows its message", async () => {
    const d = deps({ areaStatus: async () => status("failed") });
    await openSearchResult(park, d);
    expect(d.calls.at(-1)).toBe("error No trails were found there.");
    expect(d.calls.some((c) => c.startsWith("pick"))).toBe(false);
  });
  it("a busy server's message is shown as it is", async () => {
    const d = deps({ openArea: async () => { throw new ApiError("The server is busy setting up other areas. Try again in a few minutes.", 429); } });
    await openSearchResult(park, d);
    expect(d.calls).toEqual(["error The server is busy setting up other areas. Try again in a few minutes."]);
  });
  it("a box opens the same way", async () => {
    const d = deps();
    await openSearchResult({ box: [-149.75, 10.75, -149.65, 10.85] }, d);
    expect(d.calls[0]).toBe('open {"box":[-149.75,10.75,-149.65,10.85]}');
  });
});

describe("bindSearch", () => {
  it("typing survives a panel redraw that lands before the search runs (e.g. trailheads finished loading)", async () => {
    vi.useFakeTimers();
    try {
      const store = createStore({ ...INITIAL });
      const root = document.createElement("div");
      const actions = {
        setQuery: (q: string) => store.set({ searchQuery: q }),
        async searchAreas(q: string) { store.set({ searchQuery: q, searchResults: [park] }); },
        async openTarget() {}, async openRecent() {}, async planHere() {},
      };
      const draw = () => { root.innerHTML = renderSearch(store.get()); bindSearch(root, store.get, actions, 250); };
      draw();
      const input = root.querySelector<HTMLInputElement>("#area-search")!;
      input.value = "Testp";
      input.dispatchEvent(new Event("input"));
      draw();  // the app re-renders the panel 100 ms later, before the debounce fires
      expect(root.querySelector<HTMLInputElement>("#area-search")!.value).toBe("Testp");
      await vi.advanceTimersByTimeAsync(300);
      expect(root.querySelector("#search-results")!.textContent).toContain("Testpark National Park");
    } finally { vi.useRealTimers(); }
  });
});

describe("opening in two steps", () => {
  it("trails status opens the region and keeps polling until ready", async () => {
    const answers = [status("preparing"), status("trails", { message: "Adding elevation…" }), status("trails", { message: "Adding elevation…" }), status("ready")];
    const d = deps({ areaStatus: async () => answers.shift()! });
    await openSearchResult(park, d);
    expect(d.calls).toEqual(['open {"ref":"relation/3003"}', "status Finding trails in Testpark National Park…",
      "pick testpark-national-park-r3003", "elevation pending", "elevation ready", "recent testpark-national-park-r3003"]);
    expect(answers).toEqual([]);
  });
  it("an area answering trails at once opens its trails at once", async () => {
    const d = deps({
      openArea: async () => ({ area_id: "a1", name: "A", status: "trails", message: "Adding elevation…", bbox: status("ready").bbox! }),
      areaStatus: async () => status("ready"),
    });
    await openSearchResult(park, d);
    expect(d.calls.slice(0, 3)).toEqual(["status Adding elevation…", "pick a1", "elevation pending"]);
  });
  it("elevation failing after the trails showed keeps the trails and says what happened", async () => {
    const answers = [status("trails"), status("failed")];
    const d = deps({ areaStatus: async () => answers.shift()! });
    await openSearchResult(park, d);
    expect(d.calls).toContain("pick testpark-national-park-r3003");
    expect(d.calls.at(-1)).toBe("error No trails were found there.");
    expect(d.calls.filter((c) => c.startsWith("pick")).length).toBe(1);
  });
});
