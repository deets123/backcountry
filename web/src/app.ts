import { ApiError, generate, getRegions, getTrailheads, getTrails } from "./api";
import { BASEMAPS } from "./basemaps";
import { createMap, type TripMap } from "./map";
import { evidenceHtml } from "./evidence";
import { bindCompare, renderCompare } from "./panels/compare";
import { bindTrip, renderTrip } from "./panels/trip";
import { bindPlan, renderPlan, tapMessage } from "./panels/plan";
import { fromRun, toRequest, type FormState } from "./request";
import { nextSheet } from "./sheet";
import { createStore, loadPersisted, persist, type Basemap, type State, type Store } from "./store";
import type { Trailhead } from "./types";

export function startApp(doc: Document, mapEl: HTMLElement, panel: HTMLElement, handle: HTMLElement, storage: Storage = localStorage) {
  const store: Store = createStore(loadPersisted(storage));
  persist(store, storage);
  let map: TripMap | null = null;
  let abort: AbortController | null = null;
  let editingInPanel = false;

  const actions = {
    async pickRegion(id: string) {
      const region = store.get().regions.find((r) => r.id === id);
      if (!region) return;
      store.set({ region: id, trailheads: [], run: null, view: "plan", status: "Loading trails…", error: null });
      const b = region.bbox;
      map?.fitBounds([b.west, b.south, b.east, b.north]);
      try {
        const [ths, trails] = await Promise.all([getTrailheads(id), getTrails(id)]);
        store.set({ trailheads: ths, status: "" });
        map?.setTrails(trails);
        map?.setTrailheads(ths, (th) => actions.pickTrailhead(th));
      } catch (e) { store.set({ status: "", error: (e as Error).message }); }
    },
    pickTrailhead(th: Trailhead) { store.set((s) => ({ form: { ...s.form, trailhead: th.id }, error: null, view: "plan" })); },
    tapPoint(lon: number, lat: number, trailName: string | null) {
      if (store.get().view !== "plan") return;
      store.set((s) => ({ form: { ...s.form, trailhead: `${lat.toFixed(5)},${lon.toFixed(5)}` }, error: null, status: tapMessage(trailName) }));
    },
    async generate() {
      const s = store.get();
      const built = toRequest(s.form, s.trailheads);
      if ("error" in built) return store.set({ error: built.error });
      if (!s.region) return store.set({ error: "Pick a region first." });
      abort?.abort();
      abort = new AbortController();
      const timer = setTimeout(() => abort?.abort(), 120_000);
      store.set({ busy: true, error: null, status: "Searching… this takes 5–20 s on a big network." });
      try {
        const run = await generate(s.region, built.request, abort.signal);
        store.set({ run, selected: 1, view: "compare", busy: false, status: "" });
      } catch (e) {
        const msg = (e as Error).name === "AbortError"
          ? "The search took longer than 2 minutes and was stopped. Try a narrower range or fewer must-visits."
          : e instanceof ApiError ? e.message : `Unexpected error: ${(e as Error).message}`;
        store.set({ busy: false, status: "", error: msg }); // the previous run (if any) stays
      } finally { clearTimeout(timer); }
    },
    select(i: number) { store.set({ selected: i }); },
    openTrip(i: number) { store.set({ selected: i, view: "trip", sheet: "half" }); },
    backToCompare() { store.set({ view: "compare" }); },
    replan() { const run = store.get().run; store.set({ view: "plan", form: run ? fromRun(run) : store.get().form }); },
    setBasemap(b: Basemap) { store.set({ basemap: b }); map?.setBasemap(b); },
    showJunctions(show: boolean) { const s = store.get(); map?.setJunctions(s.run?.candidates[s.selected - 1] ?? null, show); },
    sheet(gesture: "tap" | "up" | "down") { store.set((s) => ({ sheet: nextSheet(s.sheet, gesture) })); },
    // Edits typed into the plan panel: record them without re-rendering the panel (a re-render on blur
    // would replace the Generate button under the user's finger and lose the tap).
    editForm(form: FormState) { editingInPanel = true; try { store.set({ form }); } finally { editingInPanel = false; } },
  };

  // The view decides the panel; the map layers follow the run and selection. Later tasks add compare/trip here.
  const renderers: Record<string, (s: State) => string> = { plan: renderPlan, compare: renderCompare, trip: renderTrip };
  const binders: Record<string, (root: HTMLElement) => void> = {
    plan: (root) => bindPlan(root, store, actions),
    compare: (root) => bindCompare(root, store, actions),
    trip: (root) => bindTrip(root, store, actions),
  };
  let shownView: string | null = null;
  function render() {
    const s = store.get();
    panel.innerHTML = renderers[s.view]?.(s) ?? "";
    binders[s.view]?.(panel);
    handle.parentElement!.className = `sheet sheet-${s.sheet}`;
    if (s.view !== shownView) panel.scrollTop = 0;  // a new view starts at its heading, not where the last one was scrolled
    shownView = s.view;
  }
  store.subscribe((s, prev) => {
    if (s.view !== prev.view || (s.form !== prev.form && !editingInPanel) || s.status !== prev.status || s.error !== prev.error || s.busy !== prev.busy
      || s.run !== prev.run || s.selected !== prev.selected || s.trailheads !== prev.trailheads || s.sheet !== prev.sheet
      || s.regions !== prev.regions) render();
    if (s.run !== prev.run || s.selected !== prev.selected || s.view !== prev.view) {
      if (!s.run) { map?.clearCandidates(); map?.setTrip(null); map?.setJunctions(null, false); }
      else if (s.view === "trip") {
        map?.clearCandidates(); map?.setJunctions(null, false);
        map?.setTrip(s.run.candidates[s.selected - 1] ?? null, evidenceHtml);
      } else { map?.setTrip(null); map?.setJunctions(null, false); map?.setCandidates(s.run.candidates, s.selected, (i) => actions.select(i)); }
    }
  });
  // Tap cycles the sheet; a vertical drag of more than 30px on the handle moves it one step up or down.
  let dragStart: number | null = null;
  handle.addEventListener("pointerdown", (e) => { dragStart = e.clientY; });
  handle.addEventListener("pointerup", (e) => {
    const dy = dragStart == null ? 0 : e.clientY - dragStart;
    dragStart = null;
    actions.sheet(dy < -30 ? "up" : dy > 30 ? "down" : "tap");
  });

  const basemapEl = doc.createElement("select");
  basemapEl.id = "basemap";
  basemapEl.setAttribute("aria-label", "Base map");
  basemapEl.innerHTML = Object.entries(BASEMAPS).map(([k, b]) => `<option value="${k}"${k === store.get().basemap ? " selected" : ""}>${b.name}</option>`).join("");
  basemapEl.addEventListener("change", () => actions.setBasemap(basemapEl.value as Basemap));
  map = createMap(mapEl, store.get().basemap, [-79.35, 39.02], 11, basemapEl);
  map.onMapTap((lon, lat, name) => actions.tapPoint(lon, lat, name));
  render();
  getRegions().then((regions) => {
    store.set({ regions });
    const saved = store.get().region;
    const id = saved && regions.some((r) => r.id === saved) ? saved : regions[0]?.id;
    if (id) actions.pickRegion(id);
    else store.set({ error: "No region has cached data yet. Run `backcountry ingest` and `backcountry dem` first." });
  }).catch((e) => store.set({ error: (e as Error).message }));

  return { store, actions, map };
}
