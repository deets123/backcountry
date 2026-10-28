import { DEFAULT_FORM, type FormState } from "./request";
import type { ChatMessage, Destination, Finish, RecentArea, Region, Run, SearchResult, Trailhead } from "./types";
import type { Fix, Where } from "./gps";
import type { SavedTrip } from "./offline/db";
import type { SaveProgress } from "./offline/save";
import type { TripUpdate } from "./panels/update";

export type View = "plan" | "compare" | "trip" | "saved";
export type Basemap = "topo" | "hybrid" | "satellite";
export type Gps = { mode: "off" } | { mode: "waiting" } | { mode: "on"; fix: Fix; where: Where | null } | { mode: "error"; message: string };

export interface State {
  regions: Region[]; region: string | null; trailheads: Trailhead[]; form: FormState; run: Run | null; selected: number;
  view: View; status: string; error: string | null; busy: boolean; basemap: Basemap; sheet: "collapsed" | "half" | "full";
  saved: SavedTrip[]; savedId: string | null; campsM: number[] | null; gps: Gps; saving: (SaveProgress & { id: string }) | null;
  tilesMissing: boolean; junctions: boolean;
  offline: boolean; updateReady: boolean; mapNote: string | null;
  chat: ChatMessage[]; chatBusy: boolean; chatError: string | null;
  searchQuery: string; searchResults: SearchResult[] | null; recentAreas: RecentArea[];
  /** A search that failed: shown inside the results box, so the keyboard stays. */
  searchError: string | null;
  /** The open area's trails are shown but its elevation is still coming (planning waits). */
  elevationPending: boolean;
  /** The last area open failed: the plan panel offers to try it again. */
  canRetryOpen: boolean;
  /** A searched place every trip goes to and through (null: plan anywhere in the area). */
  destination: Destination | null;
  /** One way: finishing trailheads suggested for the current start and miles (null: not asked yet). */
  finishes: Finish[] | null;
  tripUpdate: TripUpdate | null;  // newer trail data offered for the open saved trip
}

export const INITIAL: State = {
  regions: [], region: null, trailheads: [], form: DEFAULT_FORM, run: null, selected: 1, view: "plan", status: "",
  error: null, busy: false, basemap: "topo", sheet: "half",
  saved: [], savedId: null, campsM: null, gps: { mode: "off" }, saving: null, tilesMissing: false, junctions: false, offline: false, updateReady: false, mapNote: null,
  chat: [], chatBusy: false, chatError: null,
  searchQuery: "", searchResults: null, searchError: null, recentAreas: [], tripUpdate: null, elevationPending: false, canRetryOpen: false,
  destination: null, finishes: null,
};

type Listener = (next: State, prev: State) => void;

export function createStore(initial: Partial<State> = {}) {
  let state: State = { ...INITIAL, ...initial };
  const listeners = new Set<Listener>();
  return {
    get: () => state,
    set(patch: Partial<State> | ((s: State) => Partial<State>)) {
      const prev = state;
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      listeners.forEach((fn) => fn(state, prev));
    },
    subscribe(fn: Listener) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
  };
}
export type Store = ReturnType<typeof createStore>;

const KEY = "backcountry.v1";

export function persist(store: Store, storage: Storage = localStorage): () => void {
  return store.subscribe((s) => {
    try { storage.setItem(KEY, JSON.stringify({ region: s.region, form: s.form, basemap: s.basemap })); } catch { /* storage may be unavailable */ }
  });
}

export function loadPersisted(storage: Storage = localStorage): Partial<State> {
  try {
    const raw = storage.getItem(KEY);
    if (!raw) return {};
    const saved = JSON.parse(raw) as Partial<State>;
    const out: Partial<State> = {};
    if (typeof saved.region === "string") out.region = saved.region;
    if (saved.form && typeof saved.form === "object") out.form = { ...DEFAULT_FORM, ...saved.form };
    if (saved.basemap === "topo" || saved.basemap === "hybrid" || saved.basemap === "satellite") out.basemap = saved.basemap;
    return out;
  } catch { return {}; }
}
