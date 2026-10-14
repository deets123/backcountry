import type { FormState } from "../request";
import type { State, Store } from "../store";
import type { Trailhead } from "../types";
import { esc } from "./common";

function option(th: Trailhead, current: string): string {
  const usable = th.in_graph && th.lat != null && th.lon != null;
  const label = `${th.name ?? th.id}${usable ? "" : " (not on the trail network)"}${th.source === "data" && usable ? " (from the data, unverified)" : ""}`;
  return `<option value="${esc(th.id)}"${usable ? "" : " disabled"}${th.id === current ? " selected" : ""}>${esc(label)}</option>`;
}

export function renderPlan(s: State): string {
  const f = s.form;
  const regions = s.regions.length > 1
    ? `<label>Region<select name="region">${s.regions.map((r) => `<option value="${esc(r.id)}"${r.id === s.region ? " selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>`
    : "";
  const yaml = s.trailheads.filter((t) => t.source === "yaml");
  const data = s.trailheads.filter((t) => t.source === "data");
  const custom = f.trailhead && !s.trailheads.some((t) => t.id === f.trailhead)
    ? `<option value="${esc(f.trailhead)}" selected>${esc(f.trailhead)} (map point)</option>` : "";
  const types = ["any", "loop", "lollipop"].map((t) => `<option value="${t}"${f.type === t ? " selected" : ""}>${t}</option>`).join("");
  return `
<h1>Plan a trip</h1>
${regions}
<label>Trailhead<select name="trailhead">
  <option value=""${f.trailhead ? "" : " selected"}>Pick one, or tap the map</option>
  ${custom}
  ${yaml.length ? `<optgroup label="Trailheads">${yaml.map((t) => option(t, f.trailhead)).join("")}</optgroup>` : ""}
  ${data.length ? `<optgroup label="Other start points from the data">${data.map((t) => option(t, f.trailhead)).join("")}</optgroup>` : ""}
</select></label>
<p class="note">Tap a TH badge or a grey start dot on the map, pick from the list, or tap anywhere on a trail to start there (snapped within 650 ft).</p>
<div class="row">
  <label>Nights<input name="nights" inputmode="numeric" value="${esc(f.nights)}" placeholder="2"></label>
  <label>Total miles<input name="total" value="${esc(f.total)}" placeholder="18-26"></label>
</div>
<div class="row">
  <label>Miles per day<input name="daily" value="${esc(f.daily)}" placeholder="6-11"></label>
  <label>Max gain per day (ft)<input name="maxGainDay" inputmode="numeric" value="${esc(f.maxGainDay)}" placeholder="2500"></label>
</div>
<div class="row">
  <label>Shape<select name="type">${types}</select></label>
  <label>Direction<select name="direction"><option value=""${f.direction ? "" : " selected"}>either</option><option value="cw"${f.direction === "cw" ? " selected" : ""}>clockwise</option><option value="ccw"${f.direction === "ccw" ? " selected" : ""}>counterclockwise</option></select></label>
</div>
<label><input type="checkbox" name="noRoads"${f.noRoads ? " checked" : ""}> No roads at all</label>
<label><input type="checkbox" name="campsAtSites"${f.campsAtSites ? " checked" : ""}> Camps only at known campsites</label>
<label>Must visit (trail names or trailhead ids, one per line)<textarea name="mustVisit" rows="2">${esc(f.mustVisit.join("\n"))}</textarea></label>
<label>Avoid (trail names, one per line)<textarea name="avoid" rows="2">${esc(f.avoid.join("\n"))}</textarea></label>
<label>Pin camps (miles in, or lat,lon; one per line)<textarea name="camps" rows="2">${esc(f.camps.join("\n"))}</textarea></label>
<button class="primary" id="generate"${s.busy ? " disabled" : ""}>${s.busy ? "Searching…" : "Generate loops"}</button>
<div class="status${s.error ? " error" : ""}" id="status">${esc([s.status, s.error].filter(Boolean).join(" "))}</div>
${s.run ? `<button class="link" id="back-to-compare">Back to the last results</button>` : ""}`;
}

/** Status line after a map tap: names the trail so the user can type it under must-visit or avoid. */
export function tapMessage(trailName: string | null): string {
  const on = trailName ? `on ${trailName}` : "on an unnamed trail or road";
  return `Start set to the tapped point ${on}. Type a trail name under "must visit" or "avoid" to use it.`;
}

export interface PlanActions { generate(): void; pickRegion(id: string): void; backToCompare(): void; editForm(form: FormState): void }

export function bindPlan(root: HTMLElement, store: Store, actions: PlanActions): void {
  const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("[name]").forEach((el) => {
    el.addEventListener("change", () => {
      const name = el.getAttribute("name")!;
      if (name === "region") return actions.pickRegion(el.value);
      const f = { ...store.get().form } as unknown as Record<string, unknown>;
      if (el instanceof HTMLInputElement && el.type === "checkbox") f[name] = el.checked;
      else if (name === "mustVisit" || name === "avoid" || name === "camps") f[name] = lines(el.value);
      else f[name] = el.value;
      actions.editForm(f as unknown as FormState);  // the app records it without re-rendering, so focus and the next tap survive
    });
  });
  root.querySelector("#generate")?.addEventListener("click", () => actions.generate());
  root.querySelector("#back-to-compare")?.addEventListener("click", () => actions.backToCompare());
}
