# Progress

## M0 — Project setup (done 2026-09-29)

**Done**
- `uv` project (`pyproject.toml`, Python 3.12 via `.python-version`, `requires-python >=3.11`),
  hatchling build, `backcountry` console script → `backcountry.cli:app`.
- Package skeleton per CLAUDE.md under `src/backcountry/`. Modules for later milestones
  (`elevation`, `loops`, `split`, `scoring`) are docstring-only stubs.
- Added `region.py` (not in the CLAUDE.md layout) to load/validate `regions/*.yaml`.
  It rejects inverted bboxes and any trailhead entry with coordinates that isn't `verified: true`.
- `regions/dolly_sods.yaml`: roadmap starting bbox (38.94–39.10, -79.42 to -79.26),
  `EPSG:32617`, empty `trailheads:`.
- `.gitignore` covers `data/raw`, `data/processed`, `out/`.
- Moved `ROADMAP.md` from the repo root to `docs/ROADMAP.md` to match CLAUDE.md.

**Acceptance:** `uv run pytest` → 5 passed. `uv run backcountry --help` lists `ingest`, `qa`, `trailheads`.

**Dependencies added:** folium, networkx, pyproj, shapely, typer (all in the CLAUDE.md stack),
plus `pyyaml` (not in the stack) to read `regions/*.yaml`. osmnx, geopandas, py3dep, gpxpy
are deferred until a milestone needs them.

**Known wrong / open questions**
- `docs/vision.md` doesn't exist yet. I haven't created it; that's your call.
