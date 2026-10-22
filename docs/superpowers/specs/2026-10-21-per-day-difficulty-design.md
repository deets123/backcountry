# Per-day difficulty and difficulty levels (design)

Status: discussed and confirmed in conversation 2026-10-21. Builds on the merged Phase 2 pieces 1–4 (API, web app,
offline trips, chat). Part of the "backpacking version of AllTrails" direction; the other ideas from that list are
out of scope here (see Non-goals).

## Intent

Real trips aren't uniform. The user wants to say "4 days: day 1 short because of a late start, days 2 and 3 hard,
day 4 light" and get a loop whose days match, and wants every day and every trip labeled **easy / moderate / hard /
strenuous** with a short reason ("**Hard** · steep"). They plan in numbers (miles, climbing, steepness); other
people will say "easy" or "hard" to the chat, and the app has to understand both.

The labels must be trustworthy. The user's words: a gentle day marked hard, or a hard day marked easy, "would make
me mad" either way. AllTrails assigns difficulty by staff judgment plus community votes and doesn't publish a method,
and hikers complain it's inconsistent. This app's version must be computed from sourced data and explainable: every
level comes with the reason that produced it.

## Goals

1. **Difficulty levels** for each day and each trip, with short reasons, computed only from sourced data.
2. **Per-day targets** (miles, climbing, steepness) set in the plan form or by chat; days without targets use the
   trip-wide settings as today.
3. **Levels as shortcuts**: picking "easy" fills a day's targets from one shared table; the chat maps words to levels.
4. **A trip-level filter** on the plan form: any / easy / moderate / hard / strenuous.
5. **Closest fit, honestly**: when no loop matches every target, show the closest loop, say which days miss and by
   how much, and suggest one setting change that would fix it.

## Non-goals (each is on the list as its own piece)

Point-to-point trips (the user needs them, later); community loops, ratings, reviews and popular loops (with
accounts, piece 5); offline route alternatives and offline commands; smaller offline map downloads; viewpoints and
"reward for effort"; temperatures and gear; a learned difficulty model (needs real user ratings first); visual
polish beyond what these screens need (the frontend pass comes after the backend pieces).

## Anti-goals (failure even if it technically works)

- A gentle day labeled hard, or a hard day labeled easy.
- Claiming "rocky", "technical" or any terrain the data doesn't say. Where terrain isn't tagged, the details say
  "terrain unknown".
- Long report-style text in the app. Labels are short ("**Hard** · steep"); numbers and places are a tap away.
- A suggestion ("allow day 3 up to 12.5 mi") that wasn't actually checked.

## Constraints

- Every CLAUDE.md rule: sourced data only, "unknown" where data is silent, never "safe", provenance, miles and feet,
  no raw OSM ids in user-facing text. A difficulty level describes effort and steepness from data; it never implies
  a route is safe or suitable.
- No new dependencies.
- The engine stays the only source of routes; the chat only proposes settings (as in piece 4).
- Saved trips work offline: moving a camp on the phone must recompute day levels the same way the engine does
  (like the existing `resplit.ts` parity with `split_route`). New saves store what the phone needs for that (the
  route's terrain tags and the per-day targets).
- **Trips saved before this feature get levels too** (user: they "should get updated"): when the app is online, it
  asks the server to compute levels for each saved trip's route (camps moved on the phone included) and stores them
  with the trip, along with the terrain data the phone needs from then on. Until that refresh, an old saved trip shows
  no levels, never levels computed without its terrain data.
- Phone-first and app-like (user: "make sure that the app is still an app").

## How difficulty is computed (decided)

Per day, from the day's own section of the route, in its direction of travel:

1. **Effort score**: Shenandoah National Park's published formula, `sqrt(gain_ft × 2 × miles)`
   (nps.gov/shen "How to Determine Hiking Difficulty"). Climbing counts more than distance; descent doesn't count,
   which matches the user's "Zion West Rim is moderate since it's mostly downhill". The park's five bands collapse to
   four levels: easy, moderate, hard (the park's "moderately strenuous" and "strenuous"), strenuous (the park's
   "very strenuous"). Starting cutoffs (my call, tuned by calibration): easy < 50, moderate 50–100, hard 100–200,
   strenuous > 200.
2. **Steepness** raises the level **by one at most**, and only for a long sustained climb, never a short steep
   pitch. The user's Mount Tammany (short, steep, rocky) is "moderate at best". The measure is the steepest sustained
   **uphill** grade over a window along the smoothed elevation profile; downhill steepness doesn't raise the level.
3. **Terrain tags** from OpenStreetMap (`sac_scale` of demanding_mountain_hiking or harder, scramble-type tags, very
   bad `trail_visibility`) make a day **at least hard**. A scramble alone does **not** make a day strenuous: the user
   rates Old Rag "hard, not strenuous" and the Needles slot canyons "hard, some technical scrambles". Strenuous needs
   the effort as well.
4. **A trip's level is its hardest day's level** (confirmed by the user).
5. **Two scales, by type of hike** (user: "I want this app to work for day and backpack hikers, so scale based on the
   type of hike"). A day hike (no nights) uses the day-hike scale; a day of a trip with nights uses the backpacking
   scale, where the same miles and climbing rate harder because of the pack. Both use the same formula, steepness
   and terrain rules; only the cutoffs differ, and both are calibrated (below).
6. Levels only go **up** from the effort score; steepness and terrain never lower one. That is what stops a short
   brutal day from coming out easy, the gap the park itself notes in its formula.

**Reasons** shown with the level are short tags for whatever raised it: "steep", "scramble (tagged)",
"demanding trail (tagged)", "hard to follow (tagged)". If nothing raised it, no tag. Tap for details: effort score,
where the steep stretch is ("19% for 0.4 mi at mile 14.2"), which sections are tagged, and "terrain unknown" for the
share of the day with no terrain tags.

**What the data has** (checked 2026-10-21): steepness from the elevation data, everywhere. Terrain tags are sparse:
Dolly Sods 2 of 261 trails carry `sac_scale`; Pemigewasset 151 of 1,254 (6 demanding, 2 alpine) and 53 with
`trail_visibility`. Forest Service `trail_class` (1–5) describes how developed a trail is, not how hard; don't use it
as difficulty. So in Dolly Sods most labels will rest on effort and steepness, with "terrain unknown" in the details.

## Calibration (decided: must pass before the levels ship)

The user's known trips, with their labels:

| Trip | Type | User's level | Route |
|---|---|---|---|
| Mount Tammany (Delaware Water Gap) | day hike | moderate at best | to confirm with the user (guess: Red Dot up, Blue Blaze down) |
| Zion West Rim | backpacking, 2 days | moderate (mostly downhill) | the user's GPX, top-down from Lava Point; camp location to confirm |
| Needles slot canyons (Canyonlands) | to confirm | hard (some technical scrambles) | to confirm with the user |
| Old Rag (Shenandoah) | day hike | hard, not strenuous | the user's GPX (the loop) |

The user's two GPX files (Old Rag, Zion West Rim) are AllTrails exports, so they are **not committed** (the repo is
public; AllTrails content may not be redistributed). They live in `tests/reference/local/` (git-ignored); the
calibration test reads them on the laptop and skips without them. They only guide which OSM ways the route follows:
the calibration route itself is rebuilt from OSM ways near the track, and its numbers come from our own elevation
data. Zion West Rim is point-to-point, which the app can't plan yet; the calibration harness builds that one route
from the track directly, outside the planner.

For each trip, fetch a small OSM area and elevation once (cached, Overpass used politely), build the route as the user
hikes it, and check the computed level matches. Tune the cutoffs, the steepness window and threshold, and the terrain
rules until all four match, and the Dolly Sods and Pemi routes don't produce anything obviously wrong. The test runs
on the laptop (real data; skips on GitHub like the other real-data tests). These calibration areas are not regions in
the app.

## Per-day targets (decided)

- Each day can have its own targets: a miles range, a climbing cap (feet), and a steepness cap (percent grade). Any
  target left blank uses the trip-wide setting. A level sets all three from one shared table (the same table the
  labels use, so "hard" means the same thing everywhere). Editing a number after picking a level makes that day
  "custom".
- **A day set to a level must come out at that level** (my call, from the anti-goal: "D2 Hard" next to a computed
  "Easy" is the failure the user named). So a level is a range with a floor as well as a ceiling (e.g. a hard day has
  a minimum effort as well as caps), the day splitter scores against the level itself, and a day that lands on another
  level is a miss reported in the closest-fit note ("Day 2 came out moderate, not hard").
- The engine uses the targets in two places: **placing camps** (the day splitter scores each day against its own
  targets instead of one daily range) and **picking loops** (a loop whose days can't fit the targets ranks worse,
  through the plan penalty that already feeds the ranking).
- **Trip-level filter**: exact match (my call): "moderate" keeps moderate trips, not easy ones; loops whose trip level
  doesn't match are dropped. If none match, the closest are shown with
  the closest-fit note.
- **Closest fit**: each missed target is named, as violations are today ("Day 1: 6.2 mi is over your 5 mi target"),
  plus at most one suggestion, a single setting change the engine actually re-checked and found to fix it
  ("Allowing day 3 up to 12.5 mi would fix it").

## Screens (decided)

- **Plan form**: a "Set days individually" switch under nights/total. When on, a **day strip**: one row of colored
  blocks, one per day, widths sized by target miles, each labeled with its level ("D1 Easy"). Tapping a block opens
  a small editor for that day: the four level buttons plus the three numbers. Plus an optional "Trip level" choice.
  (The user picked this over a full list of days.)
- **Results** (compare cards and the trip view's days): each day shows `Day 1 · [Easy] · 4.6 mi · +720 ft`, with a
  reason tag when there is one, and the closest-fit note under the days. Tap a day for its details.
- **Chat**:
  - "day 1 easy, days 2–3 hard, day 4 light" becomes a card listing the numbers the levels chose. Run works as
    today.
  - "make day 1 shorter, we're starting late" sets a day-1 target instead of guessing camp miles.
  - "Which day is hardest?" answers from the computed levels and reasons.
  - Words like "light" or "chill" map to easy, "brutal" to strenuous; ambiguous requests get one question.
  - The fact sheet carries each day's level and reasons. The honesty check treats level words as claims that must
    match the computed levels.

## Left to the builder

The level table's starting numbers per level (tuned with calibration); the steepness window length and threshold;
how the day splitter handles a per-day steepness cap (it needs each candidate day's steepest sustained climb);
request/response field names and shapes; exact reason wording; how the one suggestion is found (it must be
re-checked by the engine); colors and sizing of the day strip; new chat eval cases (at least: levels per day,
"late start", "which day is hardest", a trip-level request); keeping `resplit.ts` in parity for levels.

## Done when

All four calibration trips match the user's levels; per-day targets work from the form, the chat and the API; the
trip-level filter and closest-fit notes work; levels recompute on the phone for moved camps (parity test); all suites
pass and the chat eval covers the new requests; the user's phone check on a 4-day Pemi trip with "day 1 easy, days
2–3 hard, day 4 easy" gives sensible days and labels.
