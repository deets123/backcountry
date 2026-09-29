"""Fetch and cache OSM trail data for a region.

One Overpass query per region fetch. The raw JSON response is cached under
data/raw/<region>/ together with the query and a `retrieved_at` timestamp, and
every later step reads from that cache. Re-querying only happens with --refresh.

OSM data (c) OpenStreetMap contributors, available under the ODbL.
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


def _overpass_post(query: str) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": USER_AGENT})
    # Explicit CA bundle: some Python builds (e.g. python.org macOS) ship without one.
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=240, context=context) as response:
        return json.load(response)


def region_raw_dir(region: Region, raw_dir: Path | None = None) -> Path:
    return (raw_dir or RAW_DIR) / region.id


def latest_cache(region: Region, raw_dir: Path | None = None) -> Path | None:
    files = sorted(region_raw_dir(region, raw_dir).glob("overpass_*.json"))
    return files[-1] if files else None


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
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "region": region.id,
        "retrieved_at": retrieved_at,
        "source": "OpenStreetMap via Overpass API",
        "endpoint": OVERPASS_URL,
        "attribution": OSM_ATTRIBUTION,
        "osm_base": response.get("osm3s", {}).get("timestamp_osm_base"),
        "bbox": vars(region.bbox),
        "query": query,
        "response": response,
    }
    out_dir = region_raw_dir(region, raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"overpass_{retrieved_at.replace('-', '').replace(':', '')}.json"
    path.write_text(json.dumps(doc))
    return path


def load_raw(region: Region, raw_dir: Path | None = None) -> dict:
    path = latest_cache(region, raw_dir)
    if path is None:
        raise FileNotFoundError(
            f"No cached OSM data for region {region.id!r}. Run `backcountry ingest --region {region.id}` first."
        )
    doc = json.loads(path.read_text())
    doc["cache_path"] = str(path)
    return doc
