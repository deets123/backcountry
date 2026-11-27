import { type DayNumber, type FormDay, type FormState, LEVELS, dayTargets, daysFor, isCustom, pickLevel, setDayNumber } from "../request";
import type { State, Store } from "../store";
import type { Level, Trailhead } from "../types";
import { esc, levelName } from "./common";
import { bindSearch, renderSearch, type SearchActions } from "./search";

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
  const nights = nightsOf(f);
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
  const label = `${th.name ?? th.id}${usable ? "" : " (not on the trail network)"}${th.source === "data" && usable ? " (from the data, unverified)" : ""}`;
  return `<option value="${esc(th.id)}"${usable ? "" : " disabled"}${th.id === current ? " selected" : ""}>${esc(label)}</option>`;
}

export function renderPlan(s: State): string {
  const f = s.form;
  const yaml = s.trailheads.filter((t) => t.source === "yaml");
  const data = s.trailheads.filter((t) => t.source === "data");
  const custom = f.trailhead && !s.trailheads.some((t) => t.id === f.trailhead)
    ? `<option value="${esc(f.trailhead)}" selected>${esc(f.trailhead)} (map point)</option>` : "";
  const types = ["any", "loop", "lollipop"].map((t) => `<option value="${t}"${f.type === t ? " selected" : ""}>${t}</option>`).join("");
  return `
<h1>Plan a trip</h1>
${s.saved.length ? `<button class="link" id="to-saved">Saved trips (${s.saved.length})</button>` : ""}
${renderSearch(s)}
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
<div id="per-day">${renderPerDay(f, null)}</div>
<label>Trip level<select name="tripLevel"><option value=""${f.tripLevel ? "" : " selected"}>Any</option>${LEVELS.map((l) => `<option value="${l}"${f.tripLevel === l ? " selected" : ""}>${levelName(l)}</option>`).join("")}</select></label>
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

export interface PlanActions extends SearchActions { generate(): void; backToCompare(): void; editForm(form: FormState): void; showSaved(): void }

export function bindPlan(root: HTMLElement, store: Store, actions: PlanActions): void {
  const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);
  // The latest form: ours after an edit (the app records edits without re-rendering this panel), or the store's
  // when something else changed it since (a map tap sets the trailhead without redrawing the form).
  let form = store.get().form;
  let seen = form;
  const latest = () => { if (store.get().form !== seen) form = seen = store.get().form; return form; };
  let open: number | null = null;
  const edit = (f: FormState) => { form = f; actions.editForm(f); seen = store.get().form; };
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
      edit(f as unknown as FormState);
      if (name === "perDay" || name === "nights" || name === "daily" || name === "maxGainDay") refresh();
    });
  };
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("[name]").forEach((el) => {
    if (!perDay()?.contains(el)) bindField(el);
  });
  refresh();
  bindSearch(root, store.get, actions);
  root.querySelector("#to-saved")?.addEventListener("click", () => actions.showSaved());
  root.querySelector("#generate")?.addEventListener("click", () => actions.generate());
  root.querySelector("#back-to-compare")?.addEventListener("click", () => actions.backToCompare());
}
