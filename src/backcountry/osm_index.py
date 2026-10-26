"""A local index of US OpenStreetMap data, so any area can be cut out in seconds without Overpass.

`backcountry usdata build` reads the Geofabrik US extract (a .osm.pbf) once and keeps only what the engine
reads: every `highway` way, trailheads, parking, camp sites, water, protected-area boundaries, and named
places for search. An area is then cut out of this index into the same raw JSON shape an Overpass query
returns (see ingest.fetch), so graph building, trailheads, planning and export run unchanged.

Geometry is OSM's own, kept at OSM's precision (1e-7 degree integers); nothing is simplified or invented.
OSM data (c) OpenStreetMap contributors, available under the ODbL.
"""

import json
import os
import re
import sqlite3
import zlib
from array import array
from datetime import datetime, timezone
from pathlib import Path

from pyproj import Transformer
from shapely import wkb
from shapely.geometry import Point, box
from shapely.ops import transform as shp_transform

from backcountry.ingest import OSM_ATTRIBUTION

SOURCE = "OpenStreetMap via Geofabrik US extract"
SCALE = 10_000_000  # OSM stores coordinates as 1e-7 degree integers
MAX_QUERY_CHARS = 100

FEATURE_NODE_TAGS = {"highway": ("trailhead",), "amenity": ("parking", "drinking_water"),
                     "tourism": ("camp_site", "camp_pitch"), "natural": ("spring", "water")}
FEATURE_WAY_TAGS = {"amenity": ("parking",), "tourism": ("camp_site", "camp_pitch"), "natural": ("water",),
                    "waterway": ("stream", "river")}
PLACE_NODE_TAGS = {"natural": ("peak", "saddle", "arch", "valley", "cliff"), "place": ("locality", "hamlet"),
                   "tourism": ("viewpoint",)}
PLACE_LABELS = {"peak": "Peak", "saddle": "Saddle", "arch": "Arch", "valley": "Valley", "cliff": "Cliff",
                "locality": "Place", "hamlet": "Place", "viewpoint": "Viewpoint"}
TRAIL_HIGHWAYS = ("path", "footway")
FILTER_KEYS = ("highway", "amenity", "tourism", "natural", "waterway", "place", "boundary", "leisure")

_TO_ALBERS = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)  # equal-area, for area_km2

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE elements(id INTEGER PRIMARY KEY, otype TEXT, oid INTEGER, tags TEXT, nodes BLOB, coords BLOB,
                      lon REAL, lat REAL, ts TEXT, ver INTEGER);
CREATE VIRTUAL TABLE elements_rt USING rtree(id, minlon, maxlon, minlat, maxlat);
CREATE TABLE areas(id INTEGER PRIMARY KEY, osm TEXT, name TEXT, type_label TEXT, park INTEGER, is_state INTEGER,
                   tags TEXT, wkb BLOB, area_km2 REAL, ts TEXT, ver INTEGER);
CREATE VIRTUAL TABLE areas_rt USING rtree(id, minlon, maxlon, minlat, maxlat);
CREATE TABLE places(id INTEGER PRIMARY KEY, osm TEXT, name TEXT, type_label TEXT, lon REAL, lat REAL,
                    minlon REAL, minlat REAL, maxlon REAL, maxlat REAL);
CREATE VIRTUAL TABLE names USING fts5(name, ref UNINDEXED);
"""


# --- encoding -------------------------------------------------------------------------------------------


def _pack(values: list[int]) -> bytes:
    deltas, prev = array("q"), 0
    for v in values:
        deltas.append(v - prev)
        prev = v
    return zlib.compress(deltas.tobytes())


def _unpack(blob: bytes) -> list[int]:
    deltas = array("q")
    deltas.frombytes(zlib.decompress(blob))
    out, acc = [], 0
    for d in deltas:
        acc += d
        out.append(acc)
    return out


def _iso(ts) -> str | None:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def _matches(tags: dict, table: dict) -> bool:
    return any(tags.get(k) in vals for k, vals in table.items())


# --- labels ---------------------------------------------------------------------------------------------


def _area_kind(tags: dict) -> tuple[str, bool, bool] | None:
    """(type_label, park, is_state) for a boundary worth keeping, else None."""
    name = tags.get("name") or ""
    if not name:
        return None
    if tags.get("boundary") == "administrative" and tags.get("admin_level") == "4":
        return "State", False, True
    if tags.get("boundary") == "national_park" or tags.get("protect_class") == "2":
        return "National Park", True, False
    if tags.get("boundary") == "protected_area":
        title = tags.get("protection_title")
        return (title, "Park" in title, False) if title else ("Protected Area", False, False)
    if tags.get("leisure") == "nature_reserve":
        return "Nature Reserve", False, False
    if tags.get("leisure") == "park" and name.endswith("State Park"):
        return "State Park", True, False
    return None


# --- build ----------------------------------------------------------------------------------------------


def build_index(pbf: Path, out: Path, extract_date: str, source_url: str | None = None,
                locations: str = "flex_mem") -> dict:
    """Filter an OSM extract into a SQLite index at `out` (written to a temp file, then renamed)."""
    import osmium

    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    counts = {"ways": 0, "points": 0, "areas": 0, "names": 0, "skipped_ways": 0}
    wkbf = osmium.geom.WKBFactory()

    def add_element(otype, oid, tags, nodes, xs, ys, ts, ver, lon=None, lat=None):
        cur = con.execute("INSERT INTO elements(otype, oid, tags, nodes, coords, lon, lat, ts, ver) VALUES (?,?,?,?,?,?,?,?,?)",
                          (otype, oid, json.dumps(tags), _pack(nodes) if nodes else None,
                           _pack([v for xy in zip(xs, ys) for v in xy]) if xs else None, lon, lat, ts, ver))
        if xs:
            bb = (min(xs) / SCALE, max(xs) / SCALE, min(ys) / SCALE, max(ys) / SCALE)
        else:
            bb = (lon, lon, lat, lat)
        con.execute("INSERT INTO elements_rt VALUES (?,?,?,?,?)", (cur.lastrowid, *bb))

    def add_place(osm, name, label, lon, lat, bb):
        cur = con.execute("INSERT INTO places(osm, name, type_label, lon, lat, minlon, minlat, maxlon, maxlat) "
                          "VALUES (?,?,?,?,?,?,?,?,?)", (osm, name, label, lon, lat, *bb))
        con.execute("INSERT INTO names(name, ref) VALUES (?, ?)", (name, f"p:{cur.lastrowid}"))
        counts["names"] += 1

    fp = (osmium.FileProcessor(str(pbf)).with_locations(locations)
          .with_areas(osmium.filter.KeyFilter("boundary", "leisure"))
          .with_filter(osmium.filter.KeyFilter(*FILTER_KEYS)))
    for obj in fp:
        tags = dict(obj.tags)
        if obj.is_node():
            feature, place = _matches(tags, FEATURE_NODE_TAGS), _matches(tags, PLACE_NODE_TAGS) and tags.get("name")
            if not (feature or place):
                continue
            x, y = obj.location.x, obj.location.y
            lon, lat = x / SCALE, y / SCALE
            if feature:
                add_element("node", obj.id, tags, None, None, None, _iso(obj.timestamp), obj.version, lon, lat)
                counts["points"] += 1
            if place:
                kind = next(tags[k] for k in PLACE_NODE_TAGS if tags.get(k) in PLACE_NODE_TAGS[k])
                add_place(f"node/{obj.id}", tags["name"], PLACE_LABELS[kind], lon, lat, (lon, lat, lon, lat))
        elif obj.is_way():
            if "highway" not in tags and not _matches(tags, FEATURE_WAY_TAGS):
                continue
            try:
                nodes = [n.ref for n in obj.nodes]
                xs = [n.location.x for n in obj.nodes]
                ys = [n.location.y for n in obj.nodes]
                if not all(n.location.valid() for n in obj.nodes):
                    raise osmium.InvalidLocationError("missing node")
            except osmium.InvalidLocationError:
                counts["skipped_ways"] += 1  # a node outside the extract: never guessed
                continue
            add_element("way", obj.id, tags, nodes, xs, ys, _iso(obj.timestamp), obj.version)
            counts["ways"] += 1
            if tags.get("highway") in TRAIL_HIGHWAYS and tags.get("name"):
                mid = len(xs) // 2
                add_place(f"way/{obj.id}", tags["name"], "Trail", xs[mid] / SCALE, ys[mid] / SCALE,
                          (min(xs) / SCALE, min(ys) / SCALE, max(xs) / SCALE, max(ys) / SCALE))
        elif obj.is_area():
            kind = _area_kind(tags)
            if kind is None:
                continue
            try:
                geom = wkb.loads(wkbf.create_multipolygon(obj), hex=True)
            except RuntimeError:
                continue  # an incomplete multipolygon (e.g. cut by the extract's edge)
            label, park, is_state = kind
            osm = f"{'way' if obj.from_way() else 'relation'}/{obj.orig_id()}"
            km2 = shp_transform(_TO_ALBERS.transform, geom).area / 1e6
            cur = con.execute("INSERT INTO areas(osm, name, type_label, park, is_state, tags, wkb, area_km2, ts, ver) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?)", (osm, tags["name"], label, int(park), int(is_state),
                                                               json.dumps(tags), geom.wkb, km2,
                                                               _iso(obj.timestamp), obj.version))
            w, s, e, n = geom.bounds
            con.execute("INSERT INTO areas_rt VALUES (?,?,?,?,?)", (cur.lastrowid, w, e, s, n))
            if not is_state:
                con.execute("INSERT INTO names(name, ref) VALUES (?, ?)", (tags["name"], f"a:{cur.lastrowid}"))
                counts["names"] += 1
            counts["areas"] += 1

    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [("extract_date", extract_date), ("built_at", built_at),
                                                       ("source_url", source_url or ""), ("counts", json.dumps(counts))])
    con.commit()
    con.close()
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    return counts


# --- read -----------------------------------------------------------------------------------------------


def open_index(path: Path) -> sqlite3.Connection:
    """Read-only: the API can query the index but never change it."""
    con = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def index_meta(con) -> dict:
    meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
    return {"extract_date": meta.get("extract_date"), "built_at": meta.get("built_at"),
            "source_url": meta.get("source_url") or None, "counts": json.loads(meta.get("counts") or "{}")}


def _areas_at(con, lon: float, lat: float, states: bool) -> list[sqlite3.Row]:
    rows = con.execute("SELECT a.* FROM areas a JOIN areas_rt r ON a.id = r.id WHERE r.minlon <= ? AND r.maxlon >= ? "
                       "AND r.minlat <= ? AND r.maxlat >= ? AND a.is_state = ?", (lon, lon, lat, lat, int(states)))
    pt = Point(lon, lat)
    return [r for r in rows if wkb.loads(r["wkb"]).covers(pt)]


def state_at(con, lon: float, lat: float) -> str | None:
    """The US state boundary containing this point, or None (outside the US, as far as the index knows)."""
    rows = _areas_at(con, lon, lat, states=True)
    return rows[0]["name"] if rows else None


def _area_record(r: sqlite3.Row) -> dict:
    geom = wkb.loads(r["wkb"])
    return {"kind": "area", "osm": r["osm"], "name": r["name"], "type_label": r["type_label"], "park": bool(r["park"]),
            "area_km2": r["area_km2"], "bbox": list(geom.bounds), "center": [round(c, 7) for c in geom.centroid.coords[0]]}


def containing_areas(con, lon: float, lat: float) -> list[dict]:
    """Protected areas and parks holding this point, smallest first. States are not included."""
    return sorted((_area_record(r) for r in _areas_at(con, lon, lat, states=False)), key=lambda a: a["area_km2"])


def area_by_osm(con, osm: str) -> dict | None:
    r = con.execute("SELECT * FROM areas WHERE osm = ? AND is_state = 0", (osm,)).fetchone()
    return _area_record(r) | {"state": state_at(con, *wkb.loads(r["wkb"]).representative_point().coords[0])} if r else None


def place_by_osm(con, osm: str) -> dict | None:
    r = con.execute("SELECT * FROM places WHERE osm = ?", (osm,)).fetchone()
    return _place_record(con, r) if r else None


def _place_record(con, r: sqlite3.Row) -> dict:
    return {"kind": "place", "osm": r["osm"], "name": r["name"], "type_label": r["type_label"],
            "bbox": [r["minlon"], r["minlat"], r["maxlon"], r["maxlat"]], "center": [r["lon"], r["lat"]],
            "state": state_at(con, r["lon"], r["lat"])}


def _fts_query(text: str) -> str | None:
    tokens = re.findall(r"\w+", text[:MAX_QUERY_CHARS])
    if not tokens:
        return None
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    quoted[-1] += "*"  # the last word may still be being typed
    return " ".join(quoted)


def search(con, text: str, limit: int = 10) -> list[dict]:
    """Areas and places whose names match `text`: exact names first, then areas (parks first, larger first)."""
    query = _fts_query(text)
    if query is None:
        return []
    want = " ".join(re.findall(r"\w+", text[:MAX_QUERY_CHARS])).lower()
    out, seen = [], set()
    for row in con.execute("SELECT ref FROM names WHERE names MATCH ? LIMIT 200", (query,)):
        kind, rid = row["ref"].split(":")
        if kind == "a":
            r = con.execute("SELECT * FROM areas WHERE id = ?", (int(rid),)).fetchone()
            rec = _area_record(r) | {"state": state_at(con, *wkb.loads(r["wkb"]).representative_point().coords[0])}
        else:
            rec = _place_record(con, con.execute("SELECT * FROM places WHERE id = ?", (int(rid),)).fetchone())
        key = (rec["kind"], rec["name"].lower(), rec["type_label"], rec["state"]) if rec["kind"] == "place" else rec["osm"]
        if key in seen:
            continue  # a trail split into many ways is one result
        seen.add(key)
        out.append(rec)
    exact = lambda r: " ".join(re.findall(r"\w+", r["name"])).lower() == want  # noqa: E731
    out.sort(key=lambda r: (not exact(r), r["kind"] != "area", not r.get("park", False), -r.get("area_km2", 0.0), r["name"]))
    return out[:limit]


def _way_element(r: sqlite3.Row) -> dict:
    flat = _unpack(r["coords"])
    geometry = [{"lat": flat[i + 1] / SCALE, "lon": flat[i] / SCALE} for i in range(0, len(flat), 2)]
    lats, lons = [g["lat"] for g in geometry], [g["lon"] for g in geometry]
    return {"type": "way", "id": r["oid"], "tags": json.loads(r["tags"]), "nodes": _unpack(r["nodes"]),
            "geometry": geometry, "bounds": {"minlat": min(lats), "minlon": min(lons), "maxlat": max(lats), "maxlon": max(lons)},
            "timestamp": r["ts"], "version": r["ver"]}


def _boundary_element(r: sqlite3.Row) -> dict:
    geom = wkb.loads(r["wkb"])
    w, s, e, n = geom.bounds
    otype, oid = r["osm"].split("/")
    rings = [list(p.exterior.coords) for p in getattr(geom, "geoms", [geom])]
    el = {"type": otype, "id": int(oid), "tags": json.loads(r["tags"]), "timestamp": r["ts"], "version": r["ver"],
          "bounds": {"minlat": s, "minlon": w, "maxlat": n, "maxlon": e}}
    as_geom = lambda ring: [{"lat": lat, "lon": lon} for lon, lat in ring]  # noqa: E731
    if otype == "way":
        el["geometry"] = as_geom(rings[0])
    else:
        el["members"] = [{"type": "way", "role": "outer", "geometry": as_geom(ring)} for ring in rings]
    return el


def cut(con, bbox: tuple, extract_date: str) -> dict:
    """Every indexed element touching bbox (west, south, east, north), in the raw shape ingest.fetch caches."""
    w, s, e, n = bbox
    elements = []
    q = ("SELECT x.* FROM {t} x JOIN {t}_rt r ON x.id = r.id "
         "WHERE r.maxlon >= ? AND r.minlon <= ? AND r.maxlat >= ? AND r.minlat <= ?{extra} ORDER BY x.id")
    for r in con.execute(q.format(t="elements", extra=""), (w, e, s, n)):
        if r["otype"] == "way":
            elements.append(_way_element(r))
        else:
            elements.append({"type": "node", "id": r["oid"], "lat": r["lat"], "lon": r["lon"], "tags": json.loads(r["tags"]),
                             "timestamp": r["ts"], "version": r["ver"]})
    area_box = box(w, s, e, n)
    for r in con.execute(q.format(t="areas", extra=" AND x.is_state = 0"), (w, e, s, n)):
        if wkb.loads(r["wkb"]).intersects(area_box):
            elements.append(_boundary_element(r))
    meta = index_meta(con)
    return {
        "retrieved_at": f"{extract_date}T00:00:00Z",
        "source": SOURCE,
        "endpoint": meta["source_url"],
        "attribution": OSM_ATTRIBUTION,
        "osm_base": extract_date,
        "bbox": {"south": s, "west": w, "north": n, "east": e},
        "query": f"local index (extract {extract_date}), bbox {w},{s},{e},{n}",
        "response": {"elements": elements},
    }
