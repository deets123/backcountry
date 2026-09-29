"""Synthetic Overpass-shaped fixtures.

Geometry here is made up and laid out in UTM 17N meters around an arbitrary
origin so lengths are exact. It does not describe any real trail or place.
"""

import pytest
from pyproj import Transformer

CRS = "EPSG:32617"
_TO_WGS84 = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
X0, Y0 = 500_000.0, 4_000_000.0
RETRIEVED_AT = "2026-01-01T00:00:00Z"


class RawBuilder:
    def __init__(self):
        self.ids: dict[str, int] = {}
        self.coords: dict[int, tuple[float, float]] = {}  # id -> (lon, lat)
        self.xy: dict[str, tuple[float, float]] = {}
        self.elements: list[dict] = []

    def node(self, name: str, x: float, y: float) -> int:
        nid = 1000 + len(self.ids)
        lon, lat = _TO_WGS84.transform(X0 + x, Y0 + y)
        self.ids[name] = nid
        self.coords[nid] = (lon, lat)
        self.xy[name] = (x, y)
        return nid

    def way(self, way_id: int, names: list[str], **tags) -> None:
        nodes = [self.ids[n] for n in names]
        self.elements.append(
            {
                "type": "way",
                "id": way_id,
                "nodes": nodes,
                "geometry": [{"lat": self.coords[n][1], "lon": self.coords[n][0]} for n in nodes],
                "tags": tags,
            }
        )

    def poi(self, node_id: int, x: float, y: float, **tags) -> None:
        lon, lat = _TO_WGS84.transform(X0 + x, Y0 + y)
        self.elements.append({"type": "node", "id": node_id, "lat": lat, "lon": lon, "tags": tags})

    def doc(self) -> dict:
        return {
            "region": "synthetic",
            "retrieved_at": RETRIEVED_AT,
            "source": "synthetic fixture",
            "response": {"elements": self.elements},
        }


def build_network() -> RawBuilder:
    """Small network exercising every graph/QA case.

        L1                    D ~T         E -------- F -- (tertiary up)
        |                     |            |
        |                     b1           c1
        |          t2         |            |
        A ------- m1 ------- B -- m2 ---- C - S          G
        |          t1         I                          H
        L2                    J
    """
    b = RawBuilder()
    for name, x, y in [
        ("A", 0, 0), ("m1", 500, 0), ("B", 1000, 0), ("m2", 1500, 0), ("C", 2000, 0),
        ("b1", 1000, 400), ("D", 1000, 800),
        ("c1", 2000, 500), ("E", 2000, 1000),
        ("d1", 1500, 800),
        ("F", 2500, 1000), ("f1", 2500, 1500),
        ("t1", 500, -300), ("t2", 500, 300),
        ("G", 5000, 0), ("H", 5000, 300),
        ("S", 2010, 0),
        ("I", 990, -15), ("J", 990, -500),
        ("L1", -500, 500), ("L2", -500, -500),
        ("P1", 2520, 1020), ("P2", 2580, 1020), ("P3", 2580, 1080), ("P4", 2520, 1080),
    ]:
        b.node(name, x, y)

    b.way(1, ["A", "m1", "B", "m2", "C"], highway="path", name="Main Trail")
    b.way(2, ["B", "b1", "D"], highway="path", name="Spur")
    b.way(3, ["C", "c1", "E"], highway="unclassified", name="FR 1")
    b.way(4, ["D", "d1", "E"], highway="path", access="private", name="Private Path")
    b.way(5, ["E", "F"], highway="path", access="no", foot="designated", name="Override Trail")
    b.way(6, ["F", "f1"], highway="tertiary", name="State Rd")
    b.way(7, ["G", "H"], highway="path", name="Island Trail")
    b.way(8, ["C", "S"], highway="path", name="Stub")
    b.way(9, ["I", "J"], highway="path", name="Near Miss Trail")
    b.way(10, ["A", "L1", "L2", "A"], highway="path", name="Loop Trail")
    b.way(11, ["t1", "m1", "t2"], highway="tertiary", name="Cross Rd")
    b.way(12, ["P1", "P2", "P3", "P4", "P1"], amenity="parking", name="Test Lot")
    b.poi(9001, 1000, 820, highway="trailhead", name="Test TH")
    return b


@pytest.fixture
def network() -> RawBuilder:
    return build_network()


# --- synthetic USFS (ArcGIS GeoJSON-shaped) features ---------------------------


def lonlat(x: float, y: float) -> tuple[float, float]:
    return _TO_WGS84.transform(X0 + x, Y0 + y)


def usfs_line(coords: list[tuple[float, float]], **props) -> dict:
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": [list(lonlat(x, y)) for x, y in coords]}}


def usfs_point(x: float, y: float, **props) -> dict:
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": list(lonlat(x, y))}}


def usfs_polygon(coords: list[tuple[float, float]], **props) -> dict:
    ring = [list(lonlat(x, y)) for x, y in coords + coords[:1]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def bbox_xy(xmin: float, ymin: float, xmax: float, ymax: float):
    from backcountry.region import BBox

    (w, s), (e, n) = lonlat(xmin, ymin), lonlat(xmax, ymax)
    return BBox(south=s, west=w, north=n, east=e)


def usfs_doc(layers: dict[str, list[dict]]) -> dict:
    return {
        "region": "synthetic",
        "retrieved_at": "2026-01-02T00:00:00Z",
        "source": "synthetic USFS fixture",
        "layers": {name: {"url": f"synthetic://{name}", "geojson": {"type": "FeatureCollection", "features": feats}}
                   for name, feats in layers.items()},
    }
