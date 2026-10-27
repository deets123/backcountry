"""Loaded regions (graph built once per process) and saved runs."""

import json
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backcountry import ingest
from backcountry.graph import build_graph, qa_summary
from backcountry.region import AREAS_DIR, REGIONS_DIR, REPO_ROOT, load_region, region_path


class NotFound(KeyError):
    """A region or run that doesn't exist (or isn't cached). Only this becomes a 404."""


@dataclass
class Settings:
    raw_dir: Path | None = None
    runs_dir: Path | None = None
    regions_dir: Path | None = None
    areas_dir: Path | None = None  # generated areas; None (tests) means there are none
    osm_dir: Path | None = None    # the local US index; None means search finds hand-made regions only
    max_loaded: int = 4            # areas kept built in memory (least recently used goes first)
    max_loaded_bytes: float = 3e9  # ...and never more than about this much of them (raw data plus elevation)
    area_limits: dict | None = None  # {max_queue, per_hour, disk_gb}; defaults in api.area_worker
    area_thread: bool = True       # prepare areas on a background thread (tests run them inline)
    prepared_gb: float = 8.0       # disk share reserved for areas prepared ahead (backcountry areas prepare-popular)
    plan_jobs: int = 2             # plans running at once, for everyone (BACKCOUNTRY_PLAN_JOBS)

    @classmethod
    def default(cls) -> "Settings":
        from backcountry import usdata

        env = {k: os.environ.get(f"BACKCOUNTRY_AREA_{k.upper()}") for k in ("max_queue", "per_hour", "disk_gb")}
        limits = {k: (float(v) if k == "disk_gb" else int(v)) for k, v in env.items() if v}
        return cls(raw_dir=ingest.RAW_DIR, runs_dir=REPO_ROOT / "data" / "runs", regions_dir=REGIONS_DIR,
                   areas_dir=AREAS_DIR, osm_dir=usdata.OSM_DIR, area_limits=limits or None,
                   prepared_gb=float(os.environ.get("BACKCOUNTRY_PREPARED_GB") or 8.0),
                   plan_jobs=int(os.environ.get("BACKCOUNTRY_PLAN_JOBS") or 2))

    @property
    def areas(self) -> Path:
        return self.areas_dir or Path("/nonexistent-backcountry-areas")


@dataclass
class LoadedRegion:
    region: object
    raw: dict
    tg: object
    summary: dict
    dem: object = None
    cut: str | None = None  # extract date of a generated area's data; None for hand-made regions
    timings: dict = field(default_factory=dict)  # seconds to build the graph, its QA summary and load the DEM
    _features: object = None
    bytes: int = 0  # about what this area holds in memory: its raw data (parsed) and the elevation blocks read
    builds = 0  # class-level counter, for tests

    @property
    def node_component(self) -> dict:
        return self.summary["node_component"]

    _trailheads: list | None = None

    def trailheads(self) -> list[dict]:
        """The area's trailheads (yaml entries, then start points found in the data), found once per loaded area."""
        if self._trailheads is None:
            from backcountry.trailheads import trailhead_list

            self._trailheads = trailhead_list(self.region, self.tg, self.raw, self.node_component)
        return self._trailheads

    def names(self) -> dict[int, str]:
        from backcountry.trailheads import trailhead_names

        return trailhead_names(self.trailheads())

    def feature_index(self, raw_dir=None):
        """The area's features in a spatial tree, built on first use and kept with the loaded area."""
        if self._features is None:
            from backcountry.planning import feature_index

            self._features = feature_index(self.region, self.raw, self.tg, raw_dir)
        return self._features


class RegionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._loaded: OrderedDict[tuple, LoadedRegion] = OrderedDict()
        self._building: dict[tuple, threading.Lock] = {}
        self._lock = threading.Lock()  # endpoints run in a threadpool; build each region once

    def ids(self) -> list[str]:
        """Hand-made regions (regions/*.yaml). Generated areas are listed by recent_areas()."""
        return sorted(p.stem for p in (self.settings.regions_dir or REGIONS_DIR).glob("*.yaml"))

    def summaries(self) -> list[dict]:
        out = []
        for rid in self.ids():
            region = load_region(rid, self.settings.regions_dir)
            cache = ingest.latest_cache(region, self.settings.raw_dir)
            out.append({
                "id": region.id, "name": region.name, "crs": region.crs, "bbox": vars(region.bbox),
                "osm_retrieved_at": json.loads(cache.read_text())["retrieved_at"] if cache else None,
                "dem": ingest.latest_cache(region, self.settings.raw_dir, prefix="dem13") is not None,
                "usfs": ingest.latest_cache(region, self.settings.raw_dir, prefix="usfs") is not None,
                "trailheads": len(region.trailheads),
            })
        return out

    def recent_areas(self, limit: int = 8) -> list[dict]:
        """Generated areas that have trail data, most recently opened first."""
        def mtime(p):
            try:
                return p.stat().st_mtime
            except FileNotFoundError:  # removed between the listing and now
                return 0.0
        paths = sorted(self.settings.areas.glob("*.yaml"), key=mtime, reverse=True)
        out = []
        for p in paths:
            try:
                region = load_region(p.stem, Path("/nonexistent"), self.settings.areas)
            except (FileNotFoundError, ValueError, KeyError):
                continue
            if not ingest.cuts(region, self.settings.raw_dir):
                continue  # still being prepared, or preparation failed
            area = region.area or {}
            out.append({"id": region.id, "name": region.name, "type_label": area.get("type_label"),
                        "state": area.get("state"), "bbox": vars(region.bbox)})
            if len(out) == limit:
                break
        return out

    def forget(self, region_id: str) -> None:
        """Drop a removed area's built graphs."""
        with self._lock:
            for key in [k for k in self._loaded if k[0] == region_id]:
                del self._loaded[key]

    def _region(self, region_id: str):
        try:
            return load_region(region_id, self.settings.regions_dir, self.settings.areas)
        except FileNotFoundError as e:
            raise NotFound(f"No region {region_id!r}") from e

    def get(self, region_id: str, cut: str | None = None) -> LoadedRegion:
        """The region's built graph. For a generated area, `cut` picks an extract date (default: the newest);
        hand-made regions have one cache and ignore it."""
        region = self._region(region_id)
        if region.generated:
            cuts = ingest.cuts(region, self.settings.raw_dir)
            if cut is None:
                if not cuts:
                    raise NotFound(f"Area {region_id!r} isn't ready yet.")
                cut = cuts[-1]
            elif cut not in cuts:
                raise NotFound(f"Area {region_id!r} has no trail data from {cut}.")
            try:
                os.utime(region_path(region_id, self.settings.regions_dir, self.settings.areas))  # for recent_areas
            except OSError:
                pass
        else:
            cut = None
        key = (region_id, cut)
        with self._lock:
            if key in self._loaded:
                self._loaded.move_to_end(key)
                return self._loaded[key]
            building = self._building.setdefault(key, threading.Lock())
        with building:  # one build per area; other areas aren't held up by this one's 10 s graph build
            with self._lock:
                if key in self._loaded:
                    self._loaded.move_to_end(key)
                    return self._loaded[key]
            try:
                raw = ingest.load_raw(region, self.settings.raw_dir, cut)
            except FileNotFoundError as e:  # names `backcountry ingest`
                raise NotFound(str(e)) from e
            t0 = time.perf_counter()
            tg = build_graph(raw, region.crs)
            t1 = time.perf_counter()
            summary = qa_summary(tg)
            t2 = time.perf_counter()
            dem = ingest.load_dem(region, raw_dir=self.settings.raw_dir)
            timings = {"graph": round(t1 - t0, 2), "qa": round(t2 - t1, 2), "dem_load": round(time.perf_counter() - t2, 2)}
            size = os.path.getsize(raw["cache_path"]) if raw.get("cache_path") and os.path.exists(raw["cache_path"]) else 0
            loaded = LoadedRegion(region=region, raw=raw, tg=tg, summary=summary, dem=dem, cut=cut, timings=timings,
                                  bytes=2 * size + 1 + (dem.max_bytes if dem is not None and hasattr(dem, "max_bytes") else 0))
            LoadedRegion.builds += 1
            with self._lock:
                self._loaded[key] = loaded
                self._building.pop(key, None)
                while len(self._loaded) > max(1, self.settings.max_loaded) or (
                        len(self._loaded) > 1 and sum(x.bytes for x in self._loaded.values()) > self.settings.max_loaded_bytes):
                    self._loaded.popitem(last=False)
            return loaded

    def loaded_ids(self) -> set[str]:
        """Areas someone is using right now (built in memory)."""
        with self._lock:
            return {k[0] for k in self._loaded}


class RunStore:
    """Runs as data/runs/<run_id>.json, written atomically, with a small index.json for lookups."""

    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self._lock = threading.Lock()

    def new_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}-{random.randrange(16**4):04x}"

    def _write_atomic(self, path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)  # a half-written file never sits at the final name

    def _index(self) -> dict:
        path = self.runs_dir / "index.json"
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except json.JSONDecodeError:
            logging.getLogger("backcountry.api").warning("runs index unreadable; rebuilding")
            return {}

    def save(self, run: dict) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.runs_dir / f"{run['run_id']}.json"
        with self._lock:
            self._write_atomic(path, json.dumps(run))
            index = self._index()
            index[run["run_id"]] = {"region": run["region"], "trailhead": run["request"]["trailhead"],
                                    "cut": run.get("data_cut")}
            self._write_atomic(self.runs_dir / "index.json", json.dumps(index))
        return path

    def load(self, run_id: str) -> dict:
        path = self.runs_dir / f"{run_id}.json"
        if "/" in run_id or ".." in run_id or not path.exists():
            raise NotFound(f"No run {run_id!r}")
        return json.loads(path.read_text())

    def latest(self, region_id: str, trailhead: str) -> dict | None:
        """The most recent saved run for this region + trailhead (for the diff); unreadable files are skipped."""
        if not self.runs_dir.exists():
            return None
        index = self._index()
        candidates = sorted((rid for rid, meta in index.items()
                             if meta.get("region") == region_id and meta.get("trailhead") == trailhead), reverse=True)
        for rid in candidates:
            try:
                return self.load(rid)
            except (NotFound, json.JSONDecodeError):
                logging.getLogger("backcountry.api").warning("skipping unreadable run %s", rid)
        return None
