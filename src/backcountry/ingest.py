"""Fetch and cache source data for a region.

OSM: one Overpass query per region fetch. The raw JSON response is cached under
data/raw/<region>/ together with the query and a `retrieved_at` timestamp, and
every later step reads from that cache. Re-querying only happens with --refresh.
OSM data (c) OpenStreetMap contributors, available under the ODbL.

USFS: official National Forest System trails, roads, recreation sites, and
wilderness boundaries from the Forest Service Enterprise Data Warehouse, cached
the same way. Used only to cross-check the OSM graph, never as routing geometry.
"""

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import certifi

from backcountry.region import REPO_ROOT, Region

RAW_DIR = Path(os.environ.get("BACKCOUNTRY_RAW_DIR") or REPO_ROOT / "data" / "raw")  # env: e.g. a git worktree using the main checkout's cache
RETRY_WAIT_S = 30.0  # one polite retry after a server-side timeout/overload


class FetchError(RuntimeError):
    """A source server refused or timed out; the message says what to try."""
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "backcountry-route-engine/0.1 (personal non-commercial tool; responses cached locally)"
OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL (openstreetmap.org/copyright)"

TRAIL_HIGHWAYS = ("path", "footway")
# Roads are routable but flagged is_road so scoring can avoid them unless needed.
# Tracks count as roads (user decision 2026-09-29): none are USFS-designated trails.
ROAD_HIGHWAYS = ("track", "unclassified", "service", "tertiary")
# Paved county roads: routable only as a last resort (user decision 2026-09-29).
LAST_RESORT_HIGHWAYS = ("tertiary",)
FOOT_ALLOWED = ("yes", "designated", "permissive")
BLOCKED = ("no", "private")

# Way tags carried onto graph edges. The raw cache keeps every tag.
KEEP_TAGS = ("highway", "name", "ref", "sac_scale", "trail_visibility", "access", "foot", "informal", "service")


def way_class(tags: dict) -> str | None:
    """'trail', 'road', or None if this way isn't part of the routable network."""
    highway = tags.get("highway")
    if highway in TRAIL_HIGHWAYS:
        return "trail"
    if highway in ROAD_HIGHWAYS:
        return "road"
    return None


def is_last_resort(tags: dict) -> bool:
    return tags.get("highway") in LAST_RESORT_HIGHWAYS


def access_exclusion_reason(tags: dict) -> str | None:
    """Why a way is closed to hikers, or None if it isn't.

    Follows OSM access semantics: foot=* is more specific than access=*, so
    access=no + foot=designated is open to hikers (common on wilderness trails).
    """
    foot = tags.get("foot")
    if foot in BLOCKED:
        return f"foot={foot}"
    access = tags.get("access")
    if access in BLOCKED and foot not in FOOT_ALLOWED:
        return f"access={access}"
    return None


def foot_overrides_access(tags: dict) -> bool:
    return tags.get("access") in BLOCKED and tags.get("foot") in FOOT_ALLOWED


def build_query(region: Region) -> str:
    bbox = region.bbox.overpass()
    boundary = ""
    if region.boundary_name_regex:
        boundary = f'  wr["boundary"]["name"~"{region.boundary_name_regex}",i]({bbox});\n'
    return (
        "[out:json][timeout:180];\n"
        "(\n"
        f'  way["highway"]({bbox});\n'
        f'  node["highway"="trailhead"]({bbox});\n'
        f'  nwr["amenity"="parking"]({bbox});\n'
        f'  nwr["tourism"~"^(camp_site|camp_pitch)$"]({bbox});\n'
        f'  nwr["natural"~"^(spring|water)$"]({bbox});\n'
        f'  way["waterway"~"^(stream|river)$"]({bbox});\n'
        f'  node["amenity"="drinking_water"]({bbox});\n'
        f"{boundary}"
        ");\n"
        "out meta geom;\n"  # meta = edit timestamp/version/changeset, kept as evidence of how established a feature is
    )


def feature_center(e: dict) -> tuple[float, float]:
    """(lon, lat) of a node, or the vertex centroid of a way/relation from its geometry or bounds."""
    if e["type"] == "node":
        return e["lon"], e["lat"]
    pts = [(g["lon"], g["lat"]) for g in e.get("geometry", []) if g]
    if e["type"] == "relation":
        pts = [(g["lon"], g["lat"]) for m in e.get("members", []) for g in (m.get("geometry") or []) if g]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]  # closed ring: don't count the repeated vertex twice
    if pts:
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    b = e["bounds"]
    return (b["minlon"] + b["maxlon"]) / 2, (b["minlat"] + b["maxlat"]) / 2


def camp_sites(raw: dict) -> list[dict]:
    """OSM camp sites / pitches in the raw data, with their source. Says nothing about legality or water."""
    out = []
    for e in raw["response"]["elements"]:
        kind = e.get("tags", {}).get("tourism")
        if kind not in ("camp_site", "camp_pitch"):
            continue
        lon, lat = feature_center(e)
        out.append({"source": f"osm:{e['type']}/{e['id']}", "kind": kind, "name": e["tags"].get("name"),
                    "lat": lat, "lon": lon, "tags": dict(e["tags"]), "meta": osm_meta(e),
                    "url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}"})
    return out


def osm_meta(e: dict) -> dict:
    """Edit timestamp and version of an OSM element (present when the query used `out meta`)."""
    return {"timestamp": e.get("timestamp"), "version": e.get("version")}


CAMP_TAGS = ("tourism", "name", "backcountry", "access", "fee", "capacity", "camp_type", "description")
WATER_TAGS = ("natural", "water", "waterway", "amenity", "name", "intermittent", "seasonal", "drinking_water", "description")


def water_features(raw: dict) -> dict:
    """OSM water features: points (springs, drinking water, water bodies) and lines (streams, rivers).

    These are map features with sources. Whether water is present, flowing, or drinkable is unknown.
    """
    points, lines = [], []
    for e in raw["response"]["elements"]:
        tags = e.get("tags", {})
        kind = None
        if tags.get("natural") == "spring":
            kind = "spring"
        elif tags.get("amenity") == "drinking_water":
            kind = "drinking_water"
        elif tags.get("natural") == "water":
            kind = "water"
        elif e["type"] == "way" and tags.get("waterway") in ("stream", "river"):
            coords = [[g["lon"], g["lat"]] for g in e.get("geometry", []) if g]
            if len(coords) >= 2:
                lines.append({"source": f"osm:way/{e['id']}", "kind": tags["waterway"], "name": tags.get("name"),
                              "coords": coords, "tags": {k: v for k, v in tags.items() if k in WATER_TAGS},
                              "meta": osm_meta(e), "url": f"https://www.openstreetmap.org/way/{e['id']}"})
            continue
        if kind is None:
            continue
        lon, lat = feature_center(e)
        points.append({"source": f"osm:{e['type']}/{e['id']}", "kind": kind, "name": tags.get("name"),
                       "lat": lat, "lon": lon, "tags": {k: v for k, v in tags.items() if k in WATER_TAGS},
                       "meta": osm_meta(e), "url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}"})
    return {"points": points, "lines": lines}


def _http_json(url: str, data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    # Explicit CA bundle: some Python builds (e.g. python.org macOS) ship without one.
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=240, context=context) as response:
        return json.load(response)


def _overpass_post(query: str) -> dict:
    return _http_json(OVERPASS_URL, urllib.parse.urlencode({"data": query}).encode())


def region_raw_dir(region: Region, raw_dir: Path | None = None) -> Path:
    return (raw_dir or RAW_DIR) / region.id


def latest_cache(region: Region, raw_dir: Path | None = None, prefix: str = "overpass") -> Path | None:
    files = sorted(region_raw_dir(region, raw_dir).glob(f"{prefix}_*.json"))
    return files[-1] if files else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_cache(region: Region, raw_dir: Path | None, prefix: str, doc: dict) -> Path:
    out_dir = region_raw_dir(region, raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{prefix}_{doc['retrieved_at'].replace('-', '').replace(':', '')}.json"
    path.write_text(json.dumps(doc))
    return path


def fetch(
    region: Region,
    raw_dir: Path | None = None,
    refresh: bool = False,
    fetcher: Callable[[str], dict] = _overpass_post,
) -> Path:
    """Return the cached raw response path, querying Overpass only if needed."""
    cached = latest_cache(region, raw_dir)
    if cached and not refresh:
        return cached

    query = build_query(region)
    try:
        response = fetcher(query)
    except urllib.error.HTTPError as e:
        if e.code not in (429, 502, 503, 504):
            raise FetchError(f"Overpass returned HTTP {e.code} {e.reason}.") from e
        time.sleep(RETRY_WAIT_S)
        try:
            response = fetcher(query)
        except urllib.error.HTTPError as e2:
            raise FetchError(
                f"HTTP {e2.code} {e2.reason} from Overpass twice. The server is busy or the query is too big for it. "
                "Wait a few minutes and retry, or shrink the region bbox.") from e2
    except urllib.error.URLError as e:
        raise FetchError(f"Could not reach Overpass: {e.reason}") from e
    doc = {
        "region": region.id,
        "retrieved_at": _utc_now(),
        "source": "OpenStreetMap via Overpass API",
        "endpoint": OVERPASS_URL,
        "attribution": OSM_ATTRIBUTION,
        "osm_base": response.get("osm3s", {}).get("timestamp_osm_base"),
        "bbox": vars(region.bbox),
        "query": query,
        "response": response,
    }
    return _write_cache(region, raw_dir, "overpass", doc)


CUT_PREFIX = "extract"  # generated areas: cut from the local US index (backcountry.osm_index)


def raw_prefix(region: Region) -> str:
    return CUT_PREFIX if region.generated else "overpass"


_CUT_NAME = re.compile(rf"^{CUT_PREFIX}_(\d{{4}})(\d{{2}})(\d{{2}})T\d{{6}}Z\.json$")


def cuts(region: Region, raw_dir: Path | None = None) -> list[str]:
    """Extract dates of a generated area's cached cuts, oldest first (read from the file names)."""
    if not region.generated:
        return []
    out = set()
    for p in region_raw_dir(region, raw_dir).glob(f"{CUT_PREFIX}_*.json"):
        if m := _CUT_NAME.match(p.name):
            out.add(f"{m[1]}-{m[2]}-{m[3]}")
    return sorted(out)


def load_raw(region: Region, raw_dir: Path | None = None, cut: str | None = None) -> dict:
    """The newest cached raw data, or (generated areas) the cut from extract date `cut`."""
    prefix = raw_prefix(region)
    if cut is None:
        path = latest_cache(region, raw_dir, prefix=prefix)
    else:
        files = sorted(region_raw_dir(region, raw_dir).glob(f"{prefix}_{cut.replace('-', '')}T*.json"))
        path = files[-1] if files and region.generated else None
    if path is None:
        if region.generated:
            raise FileNotFoundError(f"Area {region.id!r} has no trail data{' from ' + cut if cut else ''} yet.")
        raise FileNotFoundError(
            f"No cached OSM data for region {region.id!r}. Run `backcountry ingest --region {region.id}` first."
        )
    doc = json.loads(path.read_text())
    doc["cache_path"] = str(path)
    doc["cut"] = doc.get("osm_base") if region.generated else None
    return doc


# --- USFS Enterprise Data Warehouse ------------------------------------------

USFS_BASE = "https://apps.fs.usda.gov/arcx/rest/services/EDW"
USFS_ATTRIBUTION = "USDA Forest Service, Enterprise Data Warehouse (public domain)"
_ROAD_FIELDS = "id,name,oper_maint_level,surface_type,gis_miles,openforuseto,symbol_name"
USFS_LAYERS = {
    "trails": ("EDW_TrailNFSPublish_01/MapServer/0",
               "trail_no,trail_name,trail_type,trail_class,gis_miles,allowed_terra_use,hiker_pedestrian_managed"),
    "roads_open": ("EDW_RoadBasic_01/MapServer/0", _ROAD_FIELDS),
    "roads_closed": ("EDW_RoadBasic_01/MapServer/1", _ROAD_FIELDS),  # "closed to motorized uses"
    "rec_sites": ("EDW_InfraRecreationSites_01/MapServer/0",
                  "site_id,site_name,public_site_name,site_subtype,development_status,recarea_name"),
    "wilderness": ("EDW_Wilderness_01/MapServer/0", "wildernessname,boundarystatus,gis_acres"),
}


def usfs_query_url(layer_path: str, fields: str, bbox) -> str:
    params = {
        "where": "1=1",
        "geometry": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": fields,
        "outSR": 4326,
        "f": "geojson",
    }
    return f"{USFS_BASE}/{layer_path}/query?{urllib.parse.urlencode(params)}"


def fetch_usfs(
    region: Region,
    raw_dir: Path | None = None,
    refresh: bool = False,
    fetcher: Callable[[str], dict] = _http_json,
) -> Path:
    """Return the cached USFS layers path, querying the EDW services only if needed."""
    cached = latest_cache(region, raw_dir, prefix="usfs")
    if cached and not refresh:
        return cached
    layers = {}
    for name, (layer_path, fields) in USFS_LAYERS.items():
        url = usfs_query_url(layer_path, fields, region.bbox)
        fc = fetcher(url)
        if fc.get("exceededTransferLimit") or fc.get("properties", {}).get("exceededTransferLimit"):
            raise RuntimeError(f"USFS layer {name!r} returned truncated results; the bbox needs paging")
        layers[name] = {"url": url, "geojson": fc}
    doc = {
        "region": region.id,
        "retrieved_at": _utc_now(),
        "source": "USDA Forest Service EDW ArcGIS REST services",
        "attribution": USFS_ATTRIBUTION,
        "bbox": vars(region.bbox),
        "layers": layers,
    }
    return _write_cache(region, raw_dir, "usfs", doc)


def load_usfs(region: Region, raw_dir: Path | None = None) -> dict | None:
    path = latest_cache(region, raw_dir, prefix="usfs")
    if path is None:
        return None
    doc = json.loads(path.read_text())
    doc["cache_path"] = str(path)
    return doc


# --- USGS 3DEP elevation ------------------------------------------------------------

TNM_API = "https://tnmaccess.nationalmap.gov/api/v1/products"
DEM_DATASETS = {
    "13": "National Elevation Dataset (NED) 1/3 arc-second",  # ~10 m, seamless 1x1 degree tiles
    "1m": "Digital Elevation Model (DEM) 1 meter",  # lidar, 10 km project tiles
}
DEM_ATTRIBUTION = "USGS 3D Elevation Program (3DEP) (public domain)"
_TILE_13 = re.compile(r"1/3 Arc Second (n\d+w\d+) \d{8}$")


# Where USGS serves 3DEP GeoTIFFs (the TNM API's downloadURLs). Anything else is refused, never opened.
ALLOWED_DEM_HOSTS = ("prd-tnm.s3.amazonaws.com",)


def tnm_search(dataset: str, bounds: tuple, fetcher: Callable[[str], dict] = _http_json) -> list[dict]:
    """Products from the USGS National Map API intersecting bounds (west, south, east, north)."""
    params = {"datasets": dataset, "bbox": ",".join(str(b) for b in bounds), "prodFormats": "GeoTIFF",
              "max": 200, "outputFormat": "JSON"}
    return fetcher(f"{TNM_API}?{urllib.parse.urlencode(params)}").get("items", [])


def select_dem_items(kind: str, items: list[dict]) -> list[dict]:
    """10 m: the newest version of each 1x1 degree tile. 1 m: every tile, newest first (newest wins overlaps)."""
    if kind == "13":
        newest = {}
        for item in items:
            m = _TILE_13.search(item["title"])
            if m and (m[1] not in newest or item["publicationDate"] > newest[m[1]]["publicationDate"]):
                newest[m[1]] = item
        return [newest[t] for t in sorted(newest)]
    return sorted(items, key=lambda i: i["publicationDate"], reverse=True)


def _read_merged(urls: list[str], bounds: tuple):
    """Read just `bounds` (lon/lat) from remote cloud-optimized GeoTIFFs and merge them (first valid wins)."""
    for url in urls:
        u = urllib.parse.urlparse(url)
        if u.scheme != "https" or u.hostname not in ALLOWED_DEM_HOSTS:
            raise FetchError(f"USGS listed a DEM at an unexpected host ({u.hostname}); nothing was downloaded.")
    import numpy as np
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import transform_bounds

    env = {"CURL_CA_BUNDLE": certifi.where(), "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
           "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "2"}
    with rasterio.Env(**env):
        datasets = [rasterio.open(u) for u in urls]
        try:
            crs = datasets[0].crs
            same = [d for d in datasets if d.crs == crs]
            array, transform = merge(same, bounds=transform_bounds("EPSG:4326", crs, *bounds), nodata=-999999.0)
        finally:
            for d in datasets:
                d.close()
    array = array[0].astype("float32")
    array[array <= -999999.0] = np.nan
    return array, transform, crs.to_string()


def _dem_prefix(kind: str, name: str | None) -> str:
    return f"dem{kind}" + (f"_{name}" if name else "")


DEM_WORKERS = 6  # cells read at once


def _tile_box_13(title: str) -> tuple[float, float, float, float] | None:
    """A 1/3 arc-second tile's lon/lat box from its title: n39w080 has its north-west corner at 39N 80W."""
    m = re.search(r"\b([ns])(\d+)([ew])(\d+)\b", title)
    if not m:
        return None
    n = int(m[2]) * (1 if m[1] == "n" else -1)
    w = int(m[4]) * (-1 if m[3] == "w" else 1)
    return (w, n - 1, w + 1, n)


def _urls_for(items: list[dict], kind: str, cell: tuple) -> list[str]:
    """The products that can hold this cell (every product when their tiles can't be told from the title)."""
    pad = 0.01
    out = []
    for i in items:
        b = _tile_box_13(i["title"]) if kind == "13" else None
        if b is None or (b[0] - pad < cell[2] and cell[0] < b[2] + pad and b[1] - pad < cell[3] and cell[1] < b[3] + pad):
            out.append(i["downloadURL"])
    return out


def _mosaic(pieces: list[tuple]):
    """One raster from cell reads on the same pixel grid; pixels in no cell are NaN."""
    import numpy as np
    from rasterio.transform import from_origin

    crs = pieces[0][2]
    if any(p[2] != crs for p in pieces):
        raise FetchError("USGS elevation tiles came back in different coordinate systems; nothing was cached.")
    res = pieces[0][1].a
    west = min(t.c for _, t, _ in pieces)
    north = max(t.f for _, t, _ in pieces)
    east = max(t.c + a.shape[1] * res for a, t, _ in pieces)
    south = min(t.f - a.shape[0] * res for a, t, _ in pieces)
    out = np.full((round((north - south) / res), round((east - west) / res)), np.nan, dtype="float32")
    for a, t, _ in pieces:
        r0, c0 = round((north - t.f) / res), round((t.c - west) / res)
        h, w = min(a.shape[0], out.shape[0] - r0), min(a.shape[1], out.shape[1] - c0)
        window = out[r0:r0 + h, c0:c0 + w]
        np.copyto(window, a[:h, :w], where=~np.isnan(a[:h, :w]))
    return out, from_origin(west, north, res, res), crs


def fetch_dem(
    region: Region,
    bounds: tuple,
    kind: str = "13",
    name: str | None = None,
    raw_dir: Path | None = None,
    refresh: bool = False,
    searcher: Callable[[str, tuple], list[dict]] | None = None,
    reader: Callable | None = None,
    cells: list[tuple] | None = None,
    workers: int = DEM_WORKERS,
) -> Path:
    """Cache a DEM covering bounds (lon/lat west, south, east, north). Returns the metadata JSON path.

    With `cells` (elevation.corridor_cells: the grid cells trails pass through), only those are read, `workers`
    at a time, into one raster that is empty (NaN) elsewhere. The default reader opens only https URLs on
    ALLOWED_DEM_HOSTS, whatever the product search returns."""
    import rasterio

    searcher, reader = searcher or tnm_search, reader or _read_merged
    prefix = _dem_prefix(kind, name)
    cached = latest_cache(region, raw_dir, prefix=prefix)
    if cached and not refresh:
        meta = json.loads(cached.read_text())
        b = meta["bounds_wgs84"]
        if cells is not None and "cell_list" in meta:
            if {tuple(c) for c in cells} <= {tuple(c) for c in meta["cell_list"]}:
                return cached
        elif b[0] <= bounds[0] and b[1] <= bounds[1] and b[2] >= bounds[2] and b[3] >= bounds[3]:
            return cached

    items = select_dem_items(kind, searcher(DEM_DATASETS[kind], bounds))
    if not items:
        raise RuntimeError(f"USGS has no {DEM_DATASETS[kind]!r} products for bounds {bounds}")

    if cells:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            # a failed cell fails the whole fetch: nothing half-read is cached
            pieces = list(pool.map(lambda c: reader(_urls_for(items, kind, c), c), cells))
        array, transform, crs = _mosaic(pieces)
    else:
        array, transform, crs = reader([i["downloadURL"] for i in items], bounds)

    retrieved_at = _utc_now()
    out_dir = region_raw_dir(region, raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}_{retrieved_at.replace('-', '').replace(':', '')}"
    tif = out_dir / f"{stem}.tif"
    # Both files are written beside their final names and renamed, the metadata last (it is what marks a DEM
    # as cached): a server killed mid-write leaves only *.tmp files, never a cache that can't be read.
    tif_tmp, path = tif.with_name(tif.name + ".tmp"), out_dir / f"{stem}.json"
    with rasterio.open(tif_tmp, "w", driver="GTiff", height=array.shape[0], width=array.shape[1], count=1,
                       dtype="float32", crs=crs, transform=transform, nodata=float("nan"), compress="deflate",
                       predictor=3, tiled=True) as dst:  # predictor 3 (floating point): about a third smaller
        dst.write(array, 1)
    meta = {
        "region": region.id,
        "retrieved_at": retrieved_at,
        "kind": kind,
        "dataset": DEM_DATASETS[kind],
        "attribution": DEM_ATTRIBUTION,
        "bounds_wgs84": list(bounds),
        **({"cells": len(cells), "cell_list": [list(c) for c in cells]} if cells else {}),
        "crs": crs,
        "tif": tif.name,
        "sources": [{"title": i["title"], "url": i["downloadURL"], "publication_date": i["publicationDate"]}
                    for i in items],
    }
    json_tmp = path.with_name(path.name + ".tmp")
    json_tmp.write_text(json.dumps(meta, indent=1))
    os.replace(tif_tmp, tif)
    os.replace(json_tmp, path)
    return path


def load_dem(region: Region, kind: str = "13", name: str | None = None, raw_dir: Path | None = None):
    """The latest cached DEM as an elevation.DEM, or None."""
    import rasterio

    from backcountry.elevation import DEM

    path = latest_cache(region, raw_dir, prefix=_dem_prefix(kind, name))
    if path is None:
        return None
    meta = json.loads(path.read_text())
    with rasterio.open(path.parent / meta["tif"]) as ds:
        array = ds.read(1).astype("float64")
        return DEM(array=array, transform=ds.transform, crs=meta["crs"], source=meta | {"cache_path": str(path)})
