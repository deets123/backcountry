"""Camp sites and water features along a route, from source data only.

Everything listed here is an OSM (or USFS) map feature with its source and
position along the route. None of it says a site is open or legal, or that
water is present, flowing, or drinkable. The report says so next to the list.
"""

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point, box

NEAR_ROUTE_M = 150.0  # ~500 ft: list features this close to the route
NEAR_USFS_M = 500.0  # USFS sites (campgrounds) are listed from farther: they're bigger and named
WATER_WARNING = ("Water features come from OpenStreetMap. Whether water is actually there, flowing, or drinkable "
                 "is unknown; this tool has not checked.")


class FeatureIndex:
    """Every camp site, water point, USFS site and stream of an area, projected once and put in a spatial tree,
    so listing what's near a route costs a lookup, not a scan. Built once per loaded area (the data never
    changes between requests); every route then gets exactly the features the scan would find."""

    def __init__(self, camp_sites=(), water=None, usfs_sites=(), crs: str = "EPSG:4326"):
        self.crs = crs
        to_utm = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        water = water or {"points": [], "lines": []}
        # points: (item, kind, limit) with their projected geometry
        self.points = ([(s, s.get("kind", "camp_site"), NEAR_ROUTE_M) for s in camp_sites]
                       + [(s, f"usfs {s.get('subtype', 'site').lower()}", NEAR_USFS_M) for s in usfs_sites]
                       + [(p, p["kind"], NEAR_ROUTE_M) for p in water["points"]])
        self._point_geoms = [Point(*to_utm.transform(item["lon"], item["lat"])) for item, _, _ in self.points]
        self._point_tree = STRtree(self._point_geoms) if self._point_geoms else None
        self.lines = list(water["lines"])
        self._line_geoms = [LineString([to_utm.transform(lon, lat) for lon, lat in l["coords"]]) for l in self.lines]
        self._line_tree = STRtree(self._line_geoms) if self._line_geoms else None

    def near(self, line: LineString, limit_m: float) -> list[int]:
        """Indexes of points whose bounding boxes come within limit_m of the line's box (a superset: the caller
        measures the real distance)."""
        if self._point_tree is None:
            return []
        return sorted(int(i) for i in self._point_tree.query(box(*line.bounds).buffer(limit_m)))

    def crossing(self, line: LineString) -> list[int]:
        """Indexes of streams whose bounding boxes touch the line's box (a superset: the caller intersects)."""
        if self._line_tree is None:
            return []
        return sorted(int(i) for i in self._line_tree.query(box(*line.bounds)))

    def point_geom(self, i: int) -> Point:
        return self._point_geoms[i]

    def line_geom(self, i: int) -> LineString:
        return self._line_geoms[i]


def along_route(route, camp_sites=(), water=None, usfs_sites=(), plan=None, index: FeatureIndex | None = None) -> list[dict]:
    """Features within NEAR_ROUTE_M of the route (and stream crossings), sorted by distance along it.

    With `index` (one per area), the lists are ignored and the lookup is a tree query; without one the
    index is built from the lists for this call (same answers, slower)."""
    if index is None:
        index = FeatureIndex(camp_sites, water, usfs_sites, route.crs)
    to_utm = Transformer.from_crs("EPSG:4326", route.crs, always_xy=True)
    to_ll = Transformer.from_crs(route.crs, "EPSG:4326", always_xy=True)
    line = route.line_utm
    feats = []
    for i in index.near(line, NEAR_USFS_M):
        item, kind, off_limit = index.points[i]
        pt = index.point_geom(i)
        off = line.distance(pt)
        if off <= off_limit:
            feats.append({"source": item["source"], "kind": kind, "name": item.get("name"), "dist_m": float(line.project(pt)),
                          "off_m": float(off), "lat": item["lat"], "lon": item["lon"], "tags": item.get("tags", {}),
                          "meta": item.get("meta", {}), "url": item.get("url")})
    for i in index.crossing(line):
        l = index.lines[i]
        inter = line.intersection(index.line_geom(i))
        pts = [inter] if inter.geom_type == "Point" else list(getattr(inter, "geoms", []))
        for g in pts:
            p = g if g.geom_type == "Point" else g.representative_point()
            lon, lat = to_ll.transform(p.x, p.y)
            feats.append({"source": l["source"], "kind": f"{l['kind']} crossing", "name": l.get("name"),
                          "dist_m": float(line.project(p)), "off_m": 0.0, "lat": lat, "lon": lon, "tags": l.get("tags", {}),
                          "meta": l.get("meta", {}), "url": l.get("url")})
    feats.sort(key=lambda f: (f["dist_m"], f["source"]))

    if plan is not None:
        for f in feats:
            f["day"] = next((d.index for d in plan.days if d.start_m <= f["dist_m"] <= d.end_m), None)
        water_feats = [f for f in feats if f["kind"] not in ("camp_site", "camp_pitch") and not f["kind"].startswith("usfs")]
        for camp in plan.camps:
            cp = Point(*to_utm.transform(camp.lon, camp.lat))
            best = None
            for f in water_feats:
                dist = cp.distance(Point(*to_utm.transform(f["lon"], f["lat"])))
                if best is None or dist < best["distance_m"]:
                    best = {"source": f["source"], "kind": f["kind"], "name": f["name"], "distance_m": float(dist)}
            camp.nearest_water = best
    return feats
