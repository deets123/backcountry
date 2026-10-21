import { candidateColor } from "../basemaps";
import { miles, signedFeet } from "../format";
import type { State, Store } from "../store";
import type { Candidate, Trailhead } from "../types";
import { renderChat } from "./chat";
import { esc, notesHtml } from "./common";

export function segmentNames(c: Candidate): string[] {
  const out: string[] = [];
  for (const s of c.segments) if (out[out.length - 1] !== s.label) out.push(s.label);
  return out;
}

export function cardHtml(c: Candidate, selected: boolean): string {
  const days = c.plan ? (c.plan.ok ? "days: OK" : "days: NOT all limits met") : "days: not planned";
  return `<div class="card${selected ? " selected" : ""}" data-index="${c.index}">
  <div><span class="swatch" style="background:${candidateColor(c.index)}"></span><b>Candidate ${c.index}</b>: ${esc(c.kind)}</div>
  <div>${miles(c.length_m)}, ${signedFeet(c.gain_ft, c.loss_ft)}</div>
  <div>road ${miles(c.road_m)}${c.repeated_m ? `, walked twice ${miles(c.repeated_m)}` : ""} · ${days} · penalty ${c.penalty.toFixed(2)}</div>
  <div class="note">${esc(segmentNames(c).join(" › "))}</div>
</div>`;
}

/** The start as the user knows it: a trailhead's name, "Map point lat,lon", or whatever they typed. */
export function startLabel(trailhead: string, trailheads: Trailhead[]): string {
  const th = trailheads.find((t) => t.id === trailhead);
  if (th) return th.name ?? th.id;
  return /^-?\d+(\.\d+)?,-?\d+(\.\d+)?$/.test(trailhead) ? `Map point ${trailhead}` : trailhead;
}

export function renderCompare(s: State): string {
  const run = s.run;
  if (!run) return `<h1>Results</h1><p class="note">Nothing generated yet.</p><button class="link" id="replan">Back to the plan</button>`;
  const r = run.request;
  const total = Array.isArray(r.total) ? `${r.total[0]}–${r.total[1]}` : r.total;
  const start = startLabel(r.trailhead, s.trailheads);
  const lines = [...run.search.notes, ...run.search.must_visit_notes];
  if (run.search.widened) lines.push("Nothing fit the requested range, so the search looked 25% wider; the distance deviation is included in each penalty.");
  if (run.search.truncated) lines.push("The search hit its time budget, so some loops were never examined.");
  return `
<h1>${esc(start)}, ${esc(total)} mi</h1>
<p class="note">${run.search.scored} loops scored from ${run.search.via_pairs_tried.toLocaleString("en-US")} waypoint pairs in ${run.search.seconds.toFixed(1)} s. Tap a card or a line on the map to focus it.</p>
${notesHtml(lines)}
${run.candidates.length ? `<div class="cards">${run.candidates.map((c) => cardHtml(c, c.index === s.selected)).join("")}</div>` : `<p class="note">No candidates. Change the settings and try again.</p>`}
${run.candidates.length ? `<button class="primary" id="open-trip">Open candidate ${s.selected}</button>` : ""}
${run.diff ? `<details><summary>What changed since last time</summary><pre class="note" style="white-space:pre-wrap">${esc(run.diff)}</pre></details>` : ""}
${renderChat(s)}
<button class="link" id="replan">Change the settings</button>`;
}

export interface CompareActions { select(i: number): void; openTrip(i: number): void; replan(): void }

export function bindCompare(root: HTMLElement, store: Store, actions: CompareActions): void {
  root.querySelectorAll<HTMLElement>(".card").forEach((el) => el.addEventListener("click", () => actions.select(Number(el.dataset.index))));
  root.querySelector("#open-trip")?.addEventListener("click", () => actions.openTrip(store.get().selected));
  root.querySelector("#replan")?.addEventListener("click", () => actions.replan());
}
