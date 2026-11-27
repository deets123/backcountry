# Destinations, trip shapes and speed (design)

Status: discussed in conversation 2026-10-27. Builds on "Any US area by name" (PR #8, branch `any-area`; this
branch starts from it). Park rules, permits and closures stay the piece after this one.

## Intent

The any-area piece made every US park searchable, but three things still stop it feeling like AllTrails:

1. **The engine only plans loops and lollipops.** Angels Landing is a dead-end spur, so no trip can reach it; the
   user's top-down West Rim trip (Lava Point to the Grotto) was one-way, which the engine can't plan at all.
2. **A searched place isn't treated as where you want to go.** Searching "Chesler Park" opens the whole Needles
   District and the user then has to find Chesler on their own. Searching "Zion" and "Zion National Park" give the
   same thing.
3. **It's slow.** Planning from Bridalveil Creek in Yosemite took 185 s; from the Grotto in Zion the search hit its
   90 s limit and found nothing. The first open of Grand Canyon took 4.4 minutes, almost all of it elevation
   download (`areas.py` records the split). The user is used to AllTrails, which loads as fast as the connection
   and has never made them wait more than a minute; a server on a slow connection would make today's 4–5 minutes
   into 10 or an error.

## Goals

### 1. A searched place is a destination ("to and through")

- Searching a **place, trail or peak** (Chesler Park, Angels Landing, Joint Trail) opens the trails around it, as
  now, but the place becomes the trip's **destination**:
  - the map zooms to it and marks it;
  - the trailhead list shows only starts that can reach it within the trip length, nearest (by trail) first;
    tapping the map for a start still works;
  - **every planned trip goes to it and through it**: a loop or lollipop passes through it; a spur like Angels
    Landing becomes an out-and-back to its end and back the same way; a one-way trip passes through it on the
    way to the finish (with an out-and-back spur in the middle if needed).
- What "reaching" means, by kind of place (the place's location comes from the index, never invented):
  - a **trail** (a named way): the route walks some part of that trail (as the existing must-visit by name);
  - a **peak, viewpoint or other point**: the route reaches the trail node nearest to it. If that node is more
    than 0.25 mi (400 m) away in a straight line, the trip summary says so, with the distance: "The nearest mapped
    trail point is 0.4 mi from Angels Landing; no mapped trail reaches it." No line is drawn to the summit;
  - an **area-like place** (a named meadow or basin like Chesler Park, mapped as a polygon or a point): the route
    passes inside it, or for a point, within 0.25 mi of it.
- If no start can reach the destination within the trip's mileage, the app says so and shows the shortest trip
  to it that it found, with its real length. It never invents a connection.
- On a multi-night trip the destination may fall on any day; the engine places camps as it does today.
- The destination is part of the saved trip: reopening, updating to newer data, offline and chat keep it.

### 1b. Day hikes, and destinations inside longer trips (the user chose both)

- **Day hikes:** (found while planning: day hikes already exist; nights left blank plans one. The piece adds a
  clear "Day hike" choice to the form rather than a new mode.) A day hike is one day with
  no camps, and has every trip type, a difficulty level, GPX, saving, offline use and the chat, the same as
  overnight trips. The form offers "Day hike" alongside nights. "Angels Landing" with a day hike gives the
  out-and-back from the Grotto with its real length.
- **Destinations inside longer trips:** with an overnight length (for example 2 nights, 18–26 mi), the engine
  fits the destination into a trip of that length. Examples: a loop or one-way trip with an out-and-back spur to
  the destination, or an out-and-back that goes past it. A destination spur may be any length the trip's range
  allows; the 1 mi limit on today's must-visit detours doesn't apply to destinations. The spur's miles are shown
  as "side trip to Angels Landing, X mi round trip".
- If the destination is shorter than the requested trip and nothing fits the range, the app says so and offers
  the shorter trip with its real length (for example, as a day hike), rather than padding it with made-up
  turnarounds.

### 2. Searching a park or area: short name vs full name

- A **full name** ("Zion National Park", or a common abbreviation: "Zion NP") opens just that area, as now.
- A **short name** that several touching areas share ("Zion": Zion National Park plus the Zion Wilderness and
  any other protected area whose name starts with that word and touches it) opens them **together** as one area,
  listed first as "Zion (park and nearby wilderness)". The combined size obeys the existing 5,000 km² cap;
  combinations bigger than that fall back to the largest single area.
- Abbreviations understood: NP (National Park), NF (National Forest), NM (National Monument), NRA (National
  Recreation Area), SP (State Park), W or WA (Wilderness). Anything else is matched as typed.

### 3. Out-and-back and one-way trips

- Trip types become **loop, lollipop, out-and-back, one-way**, and **any**. "Any" picks the best of loop,
  lollipop and out-and-back. One-way needs a second car or a shuttle, so it is only planned when chosen.
- **Out-and-back**: from the start to a turnaround and back the same way. With a destination, the turnaround is
  the destination (or the far end of its spur). Without one, the engine picks turnarounds that fit the mileage:
  a trail end, a junction, or a named feature (peak, lake, viewpoint) from the data, about half the total from
  the start. Walked-twice miles are shown as "out and back, X mi each way", not hidden in the total.
- **One-way (shuttle)**: the user picks a start and chooses "one way". The app **suggests finishing trailheads**
  that fit the mileage (up to 5), each showing miles, gain, **loss** (matters for top-down trips like the West Rim)
  and the straight-line distance between the two trailheads, labelled as straight-line. The user picks one, and
  the trip is planned to it. With a destination, the trip passes through it.
- Finishing trailheads come from the same trailhead sources as starts (OSM, unverified, with where they came
  from). Only trailheads on the same connected network count.
- **Shuttle drive**: a "Shuttle drive" button opens Google Maps driving directions from the start trailhead to
  the finish (and Apple Maps on iPhone), passing only the two trailhead coordinates. The app shows no drive time or
  road distance itself; that is "unknown" in the trip summary and the GPX. The button carries the note: "Check
  road conditions; backcountry roads may be closed or need high clearance."
- Multi-night out-and-back and one-way trips split into days and camps as today, with per-day difficulty.
- GPX and the map show start and finish as separate points ("Start: …", "Finish: …") when they differ.
- Saved trips, offline trips, updates to newer data (retrace) and the chat work for the new types. The chat can
  change days and distances on them as on loops; asking the chat to change the trip type is out of scope.

### 4. Speed

Targets on this laptop as the server (measured before and after; numbers go in PROGRESS.md):

| What | Today | Target |
|---|---|---|
| An area opened before | ~0.1 s | as fast as the connection; a few seconds at most |
| An area nobody opened: trails and trailheads on screen | after everything, 28 s–4.4 min | 15–30 s |
| …elevation ready (planning possible) | same | under 1 minute for parks up to the 5,000 km² cap |
| Planning: first trips on screen | 18–185 s, or nothing at 90 s | about 10 s |
| Planning: search stops | 90 s | by 60 s, keeping the best found |

How (the user agreed to these directions; the details are the builder's):

- **Search only what can be part of the trip.** Today every search step runs over the whole area's network.
  Nothing farther than half the maximum trip length (by trail) from the start can be on a loop or out-and-back;
  for one-way, nothing farther than the maximum from both ends combined. Cut the network to that before searching.
- **First trips fast, better ones after.** The phone shows the first good trips within about 10 s; the search
  keeps going and better ones replace them, stopping by 60 s. The phone shows that it's still looking.
- **Don't redo work** the search already did (repeated shortest-path runs from the same points).
- **Elevation only along trails.** Fetch elevation for the area around the trails (a corridor), not the whole
  area box, and keep what planning needs (elevation along each trail) rather than the whole raster, if that is
  what measurement shows is slow or big. Provenance stays the same (USGS 3DEP, dataset, publication dates).
- **Open in two steps.** Trails and trailheads appear as soon as the cut is done; elevation fills in behind with
  progress ("Loading trails…", "Adding elevation…"). The user can set trip options meanwhile; Plan waits for
  elevation.
- **Prepare popular areas ahead of time** with an operator command run overnight: national parks first, then
  national monuments and recreation areas, then wilderness areas with the most trail miles, until the 15 GB
  disk budget (including what users opened) would be exceeded. These then open instantly.
- **On a slow connection** the phone shows progress, never a blank screen; a failure shows a retry button and
  keeps the trip settings.

## Non-goals

- Drive times or road distances computed by us (public routing servers don't allow app use; our own would be a
  big project). Google/Apple Maps links only.
- Park rules, permits, closures (next piece); they stay "unknown".
- Changing trip type through the chat.
- Visual polish beyond the new controls and progress messages.
- Bulk downloading map tiles (rule 6).

## Anti-goals

- A destination trip that silently skips the destination, or draws a line to an off-trail summit.
- A shuttle drive time or "road open" claim from anywhere other than the user's map app.
- Speedups that change which trips are found for the same request beyond "found more, or sooner" (the
  Dolly Sods and Pemi golden routes and reference checks must still pass).
- Breaking existing regions, saved trips (old ones without a type load as loops/lollipops) or offline trips.
- Any new way for user input to make the server fetch from a place of the user's choosing (the any-area security
  rules hold; map links are built on the phone from trailhead coordinates only).

## Decisions made with the user

- Searched place = destination; trips go **to and through** it (not "stay inside it").
- Both day hikes and destinations fitted inside longer trips.
- "Zion" = park plus nearby wilderness; "Zion National Park" = only the park.
- One-way: the app suggests finishes, the user picks by mileage.
- Shuttle drive via a Google Maps (and Apple Maps) link with a road-conditions note.
- Speed target: as fast as the connection, never more than a minute; first trips in about 10 s.
- Speed work comes first, then trip shapes with destinations, with the search-name change alongside.

## My calls (the user can override)

- 0.25 mi (400 m) as "near enough" for a point destination; farther gets the "no mapped trail reaches it" note.
- Up to 5 suggested finishes; one-way is never picked by "any".
- Out-and-back without a destination uses trail ends, junctions and named features as turnarounds.
- Prepared-ahead order (national parks, monuments and recreation areas, then wilderness by trail miles).
- Abbreviation list above.
- **Area-like places without a rebuild.** The index stores places as a point plus a bounding box, with no polygon
  and no meadow or basin tags. So an area-like place counts as reached when the route comes within 0.25 mi of its
  point, or passes inside its bounding box when it has one. The US index isn't rebuilt for this piece.
- **Start list reach.** For loops, lollipops and out-and-backs a start is listed when its trail distance to the
  destination is at most half the trip's maximum. For one-way it's the full maximum. The server returns each
  trailhead's trail distance to the destination once, when the area opens, and the phone re-filters the list
  whenever the trip length changes. Before a length is set, every start that can reach the destination is
  listed, nearest first.
- **Prepared-ahead areas vs the disk budget.** Prepared areas get a reserved share of 8 GB of the 15 GB, and the
  prepare command stops at that share. User-opened areas evict only each other, and prepared areas are never
  evicted by them. An operator can re-run the command to replace stale prepared areas.
- **First-open elevation target.** The target applies to areas nobody has opened yet, measured with the corridor
  fetch and parallel tile reads. If USGS download speed alone still keeps a big park (Grand Canyon) over 1 minute,
  preparing it ahead counts as meeting the target. PROGRESS.md then records the measured first-open time, so no
  other elevation source is needed.

## Left to the builder

Algorithms for the pruned search and progressive results, how the phone gets later results (e.g. polling a job
like the area worker), the elevation corridor method and storage format, ranking of finishes and turnarounds,
and the operator command's name. Measure before changing: record the time split (cut, graph, elevation fetch,
elevation sampling, search, day split) for Zion, Yosemite and Grand Canyon first.

## Acceptance checks

1. Speed: the table above, measured on Zion (Grotto), Yosemite (Bridalveil Creek) and Grand Canyon, before and
   after. First trips within ~10 s; nothing waits more than 60 s.
2. Searching "Angels Landing" as a day hike gives an out-and-back from the Grotto that ends at the top of the Angels Landing
   trail, with real miles and gain, and a note if the summit is beyond the mapped trail. As 2 nights from the
   Grotto, it gives an overnight trip that includes the Angels Landing spur.
3. "Chesler Park": the trailhead list offers Elephant Hill and Squaw Flat (if they're in the data); a loop
   through Chesler Park is planned from one of them.
4. Lava Point, one way: the Grotto is among the suggested finishes with miles, gain and loss. Both trailheads are
   in the OSM data: "Lava Point Trailhead" (node 11074252440) and "Grotto Trailhead" (node 12081803189), both
   `highway=trailhead`, checked 2026-10-27. The planned trip must match the user's track
   `tests/reference/local/zion_west_rim_top_down.gpx` within the tolerance the existing reference checks use. The
   track is an AllTrails export and git-ignored: use it for a local check only, never commit or copy it. Shuttle
   drive must open Google Maps.
5. "Zion" lists the park plus nearby wilderness first; "Zion National Park" and "Zion NP" open the park alone.
6. Dolly Sods, Pemi, saved, offline and chat behave as before; golden routes unchanged; all suites green.
