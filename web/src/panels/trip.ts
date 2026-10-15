import { geojsonUrl, gpxUrl } from "../api";
import { dayColor } from "../basemaps";
import { featureTitle } from "../evidence";
import { feet, FT_PER_M, M_PER_MI, miles, shortDate, signedFeet } from "../format";
import { whereText } from "../gps";
import type { SavedTrip } from "../offline/db";
import { estimate, megabytes, tileList, tripBox } from "../offline/tiles";
import { profileSvg } from "../profile";
import { currentCandidate } from "../selectors";
import type { State, Store } from "../store";
import type { Candidate, Run } from "../types";
import { esc, knownGapsHtml, notesHtml } from "./common";
import { segmentNames } from "./compare";

const NEAR = feet(500 * FT_PER_M);

function daysHtml(c: Candidate, editable: boolean): string {
  const p = c.plan;
  if (!p) return `<p class="note">Days weren't planned (no nights given). Change the settings to split this loop into days.</p>`;
  const rows = p.days.map((d) => `<tr><td><span class="swatch" style="background:${dayColor(d.index)}"></span>Day ${d.index}</td><td>${esc(d.start_label)} → ${esc(d.end_label)}</td><td>${miles(d.length_m)}</td><td>${feet(d.gain_ft)}</td><td>${feet(d.loss_ft)}</td></tr>`).join("");
  const moved = p.camps.some((cp) => cp.moved);
  const camps = p.camps.map((cp) => {
    const how = cp.moved ? "moved by you" : cp.pinned ? "pinned by you" : cp.site ? "at a known campsite" : "placed for mileage";
    const water = cp.nearest_water
      ? `nearest water: ${featureTitle(cp.nearest_water).toLowerCase()}, ${feet(cp.nearest_water.distance_m * FT_PER_M)} away (presence unknown)`
      : "nearest water: none listed";
    const where = cp.moved ? "listed along the route " : "";
    const near = cp.nearby.length ? `${cp.nearby.length} known campsite${cp.nearby.length === 1 ? "" : "s"} ${where}within ${NEAR}` : `no known campsite ${where}within ${NEAR}`;
    return `<li><b>Camp ${cp.index}</b> at ${cp.lat.toFixed(5)}, ${cp.lon.toFixed(5)}, ${miles(cp.dist_m)} in, on ${esc(cp.on_name ?? "an unnamed trail")}, ${how}. Location unverified. ${near}. ${esc(water)}.</li>`;
  }).join("");
  const edit = editable && p.camps.length
    ? `<p class="note">Drag a camp marker along the route to move it, or tap Camp here at your location.</p>${moved ? `<button class="link" id="reset-camps">Reset camps to the engine's plan</button>` : ""}`
    : "";
  return `${notesHtml(p.notes)}${notesHtml(p.violations.map((v) => `Constraint not met: ${v}`), "note error")}
<table><thead><tr><th>day</th><th>from → to</th><th>distance</th><th>gain</th><th>loss</th></tr></thead><tbody>${rows}</tbody></table>
<h2>Camps</h2><p class="note">Camps are points on the route chosen for mileage, not known campsites. Legality and water: unknown.</p>${edit}<ul>${camps}</ul>`;
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

function savedDownloadsHtml(t: SavedTrip, moved: boolean): string {
  const link = (which: string, text: string, ok: boolean) => ok ? `<a class="link" href="#" data-gpx="${which}">${text}</a>` : `${text} (not saved)`;
  const days = t.gpx.days.map((g, i) => link(String(i + 1), `day ${i + 1}`, g !== null)).join(" · ");
  return `<p>${link("whole", "Download GPX (whole trip)", t.gpx.whole !== null)}${days ? ` · GPX for ${days}` : ""}</p>`
    + (moved ? `<p class="note">These GPX files were saved with the trip: they show the original camps, not the ones you moved.</p>` : "");
}

function savedLineHtml(t: SavedTrip, run: Run): string {
  const osm = run.data_sources.find((d) => d.name.startsWith("OpenStreetMap"))?.retrieved_at ?? null;
  return `<p class="note" id="saved-line">Saved for offline ${shortDate(t.savedAt)} · trail data retrieved ${shortDate(osm)} · map tiles from USGS The National Map, ${shortDate(t.savedAt)} · ${megabytes(t.bytes)}${t.persisted ? "" : " · the browser may clear it if the phone runs low on storage"}</p>`
    + (t.complete ? "" : `<div class="warn" id="incomplete"><b>Incomplete:</b> ${t.problems.map(esc).join(" ")}</div>`);
}

function gpsHtml(s: State, c: Candidate, saved: SavedTrip | null): string {
  const g = s.gps;
  const line = g.mode === "waiting" ? "Finding your location…" : g.mode === "error" ? `Location unavailable: ${g.message}`
    : g.mode === "on" && g.where ? whereText(g.where, c.length_m) : "";
  const canCamp = g.mode === "on" && g.where?.onRoute === true;
  const campBtn = saved && c.plan?.camps.length ? `<button class="primary" id="camp-here"${canCamp ? "" : " disabled"}>Camp here</button>` : "";
  return `<div class="row"><button class="primary" id="locate">${g.mode === "off" ? "Show my location" : "Stop showing my location"}</button>${campBtn}</div>
<p class="status${g.mode === "error" ? " error" : ""}" id="gps-line">${esc(line)}</p>`;
}

function saveHtml(s: State, c: Candidate, saved: SavedTrip | null): string {
  if (saved) return "";
  if (s.saving) {
    return `<p class="status" id="save-progress">Saving map tiles: ${s.saving.done} / ${s.saving.total}${s.saving.failed ? ` · ${s.saving.failed} failed` : ""} <button class="link" id="save-cancel">Cancel</button></p>`;
  }
  const box = tripBox(c);
  if (s.offline || !box) return "";
  const est = estimate(tileList(box));
  return `<button class="primary" id="save-offline">Save for offline (about ${est.count} map tiles, ${megabytes(est.bytes)})</button>`;
}

export function renderTrip(s: State): string {
  const run = s.run;
  const c = currentCandidate(s);
  if (!run || !c) return `<h1>Trip</h1><p class="note">Pick a candidate first.</p><button class="link" id="back-compare">Back</button>`;
  const saved = s.savedId ? s.saved.find((t) => t.id === s.savedId) ?? null : null;
  const sources = run.data_sources.map((d) => `${esc(d.name)} (retrieved ${esc(d.retrieved_at ?? "unknown")})`).join("; ");
  const hasJunctions = c.waypoints.some((w) => w.kind === "junction");
  const where = s.gps.mode === "on" ? s.gps.where : null;
  const status = [s.status, s.error].filter(Boolean).join(" ");
  return `
<h1>Candidate ${c.index}: ${esc(c.kind)}, ${miles(c.length_m)}</h1>
${saved ? savedLineHtml(saved, run) : ""}
<p>${signedFeet(c.gain_ft, c.loss_ft)} · road ${miles(c.road_m)}${c.repeated_m ? ` · walked twice ${miles(c.repeated_m)}` : ""}</p>
<p class="note">${esc(segmentNames(c).join(" › "))}</p>
${notesHtml(c.notes)}
${gpsHtml(s, c, saved)}
${status ? `<p class="status${s.error ? " error" : ""}" id="status">${esc(status)}</p>` : ""}
${s.mapNote ? `<p class="note" id="map-note">${esc(s.mapNote)}</p>` : ""}
${hasJunctions ? `<label><input type="checkbox" id="junctions"> Show trail junctions on the map</label>` : ""}
<h2>Days</h2>${daysHtml(c, saved !== null)}
<h2>Elevation</h2>${profileSvg(c.profile, c.plan?.days ?? null, 600, 140, where && where.onRoute ? where.alongM : null)}
<h2>Campsites and water along the route</h2>${featuresHtml(c)}
<h2>Files</h2>${saved ? savedDownloadsHtml(saved, s.campsM !== null) : downloadsHtml(run, c)}
${saveHtml(s, c, saved)}
<div class="row"><button class="primary" id="replan"${s.offline ? " disabled" : ""}>Re-plan with these settings</button></div>
${s.offline ? "" : `<button class="link" id="back-compare">Back to all candidates</button>`}
${s.saved.length ? `<button class="link" id="back-saved">Saved trips</button>` : ""}
<p class="note">Data: ${sources}.</p>
${knownGapsHtml(run.known_gaps)}`;
}

export interface TripActions {
  backToCompare(): void; replan(): void; showJunctions(show: boolean): void; save(): void; cancelSave(): void;
  toggleGps(): void; campHere(): void; resetCamps(): void; downloadSaved(which: string): void; showSaved(): void;
}

export function bindTrip(root: HTMLElement, _store: Store, actions: TripActions): void {
  const on = (sel: string, fn: () => void) => root.querySelector(sel)?.addEventListener("click", fn);
  on("#back-compare", () => actions.backToCompare());
  on("#back-saved", () => actions.showSaved());
  on("#replan", () => actions.replan());
  on("#save-offline", () => actions.save());
  on("#save-cancel", () => actions.cancelSave());
  on("#locate", () => actions.toggleGps());
  on("#camp-here", () => actions.campHere());
  on("#reset-camps", () => actions.resetCamps());
  root.querySelector<HTMLInputElement>("#junctions")?.addEventListener("change", (e) => actions.showJunctions((e.target as HTMLInputElement).checked));
  root.querySelectorAll<HTMLAnchorElement>("[data-gpx]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); actions.downloadSaved(a.dataset.gpx!); }));
}
