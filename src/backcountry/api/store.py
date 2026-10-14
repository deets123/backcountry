"""Loaded regions (graph built once per process) and saved runs."""

import json
import logging
import os
import random
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backcountry import ingest
from backcountry.graph import build_graph, qa_summary
from backcountry.region import REGIONS_DIR, REPO_ROOT, load_region


class NotFound(KeyError):
    """A region or run that doesn't exist (or isn't cached). Only this becomes a 404."""


@dataclass
class Settings:
    raw_dir: Path | None = None
    runs_dir: Path | None = None
    regions_dir: Path | None = None

    @classmethod
    def default(cls) -> "Settings":
        return cls(raw_dir=ingest.RAW_DIR, runs_dir=REPO_ROOT / "data" / "runs", regions_dir=REGIONS_DIR)


@dataclass
class LoadedRegion:
    region: object
    raw: dict
    tg: object
    summary: dict
    dem: object = None
    builds = 0  # class-level counter, for tests

    @property
    def node_component(self) -> dict:
        return self.summary["node_component"]


class RegionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._loaded: dict[str, LoadedRegion] = {}
        self._lock = threading.Lock()  # endpoints run in a threadpool; build each region once

    def ids(self) -> list[str]:
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

    def get(self, region_id: str) -> LoadedRegion:
        if region_id in self._loaded:
            return self._loaded[region_id]
        with self._lock:
            if region_id in self._loaded:
                return self._loaded[region_id]
            if region_id not in self.ids():
                raise NotFound(f"No region {region_id!r}")
            region = load_region(region_id, self.settings.regions_dir)
            try:
                raw = ingest.load_raw(region, self.settings.raw_dir)
            except FileNotFoundError as e:  # names `backcountry ingest`
                raise NotFound(str(e)) from e
            tg = build_graph(raw, region.crs)
            loaded = LoadedRegion(region=region, raw=raw, tg=tg, summary=qa_summary(tg),
                                  dem=ingest.load_dem(region, raw_dir=self.settings.raw_dir))
            LoadedRegion.builds += 1
            self._loaded[region_id] = loaded
            return loaded


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
            index[run["run_id"]] = {"region": run["region"], "trailhead": run["request"]["trailhead"]}
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
