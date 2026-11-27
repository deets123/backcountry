// "Before you go": permits, fees, closures and rules, each quoted from its source with its date. Nothing here concludes
// anything; source text is always escaped and links are shown only when they are http(s).
import type { SavedTrip } from "../offline/db";
import type { Notice, NoticeKind, ParkNotices, Run, RunNotices } from "../types";
import { esc } from "./common";

const LABELS: Record<NoticeKind, string> = {
  closure: "Closed", permit: "Permit", ticket: "Ticket", camp: "Camping", fee: "Fee", rule: "Rule", caution: "Caution", info: "Notice", unknown: "",
};
const CHIP_KINDS: NoticeKind[] = ["permit", "ticket", "closure"];

export function noticeKey(n: Notice): string {
  return `${n.kind}|${n.text}`;
}

export function kindLabel(k: NoticeKind): string {
  return LABELS[k] ?? "";
}

function hhmm(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toTimeString().slice(0, 5);
}

function sourceLine(n: Notice): string {
  const s = n.source;
  if (!s?.name) return "";
  const when = hhmm(s.checked_at);
  return `${s.name}${s.as_of ? `, ${s.as_of}` : ""}${when ? `, checked ${when}` : ""}`;
}

function linksHtml(links: string[]): string {
  return links.filter((u) => /^https?:\/\//.test(u))
    .map((u) => ` <a href="${esc(u)}" target="_blank" rel="noopener">link</a>`).join("");
}

/** The block: one line per notice with what it applies to, its source and date; the unknown line stays plain. */
export function noticesHtml(notices: Notice[], newKeys?: Set<string>): string {
  if (!notices.length) return "";
  const items = notices.map((n) => {
    if (n.kind === "unknown") return `<li class="note">${esc(n.text)}</li>`;
    const attached = n.matched && n.applies_to ? `Attached to: ${esc(n.applies_to)}. ` : "";
    const fresh = newKeys?.has(noticeKey(n)) ? ` <span class="new">new since you saved</span>` : "";
    const label = kindLabel(n.kind);
    return `<li class="notice notice-${n.kind}${n.matched ? " matched" : ""}">${label ? `<b>${label}</b> ` : ""}${esc(n.text)}${linksHtml(n.links)}${fresh}`
      + `<span class="note">${attached}${esc(sourceLine(n))}</span></li>`;
  });
  return `<ul class="notices">${items.join("")}</ul>`;
}

/** One chip each for permit, ticket and closure when a notice of that kind is attached to this route. */
export function chipsHtml(notices: Notice[] | undefined): string {
  if (!notices) return "";
  return CHIP_KINDS.filter((k) => notices.some((n) => n.matched && n.kind === k))
    .map((k) => `<span class="chip chip-${k}">${k}</span>`).join("");
}

/** The plan screen's line under the area: how many closures and other notices the park lists, opening the list. */
export function parkLineHtml(p: ParkNotices | null, open: boolean): string {
  if (!p || !p.configured) return "";
  if (p.pending) return `<p class="status" id="park-notices-line">Checking park notices…</p>`;
  if (!p.park && !p.notices.length) return "";
  const closures = p.notices.filter((n) => n.kind === "closure").length;
  const others = p.notices.length - closures;
  const when = hhmm(p.checked_at);
  const checked = p.error || p.stale ? `could not refresh; last checked ${when || "earlier"}` : `checked ${when}`;
  const summary = `Park notices: ${closures} closure${closures === 1 ? "" : "s"}, ${others} more · National Park Service, ${checked}`;
  return `<details id="park-notices"${open ? " open" : ""}><summary>${esc(summary)}</summary>${noticesHtml(p.notices)}</details>`;
}

export interface RefreshDeps {
  getRunNotices(runId: string, n: number): Promise<RunNotices>;
  /** Stores the refreshed run with a saved trip (notices are a dated snapshot, not a change to the route). */
  put?(rec: SavedTrip): Promise<void>;
}

/** A trip's notices re-matched against the park's current data. Returns the run with the candidate's notices replaced
 * and `notices_pending` cleared, the keys of notices the saved copy lacked ("new since you saved"), and whether the
 * park's fetch is still pending. Snapshot runs ("partial") are never refreshed. */
export async function refreshNotices(run: Run, n: number, rec: SavedTrip | null, deps: RefreshDeps):
  Promise<{ run: Run; noticesNew: string[]; pending: boolean } | null> {
  if (run.run_id === "partial" || !run.candidates[n - 1]) return null;
  const r = await deps.getRunNotices(run.run_id, n);
  const updated: Run = { ...run, search: { ...run.search, notices_pending: r.pending },
    candidates: run.candidates.map((c, i) => (i === n - 1 ? { ...c, notices: r.notices } : c)) };
  const savedKeys = new Set((rec?.run.candidates[n - 1]?.notices ?? []).map(noticeKey));
  const noticesNew = rec ? r.notices.filter((x) => x.kind !== "unknown" && !savedKeys.has(noticeKey(x))).map(noticeKey) : [];
  if (rec && deps.put) await deps.put({ ...rec, run: updated });
  return { run: updated, noticesNew, pending: r.pending };
}
