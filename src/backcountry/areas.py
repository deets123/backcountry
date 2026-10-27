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
from backcountry.elevation import corridor_cells, graph_bounds_ll
from backcountry.graph import build_graph
from backcountry.region import AREAS_DIR, BBox, load_region

MAX_AREA_KM2 = 5000.0  # bigger areas open as a capped box. Measured 2026-10-27 (PROGRESS.md): Grand Canyon NP,
#                        4,866 km2, opens in 4.4 min (elevation) and 1.4 GB; Yellowstone-size parks stay capped
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
    members: tuple = ()  # a group ("Zion": park and nearby wilderness): ((osm ref, name), ...) of its areas

    @property
    def display_name(self) -> str:
        """What the user sees: a park too big to open whole says only part of it is loaded."""
        return f"Part of {self.name}" if self.capped and self.osm else self.name


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


def area_from_refs(con, refs) -> AreaSpec:
    """A search result's group of touching areas, each looked up again by its ref (the client's copy isn't
    trusted). Opens as one area covering them all."""
    if not isinstance(refs, list) or not 1 <= len(refs) <= osm_index.MAX_GROUP:
        raise ValueError("That isn't a search result.")
    found = []
    for ref in refs:
        if not isinstance(ref, str) or not _REF.match(ref):
            raise ValueError("That isn't a search result.")
        a = osm_index.area_by_osm(con, ref)
        if a is None:
            raise ValueError("That place isn't in the trail data.")
        found.append(a)
    if len(found) == 1:
        return _from_area(con, found[0])
    if sum(a["area_km2"] for a in found) > MAX_AREA_KM2:
        raise ValueError("Those areas are too big to open together; open one of them instead.")
    words = [a["name"].split() for a in found]
    common = []
    for ws in zip(*words):
        if len({w.lower() for w in ws}) != 1:
            break
        common.append(ws[0])
    name = osm_index.group_name(" ".join(common) or found[0]["name"], found)
    w, s_, e, n = (min(a["bbox"][0] for a in found), min(a["bbox"][1] for a in found),
                   max(a["bbox"][2] for a in found), max(a["bbox"][3] for a in found))
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60].strip("-") or "area"
    key = ",".join(sorted(a["osm"] for a in found))
    return AreaSpec(id=f"{slug}-{hashlib.sha1(key.encode()).hexdigest()[:8]}", name=name, type_label="Areas",
                    state=found[0]["state"], bbox=_padded([w, s_, e, n]), osm=None, capped=False,
                    members=tuple((a["osm"], a["name"]) for a in found))


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
        "id": spec.id, "name": spec.display_name, "generated": True,
        "crs": utm_crs((b.west + b.east) / 2, (b.south + b.north) / 2),
        "bbox": {"south": b.south, "west": b.west, "north": b.north, "east": b.east},
        "boundary_name_regex": (re.escape(spec.name) if spec.osm
                                else "|".join(re.escape(n) for _, n in spec.members) if spec.members else None),
        "area": {"name": spec.name, "type_label": spec.type_label, "state": spec.state, "osm": spec.osm,
                 "capped": spec.capped, **({"members": [list(m) for m in spec.members]} if spec.members else {})},
        "trailheads": [],
    }
    path = d / f"{spec.id}.yaml"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("# Generated by backcountry.areas from the local US OSM index. Do not edit.\n" + yaml.safe_dump(doc, sort_keys=False))
    os.replace(tmp, path)
    return path


ELEVATION_DONE = "elevation_done_{date}"
ELEVATION_PAD_DEG = 0.05  # elevation is fetched this far (~5 km) past the area's box, no farther  # beside a cut: its elevation step finished (the area is ready)


def elevation_done(region, raw_dir: Path | None, date: str) -> bool:
    return (ingest.region_raw_dir(region, raw_dir) / ELEVATION_DONE.format(date=date)).exists()


def _cells(tg, spec: AreaSpec, con=None) -> list:
    """Elevation cells: near the area's outline when it has one (a park's box can be far bigger than the park),
    else its box, ELEVATION_PAD_DEG past it either way."""
    b, p = spec.bbox, ELEVATION_PAD_DEG
    shape = osm_index.area_shape(con, spec.osm) if con is not None and spec.osm and not spec.capped else None
    if con is not None and spec.members:
        from shapely.ops import unary_union

        shapes = [osm_index.area_shape(con, ref) for ref, _ in spec.members]
        shape = unary_union([x for x in shapes if x is not None]) if all(x is not None for x in shapes) else None
    if shape is not None:
        return corridor_cells(tg, within=shape.simplify(0.005).buffer(p))
    return corridor_cells(tg, within=(b.west - p, b.south - p, b.east + p, b.north + p))


def prepare_area(spec: AreaSpec, con, raw_dir: Path | None = None, areas_dir: Path | None = None,
                 dem_fetch=ingest.fetch_dem, on_trails=None) -> dict:
    """Cut the area's trails from the index, then fetch its elevation, in two steps: the cut is written as soon as
    the graph is built (the trails can be shown; `on_trails()` is called), and a small marker once elevation is
    done (the area is ready). An area with this cut but no marker gets only its elevation."""
    date = osm_index.index_meta(con)["extract_date"]
    write_area_region(spec, areas_dir)
    region = load_region(spec.id, regions_dir=Path("/nonexistent"), areas_dir=areas_dir)
    out_dir = ingest.region_raw_dir(region, raw_dir)
    if date in ingest.cuts(region, raw_dir):
        if elevation_done(region, raw_dir, date):
            return {"area_id": spec.id, "cut": date, "already": True}
        tg = build_graph(ingest.load_raw(region, raw_dir, date), region.crs)
        t1 = time.monotonic()
        dem_fetch(region, graph_bounds_ll(tg), raw_dir=raw_dir, cells=_cells(tg, spec, con))
        (out_dir / ELEVATION_DONE.format(date=date)).write_text(date)
        return {"area_id": spec.id, "cut": date, "already": False, "elevation_only": True,
                "seconds": {"elevation": round(time.monotonic() - t1, 2)}}
    for left in out_dir.glob("*.tmp"):
        left.unlink(missing_ok=True)  # an interrupted earlier run's half-written files (they count toward disk use)
    t0 = time.monotonic()
    b = spec.bbox
    raw = osm_index.cut(con, (b.west, b.south, b.east, b.north), date)
    tg = build_graph(raw, region.crs)
    if not any(not d["is_road"] for *_, d in tg.graph.edges(data=True)):
        raise ValueError("No trails were found there.")
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = raw | {"region": spec.id, "area": asdict(spec) | {"bbox": vars(spec.bbox)}}
    path = out_dir / f"{ingest.CUT_PREFIX}_{raw['retrieved_at'].replace('-', '').replace(':', '')}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw))
    os.replace(tmp, path)
    t1 = time.monotonic()
    if on_trails is not None:
        on_trails()
    dem_fetch(region, graph_bounds_ll(tg), raw_dir=raw_dir, cells=_cells(tg, spec, con))
    (out_dir / ELEVATION_DONE.format(date=date)).write_text(date)
    t2 = time.monotonic()
    return {"area_id": spec.id, "cut": date, "already": False, "edges": tg.graph.number_of_edges(),
            "seconds": {"cut_and_graph": round(t1 - t0, 2), "elevation": round(t2 - t1, 2)}}


# --- prepared ahead ---------------------------------------------------------------------------------------

PREPARED_FILE = "prepared.json"  # in the areas folder: areas prepared ahead, never evicted for user-opened ones


def prepared_ids(areas_dir: Path | None = None) -> set[str]:
    path = (areas_dir or AREAS_DIR) / PREPARED_FILE
    try:
        return set(json.loads(path.read_text()).get("areas", []))
    except (OSError, ValueError):
        return set()


def write_prepared(areas_dir: Path | None, ids) -> None:
    d = areas_dir or AREAS_DIR
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (PREPARED_FILE + ".tmp")
    tmp.write_text(json.dumps({"areas": sorted(set(ids)), "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
    os.replace(tmp, d / PREPARED_FILE)


def _area_bytes(area_id: str, raw_dir: Path | None) -> int:
    d = (raw_dir or ingest.RAW_DIR) / area_id
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.exists() else 0


def _trail_miles(con, spec: AreaSpec, date: str) -> float:
    b = spec.bbox
    tg = build_graph(osm_index.cut(con, (b.west, b.south, b.east, b.north), date), utm_crs((b.west + b.east) / 2, (b.south + b.north) / 2))
    return sum(d["length_m"] for *_, d in tg.graph.edges(data=True) if not d["is_road"]) / 1609.344


def prepare_popular(con, budget_gb: float, raw_dir: Path | None = None, areas_dir: Path | None = None,
                    dem_fetch=None, limit: int | None = None, log=print) -> dict:
    """Prepare popular areas ahead (osm_index.popular_areas; wilderness ranked by trail miles), stopping when
    the next one would take prepared areas past budget_gb. Each finished area is recorded in prepared.json."""
    dem_fetch = dem_fetch or ingest.fetch_dem
    date = osm_index.index_meta(con)["extract_date"]
    found = osm_index.popular_areas(con, MAX_AREA_KM2)
    parks = [a for a in found if "Wilderness" not in (a["type_label"] or "") and "Wilderness" not in a["name"]]
    wild = [a for a in found if a not in parks]
    if wild:
        log(f"Ranking {len(wild)} wilderness areas by trail miles…")
        miles = {a["osm"]: _trail_miles(con, _from_area(con, a), date) for a in wild}
        wild.sort(key=lambda a: -miles[a["osm"]])
    prepared, skipped = sorted(prepared_ids(areas_dir)), []
    budget = budget_gb * 1e9
    used = sum(_area_bytes(a, raw_dir) for a in prepared)
    for a in (parks + wild)[:limit]:
        spec = _from_area(con, a)
        if spec.id in prepared and _area_bytes(spec.id, raw_dir):
            out = prepare_area(spec, con, raw_dir, areas_dir, dem_fetch)  # quick when fresh; refreshes a stale cut
            used = sum(_area_bytes(x, raw_dir) for x in prepared)
            continue
        before = _area_bytes(spec.id, raw_dir)
        try:
            out = prepare_area(spec, con, raw_dir, areas_dir, dem_fetch)
        except ValueError as e:  # e.g. no trails there
            skipped.append({"area": spec.display_name, "reason": str(e)})
            continue
        size = _area_bytes(spec.id, raw_dir) - before
        if used + size > budget:
            if not before:  # a new one that doesn't fit: take it out again
                import shutil

                shutil.rmtree((raw_dir or ingest.RAW_DIR) / spec.id, ignore_errors=True)
                ((areas_dir or AREAS_DIR) / f"{spec.id}.yaml").unlink(missing_ok=True)
            log(f"Stopping: {spec.display_name} would go past the {budget_gb:g} GB share.")
            break
        used += size
        prepared.append(spec.id)
        write_prepared(areas_dir, prepared)
        log(f"Prepared {spec.display_name} ({size / 1e6:.0f} MB, {out.get('seconds', {})}); {used / 1e9:.2f} GB used")
    return {"prepared": [p for p in prepared], "skipped": skipped, "used_gb": round(used / 1e9, 3)}
