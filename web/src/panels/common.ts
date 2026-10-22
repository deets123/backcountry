import type { Level } from "../types";

export function levelName(level: Level): string {
  return level[0]!.toUpperCase() + level.slice(1);
}

/** A level label: <span class="lvl lvl-hard">Hard</span>, or "Level unknown" when the data can't say. */
export function levelChip(level: Level | null): string {
  return level ? `<span class="lvl lvl-${level}">${levelName(level)}</span>` : `<span class="lvl">Level unknown</span>`;
}

export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function notesHtml(lines: string[], cls = "note"): string {
  return lines.length ? `<ul class="${cls}">${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "";
}

export function knownGapsHtml(gaps: string[]): string {
  return `<div class="warn" id="known-gaps"><b>Not checked by this tool:</b> ${gaps.map(esc).join("; ")}.</div>`;
}
