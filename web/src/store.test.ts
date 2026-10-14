import { describe, expect, it } from "vitest";
import { createStore, loadPersisted, persist } from "./store";

class MemoryStorage implements Storage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); } key(i: number) { return [...this.m.keys()][i] ?? null; }
  getItem(k: string) { return this.m.get(k) ?? null; } setItem(k: string, v: string) { this.m.set(k, v); } removeItem(k: string) { this.m.delete(k); }
}

describe("store", () => {
  it("notifies subscribers with previous and next state", () => {
    const s = createStore();
    const seen: string[] = [];
    s.subscribe((next, prev) => seen.push(`${prev.view}->${next.view}`));
    s.set({ view: "compare" });
    s.set((st) => ({ status: `was ${st.view}` }));
    expect(seen).toEqual(["plan->compare", "compare->compare"]);
    expect(s.get().status).toBe("was compare");
  });
  it("persists region, form and basemap only, and survives bad JSON", () => {
    const storage = new MemoryStorage();
    const s = createStore();
    persist(s, storage);
    s.set({ region: "pemi", basemap: "satellite", status: "ignored", run: {} as never });
    const saved = JSON.parse(storage.getItem("backcountry.v1")!);
    expect(saved.region).toBe("pemi"); expect(saved.basemap).toBe("satellite");
    expect(saved.status).toBeUndefined(); expect(saved.run).toBeUndefined();
    expect(loadPersisted(storage)).toMatchObject({ region: "pemi", basemap: "satellite" });
    storage.setItem("backcountry.v1", "{not json");
    expect(loadPersisted(storage)).toEqual({});
  });
  it("unsubscribe stops notifications", () => {
    const s = createStore();
    let n = 0;
    const off = s.subscribe(() => n++);
    s.set({ status: "a" }); off(); s.set({ status: "b" });
    expect(n).toBe(1);
  });
});
