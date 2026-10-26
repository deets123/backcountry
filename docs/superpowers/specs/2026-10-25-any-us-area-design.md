# Any US area by name (design)

Status: discussed in conversation 2026-10-25. Builds on merged Phase 2 pieces 1–4 and per-day difficulty (PR #7).
Park rules, permits and closures are the next piece, not this one.

## Intent

Today the app plans only in hand-made regions (`regions/dolly_sods.yaml`, `regions/pemi.yaml`, plus small
calibration areas). On a Zion trip the user had the West Rim but couldn't add Angels Landing, because the
calibration area didn't cover it. For the Needles District of Canyonlands there was nothing at all, so the user drew
the route in CalTopo by hand, following steps an LLM gave them. That is exactly the work this app exists to remove.

The user wants it to feel like AllTrails: type any place in the US and get trails and loops right away. AllTrails is
fast because the slow work is done ahead of time on its servers, and a search is just a lookup. This piece does the
same at the size of one server: download US trail data once, then cut any area out of it locally in seconds.

## Goals

1. **Search any US area by name.** Users can search for:
   - a protected area or park (national park, national forest, wilderness, state park, or any OSM protected-area
     or park boundary), which opens the **whole area**. "Zion" means all of Zion, West Rim and Angels Landing both;
   - a place, trail or peak (Chesler Park, Angels Landing, Joint Trail), which opens the park or protected area it
     sits in, centred on that spot. If it isn't inside one, an area around it opens;
   - a box drawn, or a spot tapped, on the map, to plan anywhere with trails.
2. **The user never sees the downloading.** An area that isn't ready is prepared quietly behind one short loading
   message ("Finding trails in Needles District…"), then the plan screen opens. After the first time the area is
   ready at once.
3. **Choosing a start: both ways.** Pick from a list of trailheads found automatically in OSM, or tap the map, as
   with existing regions.
4. **Plans work exactly as in Dolly Sods**: loops, out-and-backs, per-day difficulty, GPX export, offline trips,
   chat. Same engine, same rules.
5. **Safe to run on a server that friends will use.** No user input can make the server fetch from a place of the
   user's choosing, write outside its data folders, or run anything.

## Non-goals

- Park rules, permits, closures and alerts (the next piece; the NPS data service and park rule documents). Until
  then they stay "unknown".
- Places outside the US (USGS elevation and the per-country file keep this simple). US territories only if they
  come free in the US extract.
- Trips from one point to another (on the wishlist as its own piece).
- Chat switching areas ("plan me something in Zion" from the chat). The chat works inside whichever area is open.
- Cross-checking new areas against Forest Service data. That stays limited to the two regions where it exists.
- Downloading map tiles in bulk (rule 6). Tiles stay as today.
- Visual polish beyond what the search and loading screens need.

## Anti-goals (failure even if it technically works)

- An area that works but takes minutes on every visit.
- Telling the user about downloads, file sizes or data mirrors in the normal flow.
- Inventing anything: no made-up trailheads, connecting lines or park facts. A new area obeys every CLAUDE.md rule;
  trailheads come from OSM, are shown as unverified, and say where they came from ("tagged trailhead", "trail meets
  road", "parking nearby").
- A search box that can be used to make the server download or run something.
- Breaking the existing regions, saved trips or offline trips.

## Decisions made with the user

- **US only for now.**
- **Disk:** about 15 GB for the US trail data, plus elevation that builds up area by area, is fine.
- **Trail data, ahead of time.** Download the US OpenStreetMap extract from Geofabrik once. It is published for
  bulk download, so this follows rule 6, and attribution stays ODbL. Filter it down to what the engine reads: the
  same feature classes the Overpass query takes today (`ingest.build_query`): `highway` ways, trailhead nodes,
  parking, camp sites, springs, water, streams, drinking water, protected-area boundaries. Add the named places
  search needs (peaks, localities, viewpoints, named trails). The download and filtering are an operator step, a
  CLI command, never triggered by a user.
- **Refresh is deliberate.** A refresh command downloads a newer extract, so OSM edits (like tagging the slot
  canyons) show up when the operator chooses.
- **Elevation per area, on first use.** USGS 3DEP for the area's bounds, fetched the first time and kept. The whole
  US would be hundreds of GB.
- **New dependency: `osmium` (pyosmium)**, the standard library for reading OSM PBF files. Explain it in the commit
  message, per CLAUDE.md.
- **The search runs on local data.** No outside geocoding service.
- **Security, as agreed:**
  - user input is only ever a name or a box, never a URL or a file;
  - the server downloads only from a fixed list of hosts (Geofabrik, USGS) over HTTPS;
  - every file is checked against the checksum or size the source publishes where one exists, and anything that
    fails is discarded;
  - downloaded files are only parsed as data, never executed, and are stored in their own data folders away from
    the code;
  - user text never reaches a shell command or an unparameterised query;
  - there are limits on new areas, so nobody can fill the disk one box at a time.

## How it fits the current code (for the builder)

- `region.Region` / `load_region` read `regions/*.yaml`; `api.store.RegionStore` builds each region's graph once per
  process from `ingest.load_raw` (Overpass JSON: `retrieved_at` plus `response.elements` with `tags`, `geometry`,
  `nodes` and `meta`) and `ingest.load_dem`. `graph.build_graph(raw, crs)`, `trailheads.find_candidates`,
  `features`, and water and camp extraction all read that raw shape.
- **Recommended route (my call):** cut each area out of the filtered extract into **the same raw JSON shape**
  Overpass returns, cached under `data/raw/<area_id>/`, with `source` naming the Geofabrik extract and its date.
  Then nothing downstream changes. Generate the area's region definition (id, name, UTM CRS from its centroid, bbox
  from the boundary, boundary name) into a git-ignored folder such as `data/areas/`, never into `regions/`, which
  stays hand-curated. The trailheads come from `find_candidates`, all `verified: false`, located by OSM node id
  (rule 5).
- **Existing regions stay as they are**: Dolly Sods and Pemi keep their curated files, Overpass caches and USFS
  cross-check. Whether to move them onto the extract later is the builder's call, but nothing about them may break.
- **Preparation runs in the background**: the API starts it and the app polls a status. Graph builds stay
  once-per-process. Because the number of areas is now open-ended, loaded areas need a memory bound (for example
  least-recently-used).
- **Web app**: the Region dropdown becomes a search box, with results showing name and type ("National Park",
  "Wilderness", "place in Canyonlands National Park"), plus the map tap or box. Recent areas are listed for one-tap
  return.
- **Provenance**: each run's data sources list the extract (Geofabrik, OSM ODbL, extract date) and the USGS DEM, as
  today.

## Calls settled after the builder check (marked as mine; the user can overrule)

- **Nested areas.**
  - A name that matches several boundaries ("Zion" matches the national park and the Zion Wilderness) lists each
    as its own result, with its type. Parks rank first.
  - For a place, trail or peak, the containing area is the national or state park if there is one. Otherwise it is
    the smallest protected area that fits under the size cap. Otherwise it is a capped area around the spot. So
    Angels Landing opens Zion National Park, and a peak in Dolly Sods opens Dolly Sods Wilderness, not all of
    Monongahela National Forest.
- **Existing regions win.**
  - A search that matches a hand-made region (Dolly Sods, Pemi) opens that region, with its curated trailheads and
    USFS cross-check.
  - Calibration areas are not search results. Their tests stay on their own cached data, unchanged.
  - Acceptance check 1 also rates the user's Zion GPX on the new whole-park area and reports the difference, as
    information, not a gate.
- **Refresh doesn't break saved trips.**
  - Each area cut records the extract date it came from, and each saved run records which cut it used.
  - After a refresh, new plans use a new cut, made lazily the next time the area is opened.
  - Old cuts are kept while any saved run references them, so level refreshes and chat on old trips use the
    data the trip was planned on.
- **Disk limits never break saved trips.**
  - When the elevation or area budget is reached, the least-recently-used areas that no saved run references are
    removed first.
  - If that isn't enough, new areas are refused with a plain message. Nothing a saved trip needs is ever removed.
- **The machine is the user's laptop** (`backcountry serve`, reached over Tailscale). The cap, timings and memory
  bound are measured there; a future host is unknown. The one-time US filtering runs on the laptop too.
- **Who can start a new area:** anyone who can reach the server, as the user asked ("a user can ask for any park").
  Limits are global until accounts exist: one preparation at a time, a cap on new areas per hour, and the disk
  budget above.

## Left to the builder

- **Index format** for the filtered US data and the name search (for example SQLite with stdlib `sqlite3` and
  FTS5, or pre-cut per-state files), as long as cutting an area and searching are local and fast.
- **Area size cap.** Some national forests are enormous. Pick a cap from measured graph-build and DEM times. Past
  it, a search result opens a capped area around the chosen spot and says the area was limited to fit. Measure Zion
  (about 230 sq mi) and Canyonlands' Needles District as reference points.
- **The numbers on the abuse limits** (new areas per hour, one preparation at a time, total elevation disk budget)
  and where they are configured.
- **Ranking and matching of names** (exact, prefix, fuzzy), and how to break ties between same-named places in
  different states ("Bear Creek"). Results must show state and type so the user can tell them apart.
- **What a drawn box or tap without a name is called**, for example "Area near <nearest named place>".

## Acceptance checks

1. **Zion:** searching "Zion" opens Zion National Park as a whole. The trailheads include West Rim and Angels
   Landing-area starts, a plan from the West Rim side can include Angels Landing, and the user's own Zion West Rim
   GPX (local, git-ignored) still rates as in calibration.
2. **Chesler Park:** searching "Chesler Park" opens the Needles District / Canyonlands area centred there, and plans
   loops through Chesler Park on OSM trails only.
3. **An area never set up before** (say Linville Gorge, NC) is ready within about a minute the first time and opens
   at once afterwards. Report the measured times; don't claim them.
4. Trail lengths in a new area agree with OSM's own way lengths. Where the NPS publishes a mileage for a named trail,
   note the difference in the QA report. Don't treat it as truth (rule 2).
5. Security tests: inputs shaped like URLs, paths (`../`), shell or SQL fragments, huge boxes, boxes outside the US,
   and floods of new-area requests are refused or capped, and nothing is fetched from an unlisted host.
6. Dolly Sods, Pemi, saved trips and offline trips behave exactly as before; the full test suite and chat eval stay
   green.
