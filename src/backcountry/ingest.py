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
import re
import ssl
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import certifi

from backcountry.region import REPO_ROOT, Region

RAW_DIR = REPO_ROOT / "data" / "raw"
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
    response = fetcher(query)
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


def load_raw(region: Region, raw_dir: Path | None = None) -> dict:
    path = latest_cache(region, raw_dir)
    if path is None:
        raise FileNotFoundError(
            f"No cached OSM data for region {region.id!r}. Run `backcountry ingest --region {region.id}` first."
        )
    doc = json.loads(path.read_text())
    doc["cache_path"] = str(path)
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


def fetch_dem(
    region: Region,
    bounds: tuple,
    kind: str = "13",
    name: str | None = None,
    raw_dir: Path | None = None,
    refresh: bool = False,
    searcher: Callable[[str, tuple], list[dict]] = tnm_search,
    reader: Callable = _read_merged,
) -> Path:
    """Cache a DEM covering bounds (lon/lat west, south, east, north). Returns the metadata JSON path."""
    import rasterio

    prefix = _dem_prefix(kind, name)
    cached = latest_cache(region, raw_dir, prefix=prefix)
    if cached and not refresh:
        b = json.loads(cached.read_text())["bounds_wgs84"]
        if b[0] <= bounds[0] and b[1] <= bounds[1] and b[2] >= bounds[2] and b[3] >= bounds[3]:
            return cached

    items = select_dem_items(kind, searcher(DEM_DATASETS[kind], bounds))
    if not items:
        raise RuntimeError(f"USGS has no {DEM_DATASETS[kind]!r} products for bounds {bounds}")
    array, transform, crs = reader([i["downloadURL"] for i in items], bounds)

    retrieved_at = _utc_now()
    out_dir = region_raw_dir(region, raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}_{retrieved_at.replace('-', '').replace(':', '')}"
    tif = out_dir / f"{stem}.tif"
    with rasterio.open(tif, "w", driver="GTiff", height=array.shape[0], width=array.shape[1], count=1,
                       dtype="float32", crs=crs, transform=transform, nodata=float("nan"), compress="deflate",
                       tiled=True) as dst:
        dst.write(array, 1)
    meta = {
        "region": region.id,
        "retrieved_at": retrieved_at,
        "kind": kind,
        "dataset": DEM_DATASETS[kind],
        "attribution": DEM_ATTRIBUTION,
        "bounds_wgs84": list(bounds),
        "crs": crs,
        "tif": tif.name,
        "sources": [{"title": i["title"], "url": i["downloadURL"], "publication_date": i["publicationDate"]}
                    for i in items],
    }
    path = out_dir / f"{stem}.json"
    path.write_text(json.dumps(meta, indent=1))
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
