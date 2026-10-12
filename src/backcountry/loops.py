"""Candidate loop generation (M4).

The search runs on a reduced "loop graph": the start's connected component
with dead-end spurs removed and pass-through nodes merged into super-edges.
Every super-edge remembers the real graph edges it covers, in order, so each
candidate maps back to OSM edges walked end to end. Nothing is bridged.

Loops are built from waypoints, the way a hiker plans them: for one or two
"via" junctions (a, b), take the shortest path out to a, the shortest path
a -> b that avoids the edges already used, and the shortest path back that
avoids both. No edge is walked twice. Enumerating every simple cycle was
tried first and is hopeless here: Dolly Sods has 45,000 cycles under 12 mi
and the count explodes past that (see PROGRESS.md, M4).

Two shapes:
  loop:     waypoint loop through the start node
  lollipop: the shortest path from the start to a junction v (the stem), a
            waypoint loop through v that avoids the stem, then the stem back.
            Only stem edges are walked twice.
"""

import time
from dataclasses import dataclass, field

import networkx as nx

from backcountry.route import Route, walk
from backcountry.scoring import Weights, overlap, penalty

MAX_SECONDS = 90.0  # search time budget; the report says when it was hit
MAX_STEMS = 8  # nearest junctions tried as lollipop stems
WIDEN = 0.25  # if nothing fits the range, look this much wider and report the deviation


@dataclass
class LoopGraph:
    graph: nx.MultiGraph
    start: int
    pruned: int = 0
    merged: int = 0


@dataclass
class Candidate:
    kind: str  # "loop" or "lollipop"
    start: int
    keys: list[str]  # real graph edge keys in walking order
    nodes: list[int]  # real graph nodes in walking order (len(keys) + 1)
    stem_keys: list[str]  # real edge keys of the stem, start outward ([] for a loop)
    route: Route
    length_m: float = 0.0
    road_m: float = 0.0
    last_resort_m: float = 0.0
    repeated_m: float = 0.0
    penalty: float = 0.0
    lengths: dict = field(default_factory=dict)
    plan: object = None  # a split.DayPlan once days are planned (M5)
    features: list | None = None  # camp sites / water along the route (features.along_route)

    def overlap(self, other: "Candidate") -> float:
        return overlap(self.keys, other.keys, self.lengths | other.lengths)


# --- loop graph -----------------------------------------------------------------


def loop_graph(tg, start: int, no_roads: bool = False) -> LoopGraph:
    G = tg.graph
    if start not in G:
        raise ValueError(f"start node {start} is not in the graph")
    H = nx.MultiGraph()
    H.add_node(start)
    for u, v, k, d in G.subgraph(nx.node_connected_component(G, start)).edges(keys=True, data=True):
        if no_roads and d["is_road"]:
            continue
        H.add_edge(u, v, key=k, keys=[k], path=[u, v], length_m=d["length_m"],
                   road_m=d["length_m"] if d["is_road"] else 0.0,
                   last_resort_m=d["length_m"] if d["last_resort"] else 0.0)
    lg = LoopGraph(H, start)

    # Dead-end spurs can't be part of a cycle (the start is kept: it may need a stem).
    changed = True
    while changed:
        changed = False
        for n in [n for n in H.nodes if n != start and H.degree(n) <= 1]:
            H.remove_node(n)
            lg.pruned += 1
            changed = True

    # Pass-through nodes: merge their two edges into one super-edge.
    changed = True
    while changed:
        changed = False
        for n in [n for n in H.nodes if n != start and H.degree(n) == 2]:
            inc = list(H.edges(n, keys=True, data=True))
            if len(inc) != 2:  # a self-loop also gives degree 2; leave those alone
                continue
            (_, a, ka, da), (_, b, kb, db) = inc
            pa, keys_a = _oriented(da, a)  # a -> n
            pb, keys_b = _oriented(db, n)  # n -> b
            H.remove_node(n)
            H.add_edge(a, b, key=f"{ka}+{kb}", keys=keys_a + keys_b, path=pa + pb[1:],
                       length_m=da["length_m"] + db["length_m"], road_m=da["road_m"] + db["road_m"],
                       last_resort_m=da["last_resort_m"] + db["last_resort_m"])
            lg.merged += 1
            changed = True
    return lg


def _oriented(d: dict, first: int) -> tuple[list[int], list[str]]:
    """A super-edge's node path and keys, oriented to begin at `first`."""
    path, keys = d["path"], d["keys"]
    return (path, keys) if path[0] == first else (path[::-1], keys[::-1])


# --- waypoint loops -------------------------------------------------------------------


def _weight(used: set, forbidden: set):
    """Dijkstra weight that hides used edge keys and forbidden nodes (None = edge absent)."""
    def w(u, v, keyed):
        if v in forbidden or u in forbidden:
            return None
        best = [d["length_m"] for k, d in keyed.items() if k not in used]
        return min(best) if best else None
    return w


def _dijkstra(H, source, used=frozenset(), forbidden=frozenset()):
    return nx.single_source_dijkstra(H, source, weight=_weight(set(used), set(forbidden)))


def _steps(H, nodes, used):
    """Super-edge steps along a node path, choosing the shortest unused key between neighbours."""
    steps = []
    for a, b in zip(nodes, nodes[1:]):
        k = min((k for k in H[a][b] if k not in used), key=lambda k: H[a][b][k]["length_m"])
        steps.append((a, b, k))
    return steps


def waypoint_loops(H, start, lo, hi, forbidden=frozenset(), deadline=None, report=None):
    """Edge-disjoint loops through `start` built from one or two via junctions, length in [lo, hi]."""
    forbidden = set(forbidden)
    dist_s, paths_s = _dijkstra(H, start, forbidden=forbidden)
    apsp = {}
    loops, seen = [], set()
    nodes = [n for n in sorted(dist_s, key=dist_s.get) if n != start and n not in forbidden and 2 * dist_s[n] <= hi]
    for i, a in enumerate(nodes):
        if deadline and time.time() > deadline:
            if report is not None:
                report["truncated"] = True
            break
        leg1 = _steps(H, paths_s[a], set())
        used1 = {k for *_, k in leg1}
        # One via: straight back from a on unused edges.
        dist_a, paths_a = _dijkstra(H, a, used=used1, forbidden=forbidden)
        candidates_b = [None] + [b for b in nodes[i + 1:] if b in dist_a and dist_s[a] + dist_a[b] + dist_s[b] <= hi]
        for b in candidates_b:
            if report is not None:
                report["via_pairs_tried"] += 1
            if b is None:
                if start not in dist_a:
                    continue
                leg2, back_from = [], a
                used2 = used1
                back_nodes = paths_a[start]
            else:
                leg2 = _steps(H, paths_a[b], used1)
                used2 = used1 | {k for *_, k in leg2}
                back_from = b
                back_nodes = None
            # Leg back to start on unused edges; the start may not be passed through mid-leg.
            if back_nodes is None:
                dist_b, paths_b = _dijkstra(H, back_from, used=used2, forbidden=forbidden)
                if start not in dist_b:
                    continue
                back_nodes = paths_b[start]
            leg3 = _steps(H, back_nodes, used2)
            steps = leg1 + leg2 + leg3
            length = sum(H[u][v][k]["length_m"] for u, v, k in steps)
            if not lo <= length <= hi:
                continue
            sig = frozenset(k for *_, k in steps)
            if sig in seen:
                continue
            seen.add(sig)
            loops.append((steps, length))
    return loops


def _expand(H: nx.MultiGraph, super_steps) -> tuple[list[str], list[int]]:
    """Real edge keys and node path for super-edge steps walked in order."""
    keys, nodes = [], []
    for u, v, k in super_steps:
        path, ks = _oriented(H[u][v][k], u)
        keys += ks
        nodes += path if not nodes else path[1:]
    return keys, nodes


# --- search --------------------------------------------------------------------------


def search(tg, start: int, min_m: float, max_m: float, kinds=("loop", "lollipop"), count: int = 3,
           no_roads: bool = False, weights: Weights = Weights(), max_overlap: float = 0.5,
           max_seconds: float = MAX_SECONDS, evaluate=None) -> dict:
    """Find, score, and pick candidates. `evaluate(candidate)` may add penalty (e.g. a day plan's)."""
    t0 = time.time()
    deadline = t0 + max_seconds
    lg = loop_graph(tg, start, no_roads)
    H = lg.graph
    report = {"loop_graph": {"nodes": H.number_of_nodes(), "edges": H.number_of_edges(), "pruned": lg.pruned,
                             "merged": lg.merged}, "loops_found": 0, "lollipops_found": 0, "stems_tried": 0,
              "via_pairs_tried": 0, "truncated": False, "widened": False}

    def collect(lo: float, hi: float) -> list[Candidate]:
        found = []
        if "loop" in kinds:
            for steps, _ in waypoint_loops(H, start, lo, hi, deadline=deadline, report=report):
                found.append(_candidate(tg, H, "loop", start, steps, []))
            report["loops_found"] += len(found)
        if "lollipop" in kinds and (len(found) < count or "loop" not in kinds):
            dist, paths = _dijkstra(H, start)
            stems = [v for v in sorted(dist, key=dist.get) if v != start and 2 * dist[v] < hi and H.degree(v) >= 3]
            for v in stems[:MAX_STEMS]:
                report["stems_tried"] += 1
                stem = _steps(H, paths[v], set())
                forbidden = set(paths[v][:-1])
                loops = waypoint_loops(H, v, lo - 2 * dist[v], hi - 2 * dist[v], forbidden, deadline, report)
                report["lollipops_found"] += len(loops)
                for steps, _ in loops:
                    found.append(_candidate(tg, H, "lollipop", start,
                                            stem + steps + [(b, a, k) for a, b, k in stem[::-1]], stem))
        return found

    found = collect(min_m, max_m)
    if not found and not report["truncated"]:
        report["widened"] = True
        found = collect(min_m * (1 - WIDEN), max_m * (1 + WIDEN))
    for c in found:
        c.penalty = penalty(c.length_m, c.road_m, c.last_resort_m, c.repeated_m, min_m, max_m, weights)
        if evaluate is not None:
            c.penalty += evaluate(c)
    mid = (min_m + max_m) / 2
    found.sort(key=lambda c: (c.penalty, abs(c.length_m - mid), c.length_m))

    chosen = []
    for c in found:
        if all(c.overlap(o) < max_overlap for o in chosen):
            chosen.append(c)
        if len(chosen) == count:
            break
    report.update(candidates=chosen, scored=len(found), seconds=time.time() - t0)
    return report


def _candidate(tg, H, kind, start, super_steps, stem_steps) -> Candidate:
    keys, nodes = _expand(H, super_steps)
    stem_keys, _ = _expand(H, stem_steps) if stem_steps else ([], [])
    route = walk(tg, keys, nodes)
    lengths = {e["key"]: e["length_m"] for e in route.edges}
    return Candidate(kind=kind, start=start, keys=keys, nodes=nodes, stem_keys=stem_keys, route=route,
                     length_m=route.length_m, road_m=route.road_m, last_resort_m=route.last_resort_m,
                     repeated_m=sum(lengths[k] for k in stem_keys), lengths=lengths)


def find_candidates(tg, start: int, min_m: float, max_m: float, **kw) -> list[Candidate]:
    return search(tg, start, min_m, max_m, **kw)["candidates"]
