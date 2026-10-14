import { geojsonUrl, gpxUrl } from "../api";
import { dayColor } from "../basemaps";
import { featureTitle } from "../evidence";
import { feet, FT_PER_M, M_PER_MI, miles, signedFeet } from "../format";
import { profileSvg } from "../profile";
import type { State, Store } from "../store";
import type { Candidate, Run } from "../types";
import { esc, knownGapsHtml, notesHtml } from "./common";
import { segmentNames } from "./compare";

function daysHtml(c: Candidate): string {
  const p = c.plan;
  if (!p) return `<p class="note">Days weren't planned (no nights given). Change the settings to split this loop into days.</p>`;
  const rows = p.days.map((d) => `<tr><td><span class="swatch" style="background:${dayColor(d.index)}"></span>Day ${d.index}</td><td>${esc(d.start_label)} → ${esc(d.end_label)}</td><td>${miles(d.length_m)}</td><td>${feet(d.gain_ft)}</td><td>${feet(d.loss_ft)}</td></tr>`).join("");
  const camps = p.camps.map((cp) => {
    const how = cp.pinned ? "pinned by you" : cp.site ? "at a known campsite" : "placed for mileage";
    const water = cp.nearest_water
      ? `nearest water: ${featureTitle(cp.nearest_water).toLowerCase()}, ${feet(cp.nearest_water.distance_m * FT_PER_M)} away (presence unknown)`
      : "nearest water: none listed";
    const near = cp.nearby.length ? `${cp.nearby.length} known campsite${cp.nearby.length === 1 ? "" : "s"} within ${feet(500 * FT_PER_M)}` : `no known campsite within ${feet(500 * FT_PER_M)}`;
    return `<li><b>Camp ${cp.index}</b> at ${cp.lat.toFixed(5)}, ${cp.lon.toFixed(5)}, ${miles(cp.dist_m)} in, on ${esc(cp.on_name ?? "an unnamed trail")}, ${how}. Location unverified. ${near}. ${esc(water)}.</li>`;
  }).join("");
  return `${notesHtml(p.notes)}${notesHtml(p.violations.map((v) => `Constraint not met: ${v}`), "note error")}
<table><thead><tr><th>day</th><th>from → to</th><th>distance</th><th>gain</th><th>loss</th></tr></thead><tbody>${rows}</tbody></table>
<h2>Camps</h2><p class="note">Camps are points on the route chosen for mileage, not known campsites. Legality and water: unknown.</p><ul>${camps}</ul>`;
}

function featuresHtml(c: Candidate): string {
  if (!c.features.length) return `<p class="note">No campsites or water features in the data within 492 ft of this route.</p>`;
  const rows = c.features.map((f) => `<tr><td>${(f.dist_m / M_PER_MI).toFixed(1)}</td><td>${f.day ?? "—"}</td><td>${esc(featureTitle(f))}</td><td>${feet(f.off_m * FT_PER_M)}</td><td>${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">OSM</a>` : f.source.startsWith("usfs") ? "USFS" : "—"}</td></tr>`).join("");
  return `<p class="warn">Water features come from OpenStreetMap. Whether water is actually there, flowing, or drinkable is unknown; this tool has not checked. Tap a dot on the map for details and a satellite view.</p>
<table><thead><tr><th>mile</th><th>day</th><th>feature</th><th>off route</th><th>source</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function downloadsHtml(run: Run, c: Candidate): string {
  const days = c.plan ? c.plan.days.map((d) => `<a class="link" href="${gpxUrl(run.run_id, c.index, d.index)}" download>day ${d.index}</a>`).join(" · ") : "";
  return `<p><a class="link" href="${gpxUrl(run.run_id, c.index)}" download>Download GPX (whole trip)</a>${days ? ` · GPX for ${days}` : ""} · <a class="link" href="${geojsonUrl(run.run_id, c.index)}" download>GeoJSON</a></p>`;
}

export function renderTrip(s: State): string {
  const run = s.run;
  const c = run?.candidates[s.selected - 1];
  if (!run || !c) return `<h1>Trip</h1><p class="note">Pick a candidate first.</p><button class="link" id="back-compare">Back</button>`;
  const sources = run.data_sources.map((d) => `${esc(d.name)} (retrieved ${esc(d.retrieved_at ?? "unknown")})`).join("; ");
  const hasJunctions = c.waypoints.some((w) => w.kind === "junction");
  return `
<h1>Candidate ${c.index}: ${esc(c.kind)}, ${miles(c.length_m)}</h1>
<p>${signedFeet(c.gain_ft, c.loss_ft)} · road ${miles(c.road_m)}${c.repeated_m ? ` · walked twice ${miles(c.repeated_m)}` : ""}</p>
<p class="note">${esc(segmentNames(c).join(" › "))}</p>
${notesHtml(c.notes)}
${hasJunctions ? `<label><input type="checkbox" id="junctions"> Show trail junctions on the map</label>` : ""}
<h2>Days</h2>${daysHtml(c)}
<h2>Elevation</h2>${profileSvg(c.profile, c.plan?.days ?? null)}
<h2>Campsites and water along the route</h2>${featuresHtml(c)}
<h2>Files</h2>${downloadsHtml(run, c)}
<div class="row"><button class="primary" id="replan">Re-plan with these settings</button><button class="primary" disabled title="Coming in a later piece">Save for offline (coming)</button></div>
<button class="link" id="back-compare">Back to all candidates</button>
<p class="note">Data: ${sources}.</p>
${knownGapsHtml(run.known_gaps)}`;
}

export interface TripActions { backToCompare(): void; replan(): void; showJunctions(show: boolean): void }

export function bindTrip(root: HTMLElement, store: Store, actions: TripActions): void {
  root.querySelector("#back-compare")?.addEventListener("click", () => actions.backToCompare());
  root.querySelector("#replan")?.addEventListener("click", () => actions.replan());
  root.querySelector<HTMLInputElement>("#junctions")?.addEventListener("change", (e) => actions.showJunctions((e.target as HTMLInputElement).checked));
}
