import { miles, shortDate } from "../format";
import { megabytes } from "../offline/tiles";
import type { State } from "../store";
import { esc, kindName } from "./common";

export function renderSaved(s: State): string {
  const status = [s.status, s.error].filter(Boolean).join(" ");
  const head = `<h1>Saved trips</h1>${s.offline ? `<p class="warn" id="offline-note">Offline: showing saved trips. Planning needs the server.</p>` : ""}`
    + (status ? `<p class="status${s.error ? " error" : ""}" id="status">${esc(status)}</p>` : "");
  const plan = s.offline ? "" : `<button class="link" id="to-plan">Plan a trip</button>`;
  if (!s.saved.length) return `${head}<p class="note">No saved trips yet. Open a trip and tap Save for offline.</p>${plan}`;
  const total = s.saved.reduce((sum, t) => sum + t.bytes, 0);
  const cards = s.saved.map((t) => {
    const c = t.run.candidates[t.index - 1];
    return `<div class="card saved-trip"><b>${esc(t.startName)}: ${esc(c ? kindName(c.kind) : "trip")}, ${miles(c?.length_m)}</b>
<div class="note">Saved ${shortDate(t.savedAt)} · ${megabytes(t.bytes)} · ${t.complete ? "complete" : "<b>incomplete</b>"} · ${t.persisted ? "storage kept" : "may be cleared if the phone runs low on storage"}${t.campsM ? " · camps moved by you" : ""}</div>
<div class="row"><button class="primary" data-open="${esc(t.id)}">Open</button><button class="link" data-delete="${esc(t.id)}">Delete</button></div></div>`;
  }).join("");
  return `${head}<p class="note">Map tiles for saved trips: ${megabytes(total)}.</p><div class="saved-list">${cards}</div>${plan}`;
}

export interface SavedActions { openSaved(id: string): void; deleteSaved(id: string): void; toPlan(): void }

export function bindSaved(root: HTMLElement, actions: SavedActions): void {
  root.querySelectorAll<HTMLElement>("[data-open]").forEach((b) => b.addEventListener("click", () => actions.openSaved(b.dataset.open!)));
  root.querySelectorAll<HTMLElement>("[data-delete]").forEach((b) => b.addEventListener("click", () => actions.deleteSaved(b.dataset.delete!)));
  root.querySelector("#to-plan")?.addEventListener("click", () => actions.toPlan());
}
