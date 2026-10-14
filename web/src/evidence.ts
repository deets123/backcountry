import { feet, FT_PER_M, M_PER_MI } from "./format";
import { esc } from "./panels/common";
import type { RouteFeature } from "./types";

const LABELS: Record<string, string> = { camp_site: "Campsite", camp_pitch: "Tent pitch", spring: "Spring", drinking_water: "Drinking water",
  water: "Lake/pond", "stream crossing": "Stream crossing", "river crossing": "River crossing" };
const HIDDEN = new Set(["name", "tourism", "natural", "waterway", "amenity"]);

export function kindLabel(kind: string): string {
  if (LABELS[kind]) return LABELS[kind]!;
  if (kind.startsWith("usfs")) return "USFS " + kind.slice(5).trim().replace(/_/g, " ").toLowerCase();
  return kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function featureTitle(f: { kind: string; name: string | null }): string {
  return kindLabel(f.kind) + (f.name ? `: ${f.name}` : "");
}

export function evidenceHtml(f: RouteFeature): string {
  const when = (f.meta?.timestamp ?? "").slice(0, 10);
  const tags = Object.entries(f.tags ?? {}).filter(([k]) => !HIDDEN.has(k)).sort().map(([k, v]) => `${k}=${v}`).join("; ") || "no other tags";
  const edit = when ? `last edited ${when}, version ${f.meta?.version}` : "edit history unknown";
  const src = f.url ? "OpenStreetMap" : f.source.startsWith("usfs") ? "USFS" : "source on file";
  const links = [`<a href="https://www.google.com/maps/@${f.lat.toFixed(6)},${f.lon.toFixed(6)},300m/data=!3m1!1e3" target="_blank" rel="noopener">satellite view</a>`];
  if (f.url) links.push(`<a href="${esc(f.url)}" target="_blank" rel="noopener">OpenStreetMap entry</a>`, `<a href="${esc(f.url)}/history" target="_blank" rel="noopener">edit history</a>`);
  return `<b>${esc(featureTitle(f))}</b><br>mile ${(f.dist_m / M_PER_MI).toFixed(1)}, ${feet(f.off_m * FT_PER_M)} off route<br>details: ${esc(tags)}<br>${src}: ${esc(edit)}<br>${links.join(" · ")}`
    + `<br><i>Existence, status, and (for water) presence are unknown. Check before relying on it.</i>`;
}
