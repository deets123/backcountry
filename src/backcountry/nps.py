"""The National Park Service data service (developer.nps.gov): alerts, things to do and park entries, cached per
park on disk.

Everything fetched is quoted later with its date (notices.py); nothing here interprets it. The key travels only
in the X-Api-Key header, never in a URL, a log line or an error message. Responses are read as JSON, capped in
size, and a failed refresh keeps the cache and says so. Fetches never run inside a plan: the server kicks them
off when an area is loaded, and plans use whatever the cache holds at the time.
"""

import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi

from backcountry.ingest import USER_AGENT

HOST = "https://developer.nps.gov/api/v1"
PARK_CODE = re.compile(r"^[a-z0-9]{2,8}$")
ALERTS_TTL_S, THINGS_TTL_S, PARKS_TTL_S = 6 * 3600, 7 * 86400, 30 * 86400
MAX_BYTES, TIMEOUT_S, PAGE = 5_000_000, 10, 50
PARKS_RETRY_S = 60  # after a failed parks-list fetch, the next loaded area or request tries again this much later
PIECES = {"alerts": ALERTS_TTL_S, "things": THINGS_TTL_S, "park": THINGS_TTL_S}

log = logging.getLogger("backcountry.nps")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the key to wherever it points: a 3xx is a failure, never followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def default_fetch(url: str, headers: dict) -> bytes:
    """GET `url` with `headers`; at most MAX_BYTES, else ValueError. Network errors come through as they are; redirects
    are not followed."""
    request = urllib.request.Request(url, headers={**headers, "User-Agent": USER_AGENT})
    context = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))
    with opener.open(request, timeout=TIMEOUT_S) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response too big")
    return data


def _norm(name) -> str:
    """Names compared loosely: lowercase, "&" as "and", punctuation gone, one space between words."""
    text = str(name or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NpsClient:
    """Per-park cache of the park entry, alerts and things to do under `cache_dir`, refreshed past each piece's TTL.
    `fetch(url, headers) -> bytes` is swappable (tests); `clock` too. `index_opener()` gives a US-index connection
    for mapping an area to a park by its outline; `thread=False` runs refreshes inline."""

    def __init__(self, cache_dir, key: str | None, fetch=None, clock=time.time, index_opener=None, thread: bool = True):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.key = key or None
        self.fetch = fetch or default_fetch
        self.clock = clock
        self.index_opener = index_opener
        self.thread = thread
        self._lock = threading.Lock()
        self._parks_fetch = threading.Lock()
        self._busy: set[str] = set()
        self._parks_failed_at = float("-inf")
        self._parks_error: str | None = None
        self._failed: dict[str, tuple[float, str]] = {}  # park code -> (when, why) of the last failed refresh, until one succeeds

    @property
    def configured(self) -> bool:
        return bool(self.key and self.cache_dir)

    # --- cache files -----------------------------------------------------------------------------------

    @staticmethod
    def _check(code: str) -> str:
        if not isinstance(code, str) or not PARK_CODE.match(code):
            raise ValueError("not a park code")
        return code

    def _path(self, code: str | None, name: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{name}.json" if code is None else self.cache_dir / code / f"{name}.json"

    def _read(self, path: Path | None) -> dict | None:
        """{"ts", "fetched_at", "data"} or None."""
        if path is None or not path.exists():
            return None
        try:
            doc = json.loads(path.read_text())
            if not isinstance(doc, dict) or "data" not in doc or "ts" not in doc:
                return None
            return doc
        except (OSError, json.JSONDecodeError):
            return None

    def _write(self, path: Path, data) -> dict:
        now = self.clock()
        doc = {"ts": now, "fetched_at": _iso(now), "data": data}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc))
        os.replace(tmp, path)
        return doc

    def _age(self, doc: dict | None) -> float:
        return float("inf") if doc is None else self.clock() - float(doc["ts"])

    # --- fetching --------------------------------------------------------------------------------------

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{HOST}/{endpoint}?{urllib.parse.urlencode(params)}"
        body = self.fetch(url, {"X-Api-Key": self.key, "Accept": "application/json"})
        doc = json.loads(body)
        if not isinstance(doc, dict):
            raise ValueError("unexpected answer")
        return doc

    def _pages(self, endpoint: str, params: dict) -> list:
        out: list = []
        start = 0
        while True:
            doc = self._get(endpoint, {**params, "limit": PAGE, "start": start})
            items = doc.get("data") or []
            out += items
            try:
                total = int(doc.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
            start += PAGE
            if not items or start >= total:
                return out

    def _park_entry(self, code: str):
        entries = self._get("parks", {"parkCode": code, "limit": 1}).get("data") or []
        return next((p for p in entries if p.get("parkCode") == code), entries[0] if entries else None)

    def _fetchers(self, code: str) -> dict:
        return {"alerts": lambda: self._pages("alerts", {"parkCode": code}),
                "things": lambda: self._pages("thingstodo", {"parkCode": code}),
                "park": lambda: self._park_entry(code)}

    def _safe(self, e: Exception) -> str:
        text = re.sub(r"\?\S*", "", str(e) or e.__class__.__name__)  # no query strings in error text
        return text.replace(self.key, "…") if self.key else text

    # --- what callers use ----------------------------------------------------------------------------------

    def _assemble(self, code: str, docs: dict, stale: bool, error: str | None) -> dict | None:
        if all(d is None for d in docs.values()):
            return None
        present = [d for d in docs.values() if d is not None]
        return {"park": docs["park"]["data"] if docs["park"] else None,
                "alerts": docs["alerts"]["data"] if docs["alerts"] else [],
                "things": docs["things"]["data"] if docs["things"] else [],
                "fetched_at": min(d["fetched_at"] for d in present),
                "stale": stale or any(d is None for d in docs.values()), "error": error}

    def cached(self, code: str) -> dict | None:
        """The park's cache as it is, or None when nothing is cached; after a failed refresh it says so (stale, error)."""
        code = self._check(code)
        docs = {name: self._read(self._path(code, name)) for name in PIECES}
        failed = self._failed.get(code)
        return self._assemble(code, docs, failed is not None, failed[1] if failed else None)

    def due(self, code: str) -> bool:
        """A refresh would fetch something: a piece is missing or past its TTL, and the last attempt didn't fail within
        PARKS_RETRY_S (always False when not configured)."""
        code = self._check(code)
        failed = self._failed.get(code)
        if failed is not None and self.clock() - failed[0] <= PARKS_RETRY_S:
            return False
        return self.configured and any(self._age(self._read(self._path(code, name))) > ttl for name, ttl in PIECES.items())

    def refresh(self, code: str) -> dict | None:
        """Fetch what is missing or past its TTL and return the park's data; a piece that fails keeps its cached
        value (empty when there is none) and the result says `stale` with the reason."""
        code = self._check(code)
        if not self.configured:
            return self.cached(code)
        docs = {name: self._read(self._path(code, name)) for name in PIECES}
        stale, errors = False, []
        for name, ttl in PIECES.items():
            if self._age(docs[name]) <= ttl:
                continue
            try:
                log.debug("refreshing %s for %s", name, code)
                docs[name] = self._write(self._path(code, name), self._fetchers(code)[name]())
            except Exception as e:  # noqa: BLE001 - any failure keeps the cache and is reported
                stale = True
                errors.append(f"{name}: {self._safe(e)}")
                log.warning("park %s: %s could not be refreshed (%s)", code, name, self._safe(e))
        if errors:
            self._failed[code] = (self.clock(), "; ".join(errors))
        else:
            self._failed.pop(code, None)
        return self._assemble(code, docs, stale, "; ".join(errors) or None)

    def kick(self, code: str) -> None:
        """Refresh in the background when due; one refresh per park at a time."""
        if not self.configured or not self.due(code):
            return
        with self._lock:
            if code in self._busy:
                return
            self._busy.add(code)

        def run():
            try:
                self.refresh(code)
            finally:
                with self._lock:
                    self._busy.discard(code)

        if self.thread:
            threading.Thread(target=run, name=f"nps-{code}", daemon=True).start()
        else:
            run()

    # --- parks and the area-to-park mapping ----------------------------------------------------------------

    PARK_FIELDS = ("parkCode", "fullName", "name", "designation", "states", "latitude", "longitude", "url", "entranceFees")

    def _read_parks(self, path: Path | None) -> dict | None:
        """The parks file, parsed once per change (every request maps areas through it)."""
        if path is None or not path.exists():
            return None
        stamp = path.stat().st_mtime_ns
        memo = getattr(self, "_parks_memo", None)
        if memo is None or memo[0] != stamp:
            memo = (stamp, self._read(path))
            self._parks_memo = memo
        return memo[1]

    def parks(self, refresh: bool = True) -> list[dict]:
        """The list of NPS units (cached PARKS_TTL_S); [] when there is no cache and no key. `refresh=False` never
        fetches: the cache or nothing (a request must not wait on the network; the background attach refreshes)."""
        path = self._path(None, "parks")
        doc = self._read_parks(path)
        if doc is not None and (not refresh or not self.configured or self._age(doc) <= PARKS_TTL_S):
            return doc["data"]
        if not self.configured or not refresh:
            return doc["data"] if doc else []
        with self._parks_fetch:  # one fetch at a time; a second caller finds the fresh file
            doc = self._read_parks(path)
            if doc is not None and self._age(doc) <= PARKS_TTL_S:
                return doc["data"]
            try:
                units = [{k: p.get(k) for k in self.PARK_FIELDS} for p in self._pages("parks", {})]
                return self._write(path, units)["data"]
            except Exception as e:  # noqa: BLE001
                self._parks_error = self._safe(e)
                log.warning("the parks list could not be refreshed (%s)", self._parks_error)
                return doc["data"] if doc else []

    def park_name(self, code: str) -> str | None:
        return next((p.get("fullName") for p in self.parks(refresh=False) if p.get("parkCode") == code), None)

    def attach(self, loaded) -> None:
        """A loaded area: map it to its park and refresh the park's data when due, off the request thread when
        `thread` (the region store calls this as `on_loaded`). When the parks list can't be fetched the area stays
        unmapped (pending), never "no feed", and a later request tries again after PARKS_RETRY_S."""
        if not self.configured:
            loaded.park_mapped = True  # nothing to fetch: the mapping needs no network either way
            loaded.park_code = self.park_for(loaded.region, refresh=False)
            return
        key = f"attach:{getattr(loaded.region, 'id', id(loaded))}"
        with self._lock:
            if key in self._busy:
                return
            self._busy.add(key)

        def run():
            try:
                if not self.parks():
                    self._parks_failed_at = self.clock()
                    return
                code = self.park_for(loaded.region, refresh=False)
                loaded.park_code, loaded.park_mapped = code, True
                if code:
                    self.kick(code)
            except Exception:  # noqa: BLE001
                log.exception("mapping %s to a park failed", getattr(loaded.region, "id", "?"))
                self._parks_failed_at = self.clock()
            finally:
                with self._lock:
                    self._busy.discard(key)

        if self.thread:
            threading.Thread(target=run, name="nps-attach", daemon=True).start()
        else:
            run()

    def park_of(self, loaded) -> dict:
        """What notices.assemble needs for a loaded area, from the cache only: {"code", "configured", "data", "pending"}.
        Pending while the parks list or the park's data hasn't arrived yet; a refresh is kicked when one is due."""
        if not loaded.park_mapped:
            if self.parks(refresh=False):
                loaded.park_code, loaded.park_mapped = self.park_for(loaded.region, refresh=False), True
            elif self.configured:
                if self.clock() - self._parks_failed_at > PARKS_RETRY_S:
                    self.attach(loaded)  # the list never arrived (or failed a while ago): try again, in the background
                    if loaded.park_mapped:  # inline mode mapped it just now
                        return self.park_of(loaded)
                if self._parks_error is not None and self.clock() - self._parks_failed_at <= PARKS_RETRY_S:
                    # the service refused or failed a moment ago: not pending, say so (notices.assemble quotes it)
                    return {"code": None, "configured": True, "data": None, "pending": False, "error": self._parks_error,
                            "tried_at": _iso(self._parks_failed_at)}
                return {"code": None, "configured": True, "data": None, "pending": True}
            else:
                loaded.park_code, loaded.park_mapped = None, True
        code = loaded.park_code
        if code and self.configured and self.due(code):
            self.kick(code)
        data = self.cached(code) if code else None
        return {"code": code, "configured": self.configured, "data": data, "pending": bool(code) and self.configured and data is None}

    def park_for(self, region, refresh: bool = True) -> str | None:
        """The NPS unit for an area: by name (the area's, its group members', the region's), else the unit whose point
        lies in the area's outline (or the region's box), else the smallest protected area holding the area's centre
        that is itself a unit. None when there is no parks list or no match."""
        parks = [p for p in self.parks(refresh=refresh) if p.get("parkCode") and p.get("fullName")]
        if not parks:
            return None
        by_name = {_norm(p["fullName"]): p["parkCode"] for p in parks}
        area = getattr(region, "area", None) or {}
        names = [area.get("name")] + [m[1] for m in area.get("members") or [] if len(m) > 1] + [getattr(region, "name", None)]
        for name in names:
            code = by_name.get(_norm(name)) if name else None
            if code:
                return code
        shape = self._shape(region, area)
        if shape is None:
            return None
        from shapely.geometry import Point

        inside = []
        for p in parks:
            try:
                pt = Point(float(p["longitude"]), float(p["latitude"]))
            except (KeyError, TypeError, ValueError):
                continue
            if pt.within(shape):
                inside.append(p)
        if inside:
            inside.sort(key=lambda p: (0 if "National Park" in str(p.get("designation") or "") else 1, str(p["fullName"])))
            return inside[0]["parkCode"]
        if self.index_opener is not None:
            from backcountry import osm_index

            c = shape.centroid
            try:
                con = self.index_opener()
            except Exception:  # noqa: BLE001 - no index: no containing-area step
                return None
            try:
                for a in osm_index.containing_areas(con, c.x, c.y):
                    code = by_name.get(_norm(a.get("name")))
                    if code:
                        return code
            finally:
                con.close()
        return None

    def _shape(self, region, area: dict):
        from shapely.geometry import box

        osm = area.get("osm")
        if osm and self.index_opener is not None:
            try:
                con = self.index_opener()
            except Exception:  # noqa: BLE001
                con = None
            if con is not None:
                try:
                    from backcountry import osm_index

                    return osm_index.area_shape(con, osm)
                except Exception:  # noqa: BLE001 - fall back to the box
                    pass
                finally:
                    con.close()
        b = getattr(region, "bbox", None)
        if b is None:
            return None
        return box(b.west, b.south, b.east, b.north)
