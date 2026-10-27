// Newer trail data for a saved trip: offered, never applied on its own. Opening a saved trip online asks the server
// whether its area has newer data; the offer says roughly how much updating would download (so the user can wait for
// Wi-Fi), "Review changes" re-plans the same route on the new data and lists what changed, and only "Accept" replaces
// the saved trip and its offline copy. "Keep my trip" is remembered until an even newer version appears; "Not now"
// only hides the offer until the trip is next opened.
import type { SavedTrip } from "../offline/db";
import type { Run } from "../types";
import { esc } from "./common";

export type TripUpdate =
  | { id: string; phase: "offer"; latest: string; mb: number }
  | { id: string; phase: "busy" }
  | { id: string; phase: "review"; latest: string; run: Run; changes: string[] }
  | { id: string; phase: "failed"; message: string };

export interface UpdateCheck { newer: boolean; cut: string | null; latest: string; approx_bytes: number }
export type UpdateAnswer = { ok: true; run: Run; changes: string[] } | { ok: false; message: string };

/** About how much an update downloads: the trip's saved offline size (map tiles and data), at least its data. */
function megabytes(rec: SavedTrip, check: UpdateCheck): number {
  return Math.max(1, Math.ceil(Math.max(rec.bytes, check.approx_bytes) / 1e6));
}

export async function checkForUpdate(rec: SavedTrip, deps: { online(): boolean; updateCheck(runId: string, n: number): Promise<UpdateCheck> }):
  Promise<TripUpdate | null> {
  if (!deps.online()) return null;
  try {
    const check = await deps.updateCheck(rec.run.run_id, rec.index);
    if (!check.newer || rec.declinedCut === check.latest) return null;
    return { id: rec.id, phase: "offer", latest: check.latest, mb: megabytes(rec, check) };
  } catch { return null; }  // the server is unreachable or no longer has the area: no offer
}

export async function reviewUpdate(rec: SavedTrip, latest: string, deps: { updateTrip(runId: string, n: number): Promise<UpdateAnswer> }):
  Promise<TripUpdate> {
  try {
    const a = await deps.updateTrip(rec.run.run_id, rec.index);
    return a.ok ? { id: rec.id, phase: "review", latest, run: a.run, changes: a.changes } : { id: rec.id, phase: "failed", message: a.message };
  } catch (e) {
    return { id: rec.id, phase: "failed", message: `Couldn't check the newer data: ${(e as Error).message} Your trip is unchanged.` };
  }
}

export async function declineUpdate(rec: SavedTrip, latest: string, deps: { put(t: SavedTrip): Promise<void> }): Promise<void> {
  await deps.put({ ...rec, declinedCut: latest });
}

export interface AcceptDeps {
  save(run: Run, n: number, startName: string, campsM: number[] | null): Promise<SavedTrip>;
  remove(id: string): Promise<void>;
}

/** Save the re-planned trip for offline (keeping moved camps that still fit), then remove the old one. */
export async function acceptUpdate(rec: SavedTrip, review: Extract<TripUpdate, { phase: "review" }>, deps: AcceptDeps): Promise<SavedTrip> {
  const length = review.run.candidates[0]?.length_m ?? 0;
  const fits = rec.campsM !== null && rec.campsM.every((m) => m > 0 && m < length);
  const saved = await deps.save(review.run, 1, rec.startName, fits ? rec.campsM : null);
  await deps.remove(rec.id);
  return saved;
}

export function renderUpdate(u: TripUpdate): string {
  switch (u.phase) {
    case "offer":
      return `<div class="update" id="update"><p>Newer trail data for this area. Updating uses about ${u.mb} MB.</p>
<div class="row"><button type="button" id="update-review">Review changes</button><button type="button" class="link" id="update-later">Not now</button></div></div>`;
    case "busy":
      return `<div class="update" id="update"><p class="note">Checking the newer data…</p></div>`;
    case "review":
      return `<div class="update" id="update"><p>With the newer trail data:</p><ul>${u.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
<div class="row"><button type="button" class="primary" id="update-accept">Accept</button><button type="button" class="link" id="update-keep">Keep my trip</button></div></div>`;
    case "failed":
      return `<div class="update" id="update"><p class="note">${esc(u.message)}</p></div>`;
  }
}

export interface UpdateActions { reviewUpdate(): void; declineUpdate(): void; laterUpdate(): void; acceptUpdate(): void }

export function bindUpdate(root: HTMLElement, actions: UpdateActions): void {
  root.querySelector("#update-review")?.addEventListener("click", () => actions.reviewUpdate());
  root.querySelector("#update-later")?.addEventListener("click", () => actions.laterUpdate());
  root.querySelector("#update-keep")?.addEventListener("click", () => actions.declineUpdate());
  root.querySelector("#update-accept")?.addEventListener("click", () => actions.acceptUpdate());
}
