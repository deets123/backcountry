export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function notesHtml(lines: string[], cls = "note"): string {
  return lines.length ? `<ul class="${cls}">${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "";
}

export function knownGapsHtml(gaps: string[]): string {
  return `<div class="warn" id="known-gaps"><b>Not checked by this tool:</b> ${gaps.map(esc).join("; ")}.</div>`;
}
