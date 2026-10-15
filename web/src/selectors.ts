// The trip as shown: the selected candidate, with the user's moved camps applied (memoized, since
// withCamps re-cuts the whole route and the trip view re-renders on every GPS fix).
import { withCamps } from "./resplit";
import type { State } from "./store";
import type { Candidate } from "./types";

let memo: { run: State["run"]; selected: number; campsM: number[]; out: Candidate } | null = null;

/** The id of the trip open in the trip view (`<run_id>-<n>`), or null. */
export function openTripId(s: State): string | null {
  return s.view === "trip" && s.run && s.run.candidates[s.selected - 1] ? `${s.run.run_id}-${s.selected}` : null;
}

export function currentCandidate(s: State): Candidate | null {
  const c = s.run?.candidates[s.selected - 1];
  if (!c) return null;
  if (!s.campsM || !c.plan) return c;
  if (memo && memo.run === s.run && memo.selected === s.selected && memo.campsM === s.campsM) return memo.out;
  let out: Candidate;
  try { out = withCamps(c, s.campsM); } catch { out = c; } // stored camps that don't fit this trip: show the engine's
  memo = { run: s.run, selected: s.selected, campsM: s.campsM, out };
  return out;
}
