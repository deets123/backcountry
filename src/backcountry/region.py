"""Load region definitions from regions/*.yaml."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGIONS_DIR = REPO_ROOT / "regions"
# Areas generated from the local US index (backcountry.areas); git-ignored, never hand-edited.
AREAS_DIR = Path(os.environ.get("BACKCOUNTRY_AREAS_DIR") or REPO_ROOT / "data" / "areas")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float

    def overpass(self) -> str:
        """Overpass QL bbox order: south,west,north,east."""
        return f"{self.south},{self.west},{self.north},{self.east}"

    def contains(self, south: float, west: float, north: float, east: float) -> bool:
        return self.south <= south and self.west <= west and self.north >= north and self.east >= east


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    crs: str
    bbox: BBox
    boundary_name_regex: str | None = None
    usfs_crosscheck: bool = False
    trailheads: list[dict] = field(default_factory=list)
    area: dict | None = None  # generated areas: {type_label, state, osm, capped}
    generated: bool = False


def region_path(region_id: str, regions_dir: Path | None = None, areas_dir: Path | None = None) -> Path:
    """The hand-made region file if there is one, else the generated area's. Ids are never paths."""
    if not isinstance(region_id, str) or not _ID.match(region_id):
        raise FileNotFoundError(f"No region {region_id!r}")
    for d in (regions_dir or REGIONS_DIR, areas_dir or AREAS_DIR):
        path = d / f"{region_id}.yaml"
        if path.exists():
            return path
    raise FileNotFoundError(f"No region file for {region_id!r}")


def load_region(region_id: str, regions_dir: Path | None = None, areas_dir: Path | None = None) -> Region:
    path = region_path(region_id, regions_dir, areas_dir)
    data = yaml.safe_load(path.read_text())

    bbox = BBox(**{k: float(data["bbox"][k]) for k in ("south", "west", "north", "east")})
    if not (bbox.south < bbox.north and bbox.west < bbox.east):
        raise ValueError(f"{path.name}: bbox must have south < north and west < east, got {bbox}")

    trailheads = data.get("trailheads") or []
    seen = set()
    for th in trailheads:
        if th.get("id") in seen:
            raise ValueError(f"{path.name}: duplicate trailhead id {th.get('id')!r}")
        seen.add(th.get("id"))
        has_coords = "lat" in th or "lon" in th
        # CLAUDE.md rule 5: no real-world coordinates unless the user verified them.
        if has_coords and th.get("verified") is not True:
            raise ValueError(
                f"{path.name}: trailhead {th.get('id')!r} has coordinates but is not marked verified: true"
            )
        # Otherwise the location comes from source data: a graph node (OSM node id).
        if not has_coords and not isinstance(th.get("node"), int):
            raise ValueError(f"{path.name}: trailhead {th.get('id')!r} needs an integer graph `node` (OSM node id)")

    return Region(
        id=data["id"],
        name=data["name"],
        crs=data["crs"],
        bbox=bbox,
        boundary_name_regex=data.get("boundary_name_regex"),
        usfs_crosscheck=bool(data.get("usfs_crosscheck", False)),
        trailheads=trailheads,
        area=data.get("area"),
        generated=bool(data.get("generated", False)),
    )
