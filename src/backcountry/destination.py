"""A searched place as the trip's destination: where it is comes from the local US index, never from the phone.

Three kinds, by the index's label:
  trail  a named trail (way): reached by walking some of it;
  point  a peak, viewpoint, saddle, arch, cliff or valley: reached at the trail node nearest to it. If that node
         is farther than NEAR_M, the trip says so; no line is drawn past the mapped trail;
  place  a named place (a meadow, a basin): reached by passing within NEAR_M of it, or inside its box.
"""

import math
import re
from dataclasses import dataclass

import networkx as nx
from pyproj import Transformer
from shapely.geometry import Point, box

from backcountry import osm_index

NEAR_M = 400.0  # a quarter mile
M_PER_MI = 1609.344
_REF = re.compile(r"^(node|way|relation)/\d{1,12}$")


def check_ref(value) -> str:
    from backcountry.planning import RequestError

    if not isinstance(value, str) or not _REF.match(value):
        raise RequestError("destination", "That isn't a place from the search.")
    return value


@dataclass
class Destination:
    ref: str
    name: str
    kind: str  # "trail", "point" or "place"
    node: int | None  # the trail node the trip goes to (None: not on this trail network)
    center: tuple[float, float]  # lon, lat from the index
    bbox: tuple[float, float, float, float]
    offset_m: float  # straight line from the place to `node`
    note: str | None = None
    trail_name: str | None = None
    center_utm: tuple[float, float] | None = None
    bbox_utm: tuple[float, float, float, float] | None = None

    def summary(self) -> dict:
        return {"name": self.name, "kind": self.kind, "center": list(self.center), "note": self.note,
                "offset_m": round(self.offset_m, 1)}


def _trail_nodes(tg, name: str | None = None):
    """Nodes on a trail (not a road), or on the trail with this name."""
    out = set()
    for u, v, d in tg.graph.edges(data=True):
        if name is not None:
            if (d["name"] or "").lower() == name.lower():
                out |= {u, v}
        elif not d["is_road"]:
            out |= {u, v}
    return out


def resolve(con, tg, ref: str) -> Destination:
    """The destination a search result names, looked up again in the index (the phone's copy isn't trusted)."""
    from backcountry.planning import RequestError

    check_ref(ref)
    place = osm_index.place_by_osm(con, ref)
    if place is None:
        raise RequestError("destination", "That place isn't in the trail data.")
    label = place["type_label"]
    kind = "trail" if label == "Trail" else "place" if label == "Place" else "point"
    to_utm = Transformer.from_crs("EPSG:4326", tg.crs, always_xy=True)
    cx, cy = to_utm.transform(*place["center"])
    w, s, e, n = place["bbox"]
    (bw, bs), (be, bn) = to_utm.transform(w, s), to_utm.transform(e, n)
    G = tg.graph
    candidates = _trail_nodes(tg, place["name"] if kind == "trail" else None) or _trail_nodes(tg)
    node, offset = None, math.inf
    for v in candidates:
        dist = math.hypot(G.nodes[v]["x"] - cx, G.nodes[v]["y"] - cy)
        if dist < offset:
            node, offset = v, dist
    note = None
    if kind == "point" and offset > NEAR_M:
        note = (f"The nearest mapped trail point is {offset / M_PER_MI:.1f} mi from {place['name']}; "
                "no mapped trail reaches it.")
    return Destination(ref=ref, name=place["name"], kind=kind, node=node, center=tuple(place["center"]),
                       bbox=(w, s, e, n), offset_m=offset, note=note,
                       trail_name=place["name"] if kind == "trail" else None, center_utm=(cx, cy),
                       bbox_utm=(bw, bs, be, bn))


def reached(route, dest: Destination) -> bool:
    if dest.kind == "trail":
        return any((e["name"] or "").lower() == dest.trail_name.lower() for e in route.edges)
    if dest.kind == "point":
        return dest.node in route.nodes
    near = route.line_utm.distance(Point(*dest.center_utm)) <= NEAR_M
    bw, bs, be, bn = dest.bbox_utm
    return near or (be > bw and bn > bs and route.line_utm.intersects(box(bw, bs, be, bn)))


def reachable_from(tg, start: int, dest: Destination) -> bool:
    return dest.node is not None and dest.node in nx.node_connected_component(tg.graph, start)


def spur_m(tg, node: int) -> float:
    """How far `node` is from the nearest junction that lies on a loop (0 if it does): a loop can only reach a
    spur's end by walking out and back this far."""
    G = nx.Graph(tg.graph)
    G.remove_edges_from(nx.selfloop_edges(G))
    core = set(nx.k_core(G, 2))
    if node in core:
        return 0.0
    if not core:
        return math.inf
    dist = nx.multi_source_dijkstra_path_length(tg.graph, core, weight="length_m")
    return dist.get(node, math.inf)


def trail_distances(tg, dest: Destination) -> dict[int, float]:
    """Shortest trail distance from the destination's node to every node (meters)."""
    if dest.node is None:
        return {}
    return nx.single_source_dijkstra_path_length(tg.graph, dest.node, weight="length_m")
