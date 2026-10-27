// "Where": search any US park, protected area, place or trail, or plan in the map area on screen. A new area is
// prepared quietly on the server behind one line ("Finding trails in …"); the user never sees the data work.
import { ApiError } from "../api";
import type { State } from "../store";
import type { AreaOpened, AreaStatus, Destination, RecentArea, Region, SearchResult } from "../types";
import { esc } from "./common";

export const POLL_MS = 2000;
export const GIVE_UP_MS = 10 * 60 * 1000;
const SLOW = "This is taking longer than expected. Try again in a few minutes.";

export type Target = SearchResult | { box: [number, number, number, number] };

function meta(r: { type_label: string | null; state: string | null }): string {
  return [r.type_label, r.state].filter(Boolean).join(" · ");
}

function where(s: State): string {
  const name = s.regions.find((r) => r.id === s.region)?.name ?? s.recentAreas.find((r) => r.id === s.region)?.name;
  return name ? `<p class="where">Planning in <b>${esc(name)}</b></p>` : "";
}

export function renderResults(s: State): string {
  if (!s.searchQuery.trim()) {
    if (!s.recentAreas.length) return "";
    return `<p class="note">Recent</p><ul class="results">${s.recentAreas.map((r, i) =>
      `<li><button type="button" class="result" data-recent="${i}"><span class="r-name">${esc(r.name)}</span>`
      + `<span class="r-meta">${esc(meta(r))}</span></button></li>`).join("")}</ul>`;
  }
  if (s.searchResults === null) return "";
  if (!s.searchResults.length) return `<p class="note">No places match “${esc(s.searchQuery.trim())}”.</p>`;
  return `<ul class="results">${s.searchResults.map((r, i) =>
    `<li><button type="button" class="result" data-result="${i}"><span class="r-name">${esc(r.name)}</span>`
    + `<span class="r-meta">${esc(meta(r))}</span></button></li>`).join("")}</ul>`;
}

/** Note: no `name` attributes here; the plan form binds every [name] element as a form field. */
export function renderSearch(s: State): string {
  return `<div id="search">
${where(s)}
<label>Where<input id="area-search" type="search" placeholder="Park, place or trail" autocomplete="off" value="${esc(s.searchQuery)}"></label>
<div id="search-results">${renderResults(s)}</div>
<button type="button" class="link" id="plan-here">Plan in this map area</button>
</div>`;
}

export interface SearchActions {
  /** Keep the typed text in state at once (no re-render), so a panel redraw before the search runs keeps it. */
  setQuery(q: string): void;
  searchAreas(q: string): Promise<void>;
  openTarget(t: Target): Promise<void>;
  openRecent(r: RecentArea): Promise<void>;
  planHere(): Promise<void>;
}

export function bindSearch(root: HTMLElement, get: () => State, actions: SearchActions, debounceMs = 250): void {
  const input = root.querySelector<HTMLInputElement>("#area-search");
  if (!input || !root.querySelector("#search-results")) return;
  let timer: ReturnType<typeof setTimeout> | null = null;
  // The list is looked up when results arrive: the panel may have been redrawn since the keystroke.
  const bindList = () => {
    const list = root.querySelector<HTMLElement>("#search-results");
    if (!list) return;
    list.querySelectorAll<HTMLElement>("[data-result]").forEach((b) => b.addEventListener("click", () => {
      const r = get().searchResults?.[Number(b.dataset.result)];
      if (r) void actions.openTarget(r);
    }));
    list.querySelectorAll<HTMLElement>("[data-recent]").forEach((b) => b.addEventListener("click", () => {
      const r = get().recentAreas[Number(b.dataset.recent)];
      if (r) void actions.openRecent(r);
    }));
  };
  input.addEventListener("input", () => {
    actions.setQuery(input.value);
    if (timer) clearTimeout(timer);
    timer = setTimeout(async () => {
      await actions.searchAreas(get().searchQuery);  // the latest text, even if typed in a redrawn box
      const list = root.querySelector<HTMLElement>("#search-results");
      if (list) list.innerHTML = renderResults(get());  // in place: re-rendering the panel would drop the keyboard
      bindList();
    }, debounceMs);
  });
  bindList();
  root.querySelector("#plan-here")?.addEventListener("click", () => void actions.planHere());
}

export interface WaitDeps {
  areaStatus(id: string): Promise<AreaStatus>; sleep(ms: number): Promise<void>; now?: () => number;
  /** Called once, the first time the trails are ready but elevation is still coming. */
  onTrails?(st: AreaStatus): Promise<void> | void;
}

/** Poll an area's status until it is ready, failed or unknown (the server restarted), or ten minutes pass. */
export async function waitForArea(id: string, deps: WaitDeps): Promise<AreaStatus> {
  const now = deps.now ?? (() => Date.now());
  const start = now();
  let trails = false;
  for (;;) {
    const st = await deps.areaStatus(id);
    if (st.status === "ready" || st.status === "failed" || st.status === "unknown") return st;
    if (st.status === "trails" && !trails) {
      trails = true;
      await deps.onTrails?.(st);
    }
    if (now() - start >= GIVE_UP_MS) return { ...st, status: "failed", message: SLOW };
    await deps.sleep(POLL_MS);
  }
}

export interface OpenDeps extends WaitDeps {
  openArea(body: { ref: string } | { box: [number, number, number, number] }): Promise<AreaOpened>;
  pickRegion(id: string, bbox?: Region["bbox"]): Promise<void>;
  setStatus(message: string): void;
  setError(message: string): void;
  addRecent(a: RecentArea): void;
  /** Trails are on the map but elevation is still coming: planning waits for it. */
  setElevationPending(on: boolean): void;
  /** A searched place becomes where every trip goes; an area or region clears it. */
  setDestination(d: Destination | null): void;
}

/** Open a search result or map box: hand-made regions directly; anything else is prepared first. */
export async function openSearchResult(t: Target, deps: OpenDeps): Promise<void> {
  deps.setDestination("kind" in t && t.kind === "place" ? { ref: t.ref, name: t.name, center: t.center } : null);
  if ("kind" in t && t.kind === "region") return deps.pickRegion(t.id);
  try {
    const opened = await deps.openArea("box" in t ? { box: t.box } : { ref: t.ref });
    if (opened.status !== "ready") deps.setStatus(opened.message);
    let shown = false;  // the trails are on the map already (elevation was still coming)
    const showTrails = async (bbox: Region["bbox"]) => {
      shown = true;
      await deps.pickRegion(opened.area_id, bbox);
      deps.setElevationPending(true);
    };
    if (opened.status === "trails") await showTrails(opened.bbox);
    const st = opened.status === "ready" ? { status: "ready" as const, message: null }
      : await waitForArea(opened.area_id, { ...deps, onTrails: (s) => (shown ? undefined : showTrails(s.bbox ?? opened.bbox)) });
    if (st.status !== "ready") {
      return deps.setError(st.status === "unknown" ? "The server restarted while setting up that area. Try again." : (st.message ?? SLOW));
    }
    const bbox = st.bbox ?? opened.bbox;
    if (shown) deps.setElevationPending(false);
    deps.addRecent({ id: opened.area_id, name: st.name ?? opened.name, type_label: "kind" in t ? t.type_label : "Area",
      state: "kind" in t ? t.state : null, bbox });
    if (!shown) await deps.pickRegion(opened.area_id, bbox);
  } catch (e) {
    deps.setError(e instanceof ApiError ? e.message : (e as Error).message);
  }
}
