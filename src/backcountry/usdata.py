"""The US OpenStreetMap extract: download it from Geofabrik (checksum verified) and build the local index.

Run by the operator (`backcountry usdata ...`), never by a user request. The download URL is fixed here: no
command or endpoint takes a URL. Geofabrik publishes these extracts for bulk download; data (c) OpenStreetMap
contributors, ODbL.
"""

import hashlib
import os
import re
import shutil
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import certifi

from backcountry.ingest import USER_AGENT, FetchError
from backcountry.region import REPO_ROOT

GEOFABRIK_US_URL = "https://download.geofabrik.de/north-america/us-latest.osm.pbf"
OSM_DIR = Path(os.environ.get("BACKCOUNTRY_OSM_DIR") or REPO_ROOT / "data" / "osm")
CHUNK = 8 * 1024 * 1024
_DATED = re.compile(r"^us-(\d{4}-\d{2}-\d{2})\.")


def _open(url: str):
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=120,
                                  context=context)


def _stream_to(url: str, part: Path) -> dict:
    """Stream url into `part`; returns the response headers. Progress goes to stderr (operator only)."""
    with _open(url) as response, part.open("wb") as f:
        total, done = int(response.headers.get("Content-Length") or 0), 0
        while chunk := response.read(CHUNK):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r{done / 1e9:.1f} of {total / 1e9:.1f} GB", end="", file=sys.stderr)
        print(file=sys.stderr)
        return dict(response.headers)


def _http_text(url: str) -> str:
    with _open(url) as response:
        return response.read(4096).decode("ascii", "replace")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def download_extract(dest_dir: Path, fetch_bytes=_stream_to, fetch_text=_http_text) -> Path:
    """Download the US extract to dest_dir/us-<date>.osm.pbf, kept only if its MD5 matches Geofabrik's."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    m = re.match(r"^([0-9a-f]{32})\b", fetch_text(GEOFABRIK_US_URL + ".md5").strip())
    if not m:
        raise FetchError("Geofabrik's checksum file didn't look like an MD5; nothing was downloaded.")
    part = dest_dir / "us-latest.osm.pbf.part"
    try:
        headers = fetch_bytes(GEOFABRIK_US_URL, part)
        if _md5(part) != m[1]:
            raise FetchError("The download didn't match Geofabrik's checksum, so it was discarded. Try again later.")
        modified = headers.get("Last-Modified")
        date = parsedate_to_datetime(modified).strftime("%Y-%m-%d") if modified else _today()
        final = dest_dir / f"us-{date}.osm.pbf"
        os.chmod(part, 0o644)
        os.replace(part, final)
        return final
    finally:
        part.unlink(missing_ok=True)


def extract_date(path: Path) -> str:
    m = _DATED.match(path.name)
    if not m:
        raise ValueError(f"{path.name}: expected a name like us-2026-10-24.osm.pbf")
    return m[1]


def _newest(osm_dir: Path, suffix: str) -> Path | None:
    files = sorted(p for p in osm_dir.glob(f"us-*{suffix}") if _DATED.match(p.name) and p.name.endswith(suffix))
    return files[-1] if files else None


def current_index(osm_dir: Path | None = None) -> Path | None:
    """The newest built index (us-<date>.sqlite); half-built .tmp files are ignored."""
    return _newest(osm_dir or OSM_DIR, ".sqlite")


def newest_extract(osm_dir: Path | None = None) -> Path | None:
    return _newest(osm_dir or OSM_DIR, ".osm.pbf")


def build(pbf: Path, osm_dir: Path | None = None, locations: str = "flex_mem") -> tuple[Path, dict]:
    from backcountry import osm_index

    date = extract_date(pbf)
    out = (osm_dir or OSM_DIR) / f"us-{date}.sqlite"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out, osm_index.build_index(pbf, out, date, source_url=GEOFABRIK_US_URL, locations=locations)


def disk_free_gb(path: Path) -> float:
    return shutil.disk_usage(path if path.exists() else path.parent).free / 1e9
