"""Camp sites and water features along a route, from source data only.

Everything listed here is an OSM (or USFS) map feature with its source and
position along the route. None of it says a site is open or legal, or that
water is present, flowing, or drinkable. The report says so next to the list.
"""

from pyproj import Transformer
from shapely.geometry import LineString, Point

NEAR_ROUTE_M = 150.0  # ~500 ft: list features this close to the route
WATER_WARNING = ("Water features come from OpenStreetMap. Whether water is actually there, flowing, or drinkable "
                 "is unknown; this tool has not checked.")


def along_route(route, camp_sites=(), water=None, usfs_sites=(), plan=None) -> list[dict]:
    """Features within NEAR_ROUTE_M of the route (and stream crossings), sorted by distance along it."""
    to_utm = Transformer.from_crs("EPSG:4326", route.crs, always_xy=True)
    to_ll = Transformer.from_crs(route.crs, "EPSG:4326", always_xy=True)
    line = route.line_utm
    feats = []

    def add_point(item, kind, off_limit=NEAR_ROUTE_M):
        pt = Point(*to_utm.transform(item["lon"], item["lat"]))
        off = line.distance(pt)
        if off <= off_limit:
            feats.append({"source": item["source"], "kind": kind, "name": item.get("name"), "dist_m": float(line.project(pt)),
                          "off_m": float(off), "lat": item["lat"], "lon": item["lon"], "tags": item.get("tags", {})})

    for s in camp_sites:
        add_point(s, s.get("kind", "camp_site"))
    for s in usfs_sites:
        add_point(s, f"usfs {s.get('subtype', 'site').lower()}", off_limit=500.0)
    water = water or {"points": [], "lines": []}
    for p in water["points"]:
        add_point(p, p["kind"])
    for l in water["lines"]:
        stream = LineString([to_utm.transform(lon, lat) for lon, lat in l["coords"]])
        inter = line.intersection(stream)
        pts = [inter] if inter.geom_type == "Point" else list(getattr(inter, "geoms", []))
        for g in pts:
            p = g if g.geom_type == "Point" else g.representative_point()
            lon, lat = to_ll.transform(p.x, p.y)
            feats.append({"source": l["source"], "kind": f"{l['kind']} crossing", "name": l.get("name"),
                          "dist_m": float(line.project(p)), "off_m": 0.0, "lat": lat, "lon": lon, "tags": l.get("tags", {})})
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
