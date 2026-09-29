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

TRAIL_HIGHWAYS = ("path", "footway", "track")
ROAD_HIGHWAYS = ("unclassified", "service")  # forest roads; flagged is_road
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
        f"{boundary}"
        ");\n"
        "out body geom;\n"
    )


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
