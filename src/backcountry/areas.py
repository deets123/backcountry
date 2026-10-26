"""Any US area: from a search result, a map box or a tapped point to a region the engine can plan in.

An area is cut out of the local US index (osm_index) into data/raw/<area_id>/extract_<date>.json, the same raw
shape an Overpass query gives, with a generated region file in data/areas/<area_id>.yaml. After that it is
loaded and planned exactly like a hand-made region. Trailheads come from source data only (trailheads.py);
nothing here says an area, trail or trailhead is open, legal or reachable.
"""

import hashlib
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from backcountry import ingest, osm_index
from backcountry.elevation import graph_bounds_ll
from backcountry.graph import build_graph
from backcountry.region import AREAS_DIR, BBox, load_region

MAX_AREA_KM2 = 2500.0  # bigger areas open as a capped box; set from measured build times (PROGRESS.md)
CAPPED_BOX_KM = 24.0   # side of the capped square
BBOX_PAD_DEG = 0.01    # around a boundary, so trails on its edge are whole
_REF = re.compile(r"^(node|way|relation)/\d{1,12}$")


@dataclass(frozen=True)
class AreaSpec:
    id: str
    name: str
    type_label: str
    state: str | None
    bbox: BBox
    osm: str | None
    capped: bool


def utm_crs(lon: float, lat: float) -> str:
    """The WGS84 UTM zone (northern hemisphere: the US) holding this point."""
    return f"EPSG:326{int((lon + 180) // 6) + 1:02d}"


def area_id(name: str, osm: str | None, bbox: BBox) -> str:
    """A safe, stable id: a slug of the name plus the OSM id, or a short hash of the box."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-") or "area"
    if osm:
        otype, oid = osm.split("/")
        return f"{slug}-{otype[0]}{oid}"
    key = ",".join(f"{v:.4f}" for v in (bbox.west, bbox.south, bbox.east, bbox.north))
    return f"{slug}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _check_point(lon, lat) -> None:
    if not _finite(lon, lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("A point must be a longitude and latitude in degrees.")


def _capped_box(lon: float, lat: float) -> BBox:
    half_lat = CAPPED_BOX_KM / 2 / 110.574
    half_lon = CAPPED_BOX_KM / 2 / (111.320 * math.cos(math.radians(lat)))
    return BBox(south=lat - half_lat, west=lon - half_lon, north=lat + half_lat, east=lon + half_lon)


def _padded(b: list) -> BBox:
    w, s, e, n = b
    return BBox(south=s - BBOX_PAD_DEG, west=w - BBOX_PAD_DEG, north=n + BBOX_PAD_DEG, east=e + BBOX_PAD_DEG)


def _spec(name, type_label, state, bbox: BBox, osm, capped) -> AreaSpec:
    return AreaSpec(id=area_id(name, None if capped else osm, bbox), name=name, type_label=type_label, state=state,
                    bbox=bbox, osm=osm, capped=capped)


def _from_area(con, area: dict, at: tuple | None = None) -> AreaSpec:
    """The whole area, or a capped box (around `at`, else its centre) when it is too big to plan in at once."""
    state = area.get("state") or osm_index.state_at(con, *(at or area["center"]))
    if area["area_km2"] <= MAX_AREA_KM2:
        return _spec(area["name"], area["type_label"], state, _padded(area["bbox"]), area["osm"], False)
    return _spec(area["name"], area["type_label"], state, _capped_box(*(at or area["center"])), area["osm"], True)


def _from_spot(con, lon: float, lat: float) -> AreaSpec:
    """A spot opens the park it is in, else the smallest protected area under the cap, else a capped box."""
    holding = osm_index.containing_areas(con, lon, lat)
    park = next((a for a in holding if a["park"]), None)
    if park:
        return _from_area(con, park, at=(lon, lat))
    small = next((a for a in holding if a["area_km2"] <= MAX_AREA_KM2), None)
    if small:
        return _from_area(con, small, at=(lon, lat))
    b = _capped_box(lon, lat)
    state = osm_index.state_at(con, lon, lat)
    if state is None and not osm_index.touches_us(con, (b.west, b.south, b.east, b.north)):
        raise ValueError("That area isn't in the US.")
    near = osm_index.nearest_place(con, (b.west, b.south, b.east, b.north), lon, lat)
    return _spec(f"Area near {near}" if near else "Map area", "Area", state, b, None, True)


def area_from_ref(con, osm: str) -> AreaSpec:
    """A search result, looked up again by its OSM ref (the client's copy of the result is never trusted)."""
    if not isinstance(osm, str) or not _REF.match(osm):
        raise ValueError("That isn't a search result.")
    area = osm_index.area_by_osm(con, osm)
    if area:
        return _from_area(con, area)
    place = osm_index.place_by_osm(con, osm)
    if place is None:
        raise ValueError("That place isn't in the trail data.")
    return _from_spot(con, *place["center"])


def area_from_point(con, lon, lat) -> AreaSpec:
    _check_point(lon, lat)
    return _from_spot(con, float(lon), float(lat))


def area_from_box(con, bbox) -> AreaSpec:
    """A box on the map (west, south, east, north): capped around its centre when bigger than the cap."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or not _finite(*bbox):
        raise ValueError("A box must be west, south, east, north in degrees.")
    w, s, e, n = (float(v) for v in bbox)
    _check_point(w, s), _check_point(e, n)
    if not (w < e and s < n):
        raise ValueError("A box must be west, south, east, north in degrees.")
    if not osm_index.touches_us(con, (w, s, e, n)):
        raise ValueError("That area isn't in the US.")
    lon, lat = (w + e) / 2, (s + n) / 2
    km2 = (n - s) * 110.574 * (e - w) * 111.320 * math.cos(math.radians(lat))
    b, capped = (_capped_box(lon, lat), True) if km2 > MAX_AREA_KM2 else (BBox(south=s, west=w, north=n, east=e), False)
    near = osm_index.nearest_place(con, (b.west, b.south, b.east, b.north), lon, lat)
    name = f"Area near {near}" if near else "Map area"
    return AreaSpec(id=area_id(name, None, b), name=name, type_label="Area", state=osm_index.state_at(con, lon, lat),
                    bbox=b, osm=None, capped=capped)


def write_area_region(spec: AreaSpec, areas_dir: Path | None = None) -> Path:
    """The generated region file (git-ignored); trailheads come from source data at request time."""
    d = areas_dir or AREAS_DIR
    d.mkdir(parents=True, exist_ok=True)
    b = spec.bbox
    doc = {
        "id": spec.id, "name": spec.name, "generated": True,
        "crs": utm_crs((b.west + b.east) / 2, (b.south + b.north) / 2),
        "bbox": {"south": b.south, "west": b.west, "north": b.north, "east": b.east},
        "boundary_name_regex": re.escape(spec.name) if spec.osm else None,
        "area": {"type_label": spec.type_label, "state": spec.state, "osm": spec.osm, "capped": spec.capped},
        "trailheads": [],
    }
    path = d / f"{spec.id}.yaml"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("# Generated by backcountry.areas from the local US OSM index. Do not edit.\n" + yaml.safe_dump(doc, sort_keys=False))
    os.replace(tmp, path)
    return path


def prepare_area(spec: AreaSpec, con, raw_dir: Path | None = None, areas_dir: Path | None = None,
                 dem_fetch=ingest.fetch_dem) -> dict:
    """Cut the area's trails from the index and fetch its elevation; the cut is written last, so an interrupted
    preparation leaves nothing that looks ready."""
    date = osm_index.index_meta(con)["extract_date"]
    write_area_region(spec, areas_dir)
    region = load_region(spec.id, regions_dir=Path("/nonexistent"), areas_dir=areas_dir)
    if date in ingest.cuts(region, raw_dir):
        return {"area_id": spec.id, "cut": date, "already": True}
    t0 = time.monotonic()
    b = spec.bbox
    raw = osm_index.cut(con, (b.west, b.south, b.east, b.north), date)
    tg = build_graph(raw, region.crs)
    if not any(not d["is_road"] for *_, d in tg.graph.edges(data=True)):
        raise ValueError("No trails were found there.")
    t1 = time.monotonic()
    dem_fetch(region, graph_bounds_ll(tg), raw_dir=raw_dir)
    t2 = time.monotonic()
    out_dir = ingest.region_raw_dir(region, raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = raw | {"region": spec.id, "area": asdict(spec) | {"bbox": vars(spec.bbox)}}
    path = out_dir / f"{ingest.CUT_PREFIX}_{raw['retrieved_at'].replace('-', '').replace(':', '')}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw))
    os.replace(tmp, path)
    return {"area_id": spec.id, "cut": date, "already": False, "edges": tg.graph.number_of_edges(),
            "seconds": {"cut_and_graph": round(t1 - t0, 2), "elevation": round(t2 - t1, 2)}}
