import { ApiError, generate, getRegions, getTrailheads, getTrails } from "./api";
import { BASEMAPS } from "./basemaps";
import { evidenceHtml } from "./evidence";
import { locate, type Fix } from "./gps";
import { createMap, type TripMap } from "./map";
import { cleanOrphans, deleteTrip, getTrip, listTrips, putTrip, tilesPresent } from "./offline/db";
import { saveTrip, tripId } from "./offline/save";
import { coverageNote } from "./offline/tiles";
import { bindCompare, renderCompare, startLabel } from "./panels/compare";
import { esc } from "./panels/common";
import { bindPlan, renderPlan, tapMessage } from "./panels/plan";
import { bindSaved, renderSaved } from "./panels/saved";
import { bindTrip, renderTrip } from "./panels/trip";
import { fromRun, toRequest, type FormState } from "./request";
import { campHereIndex, campsOf, moveCamp } from "./resplit";
import { RouteLine } from "./routeline";
import { currentCandidate } from "./selectors";
import { nextSheet } from "./sheet";
import { createStore, loadPersisted, persist, type Basemap, type State, type Store } from "./store";
import type { Trailhead } from "./types";

const DRAG_SNAP_M = 150; // a dropped camp must land this close to the route line

// Changes to these re-render the panel. (Form edits typed in the panel don't; see editForm.)
const RENDER_KEYS: (keyof State)[] = ["view", "status", "error", "busy", "run", "selected", "trailheads", "sheet", "regions",
  "saved", "savedId", "campsM", "gps", "saving", "offline", "updateReady", "mapNote"];

export function startApp(doc: Document, mapEl: HTMLElement, panel: HTMLElement, handle: HTMLElement, storage: Storage = localStorage) {
  const store: Store = createStore(loadPersisted(storage));
  persist(store, storage);
  const win = doc.defaultView;
  const geo = win?.navigator.geolocation;
  let map: TripMap | null = null;
  let abort: AbortController | null = null;
  let saveAbort: AbortController | null = null;
  let editingInPanel = false;
  let watchId: number | null = null;
  let lastAlongM: number | null = null;
  let pendingUpdate: (() => void) | null = null;

  async function refreshSaved() {
    try { store.set({ saved: await listTrips() }); } catch { /* no IndexedDB here: no saved trips */ }
  }

  async function setCamps(campsM: number[] | null) {
    const id = store.get().savedId;
    store.set({ campsM, error: null });
    if (!id) return;
    const rec = await getTrip(id);
    if (rec) { await putTrip({ ...rec, campsM }); await refreshSaved(); }
  }

  function onFix(p: GeolocationPosition) {
    const fix: Fix = { lon: p.coords.longitude, lat: p.coords.latitude, accuracyM: p.coords.accuracy };
    const c = currentCandidate(store.get());
    const where = c ? locate(c, fix, lastAlongM) : null;
    if (where?.onRoute) lastAlongM = where.alongM;
    store.set({ gps: { mode: "on", fix, where } });
    map?.setPosition(fix);
  }

  const actions = {
    async pickRegion(id: string) {
      const region = store.get().regions.find((r) => r.id === id);
      if (!region) return;
      store.set({ region: id, trailheads: [], run: null, savedId: null, campsM: null, view: "plan", status: "Loading trails…", error: null });
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
        store.set({ run, selected: 1, view: "compare", busy: false, status: "", savedId: null, campsM: null });
      } catch (e) {
        const msg = (e as Error).name === "AbortError"
          ? "The search took longer than 2 minutes and was stopped. Try a narrower range or fewer must-visits."
          : e instanceof ApiError ? e.message : `Unexpected error: ${(e as Error).message}`;
        store.set({ busy: false, status: "", error: msg }); // the previous run (if any) stays
      } finally { clearTimeout(timer); }
    },
    select(i: number) { store.set({ selected: i }); },
    openTrip(i: number) {
      const s = store.get();
      const rec = s.run ? s.saved.find((t) => t.id === tripId(s.run!, i)) : undefined;
      lastAlongM = null;
      store.set({ selected: i, view: "trip", sheet: "half", savedId: rec?.id ?? null, campsM: rec?.campsM ?? null, status: "", error: null });
    },
    backToCompare() { store.set({ view: "compare", status: "", error: null }); },
    replan() { const run = store.get().run; store.set({ view: "plan", form: run ? fromRun(run) : store.get().form }); },
    setBasemap(b: Basemap) { store.set({ basemap: b }); map?.setBasemap(b); },
    showJunctions(show: boolean) { map?.setJunctions(currentCandidate(store.get()), show); },
    sheet(gesture: "tap" | "up" | "down") { store.set((s) => ({ sheet: nextSheet(s.sheet, gesture) })); },
    // Edits typed into the plan panel: record them without re-rendering the panel (a re-render on blur
    // would replace the Generate button under the user's finger and lose the tap).
    editForm(form: FormState) { editingInPanel = true; try { store.set({ form }); } finally { editingInPanel = false; } },

    async save() {
      const s = store.get();
      if (!s.run || s.saving) return;
      saveAbort = new AbortController();
      store.set({ saving: { done: 0, total: 0, failed: 0 }, error: null, status: "" });
      try {
        const rec = await saveTrip(s.run, s.selected, startLabel(s.run.request.trailhead, s.trailheads), (p) => store.set({ saving: p }), saveAbort.signal);
        await refreshSaved();
        store.set({ saving: null, savedId: rec.id, campsM: null, status: rec.complete ? "Saved for offline." : "" });
      } catch (e) {
        store.set((e as Error).name === "AbortError"
          ? { saving: null, status: "Save cancelled; nothing was kept." }
          : { saving: null, error: `Couldn't save this trip: ${(e as Error).message}` });
      } finally { saveAbort = null; }
    },
    cancelSave() { saveAbort?.abort(); },
    async showSaved() { await refreshSaved(); store.set({ view: "saved", status: "", error: null }); },
    toPlan() { store.set({ view: "plan", status: "", error: null }); },
    async openSaved(id: string) {
      const rec = await getTrip(id);
      if (!rec) return store.set({ error: "That saved trip is gone." });
      lastAlongM = null;
      const tilesGone = !(await tilesPresent(rec));
      store.set({ run: rec.run, selected: rec.index, savedId: rec.id, campsM: rec.campsM, view: "trip", sheet: "half", status: "",
        error: tilesGone ? "This trip's offline map was cleared by the browser. Save the trip again while online to get the map back." : null });
      map?.setTrails(rec.trails);
      map?.fitBounds(rec.box);
    },
    async deleteSaved(id: string) {
      if (!win?.confirm("Delete this saved trip and its offline map?")) return;
      await deleteTrip(id);
      await refreshSaved();
      if (store.get().savedId === id) store.set({ savedId: null, campsM: null });
    },
    toggleGps() {
      if (store.get().gps.mode !== "off") {
        if (watchId !== null) geo?.clearWatch(watchId);
        watchId = null; lastAlongM = null;
        store.set({ gps: { mode: "off" } });
        map?.setPosition(null);
        return;
      }
      if (!win?.isSecureContext) return store.set({ gps: { mode: "error", message: "location needs a secure (https) address; see the README's Tailscale step" } });
      if (!geo) return store.set({ gps: { mode: "error", message: "this browser has no location service" } });
      store.set({ gps: { mode: "waiting" } });
      watchId = geo.watchPosition(onFix, (err) => store.set({ gps: { mode: "error", message: err.message || "no position fix yet" } }),
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 30_000 });
    },
    async campHere() {
      const s = store.get();
      const c = currentCandidate(s);
      if (s.gps.mode !== "on" || !s.gps.where?.onRoute || !c?.plan) return;
      const index = campHereIndex(s.gps.where.day, c.plan.camps.length);
      if (index === null) return store.set({ error: "You're on the last day; there's no camp ahead to move." });
      const res = moveCamp(campsOf(c), index, s.gps.where.alongM, c.length_m);
      if ("error" in res) return store.set({ error: res.error });
      await setCamps(res.campsM);
    },
    async moveCampTo(index: number, lon: number, lat: number) {
      const s = store.get();
      const base = s.run?.candidates[s.selected - 1];
      const c = currentCandidate(s);
      if (!base || !c?.plan) return;
      const current = campsOf(c);
      const { snap } = RouteLine.of(base).nearest(lon, lat, current[index - 1] ?? null);
      const res = snap.offM > DRAG_SNAP_M
        ? { error: "Drop the camp on the route line; camps can't leave the route." }
        : moveCamp(current, index, snap.alongM, c.length_m);
      if ("error" in res) { store.set({ error: res.error }); map?.setTrip(c, evidenceHtml); return; } // the marker goes back
      await setCamps(res.campsM);
    },
    resetCamps() { void setCamps(null); },
    downloadSaved(which: string) {
      const s = store.get();
      const rec = s.saved.find((t) => t.id === s.savedId);
      if (!rec) return;
      const text = which === "whole" ? rec.gpx.whole : rec.gpx.days[Number(which) - 1] ?? null;
      if (!text) return store.set({ error: "That GPX file wasn't saved with this trip." });
      const url = URL.createObjectURL(new Blob([text], { type: "application/gpx+xml" }));
      const a = doc.createElement("a");
      a.href = url;
      a.download = `${rec.run.run_id}_candidate_${rec.index}${which === "whole" ? "" : `_day_${which}`}.gpx`;
      doc.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    },
    updateReady(apply: () => void) { pendingUpdate = apply; store.set({ updateReady: true }); },
    applyUpdate() { pendingUpdate?.(); },
  };

  const renderers: Record<string, (s: State) => string> = { plan: renderPlan, compare: renderCompare, trip: renderTrip, saved: renderSaved };
  const binders: Record<string, (root: HTMLElement) => void> = {
    plan: (root) => bindPlan(root, store, actions),
    compare: (root) => bindCompare(root, store, actions),
    trip: (root) => bindTrip(root, store, actions),
    saved: (root) => bindSaved(root, actions),
  };
  let shownView: string | null = null;
  function render() {
    const s = store.get();
    const banner = s.updateReady ? `<div class="warn" id="update">A new version of the app is ready. <button class="link" id="apply-update">Reload</button></div>` : "";
    panel.innerHTML = banner + (renderers[s.view]?.(s) ?? "");
    binders[s.view]?.(panel);
    panel.querySelector("#apply-update")?.addEventListener("click", () => actions.applyUpdate());
    handle.parentElement!.className = `sheet sheet-${s.sheet}`;
    if (s.view !== shownView) panel.scrollTop = 0; // a new view starts at its heading, not where the last one was scrolled
    shownView = s.view;
  }
  function syncMap(s: State) {
    if (!s.run || s.view === "saved") { map?.clearCandidates(); map?.setTrip(null); map?.setJunctions(null, false); return; }
    if (s.view === "trip") {
      const c = currentCandidate(s);
      map?.clearCandidates(); map?.setJunctions(null, false);
      map?.setCampDrag(s.savedId && c?.plan ? (i, lon, lat) => void actions.moveCampTo(i, lon, lat) : null);
      map?.setTrip(c, evidenceHtml);
    } else {
      map?.setTrip(null); map?.setJunctions(null, false);
      map?.setCandidates(s.run.candidates, s.selected, (i) => actions.select(i));
    }
  }
  store.subscribe((s, prev) => {
    if (RENDER_KEYS.some((k) => s[k] !== prev[k]) || (s.form !== prev.form && !editingInPanel)) render();
    if (s.run !== prev.run || s.selected !== prev.selected || s.view !== prev.view || s.campsM !== prev.campsM || s.savedId !== prev.savedId) syncMap(s);
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
  basemapEl.innerHTML = Object.entries(BASEMAPS).map(([k, b]) => `<option value="${k}"${k === store.get().basemap ? " selected" : ""}>${esc(b.name)}</option>`).join("");
  basemapEl.addEventListener("change", () => actions.setBasemap(basemapEl.value as Basemap));
  map = createMap(mapEl, store.get().basemap, [-79.35, 39.02], 11, basemapEl);
  map.onMapTap((lon, lat, name) => actions.tapPoint(lon, lat, name));
  map.onMove((view, zoom) => {
    const s = store.get();
    const rec = s.savedId ? s.saved.find((t) => t.id === s.savedId) : undefined;
    const offline = s.offline || win?.navigator.onLine === false;
    const note = rec && s.view === "trip" && offline ? coverageNote(rec.box, view, zoom) : null;
    if (note !== s.mapNote) store.set({ mapNote: note });
  });
  render();
  void (async () => {
    await cleanOrphans().catch(() => []);
    await refreshSaved();
    try {
      const regions = await getRegions();
      store.set({ regions, offline: false });
      const saved = store.get().region;
      const id = saved && regions.some((r) => r.id === saved) ? saved : regions[0]?.id;
      if (id) await actions.pickRegion(id);
      else store.set({ error: "No region has cached data yet. Run `backcountry ingest` and `backcountry dem` first." });
    } catch (e) {
      const unreachable = e instanceof ApiError && e.status === 0;
      if (unreachable && store.get().saved.length) store.set({ offline: true, view: "saved", status: "", error: null });
      else store.set({ offline: unreachable, error: (e as Error).message });
    }
  })();

  return { store, actions, map };
}
