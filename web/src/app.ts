import { ApiError, areaStatus, chat, difficulty, getPlan, getRegions, getTrailheads, getTrails, openArea, search, startPlan, updateCheck,
  updateTrip } from "./api";
import { BASEMAPS } from "./basemaps";
import { evidenceHtml } from "./evidence";
import { locate, type Fix } from "./gps";
import { createMap, type TripMap } from "./map";
import { cleanOrphans, deleteTrip, getTrip, listTrips, putTrip, tilesPresent } from "./offline/db";
import { refreshLevels } from "./offline/levels";
import { saveTrip, tripId } from "./offline/save";
import { runPlanJob } from "./planjob";
import { coverageNote } from "./offline/tiles";
import { bindChat } from "./panels/chat";
import { bindCompare, renderCompare, startLabel } from "./panels/compare";
import { esc } from "./panels/common";
import { bindPlan, renderPlan, tapMessage } from "./panels/plan";
import { bindSaved, renderSaved } from "./panels/saved";
import { openSearchResult, type Target } from "./panels/search";
import { acceptUpdate, checkForUpdate, declineUpdate, reviewUpdate } from "./panels/update";
import { bindTrip, patchTrip, renderTrip } from "./panels/trip";
import { fromRun, toRequest, type FormState } from "./request";
import { campHereIndex, campsOf, moveCamp } from "./resplit";
import { RouteLine } from "./routeline";
import { currentCandidate, openTripId } from "./selectors";
import { nextSheet } from "./sheet";
import { createStore, loadPersisted, persist, type Basemap, type State, type Store } from "./store";
import type { Box } from "./offline/tiles";
import type { GenerateRequest, RecentArea, Region, Trailhead } from "./types";

const DRAG_SNAP_M = 150; // a dropped camp must land this close to the route line

// Changes to these re-render the panel. (Form edits typed in the panel don't; see editForm.)
const RENDER_KEYS: (keyof State)[] = ["view", "status", "error", "busy", "run", "selected", "trailheads", "sheet", "regions", "recentAreas",
  "saved", "savedId", "campsM", "gps", "saving", "offline", "updateReady", "mapNote", "tilesMissing", "junctions",
  "chat", "chatBusy", "chatError", "tripUpdate"];
const LIVE_KEYS = new Set<keyof State>(["gps", "saving"]); // updated in place in the trip view (patchTrip)
const BOOT_TIMEOUT_MS = 5000; // with saved trips on the phone, don't wait longer than this for the server

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
  let lastView: { view: Box; zoom: number } | null = null;
  let lastTarget: Target | null = null;  // the last area opened, for "Try again"

  function updateMapNote() {
    const s = store.get();
    const rec = s.savedId ? s.saved.find((t) => t.id === s.savedId) : undefined;
    const offline = s.offline || win?.navigator.onLine === false;
    const note = rec && lastView && s.view === "trip" && offline ? coverageNote(rec.box, lastView.view, lastView.zoom, s.basemap) : null;
    if (note !== s.mapNote) store.set({ mapNote: note });
  }

  async function refreshSaved() {
    try { store.set({ saved: await listTrips() }); } catch { /* no IndexedDB here: no saved trips */ }
  }

  /** Online: give trips saved before levels existed their levels (offline/levels.ts), then show them. */
  async function refreshTripLevels() {
    const done = await refreshLevels(store.get().saved, { difficulty, put: putTrip, now: () => new Date().toISOString() });
    if (done.length) await refreshSaved();
  }

  // Saved trips store the change first and show it after, so what's on screen is what's on the phone
  // (closing the app right after a move can't lose it).
  async function setCamps(campsM: number[] | null) {
    const id = store.get().savedId;
    if (id) {
      try {
        const rec = await getTrip(id);
        if (rec) await putTrip({ ...rec, campsM });
      } catch (e) {
        store.set({ error: `Couldn't store the camp change on this phone: ${(e as Error).message}` });
        syncMap(store.get()); // markers go back to the stored camps
        return;
      }
    }
    store.set({ campsM, error: null });
    if (id) await refreshSaved();
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
    /** A hand-made region, or a generated area (from the recent list, or with its box when just prepared). */
    async pickRegion(id: string, bbox?: Region["bbox"]) {
      const s0 = store.get();
      const b = s0.regions.find((r) => r.id === id)?.bbox ?? s0.recentAreas.find((r) => r.id === id)?.bbox ?? bbox;
      if (!b) return;
      store.set({ region: id, trailheads: [], run: null, savedId: null, campsM: null, view: "plan", status: "Loading trails…", error: null, busy: false,
        searchQuery: "", searchResults: null, elevationPending: false, canRetryOpen: false });
      map?.fitBounds([b.west, b.south, b.east, b.north]);
      try {
        const [ths, trails] = await Promise.all([getTrailheads(id), getTrails(id)]);
        store.set({ trailheads: ths, status: "" });
        map?.setTrails(trails);
        map?.setTrailheads(ths, (th) => actions.pickTrailhead(th));
      } catch (e) { store.set({ status: "", error: (e as Error).message }); }
    },
    setQuery(q: string) { store.set({ searchQuery: q }); },
    async searchAreas(q: string) {
      store.set({ searchQuery: q });
      if (!q.trim()) return store.set({ searchResults: null });
      try {
        const results = await search(q.trim());
        if (store.get().searchQuery === q) store.set({ searchResults: results });  // a later keystroke wins
      } catch (e) { store.set({ error: (e as Error).message }); }
    },
    openTarget(t: Target) {
      lastTarget = t;
      store.set({ canRetryOpen: false });
      return openSearchResult(t, {
        openArea, areaStatus, sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
        pickRegion: (id, bbox) => actions.pickRegion(id, bbox),
        setStatus: (message) => store.set({ status: message, error: null, busy: true }),
        setError: (message) => store.set({ error: message, status: "", busy: false, elevationPending: false, canRetryOpen: true }),
        setElevationPending: (on) => store.set({ elevationPending: on, status: "" }),
        addRecent: (a: RecentArea) => store.set((s) => ({ recentAreas: [a, ...s.recentAreas.filter((r) => r.id !== a.id)].slice(0, 8) })),
      });
    },
    retryOpen() { if (lastTarget) void actions.openTarget(lastTarget); },
    openRecent(r: RecentArea) { return actions.pickRegion(r.id, r.bbox); },
    planHere() {
      if (!lastView) return Promise.resolve(store.set({ error: "Move the map to the area first." }));
      return actions.openTarget({ box: lastView.view });
    },
    pickTrailhead(th: Trailhead) { store.set((s) => ({ form: { ...s.form, trailhead: th.id }, error: null, view: "plan" })); },
    tapPoint(lon: number, lat: number, trailName: string | null) {
      if (store.get().view !== "plan") return;
      store.set((s) => ({ form: { ...s.form, trailhead: `${lat.toFixed(5)},${lon.toFixed(5)}` }, error: null, status: tapMessage(trailName) }));
    },
    async generate(override?: GenerateRequest) {
      const s = store.get();
      const built = override ? { request: override } : toRequest(s.form, s.trailheads);
      if ("error" in built) return store.set({ error: built.error });
      if (!s.region) return store.set({ error: "Pick a region first." });
      abort?.abort();
      abort = new AbortController();
      const timer = setTimeout(() => abort?.abort(), 120_000);
      store.set({ busy: true, error: null, status: "Searching… the first trips show in about 10 s." });
      try {
        const signal = abort.signal;
        const run = await runPlanJob({
          startPlan, getPlan, sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
          // the first trips found: shown while the search goes on (status stays busy; not saved)
          onPartial: (partial) => store.set({ run: partial, form: fromRun(partial), selected: 1, view: "compare", savedId: null, campsM: null }),
        }, s.region, built.request, signal);
        store.set({ run, form: fromRun(run), selected: 1, view: "compare", busy: false, status: "", savedId: null, campsM: null });
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
      store.set({ selected: i, view: "trip", sheet: "half", savedId: rec?.id ?? null, campsM: rec?.campsM ?? null, tilesMissing: false, status: "", error: null });
      if (rec) void tilesPresent(rec).then((ok) => { if (!ok && openTripId(store.get()) === rec.id) store.set({ tilesMissing: true }); });
    },
    backToCompare() { store.set({ view: "compare", status: "", error: null }); },
    replan() { const run = store.get().run; store.set({ view: "plan", form: run ? fromRun(run) : store.get().form }); },
    async sendChat(text: string) {
      const s = store.get();
      const message = text.trim();
      if (!message || !s.run || s.chatBusy) return;
      const runId = s.run.run_id;
      const history = s.chat.filter((m) => m.runId === runId).slice(-6).map((m) => ({ role: m.role, text: m.text })); // this result's only
      store.set({ chat: [...s.chat, { role: "user", text: message, runId }], chatBusy: true, chatError: null });
      try {
        const a = await chat({ run_id: s.run.run_id, candidate: s.selected, message, history });
        store.set((x) => ({ chatBusy: false, chat: [...x.chat, { role: "assistant", text: a.reply, proposal: a.proposal, note: a.note,
          withheld: a.withheld, status: a.proposal ? "open" : undefined, runId }] }));
      } catch (e) {
        store.set({ chatBusy: false, chatError: (e as Error).message }); // the rest of the app keeps working
      }
    },
    async runProposal(i: number) {
      const m = store.get().chat[i];
      if (!m?.proposal || m.status !== "open" || m.runId !== store.get().run?.run_id) return;
      store.set((x) => ({ chat: x.chat.map((c, j) => (j === i ? { ...c, status: "ran" } : c)) }));
      await actions.generate(m.proposal.request);
    },
    dismissProposal(i: number) {
      store.set((x) => ({ chat: x.chat.map((c, j) => (j === i ? { ...c, status: "dismissed" } : c)) }));
    },
    setBasemap(b: Basemap) { store.set({ basemap: b }); map?.setBasemap(b); updateMapNote(); },
    showJunctions(show: boolean) { store.set({ junctions: show }); map?.setJunctions(currentCandidate(store.get()), show); },
    sheet(gesture: "tap" | "up" | "down") { store.set((s) => ({ sheet: nextSheet(s.sheet, gesture) })); },
    // Edits typed into the plan panel: record them without re-rendering the panel (a re-render on blur
    // would replace the Generate button under the user's finger and lose the tap).
    editForm(form: FormState) { editingInPanel = true; try { store.set({ form }); } finally { editingInPanel = false; } },

    async save() {
      const s = store.get();
      if (!s.run || s.saving) return;
      saveAbort = new AbortController();
      const id = tripId(s.run, s.selected);
      const before = s.saved.find((t) => t.id === id); // a re-save keeps the camps the user moved
      const name = before?.startName ?? startLabel(s.run.request.trailhead, s.trailheads);
      store.set({ saving: { id, done: 0, total: 0, failed: 0 }, error: null, status: "" });
      try {
        const rec = await saveTrip(s.run, s.selected, name, (p) => store.set({ saving: { id, ...p } }), saveAbort.signal, undefined, before?.campsM ?? null);
        await refreshSaved();
        // The save took a while: only mark the trip on screen as saved if it is still this one.
        const open = openTripId(store.get()) === rec.id;
        store.set(open ? { saving: null, savedId: rec.id, campsM: rec.campsM, tilesMissing: false, status: rec.complete ? "Saved for offline." : "" }
          : { saving: null, status: rec.complete ? `Saved ${rec.startName} for offline.` : "" });
      } catch (e) {
        store.set((e as Error).name === "AbortError"
          ? { saving: null, status: "Save cancelled; nothing was kept." }
          : { saving: null, error: `Couldn't save this trip: ${(e as Error).message}` });
      } finally { saveAbort = null; }
    },
    cancelSave() { saveAbort?.abort(); },
    async showSaved() { await refreshSaved(); store.set({ view: "saved", status: "", error: null }); },
    toPlan() {
      const s = store.get();
      if (!s.trailheads.length && s.regions.length) { void actions.pickRegion(s.regions.some((r) => r.id === s.region) ? s.region! : s.regions[0]!.id); return; }
      store.set({ view: "plan", status: "", error: null });
    },
    async openSaved(id: string) {
      const rec = await getTrip(id);
      if (!rec) return store.set({ error: "That saved trip is gone." });
      lastAlongM = null;
      const tilesGone = !(await tilesPresent(rec));
      store.set({ run: rec.run, selected: rec.index, savedId: rec.id, campsM: rec.campsM, view: "trip", sheet: "half", status: "", tilesMissing: tilesGone,
        error: tilesGone ? "This trip's offline map was cleared by the browser. Tap Save again while online to get the map back." : null });
      map?.setTrails(rec.trails);
      map?.fitBounds(rec.box);
      store.set({ tripUpdate: null });
      const offer = await checkForUpdate(rec, { online: () => !store.get().offline && win?.navigator.onLine !== false, updateCheck });
      if (offer && store.get().savedId === rec.id) store.set({ tripUpdate: offer });
    },
    // Newer trail data for the open saved trip: each step waits for the user's tap (panels/update.ts).
    async reviewUpdate() {
      const s = store.get(), u = s.tripUpdate, rec = s.saved.find((t) => t.id === s.savedId);
      if (!rec || u?.phase !== "offer") return;
      store.set({ tripUpdate: { id: rec.id, phase: "busy" } });
      store.set({ tripUpdate: await reviewUpdate(rec, u.latest, { updateTrip }) });
    },
    laterUpdate() { store.set({ tripUpdate: null }); },  // offered again the next time the trip is opened
    async declineUpdate() {
      const s = store.get(), u = s.tripUpdate, rec = s.saved.find((t) => t.id === s.savedId);
      if (!rec || !u || (u.phase !== "offer" && u.phase !== "review")) return;
      await declineUpdate(rec, u.latest, { put: putTrip });
      await refreshSaved();
      store.set({ tripUpdate: null, status: "Kept your trip as it was." });
    },
    async acceptUpdate() {
      const s = store.get(), u = s.tripUpdate, rec = s.saved.find((t) => t.id === s.savedId);
      if (!rec || u?.phase !== "review") return;
      store.set({ tripUpdate: { id: rec.id, phase: "busy" }, error: null });
      try {
        const saved = await acceptUpdate(rec, u, {
          save: (run, n, name, campsM) => saveTrip(run, n, name, (p) => store.set({ saving: { id: tripId(run, n), ...p } }),
            new AbortController().signal, undefined, campsM),
          remove: deleteTrip,
        });
        await refreshSaved();
        store.set({ saving: null, tripUpdate: null });
        await actions.openSaved(saved.id);
        store.set({ status: "Updated to the newer trail data." });
      } catch (e) {
        store.set({ saving: null, tripUpdate: { id: rec.id, phase: "failed", message: `Couldn't save the updated trip: ${(e as Error).message} Your trip is unchanged.` } });
      }
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
    compare: (root) => { bindCompare(root, store, actions); bindChat(root, actions); },
    trip: (root) => { bindTrip(root, store, actions); bindChat(root, actions); },
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
      map?.clearCandidates(); map?.setJunctions(c, s.junctions);
      map?.setCampDrag(s.savedId && c?.plan ? (i, lon, lat) => void actions.moveCampTo(i, lon, lat) : null);
      map?.setTrip(c, evidenceHtml);
    } else {
      map?.setTrip(null); map?.setJunctions(null, false);
      map?.setCandidates(s.run.candidates, s.selected, (i) => actions.select(i));
    }
  }
  store.subscribe((s, prev) => {
    const changed = RENDER_KEYS.filter((k) => s[k] !== prev[k]);
    const formChanged = s.form !== prev.form && !editingInPanel;
    const live = changed.length > 0 && !formChanged && changed.every((k) => LIVE_KEYS.has(k));
    // GPS fixes and save progress arrive several times a second: patch them in place in the trip view and
    // don't touch other views, so buttons and form fields aren't replaced under the user's finger.
    if (live && (s.view !== "trip" || patchTrip(panel, prev, s))) { /* nothing else to redraw */ }
    else if (changed.length || formChanged) render();
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
  map.onMove((view, zoom) => { lastView = { view, zoom }; updateMapNote(); });
  render();
  void (async () => {
    await cleanOrphans().catch(() => []);
    await refreshSaved();
    const hasSaved = store.get().saved.length > 0;
    try {
      const { regions, recent_areas: recentAreas } = await getRegions(hasSaved ? BOOT_TIMEOUT_MS : undefined);
      store.set({ regions, recentAreas, offline: false });
      void refreshTripLevels();
      const saved = store.get().region;
      const known = (id: string) => regions.some((r) => r.id === id) || recentAreas.some((r) => r.id === id);
      const id = saved && known(saved) ? saved : regions[0]?.id;
      if (id) await actions.pickRegion(id);
      else store.set({ error: "No region has cached data yet. Run `backcountry ingest` and `backcountry dem` first." });
    } catch (e) {
      const unreachable = e instanceof ApiError && e.status === 0;
      if (unreachable && hasSaved) {
        store.set({ offline: true, view: "saved", status: "", error: null });
        // If the server answers later (slow signal), planning comes back without leaving the saved trips.
        getRegions().then(({ regions, recent_areas: recentAreas }) => { store.set({ regions, recentAreas, offline: false }); void refreshTripLevels(); })
          .catch(() => { /* still offline */ });
      }
      else store.set({ offline: unreachable, error: (e as Error).message });
    }
  })();

  return { store, actions, map };
}
