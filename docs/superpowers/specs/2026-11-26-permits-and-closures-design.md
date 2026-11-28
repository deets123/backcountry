# Park rules, permits and closures (design)

Status: discussed in conversation 2026-11-26. Builds on everything merged through PR #10 (any US area, trip shapes,
speed and accuracy). Recreation.gov booking data is the piece after this one, if this one leaves gaps.

## Intent

The app plans trips that may need paperwork the user doesn't know about, and says nothing. The user almost hiked
Old Rag without the seasonal day-use ticket Shenandoah requires. The Angels Landing day hike the app planned said
nothing about the permit every hiker needs. The map data the engine already holds carries some of this (Angels
Landing is tagged `access=permit`), and the engine drops it on the floor.

The distinctions that matter to a backpacker, in the user's words: Angels Landing needs a permit to walk it at
all; the West Rim top-down needs a permit only to camp, and is free as a day hike; the Narrows bottom-up needs
none; Old Rag has a day-use ticket from March to November on top of the park entrance fee. A bare "permit" flag
can't say any of that. This piece makes the trip say it, with the source and date of every statement, and say
"unknown" everywhere the sources are silent.

## Goals

1. **A "Before you go" block on every trip** listing, in this order: closures and permits that touch the route;
   park-wide permit and closure notices; the park entrance fee; rules (dogs, seasons, hours); then one line for what
   remains unknown. Each item names what it applies to, quotes its source, and shows the source's date.
2. **Kinds of permit are kept apart:** a permit to walk a trail, a permit or reservation to camp at a site, a
   day-use ticket for a hike, and the park entrance fee are four different statements. A day hike on the West Rim
   shows no camping permit; the same route with a night at a Zion wilderness site shows the site's permit.
3. **Two sources, both quoted, never interpreted:**
   - the OpenStreetMap tags already in every area's cut (permit, fee, reservation, dogs, hours, seasonal, notes
     and closure reasons on trails and camp sites);
   - the National Park Service's data service, for national parks and other NPS units: current alerts
     (closures, cautions, permit notices), the park's per-hike records (reservation required, fees, pets, season),
     and the entrance fee.
4. **Known before you plan:** when a park opens, its current closures and notices are one tap away on the plan
   screen, so a closed trailhead road is known before a trip is planned to it.
5. **A switch to avoid trails that need a permit**, like today's `--avoid`, driven only by map tags.
6. **Everything else keeps working:** saved and offline trips carry their notices with dates; the chat answers
   "do I need a permit?" from these facts only; GPX and summaries carry the block; areas with no NPS unit (Forest
   Service wilderness, state parks) get the map-data items and say there is no official feed.
7. **Safe to run for friends:** a server without a key still works; the key never leaves the server or lands in a
   log or a URL; third-party text is shown as text, never rendered as HTML; the only new outbound host is
   `developer.nps.gov`.

## Non-goals

- Booking or availability (lottery odds, quota left, dates open). Links to the source page only.
- Recreation.gov data (the next piece, if needed) and Forest Service or state alert feeds. Those areas say the feed
  is unknown.
- Interpreting rules: the app never concludes "you don't need a permit", "this is open" or "dogs are fine". It
  quotes what a source says and dates it.
- Changing a route because of an alert. Alerts are free text; only map tags (`access`, `foot`) decide what is
  walkable, as today. An alert that names a trail on the route is attached to it and shown first; the route stands.
- Rendering alert HTML, images or maps from NPS. Plain text and links only.
- Visual polish beyond the new block, chips and the notices sheet.

## Anti-goals (failure even if it technically works)

- Silence read as permission: a trip with no permit item must still say that permits beyond the listed sources
  are unknown.
- A notice shown without its date, or stale cached data shown as current.
- A route altered, excluded or re-ordered by alert text.
- Third-party text injected into the page as markup.
- A key committed, logged, or put in a query string.
- Slower opens or plans: the NPS fetch runs beside the open, never in the plan's path.

## Decisions made with the user

- **Approach 2** (below): map tags plus the NPS data service. Recreation.gov waits until this shows its gaps.
- **Walk, camp, day-use, entrance are separate statements** (goal 2).
- **Acceptance cases are the user's own:** Old Rag (day-use ticket), Angels Landing (permit to walk), the West Rim
  top-down (permit only to camp), the Narrows bottom-up (no permit; the top-down is tagged permit in OSM and may
  show as such for a route that uses that section).

## Approaches considered

1. **Map data only.** No key, works wherever OSM is tagged, including wilderness areas. Misses Old Rag's ticket,
   parking and road closures, and anything live. Rejected alone: it fails the user's own case.
2. **Map data plus the NPS data service** (chosen). Catches both of the user's cases, adds one dependency that can
   be tried with the public demo key, keeps every claim quoted and dated, and falls back to approach 1 outside NPS
   land with a line saying so.
3. **2 plus recreation.gov** for booking links and Forest Service permits. Needs a second key, its documentation
   wasn't readable, and the site discourages bulk use. Deferred.

## What the sources hold (checked 2026-11-26)

**OpenStreetMap, in the cuts already on disk.** Zion's cut: `access=permit` and `foot=permit` on Angels Landing,
the Subway and the Narrows top-down; `fee=yes`, `reservation=required`, `operator=<recreation.gov permit URL>` on
Angels Landing; 534 camp sites, the wilderness ones with `access=permit`, `fee=yes`, `reservation=required|no`,
`capacity:persons`, `operator`, `website`; `dog=no` on 113 ways; two trails with `access=no`, `note="Closed
indefinitely due to 2019 rockfall."`, `damage:date`, `damage:type`; free-text `description` with seasonal road
closures. Across the US index: `access=permit` on 28,283 ways, `fee` on 3,119, `dog` on 40,133, 10,060 camp sites
with a permit, fee or reservation tag. Old Rag's trails carry none of these.

**National Park Service data service** (`developer.nps.gov/api/v1`, key in the `X-Api-Key` header; 1,000 requests
an hour per key; the public `DEMO_KEY` allows 30 an hour and 50 a day for trying it; NPS reloads its data every
two hours):

- `/parks` (475 units): `parkCode`, `fullName`, `name`, `designation`, `states`, `latitude`, `longitude`, `url`,
  `entranceFees[{title, cost, description}]`, `entrancePasses[...]`, `operatingHours`.
- `/alerts?parkCode=`: `title`, `description`, `category` (Park Closure, Danger, Caution, Information), `url`,
  `lastIndexedDate`. Shenandoah today: "Ticket Required to Hike Old Rag" (Information, March 1 to November 30, with
  a link), the Old Rag parking lot closure, a backcountry zone closure, Skyline Drive closed for weather. Zion:
  "Road to Lava Point and West Rim Trailhead closed" (seasonal), "Watchman Trail closed", "Angels Landing Pilot
  Permit Program: all hikers need a permit", toxic cyanobacteria in streams.
- `/thingstodo?parkCode=` (Zion 20 records, Shenandoah about 40): `title`, `latitude`, `longitude`, `location`,
  `isReservationRequired`, `reservationDescription`, `doFeesApply`, `feeDescription`, `arePetsPermitted`,
  `arePetsPermittedWithRestrictions`, `petsDescription`, `season`, `seasonDescription`, `timeOfDayDescription`
  (Zion puts "Overnight trips require a Wilderness Permit" here), `activities`, `tags`, `url`. Descriptions are HTML
  with links. "Old Rag Circuit": reservation required, fee text "You must purchase a $1 Old Rag day-use ticket",
  pets prohibited on the Ridge, Saddle and Ridge Access trails.

## Design

### Notices

One vocabulary for both sources. A **notice** is one dated, sourced statement:

- `kind`: `permit` (to walk), `camp` (permit, fee or reservation to camp at a site), `ticket` (a day-use or timed
  ticket for a hike), `fee` (park entrance), `closure`, `caution`, `rule` (dogs, hours, season, pets), `info`.
- `text`: plain words, HTML stripped, at most a few hundred characters, the source's wording kept.
- `applies_to`: what it is attached to: a trail name on the route (with the miles the route spends on it), a
  trailhead (start or finish), a camp of the plan, the destination, or the park.
- `source`: `OpenStreetMap` (with the way or site id as provenance, never shown as raw text in the UI; the cut's
  extract date as the date) or `National Park Service` (the record's `lastIndexedDate` and the time it was fetched).
- `links`: http(s) URLs from the source (`operator`, `website`, alert `url`, record `url`), shown as links.

Ordering within "Before you go": route-matched closures, route-matched permits/tickets/camps, park-wide closures,
park-wide permit notices, entrance fee, rules, cautions, info, then the fixed last line:
"Everything else (water, conditions, permits and closures the sources don't list): unknown."

### From map tags (every area)

- `ingest.KEEP_TAGS` grows by `dog`, `fee`, `reservation`, `permit`, `operator`, `website`, `note`,
  `description`, `opening_hours`, `seasonal`, `damage:date`, `damage:type`. Edges carry them; the graph is built
  from the cuts already on disk, so no area needs re-cutting. Permit ways stay routable (unchanged) and mark the
  edge `permit`. **Permit follows OSM access semantics** (my call, matching `access_exclusion_reason`): `foot` is
  more specific than `access`, so an edge is `permit` when `foot=permit`, or when `access=permit` and `foot` is
  absent or not one of yes/designated/permissive. Shenandoah's `access=permit, foot=designated` doubletracks are
  not permit trails on foot and are not removed by `avoid_permit`.
- For a route: one `permit` notice per named trail with permit edges ("Permit to hike Angels Landing Trail, 0.9 mi
  of this trip. Reservation required; fee. Source: OpenStreetMap tags, data of 2026-10-26", with the `operator`
  URL as a link); one `rule` notice per dog value ("Dogs: no, on 2.3 mi (Angels Landing Trail, West Rim Trail)");
  `opening_hours` and `seasonal` quoted per trail; a `note` or `description` on a **trail** way (never a road: a
  road's "closed to private vehicles" is about cars) that contains "closed" or "closure" is a `closure` notice on
  that trail (quoted; the route still stands because the tag is routable).
- Closed ways: `access=no|private` and `foot=no` are excluded today. When the destination, a must-visit or the
  finish resolves within 400 m of an excluded way with a `note`, `description` or `damage:*`, the existing
  "nothing reaches" note gains the reason: "Hidden Canyon is tagged closed in OpenStreetMap: 'Closed indefinitely
  due to 2019 rockfall.' (landslide, 2019-08-24)."
- Camp sites: the feature index keeps `access`, `fee`, `reservation`, `permit`, `operator`, `website`,
  `capacity:persons`, `backcountry`. A camp of the plan with a known site within today's 500 m gets a `camp`
  notice quoting them ("Camp 1 near La Verkin Creek 7 (OpenStreetMap): permit, fee, reservation required, 2
  people."). A day hike has no camps and so no `camp` notice.
- `avoid_permit` (request setting; `--avoid-permit` on the CLI; "Avoid trails that need a permit" in the form):
  excludes permit-tagged edges exactly as `--avoid` excludes names. A destination that is then unreachable gets the
  reason in its note.

### From the National Park Service (NPS units only)

- **Area to park.** A cached parks list (`data/nps/parks.json`, refreshed after 30 days or when missing). An area
  maps to an NPS unit by, in order: the unit whose normalised `fullName` equals the area's OSM name; a unit whose
  point lies inside the area's outline; the smallest protected area in the US index that contains the area's centre
  (a drawn box, a tapped point, or a named district such as the Needles) and whose name equals a unit's `fullName`
  (my call: boxes today carry no containing area, so this lookup runs at mapping time, on the server, where the
  index is; without the index the step is skipped). A combined short-name area ("Zion") takes its park member. No
  match: no NPS notices, and the block says "No official notice feed for this area." Mapping is computed once per
  loaded area.
- **Fetching.** `nps.py`, standard library only, like the Overpass client: fixed host, 10 s timeout, responses
  capped at 5 MB and parsed as JSON only, the key read from `BACKCOUNTRY_NPS_API_KEY` and sent as a header, never in
  the URL, never logged. Per park: `/alerts` and `/thingstodo` (all pages, limit 50) and the park's `/parks` entry.
  Cached under `data/nps/<parkCode>/` (`parkCode` must match `^[a-z0-9]{2,8}$`) with `fetched_at`. Alerts refresh
  after 6 hours, things to do and the park entry after 7 days. A failed refresh keeps the cache and marks it
  "couldn't refresh; last checked <time>". No key: nothing is fetched; the block and the server's startup summary
  say "Park notices from the National Park Service aren't set up on this server." The fetch starts whenever an
  area is loaded into memory (prepared parks and hand-made regions never pass through the area worker, so this is
  the region store's load, on a small background thread, not the worker); a plan never waits for it, and uses
  whatever cache exists at the time, dated.
- **Alert kinds** (my call on precedence): a title containing "closed" or "closure" gives `closure`; else a title
  containing "permit", "ticket" or "reservation" gives `permit` (`ticket` when it says ticket); else the NPS
  category: Park Closure gives `closure`, Danger and Caution give `caution`; else text containing those permit
  words gives `permit`/`ticket`; else `info`. The full text is always quoted, so an Information alert that mentions
  both a parking closure and tickets loses nothing by landing in one kind. Every alert is a park-wide notice.
- **Matching a notice to the route** (my call; the point-distance rule I first wrote is dropped because NPS places
  its records at trailheads, where it would attach the Angels Landing record to every Grotto trip). Route items are
  the trails on the route, the start and finish trailheads, the destination, and the camps' sites. Trailhead names
  from the data are split on " | " and " @ " into their parts. A notice matches an item when its title or text
  and the item's name share a run of two or more consecutive whole words, case-insensitively, that carries every
  word of the item's name outside a short generic list (trail, trailhead, trailheads, road, loop, lot, parking,
  mountain, peak, lake, creek, the, to, and, of). Direction words stay significant (amended after the review: with
  them generic, "South Kaibab Trail closed" attached to the North Kaibab Trail and "East Rim Trail closed" to the
  West Rim Trail). So "Road to Lava Point and West Rim Trailhead closed" matches the start's part "West Rim Trail" on
  "West Rim"; "Ticket Required to Hike Old Rag" matches the destination "Old Rag Mountain" on "Old Rag"; "Watchman
  Trail closed" matches "Watchman Trail"; the Zion "Angels Landing" record does not match a West Rim day hike, whose
  items never say Angels Landing. Hike records match on their title only (their tags name neighbours too). A matched
  notice is shown first with its attachment ("Attached to: trail: West Rim Trail.").
- **Things to do** records are matched by the same rule on their title and tags. A matched record yields:
  `ticket` or `permit` when `isReservationRequired` is true (the `reservationDescription` text; "ticket" when the
  text says ticket, else "permit"); `fee` when `doFeesApply` is true and the fee text says more than the entrance
  fee; `rule` for pets when `arePetsPermitted` is false or restricted; `rule` for `seasonDescription` and
  `timeOfDayDescription` when non-empty (that is where Zion states "Overnight trips require a Wilderness Permit";
  it is quoted, not interpreted). Record `url` is the link. Unmatched records are not shown.
- **Refreshing** (my call). Notices are a dated snapshot, not part of the route, so they may be refreshed without
  the Accept step that trail data needs; the route itself never changes. `GET /runs/{id}/notices?candidate=n`
  re-matches a stored run against the park's current cache. The trip view asks for it whenever it opens online and
  shows the result as the block, "checked <time>", marking items that weren't there before as "new since you
  saved"; a saved trip stores the refreshed snapshot so the offline copy is the newest seen. A plan made before a
  park's first fetch has finished returns `notices_pending: true` and the trip view keeps asking that endpoint until
  it clears, the way area status is polled today.
- **Entrance fee**: one `fee` notice from the park entry: the park name, up to three fee titles with costs, and
  the park's fees page as the link. "Fee free" parks say so, quoted.
- Text is HTML-stripped; links are extracted from `href` and kept only when http(s); text is cut at 400 characters
  with an ellipsis.

### Where it shows

- **Plan screen**, under the area name once the fetch has returned: "Park notices: 2 closures, 4 more · National
  Park Service, checked 10:04" opening a sheet with closures first, each with its date and link. Areas without a
  feed show nothing here. The same data is `GET /api/regions/{id}/notices`.
- **Compare cards**: chips `permit`, `ticket`, `closure` when a candidate has a route-matched notice of that kind.
- **Trip view**: "Before you go" above the days, then the existing sections. Sketch:

```
Before you go
  Ticket required to hike Old Rag: March 1 – November 30, day-use ticket
  before arriving.                      National Park Service alert, 2024-01-17 · link
      Attached to: destination, Old Rag Mountain
  Old Rag Circuit: reservation required; "$1 Old Rag day-use ticket";
  park entrance pass also required.     National Park Service, checked 2026-11-26 · link
  Infrastructure improvements to Old Rag Trailhead: main parking lot closed
  until early 2027; day-use tickets still required.
                                        National Park Service alert, 2026-11-19
  Entrance fee: Shenandoah National Park, private vehicle $30, per person $15.
                                        National Park Service, checked 2026-11-26 · link
  Pets: prohibited on the Ridge, Saddle and Ridge Access trails.
                                        National Park Service, Old Rag Circuit record
  Everything else (water, conditions, permits and closures the sources
  don't list): unknown.
```

- **GPX summary.md and map.html**: the same block replaces the first line of "Not checked by this tool"; the
  unknown line stays.
- **Chat**: the fact sheet gains a "Before you go" section with one line per notice. The honesty check, which today
  withholds any permit, closure or "allowed" claim, lets such a claim through only when the clause restates a
  notice: it names an item that appears in the notices and makes the same kind of statement (permit, ticket, closed,
  fee). A negative or absence claim ("you don't need a permit", "it's open", "no closures") is withheld always, as
  the non-goals require (my call, closing the gap the reviewer found). The model's instructions change to match:
  quote the notices, never conclude from their absence.
- **Saved and offline trips**: notices are part of the run payload and travel with the trip, dates included.
  Online reopens refresh them as described under Refreshing; offline the saved snapshot shows with its dates.
- **CLI**: `generate --avoid-permit`; `summary.md` as above; `backcountry notices --region <id>` prints the park's
  current notices (so an operator can see what the server would show).

### Server, settings, security

- `Settings.nps_key` (from `BACKCOUNTRY_NPS_API_KEY`), `Settings.nps_dir` (`data/nps`, added to `.gitignore` like
  the other data folders). `.env.example`
  gains the line with no value and a comment on where a free key comes from
  (https://www.nps.gov/subjects/developer/get-started.htm); `DEMO_KEY` works for a first try.
- The startup summary prints whether park notices are on and the cache's size.
- The key is sent as a header, kept out of URLs, logs, error messages and payloads; a test asserts none of the
  serialised run, the notices endpoint or the log lines contain it.
- Notice text is escaped by the web app's existing `esc()`; links are rendered only for `http(s)://` values; no
  `innerHTML` with source text anywhere.
- Rate: at most one fetch per park per TTL window, so a server serving many parks stays far under 1,000 an hour;
  concurrent opens of the same park share one fetch (the per-key build lock pattern already used for areas).
- Disk: the NPS cache is small (tens of KB per park); it is not counted against the area budget and is not pruned
  with an area's eviction (parks are shared by every area inside them; a stale file is simply refreshed when next
  used).
- When the National Park Service can't be reached at all (the parks list fails: a bad key, a rate limit), the block
  says so with the time of the last try ("Couldn't reach the National Park Service; last tried hh:mm. Park notices:
  unknown.") rather than staying pending; the next try comes a minute later. A failed refresh of one park's data
  shows its cached notices with "Couldn't refresh park notices; last checked hh:mm." and is not retried for a minute.

### Acceptance checks (real data, this laptop)

1. **Old Rag** (Shenandoah, from the Old Rag parking area, day hike): the block shows the day-use ticket alert
   with its dates and link, the Old Rag Circuit record's reservation and fee text, the parking lot closure, the
   entrance fee, and the pets rule. Nothing says the ticket isn't needed in winter: the alert's own dates are
   quoted as written.
2. **Angels Landing** day hike from the Grotto: a `permit` notice from the OSM tag (with the recreation.gov link
   from `operator`) and the NPS permit alert; the entrance fee; no `camp` notice. With "Avoid trails that need a
   permit" the destination is unreachable and the note says the trail needs a permit.
3. **West Rim** one way, Lava Point to the Grotto, day hike: no walking permit, the Lava Point road closure alert
   attached to the start trailhead. The same with 1 night at a Zion wilderness site: a `camp` notice quoting the
   site's tags.
4. **Dolly Sods** (Forest Service): "No official notice feed for this area"; map-tag rules only; every existing
   test and golden route unchanged.
5. **No key**: the server starts, plans work, the block says notices aren't set up. **Key but offline**: cached
   notices shown with "couldn't refresh; last checked <time>".
6. **Timing**: open times and plan times within measurement noise of today's (`scripts/measure-speed`).

### Left to the builder

- Module layout (`nps.py`, `notices.py` suggested), cache file names, pagination details, the exact HTML-to-text
  routine, test fixtures recorded from the real responses above (trimmed; no key in fixtures).
- Name normalisation for area-to-park matching beyond "lowercase, strip punctuation, '&' to 'and'", and the exact
  generic-word list for route matching beyond the one given.
- Whether "permits, closures" leave the fixed "not checked" list in `export.KNOWN_GAPS` (they should, since the
  block now covers them, with the unknown line keeping the caveat).
- Wording of chips and headings beyond the examples; where the notices sheet sits on small screens.
- Whether `backcountry notices` also accepts a park code.
