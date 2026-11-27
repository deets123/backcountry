"""Prepares new areas in the background, one at a time, within global limits (no accounts yet).

A request names an area by a search result's OSM ref, a box or a point; the spec comes from the local index,
never from the client. Limits: a short queue, a cap on new areas per hour, and a disk budget. When over budget,
the least-recently-opened generated areas that no saved run uses are removed first; hand-made regions and
anything a saved trip needs are never touched.
"""

import logging
import queue
import shutil
import threading
import time
from collections import deque
from pathlib import Path

from backcountry import areas, ingest, osm_index, usdata
from backcountry.region import load_region

DEFAULT_LIMITS = {"max_queue": 3, "per_hour": 6, "disk_gb": 40.0}
FAILED = "Couldn't get the trail data ready for this area. Try again later."
log = logging.getLogger("backcountry.api")


class Busy(RuntimeError):
    """Too many new areas right now (HTTP 429)."""


class NoRoom(RuntimeError):
    """The disk budget is used by areas saved trips need (HTTP 507)."""


class NoIndex(RuntimeError):
    """This server has no US index built (HTTP 503)."""


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


class AreaWorker:
    def __init__(self, settings, store, runs, dem_fetch=None):
        self.settings, self.store, self.runs = settings, store, runs
        self.dem_fetch = dem_fetch or ingest.fetch_dem
        self.limits = DEFAULT_LIMITS | (settings.area_limits or {})
        self.status: dict[str, dict] = {}
        self._queue: queue.Queue = queue.Queue()
        self._started: deque = deque()  # monotonic times of accepted new preparations
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._index_path: Path | None = None

    # --- the index ------------------------------------------------------------------------------------------

    def index(self):
        """A fresh read-only connection to the newest index (one per caller: sqlite connections aren't shared)."""
        path = usdata.current_index(self.settings.osm_dir) if self.settings.osm_dir else None
        if path is None:
            raise NoIndex("Search isn't set up on this server yet.")
        return osm_index.open_index(path)

    # --- requests -------------------------------------------------------------------------------------------

    def spec_for(self, body: dict) -> areas.AreaSpec:
        """The area a request names; ValueError (shown to the user) for anything else."""
        keys = [k for k in ("ref", "box", "point") if k in body]
        if len(keys) != 1 or len(body) != 1:
            raise ValueError("Send one of: a search result, a map box, or a point.")
        con = self.index()
        try:
            if keys[0] == "ref":
                return areas.area_from_ref(con, body["ref"])
            value = body[keys[0]]
            if keys[0] == "box":
                return areas.area_from_box(con, value)
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError("A point must be a longitude and latitude in degrees.")
            return areas.area_from_point(con, *value)
        finally:
            con.close()

    def _cuts(self, area_id: str) -> list[str]:
        try:
            region = load_region(area_id, Path("/nonexistent"), self.settings.areas)
        except FileNotFoundError:
            return []
        return ingest.cuts(region, self.settings.raw_dir)

    def _ready(self, area_id: str) -> bool:
        return bool(self._cuts(area_id))

    def _index_date(self) -> str | None:
        try:
            con = self.index()
        except NoIndex:
            return None
        try:
            return osm_index.index_meta(con)["extract_date"]
        finally:
            con.close()

    def request(self, spec: areas.AreaSpec) -> dict:
        """Ready if the area has a cut of the current index. An area opened before a refresh gets a new cut
        (quick: its elevation is cached; not counted against the hourly cap), or opens on its older cut when
        the queue is full. A new area is queued within the limits."""
        with self._lock:
            current = self.status.get(spec.id)
            if current and current["status"] in ("queued", "preparing"):
                return current
            cuts, date = self._cuts(spec.id), self._index_date()
            if cuts and (date is None or date in cuts):
                return self._set(spec, "ready")
            now = time.monotonic()
            while self._started and now - self._started[0] > 3600:
                self._started.popleft()
            if self._queue.qsize() >= self.limits["max_queue"]:
                if cuts:
                    return self._set(spec, "ready")  # usable on its older data; refreshed on a later open
                raise Busy("The server is busy setting up other areas. Try again in a few minutes.")
            if not cuts:
                if len(self._started) >= self.limits["per_hour"]:
                    raise Busy("Lots of new areas were opened in the last hour. Try again later, or pick one opened recently.")
                self._make_room()
                self._started.append(now)
            st = self._set(spec, "queued")
            self._queue.put(spec)
        self._ensure_thread()
        return st

    def newer_data(self, area_id: str) -> str | None:
        """The current index's date if this generated area has no cut of it yet (a refresh happened since it was
        opened); that cut is then queued quietly, so an update can follow. None otherwise."""
        cuts, date = self._cuts(area_id), self._index_date()
        if date is None or not cuts or date in cuts:
            return None
        region = load_region(area_id, Path("/nonexistent"), self.settings.areas)
        a = region.area or {}
        spec = areas.AreaSpec(id=region.id, name=a.get("name") or region.name, type_label=a.get("type_label") or "Area",
                              state=a.get("state"), bbox=region.bbox, osm=a.get("osm"), capped=bool(a.get("capped")))
        self.request(spec)  # a known area: never Busy, at worst left for a later open
        return date

    def _set(self, spec, status: str, message: str | None = None) -> dict:
        st = {"area_id": spec.id, "name": spec.display_name, "status": status,
              "message": message or f"Finding trails in {spec.display_name}…", "bbox": vars(spec.bbox)}
        self.status[spec.id] = st
        return st

    def status_of(self, area_id: str) -> dict:
        st = self.status.get(area_id)
        if st is None:
            if not self._ready(area_id):
                return {"status": "unknown", "message": None}
            region = load_region(area_id, Path("/nonexistent"), self.settings.areas)
            return {"status": "ready", "message": None, "name": region.name, "bbox": vars(region.bbox)}
        if st["status"] == "failed":
            return {"status": "failed", "message": st["message"], "name": st["name"]}
        return {k: st[k] for k in ("status", "message", "name", "bbox")}

    # --- disk budget ----------------------------------------------------------------------------------------

    def _referenced(self) -> set[str]:
        return {meta.get("region") for meta in (self.runs._index().values() if self.runs else [])}

    def _area_ids(self) -> list[Path]:
        return sorted(self.settings.areas.glob("*.yaml"), key=lambda p: p.stat().st_mtime)  # oldest first

    def _usage(self) -> int:
        raw = self.settings.raw_dir or ingest.RAW_DIR
        return sum(_dir_bytes(raw / p.stem) for p in self._area_ids())

    def _make_room(self) -> None:
        budget = self.limits["disk_gb"] * 1e9
        if self._usage() <= budget:
            return
        keep = self._referenced() | {k for k, st in self.status.items() if st["status"] in ("queued", "preparing")}
        raw = self.settings.raw_dir or ingest.RAW_DIR
        for p in self._area_ids():
            if p.stem in keep:
                continue
            shutil.rmtree(raw / p.stem, ignore_errors=True)
            p.unlink(missing_ok=True)
            self.status.pop(p.stem, None)
            self.store.forget(p.stem)
            log.info("removed unused area %s to stay within the disk budget", p.stem)
            if self._usage() <= budget:
                return
        if self._usage() > budget:
            raise NoRoom("The server is out of room for new areas right now.")

    # --- the worker -----------------------------------------------------------------------------------------

    def _ensure_thread(self) -> None:
        if not self.settings.area_thread:
            return  # tests call run_pending()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="area-worker", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            self._prepare(self._queue.get())

    def run_pending(self) -> None:
        while not self._queue.empty():
            self._prepare(self._queue.get())

    def _prepare(self, spec: areas.AreaSpec) -> None:
        self._set(spec, "preparing")
        try:
            con = self.index()
            try:
                out = areas.prepare_area(spec, con, raw_dir=self.settings.raw_dir, areas_dir=self.settings.areas,
                                         dem_fetch=self.dem_fetch)
            finally:
                con.close()
            log.info("area %s ready: %s", spec.id, out)
            self._set(spec, "ready", "")
        except ValueError as e:  # our own plain messages (e.g. no trails there)
            self._set(spec, "failed", str(e))
        except Exception:
            log.exception("preparing area %s failed", spec.id)  # details stay in the server log
            self._set(spec, "failed", FAILED)
