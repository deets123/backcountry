import { DEFAULT_FORM, type FormState } from "./request";
import type { Region, Run, Trailhead } from "./types";

export type View = "plan" | "compare" | "trip";
export type Basemap = "topo" | "hybrid" | "satellite";

export interface State {
  regions: Region[]; region: string | null; trailheads: Trailhead[]; form: FormState; run: Run | null; selected: number;
  view: View; status: string; error: string | null; busy: boolean; basemap: Basemap; sheet: "collapsed" | "half" | "full";
}

export const INITIAL: State = {
  regions: [], region: null, trailheads: [], form: DEFAULT_FORM, run: null, selected: 1, view: "plan", status: "",
  error: null, busy: false, basemap: "topo", sheet: "half",
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
