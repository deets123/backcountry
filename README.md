# Backcountry route engine

Generates multi-day backpacking loops from real trail data (OpenStreetMap), with elevation
from USGS, split into days with camp points, exported as GPX for CalTopo or Garmin.

Every route is assembled from real trail segments; nothing is drawn. Water, campsites,
permits, closures and trail conditions are **not checked**: the outputs list map features
with their sources and say "unknown" everywhere else. Nothing here says a route is safe.

## Running it

All commands run from this folder in a terminal. `uv` installs everything on first use.

One-time setup per region (cached afterwards; re-run with `--refresh` to update):

```
uv run backcountry ingest --region dolly_sods     # trail data from OpenStreetMap
uv run backcountry dem --region dolly_sods        # ~10 m elevation data from USGS 3DEP
uv run backcountry verify --region dolly_sods     # optional: cross-check against USFS trail data
```

Generate a trip:

```
uv run backcountry generate --region dolly_sods --trailhead bear_rocks \
    --nights 2 --total 18-26 --daily 6-11 --max-gain-day 2500
```

| Option | Meaning |
|---|---|
| `--trailhead` | an id from `regions/<region>.yaml`, an OSM node id, or `lat,lon` |
| `--total 18-26` | total loop distance range, miles |
| `--nights 2` | split into nights+1 days (needs `--daily`) |
| `--daily 6-11` | per-day distance range, miles |
| `--max-gain-day 2500` | per-day climbing cap, feet |
| `--type loop|lollipop|any` | loop shape (default any) |
| `--camps-at-sites` | camps only at known campsites on the route |
| `--camp 8.5` / `--camp lat,lon` | pin a camp (repeatable); the planner places the rest |
| `--must-visit X` | a spot or trail the loop must include (trailhead id, node id, `lat,lon`, or trail name); repeatable |
| `--avoid X` | a trail/road name or way id to stay off; repeatable |
| `--direction cw|ccw` | walk the loop clockwise or counterclockwise |
| `--no-roads`, `--road-weight N` | forbid roads, or make a road mile cost N trail miles |
| `--basemap topo|hybrid|satellite` | map background |
| `--count 3` | how many candidates |

Results go to `out/loops/<trailhead>_<total>mi/`:

- `candidates.md`, `candidates_map.html`: all candidates side by side
- `candidate_<i>/route.gpx`: one track per day plus Start/End/camp/junction waypoints (import this)
- `candidate_<i>/day_<n>.gpx`: one day per file
- `candidate_<i>/map.html`, `summary.md`, `route.geojson`
- `diff.md` when you re-run into the same folder: what changed since last time

### API

`uv run backcountry serve --host 0.0.0.0` runs the engine as a local web service (the web app
in Phase 2 piece 2 uses it). Endpoints under `/api`: `regions`, `regions/{id}/trailheads`,
`regions/{id}/trails`, `regions/{id}/features?bbox=w,s,e,n`, `POST regions/{id}/generate`
(same settings as the command, as JSON), `runs/{id}`, `runs/{id}/candidates/{n}.gpx[?day=k]`,
`runs/{id}/candidates/{n}.geojson`. Runs are saved under `data/runs/`.

### Web app (phone or laptop)

One-time: `cd web && npm install && npm run build`. Then `uv run backcountry serve --host 0.0.0.0`
and open `http://<your-laptop-ip>:8000` on your phone (same Wi-Fi) or `http://localhost:8000` on the
laptop. Pick a trailhead on the map, set the limits, Generate, compare the candidates, open one,
download its GPX. Add it to your home screen for an app-style window.

Development: `uv run backcountry serve` in one terminal, `cd web && npm run dev` in another, open
http://localhost:5173. Tests: `npm test` (unit), `npm run test:e2e` (one end-to-end run against the API
on synthetic data; needs `npx playwright install chromium` once).

### Offline trips (phone)

Saving trips and showing your position need a secure (https) address. Plain `http://<laptop-ip>:8000`
works for planning only. The simplest secure address is Tailscale (free):

1. Install Tailscale on the laptop and the phone; sign in to the same account on both.
2. In the Tailscale admin console, turn on HTTPS certificates (DNS → HTTPS Certificates). Once.
3. On the laptop: `uv run backcountry serve`, then `tailscale serve --bg 8000`.
4. On the phone, with Tailscale on, open `https://<laptop-name>.<tailnet>.ts.net` (the address
   `tailscale serve` prints). Add it to your home screen.

Then: open a trip and tap **Save for offline** (on Wi-Fi; the 19 mi Bear Rocks loop is about 1,400 map tiles, 30 MB, and took 1.5 minutes).
With no signal, the app opens on **Saved trips**. In a saved trip, **Show my location** gives your mile,
day, and distance and climb to the next camp; drag a camp marker along the route (or tap **Camp here**)
to move a camp, and the days are recomputed on the phone. Saved trips can be deleted from the list.
Map tiles: USGS The National Map (public domain).

Other commands: `route --from A --to B` (shortest point-to-point), `trailheads` (candidate
trailheads from the data), `qa` (data quality report and map), `reference` (export a trail
for a CalTopo comparison). `uv run backcountry --help` lists them all.

### Chat (local model)

The Compare and Trip views have a chat box: "make day 2 shorter", "3 nights instead", "avoid roads" become a
proposed settings change you run with one tap; "which day is hardest?" is answered only from the trip's numbers.
Replies with numbers, names or claims the trip's data doesn't support are withheld ("I can't answer that from
this trip's data."). Nothing is run until you tap Run.

It uses a local model through [Ollama](https://ollama.com) (free; nothing leaves the laptop): install Ollama,
`ollama pull <model>`, and optionally copy `.env.example` to `.env` to pick the model (`BACKCOUNTRY_LLM`). Never
commit `.env`. Compare models on the test set with
`uv run backcountry chat-eval --model ollama:<model> [--model …]` (cases in `evals/chat_cases.yaml`).

### Difficulty levels

Every day and every trip is labeled **easy**, **moderate**, **hard** or **strenuous**, with a short reason
("Hard · steep"); tap a day for the numbers behind it. Levels are computed only from the trail and elevation
data (`src/backcountry/difficulty.py`):

- **Effort**: Shenandoah National Park's formula, √(2 × feet climbed × miles). Descent doesn't count.
- **Steep**: a sustained climb of 15%+ over 400 m is tagged "steep"; 30%+ raises the level by one, never into
  strenuous.
- **Terrain**: OpenStreetMap `sac_scale` and `trail_visibility` tags ("scramble (tagged)", "demanding trail
  (tagged)", "hard to follow (tagged)") make a day at least hard. Where OSM has no terrain tags, the details say
  "terrain unknown"; most trails have none.
- **Two scales**: a day hike and a day of a backpacking trip use different cutoffs (a pack makes the same miles
  harder). A trip is as hard as its hardest day. No elevation data means "Level unknown", never a guess.

Set days individually in the plan form (the day strip: tap a day, pick a level or type miles, climbing and
grade) or in the chat ("day 1 easy, days 2 and 3 hard", "we start late on day 1"), and filter by trip level.
When no loop meets every day's target, the closest one says which days miss and suggests one change the engine
re-checked. The levels were tuned against trips the user has hiked (`tests/calibration/`); the reference tracks
are local only and never committed.

## Regions

Two hand-made regions exist: `regions/dolly_sods.yaml` (Dolly Sods Wilderness, WV) and `regions/pemi.yaml`
(Pemigewasset Wilderness, NH). A region is a bounding box, a projected CRS, and a trailhead list;
nothing region-specific lives in the code. To add one, copy a yaml, adjust it, and run `ingest`,
`dem`, and optionally `verify` for it. Any other US area comes from the local US index (next section).

## Any US area

The web app's "Where" box searches every US park, wilderness, forest and other protected area, plus peaks,
places and named trails ("Zion", "Chesler Park", "Linville Gorge"), and "Plan in this map area" uses the map on
screen. A park opens whole; a place opens the park it's in. The first time an area is opened the server cuts its
trails out of a local US index (seconds: the trails show at once) and then fetches elevation from USGS for the
cells trails pass through ("Adding elevation…"; Zion 17 s, Yosemite 40 s, Grand Canyon 74 s on this laptop);
after that it opens at once. A short name opens touching areas together ("Zion": the park and the Zion
Wilderness); a full name ("Zion National Park", "Zion NP") opens that one. NP, NF, NM, NRA, SP and W/WA are
understood.

Searching a place (a peak, a viewpoint, a named place or trail) makes it the trip's destination: the start list
shows only the starts that reach it within the trip's miles, nearest first, and every trip goes to and through
it (an out-and-back turns there; a longer trip takes it as a side trip). If no mapped trail reaches a summit the
trip says how far the nearest trail point is.

Trip shapes: loop, lollipop, out and back, one way, or any (loop, lollipop or out and back). One way lists up to
five finishing trailheads that fit the miles (with gain, loss and the straight line between the trailheads); the
trip has Google Maps and Apple Maps buttons for the shuttle drive. The app never shows a drive time itself.
"Day hike" plans a trip with no nights.

Planning shows the first trips after about 8 seconds while the search goes on, and stops by 60 seconds.

Prepare popular areas ahead (national parks, then monuments and recreation areas, then the wilderness areas with
the most trail miles), so they open at once. Long: run it overnight, plugged in:

```
caffeinate -i uv run backcountry areas prepare-popular   # stops at BACKCOUNTRY_PREPARED_GB (default 8)
```

Prepared areas are listed in `data/areas/prepared.json`; areas people open never remove them.

Operator setup, once (then about monthly):

```
uv run backcountry usdata refresh     # download the Geofabrik US extract (checksum-checked) and build the index
uv run backcountry usdata status      # which extract and index are in use
```

`refresh` is `usdata download` then `usdata build`. Measured on this laptop (2026-10-27): the download is
12.2 GB (13 min); the build took about 7 hours, peaks at 4.4 GB of memory, and needs about 26 GB of temporary
space (deleted afterwards); the index is 23.3 GB. The build prints progress lines; from the second build on they
include a percentage and time left. After a build, the downloaded `.osm.pbf` can be deleted (only a rebuild needs
it). Older indexes and the areas cut from them are kept, so saved trips keep working; delete old `us-*.sqlite`
files in `data/osm/` by hand when the new one is in use.

Opened areas live in `data/raw/<area>/` and `data/areas/` (git-ignored): Zion about 55 MB, Yosemite 170 MB,
Grand Canyon 310 MB. Areas over 5,000 km² (Yellowstone, Death Valley) open as a 24 km square, shown as "Part of
…": around the place or map spot you picked, or the park's middle when you search the park itself. After a
`refresh`, an area opened before it gets the newer trail data the next time it is opened (or when a saved trip there
checks for an update). Server limits (environment variables): `BACKCOUNTRY_AREA_MAX_QUEUE` (3 waiting), `BACKCOUNTRY_AREA_PER_HOUR`
(6 new areas an hour), `BACKCOUNTRY_AREA_DISK_GB` (40; the least recently opened areas no saved trip uses are
removed first), `BACKCOUNTRY_PREPARED_GB` (8; the share kept for prepared areas), `BACKCOUNTRY_PLAN_JOBS` (2 plans at
once, for everyone). `BACKCOUNTRY_OSM_DIR` and `BACKCOUNTRY_AREAS_DIR` move the index and generated areas.
`scripts/measure-speed [--fresh]` times opening and planning in Zion, Yosemite and Grand Canyon, step by step.

Saved trips: when a saved trip is opened online and its area has newer trail data, the app offers to update it
and says about how much it would download. "Review changes" re-plans the same route on the new data and lists what
changed; only "Accept" replaces the trip. Nothing updates on its own.

Users only ever send a search result, a box or a point. Downloads come from fixed hosts (Geofabrik, USGS) and are
read as data, never run.

## Development

`uv run pytest` runs the tests; `npm test` and `npm run test:e2e` in `web/` run the web app's unit and browser
tests. GitHub Actions (`.github/workflows/tests.yml`) runs all three on every pull request and push to `main`;
tests that need the cached OSM/elevation data skip there. `scripts/heavy-tests` runs what GitHub can't: the whole
suite against the cached data, plus `chat-eval` against Ollama when the chat changed (`--eval` to force it). Turn
it on before every push with `git config core.hooksPath .githooks` (skip once with `git push --no-verify`). See `CLAUDE.md` for the project rules, `docs/ROADMAP.md` for
the plan, and `PROGRESS.md` for what's done, what's known to be wrong, and the decisions made.

Trail data © OpenStreetMap contributors, ODbL. Elevation: USGS 3D Elevation Program.
Cross-check data: USDA Forest Service Enterprise Data Warehouse. Base maps: USGS The National Map.

## License

MIT (see `LICENSE`). The trail data the tool downloads is © OpenStreetMap contributors under
the ODbL, which is separate from this code license; keep the attribution the outputs carry.
