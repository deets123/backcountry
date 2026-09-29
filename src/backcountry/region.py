"""Load region definitions from regions/*.yaml."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGIONS_DIR = REPO_ROOT / "regions"


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
    trailheads: list[dict] = field(default_factory=list)


def load_region(region_id: str, regions_dir: Path | None = None) -> Region:
    path = (regions_dir or REGIONS_DIR) / f"{region_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No region file at {path}")
    data = yaml.safe_load(path.read_text())

    bbox = BBox(**{k: float(data["bbox"][k]) for k in ("south", "west", "north", "east")})
    if not (bbox.south < bbox.north and bbox.west < bbox.east):
        raise ValueError(f"{path.name}: bbox must have south < north and west < east, got {bbox}")

    trailheads = data.get("trailheads") or []
    for th in trailheads:
        # CLAUDE.md rule 5: no real-world coordinates unless the user verified them.
        if ("lat" in th or "lon" in th) and th.get("verified") is not True:
            raise ValueError(
                f"{path.name}: trailhead {th.get('id')!r} has coordinates but is not marked verified: true"
            )

    return Region(
        id=data["id"],
        name=data["name"],
        crs=data["crs"],
        bbox=bbox,
        boundary_name_regex=data.get("boundary_name_regex"),
        trailheads=trailheads,
    )
