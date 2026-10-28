import { type DayNumber, type FormDay, type FormState, LEVELS, dayTargets, daysFor, isCustom, pickLevel, setDayNumber } from "../request";
import type { State, Store } from "../store";
import { feet, miles } from "../format";
import type { Finish, Level, Trailhead, TripType } from "../types";
import { esc, levelName } from "./common";
import { bindSearch, renderSearch, type SearchActions } from "./search";

const TYPES: [TripType, string][] = [["any", "Any"], ["loop", "Loop"], ["lollipop", "Lollipop"], ["out_and_back", "Out and back"],
  ["one_way", "One way"]];
const M_PER_MI = 1609.344;

/** The form's total miles as [low, high], or null while it doesn't parse. */
function totalOf(f: FormState): [number, number] | null {
  const [lo, hi] = f.total.split("-").map((x) => Number(x.trim()));
  return lo !== undefined && hi !== undefined && Number.isFinite(lo) && Number.isFinite(hi) && 0 < lo && lo < hi ? [lo, hi] : null;
}

/** With a destination: the starts that can reach it within the trip (half the longest for trips that come back,
 * the whole of it one way), nearest first; every reachable start while the miles don't parse. */
export function startsInReach(s: State): Trailhead[] {
  const f = s.form;
  const total = totalOf(f);
  const limit = total ? total[1] * M_PER_MI * (f.type === "one_way" ? 1 : 0.5) : Infinity;
  return s.trailheads.filter((t) => t.to_destination_m != null && t.to_destination_m <= limit)
    .sort((a, b) => a.to_destination_m! - b.to_destination_m!);
}

function finishesHtml(f: FormState, finishes: Finish[] | null): string {
  if (f.type !== "one_way") return "";
  if (!f.trailhead) return `<p class="note">Pick a start to see where a one-way trip can finish.</p>`;
  if (finishes === null) return `<p class="status">Finding where a one-way trip can finish…</p>`;
  const total = totalOf(f);
  if (!finishes.length) return `<p class="note">No finishing trailhead fits ${total ? `${total[0]}–${total[1]}` : esc(f.total)} mi from this start.</p>`;
  return `<p class="note">Finish at (pick one):</p><div class="finishes">${finishes.map((x) =>
    `<button type="button" class="finish${f.finish === x.id ? " on" : ""}" data-finish="${esc(x.id)}">`
    + `<b>${esc(x.name ?? "Unnamed trailhead")}</b> · ${miles(x.length_m)} · gain ${feet(x.gain_ft)}, loss ${feet(x.loss_ft)} · `
    + `${(x.straight_line_m / M_PER_MI).toFixed(1)} mi apart in a straight line${x.source === "data" ? " (from the data, unverified)" : ""}</button>`).join("")}</div>`;
}

function nightsOf(f: FormState): number | null {
  const n = Number(f.nights.trim());
  return f.nights.trim() && Number.isInteger(n) && n >= 1 ? n : null;
}

function dayLabel(d: FormDay): string {
  if (!d.level) return isCustom(d) ? "Custom" : "Any";
  return levelName(d.level) + (isCustom(d) ? ", custom" : "");
}

function tripDaily(f: FormState): [number, number] | null {
  const [lo, hi] = f.daily.split("-").map((x) => Number(x.trim()));
  return lo !== undefined && hi !== undefined && Number.isFinite(lo) && Number.isFinite(hi) && lo < hi ? [lo, hi] : null;
}

function dayEditor(d: FormDay, i: number, f: FormState): string {
  const t = dayTargets(d, "backpacking");
  const daily = tripDaily(f);
  const hint = (v: number | null, fallback: string) => (v == null ? fallback : String(v));
  const row = d.level ? dayTargets({ ...d, miles: "", maxGainFt: "", maxGradePct: "" }, "backpacking") : null;
  const milesHint = row?.miles ? `${row.miles[0]}-${row.miles[1]}` : daily ? `${daily[0]}-${daily[1]}` : "6-11";
  const buttons = LEVELS.map((l) => `<button type="button" class="lvl lvl-${l}${d.level === l ? " on" : ""}" data-level="${l}">${levelName(l)}</button>`).join("");
  return `<div class="day-editor" data-day="${i}">
  <div class="row levels">${buttons}</div>
  <div class="row">
    <label>Miles<input data-day-field="miles" value="${esc(d.miles)}" placeholder="${esc(milesHint)}"></label>
    <label>Max climbing (ft)<input data-day-field="maxGainFt" inputmode="numeric" value="${esc(d.maxGainFt)}" placeholder="${esc(hint(row?.max_gain_ft ?? (f.maxGainDay ? Number(f.maxGainDay) : null), "none"))}"></label>
    <label>Max grade (%)<input data-day-field="maxGradePct" inputmode="numeric" value="${esc(d.maxGradePct)}" placeholder="${esc(hint(row?.max_grade_pct ?? null, "none"))}"></label>
  </div>
  <p class="note">${t.miles ? "" : "Blank numbers use the trip's settings. "}A level fills in its numbers; type a number to change one.</p>
</div>`;
}

/** The "Set days individually" switch and, when on, the day strip: one block per day sized by its target miles. */
export function renderPerDay(f: FormState, open: number | null): string {
  const nights = f.dayHike ? null : nightsOf(f);
  if (nights === null) return "";
  const sw = `<label><input type="checkbox" name="perDay"${f.perDay ? " checked" : ""}> Set days individually</label>`;
  if (!f.perDay) return sw;
  const daily = tripDaily(f);
  const days = daysFor(f, nights);
  const blocks = days.map((d, i) => {
    const m = dayTargets(d, "backpacking").miles ?? daily;
    const grow = m ? (m[0] + m[1]) / 2 : 1;
    return `<button type="button" class="day-block${d.level ? ` lvl-${d.level}` : ""}${open === i ? " open" : ""}" data-day="${i}" style="flex-grow:${grow}">D${i + 1} <span>${esc(dayLabel(d))}</span></button>`;
  }).join("");
  return `${sw}<div class="day-strip">${blocks}</div>${open !== null && open < days.length ? dayEditor(days[open]!, open, f) : ""}`;
}

function option(th: Trailhead, current: string): string {
  const usable = th.in_graph && th.lat != null && th.lon != null;
  const name = th.name ?? (th.source === "data" ? "Unnamed start point" : th.id);
  const label = `${name}${usable ? "" : " (not on the trail network)"}${th.source === "data" && usable ? " (from the data, unverified)" : ""}`;
  return `<option value="${esc(th.id)}"${usable ? "" : " disabled"}${th.id === current ? " selected" : ""}>${esc(label)}</option>`;
}

/** The start list (with a destination: only the starts that reach it within the trip's miles). Redrawn in place
 * when the miles or the trip shape change. */
export function renderStarts(s: State): string {
  const f = s.form;
  const reach = s.destination ? startsInReach(s) : null;
  const listed = reach ?? s.trailheads;
  const yaml = listed.filter((t) => t.source === "yaml");
  const data = listed.filter((t) => t.source === "data");
  const custom = f.trailhead && !s.trailheads.some((t) => t.id === f.trailhead)
    ? `<option value="${esc(f.trailhead)}" selected>${esc(f.trailhead)} (map point)</option>` : "";
  const startLabel = s.destination ? `Starts that reach ${esc(s.destination.name)}` : "Trailhead";
  const destination = s.destination ? `<p class="where">Going to <b>${esc(s.destination.name)}</b>${reach && !reach.length
    ? ". No start reaches it within these miles; change the miles or tap the map to start elsewhere." : ""}</p>` : "";
  return `${destination}
<label>${startLabel}<select name="trailhead">
  <option value=""${f.trailhead ? "" : " selected"}>Pick one, or tap the map</option>
  ${custom}
  ${yaml.length ? `<optgroup label="Trailheads">${yaml.map((t) => option(t, f.trailhead)).join("")}</optgroup>` : ""}
  ${data.length ? `<optgroup label="Other start points from the data">${data.map((t) => option(t, f.trailhead)).join("")}</optgroup>` : ""}
</select></label>`;
}

export function renderPlan(s: State): string {
  const f = s.form;
  const types = TYPES.map(([t, label]) => `<option value="${t}"${f.type === t ? " selected" : ""}>${label}</option>`).join("");
  return `
<h1>Plan a trip</h1>
${s.saved.length ? `<button class="link" id="to-saved">Saved trips (${s.saved.length})</button>` : ""}
${renderSearch(s)}
<div id="th-box">${renderStarts(s)}</div>
<p class="note">Tap a TH badge or a grey start dot on the map, pick from the list, or tap anywhere on a trail to start there (snapped within 650 ft).</p>
<label><input type="checkbox" name="dayHike"${f.dayHike ? " checked" : ""}> Day hike</label>
<div class="row">
  ${f.dayHike ? "" : `<label>Nights<input name="nights" inputmode="numeric" value="${esc(f.nights)}" placeholder="2"></label>`}
  <label>Total miles<input name="total" value="${esc(f.total)}" placeholder="18-26"></label>
</div>
<div class="row">
  ${f.dayHike ? "" : `<label>Miles per day<input name="daily" value="${esc(f.daily)}" placeholder="6-11"></label>`}
  <label>Max gain${f.dayHike ? "" : " per day"} (ft)<input name="maxGainDay" inputmode="numeric" value="${esc(f.maxGainDay)}" placeholder="2500"></label>
</div>
<div class="row">
  <label>Shape<select name="type">${types}</select></label>
  ${f.type === "one_way" || f.type === "out_and_back" ? "" : `<label>Direction<select name="direction"><option value=""${f.direction ? "" : " selected"}>either</option><option value="cw"${f.direction === "cw" ? " selected" : ""}>clockwise</option><option value="ccw"${f.direction === "ccw" ? " selected" : ""}>counterclockwise</option></select></label>`}
</div>
<div id="finishes-box">${finishesHtml(f, s.finishes)}</div>
<div id="per-day">${renderPerDay(f, null)}</div>
<label>Trip level<select name="tripLevel"><option value=""${f.tripLevel ? "" : " selected"}>Any</option>${LEVELS.map((l) => `<option value="${l}"${f.tripLevel === l ? " selected" : ""}>${levelName(l)}</option>`).join("")}</select></label>
<label><input type="checkbox" name="noRoads"${f.noRoads ? " checked" : ""}> No roads at all</label>
<label><input type="checkbox" name="campsAtSites"${f.campsAtSites ? " checked" : ""}> Camps only at known campsites</label>
<label>Must visit (trail names or trailhead ids, one per line)<textarea name="mustVisit" rows="2">${esc(f.mustVisit.join("\n"))}</textarea></label>
<label>Avoid (trail names, one per line)<textarea name="avoid" rows="2">${esc(f.avoid.join("\n"))}</textarea></label>
<label>Pin camps (miles in, or lat,lon; one per line)<textarea name="camps" rows="2">${esc(f.camps.join("\n"))}</textarea></label>
${s.elevationPending ? `<p class="status">Adding elevation…</p>` : ""}
${s.error && s.canRetryOpen ? `<button type="button" id="retry-open">Try again</button>` : ""}
<button class="primary" id="generate"${s.busy || s.elevationPending ? " disabled" : ""}>${s.busy ? "Searching…" : "Find trips"}</button>
<div class="status${s.error ? " error" : ""}" id="status">${esc([s.status, s.error].filter(Boolean).join(" "))}</div>
${s.run ? `<button class="link" id="back-to-compare">Back to the last results</button>` : ""}`;
}

/** Redraw only the one-way finishes (they arrive after the start or miles change), leaving the rest alone. */
export function patchFinishes(root: HTMLElement, s: State): void {
  const box = root.querySelector<HTMLElement>("#finishes-box");
  if (box) box.innerHTML = finishesHtml(s.form, s.finishes);
}

/** Status line after a map tap: names the trail so the user can type it under must-visit or avoid. */
export function tapMessage(trailName: string | null): string {
  const on = trailName ? `on ${trailName}` : "on an unnamed trail or road";
  return `Start set to the tapped point ${on}. Type a trail name under "must visit" or "avoid" to use it.`;
}

export interface PlanActions extends SearchActions {
  generate(): void; backToCompare(): void; showSaved(): void; retryOpen(): void;
  /** `redraw`: the change alters the form itself (day hike, trip shape, miles, start), so redraw the panel. */
  editForm(form: FormState, redraw?: boolean): void;
}

// Checkboxes and selects that change the form's layout redraw the panel (their change fires at once, so nothing
// is lost); typed fields never do, or a tap on the next button could land on a replaced one.
const REDRAW = new Set(["dayHike", "type"]);

export function bindPlan(root: HTMLElement, store: Store, actions: PlanActions): void {
  const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);
  // The latest form: ours after an edit (the app records edits without re-rendering this panel), or the store's
  // when something else changed it since (a map tap sets the trailhead without redrawing the form).
  let form = store.get().form;
  let seen = form;
  const latest = () => { if (store.get().form !== seen) form = seen = store.get().form; return form; };
  let open: number | null = null;
  const edit = (f: FormState, redraw = false) => { form = f; actions.editForm(f, redraw); seen = store.get().form; };
  const perDay = () => root.querySelector<HTMLElement>("#per-day");
  const editDay = (i: number, d: FormDay) => {
    latest();
    const nights = nightsOf(form) ?? 0;
    const days = daysFor(form, nights);
    days[i] = d;
    edit({ ...form, days: [...days, ...form.days.slice(days.length)] });
    refresh();
  };
  // The strip lives in its own container so its taps redraw only the strip, not the whole form.
  const refresh = () => {
    const box = perDay();
    if (!box) return;
    box.innerHTML = renderPerDay(form, open);
    bindField(box.querySelector<HTMLInputElement>('input[name="perDay"]'));
    box.querySelectorAll<HTMLElement>(".day-block").forEach((b) => b.addEventListener("click", () => {
      const i = Number(b.dataset.day);
      open = open === i ? null : i;
      refresh();
    }));
    const editor = box.querySelector<HTMLElement>(".day-editor");
    if (!editor) return;
    const i = Number(editor.dataset.day);
    editor.querySelectorAll<HTMLElement>("[data-level]").forEach((b) => b.addEventListener("click", () => {
      const level = b.dataset.level as Level;
      const day = daysFor(latest(), nightsOf(form) ?? 0)[i]!;
      editDay(i, pickLevel(day, day.level === level && !isCustom(day) ? "" : level));  // tapping the chosen level again clears it
    }));
    editor.querySelectorAll<HTMLInputElement>("input[data-day-field]").forEach((inp) => inp.addEventListener("change", () => {
      const day = daysFor(latest(), nightsOf(form) ?? 0)[i]!;
      editDay(i, setDayNumber(day, inp.dataset.dayField as DayNumber, inp.value));
    }));
  };
  const bindField = (el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null) => {
    el?.addEventListener("change", () => {
      const name = el.getAttribute("name")!;
      const f = { ...latest() } as unknown as Record<string, unknown>;
      if (el instanceof HTMLInputElement && el.type === "checkbox") f[name] = el.checked;
      else if (name === "mustVisit" || name === "avoid" || name === "camps") f[name] = lines(el.value);
      else f[name] = el.value;
      edit(f as unknown as FormState, REDRAW.has(name));
      if (name === "perDay" || name === "nights" || name === "daily" || name === "maxGainDay") refresh();
      if (name === "total" && store.get().destination) refreshStarts();
    });
  };
  const refreshStarts = () => {
    const box = root.querySelector<HTMLElement>("#th-box");
    if (!box) return;
    box.innerHTML = renderStarts({ ...store.get(), form: latest() });
    bindField(box.querySelector<HTMLSelectElement>('select[name="trailhead"]'));
  };
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("[name]").forEach((el) => {
    if (!perDay()?.contains(el)) bindField(el);
  });
  refresh();
  // The finishes box is redrawn in place when finishes arrive (patchFinishes), so its click is bound once, here.
  root.querySelector("#finishes-box")?.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-finish]");
    if (!b) return;
    edit({ ...latest(), finish: b.dataset.finish! });
    patchFinishes(root, { ...store.get(), form });
  });
  bindSearch(root, store.get, actions);
  root.querySelector("#to-saved")?.addEventListener("click", () => actions.showSaved());
  root.querySelector("#generate")?.addEventListener("click", () => actions.generate());
  root.querySelector("#retry-open")?.addEventListener("click", () => actions.retryOpen());
  root.querySelector("#back-to-compare")?.addEventListener("click", () => actions.backToCompare());
}
