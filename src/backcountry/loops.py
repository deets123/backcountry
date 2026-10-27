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

import math
import time
from dataclasses import dataclass, field
from heapq import heappop, heappush

import networkx as nx

from backcountry.route import Route, against_one_way, edge_weight, routing_graph, shortest_route, walk
from backcountry.scoring import Weights, overlap, penalty

MAX_SECONDS = 60.0  # search time budget; the report says when it was hit
MAX_DETOUR_M = 1609.0  # a must-visit spot can be reached by an out-and-back of at most this (one way)
WIDEN = 0.25  # if nothing fits the range, look this much wider and report the deviation
SNAPSHOT_EVALUATED = 12  # the first look day-plans only this many (the best by distance, roads, repeats)
FIRST_RESULTS_S = 8.0  # after this long, hand over the best trips found so far while the search goes on


@dataclass
class LoopGraph:
    graph: nx.MultiGraph
    start: int
    pruned: int = 0
    merged: int = 0
    reach_m: float | None = None


@dataclass
class Candidate:
    kind: str  # "loop", "lollipop", "out_and_back" or "one_way"
    start: int
    keys: list[str]  # real graph edge keys in walking order
    nodes: list[int]  # real graph nodes in walking order (len(keys) + 1)
    stem_keys: list[str]  # real edge keys of the stem, start outward ([] for a loop)
    route: Route
    detour_keys: list[str] = field(default_factory=list)  # out-and-back to a must-visit spot (M7)
    length_m: float = 0.0
    road_m: float = 0.0
    last_resort_m: float = 0.0
    repeated_m: float = 0.0
    penalty: float = 0.0
    lengths: dict = field(default_factory=dict)
    plan: object = None  # a split.DayPlan once days are planned (M5)
    features: list | None = None  # camp sites / water along the route (features.along_route)
    terrain: list | None = None  # difficulty.route_terrain intervals
    rating: dict | None = None  # a day hike's difficulty rating (whole route, day-hike scale); trips rate per day
    suggestion: dict | None = None  # planning.suggest_fix: one re-checked setting change {text, days}
    pins_m: list | None = None  # resolved pinned camps, kept so the suggestion re-splits the same way
    turnaround: int | None = None  # out-and-back: the node it turns back at
    finish: int | None = None  # one way: the node it ends at

    def overlap(self, other: "Candidate") -> float:
        return overlap(self.keys, other.keys, self.lengths | other.lengths)


# --- loop graph -----------------------------------------------------------------


def avoided_keys(tg, specs) -> set[str]:
    """Edge keys matching --avoid specs: a trail/road name (case-insensitive) or a way id (osm:way/123 or 123)."""
    keys = set()
    for spec in specs:
        text = str(spec).strip()
        way_id = int(text[8:]) if text.startswith("osm:way/") and text[8:].isdigit() else int(text) if text.isdigit() else None
        def matches(d):
            return d["way_id"] == way_id if way_id is not None else (d["name"] or "").lower() == text.lower()

        hits = {k for u, v, k, d in tg.graph.edges(keys=True, data=True) if matches(d)}
        if not hits:
            raise ValueError(f"No trail or road matches --avoid {text!r} (names are matched whole, case-insensitive)")
        keys |= hits
    return keys


def orientation(route: Route) -> str:
    """'ccw' or 'cw' by the shoelace sign of the walked line (x east, y north)."""
    pts = list(route.line_utm.coords)
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:]))
    return "ccw" if area > 0 else "cw"


def loop_graph(tg, start: int, no_roads: bool = False, exclude_keys=frozenset(), reach_m: float | None = None) -> LoopGraph:
    """`reach_m`: drop junctions farther than this by trail from the start. A loop (or lollipop) no longer than
    2 * reach_m never gets farther than reach_m from its start, so the search finds the same trips, faster."""
    G = tg.graph
    if start not in G:
        raise ValueError(f"start node {start} is not in the graph")
    H = nx.MultiGraph()
    H.add_node(start)
    for u, v, k, d in G.subgraph(nx.node_connected_component(G, start)).edges(keys=True, data=True):
        if (no_roads and d["is_road"]) or k in exclude_keys:
            continue
        # Millimetres: each platform's math library converts coordinates slightly differently (~1e-9 m), and the
        # search must break ties between equal-length paths the same way everywhere.
        length = round(d["length_m"], 3)
        H.add_edge(u, v, key=k, keys=[k], path=[u, v], length_m=length,
                   road_m=length if d["is_road"] else 0.0,
                   last_resort_m=length if d["last_resort"] else 0.0)
    lg = LoopGraph(H, start, reach_m=reach_m)

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

    # Out of reach: drop junctions too far to be on any trip, then the spurs that leaves. Done after merging, so
    # the junctions left are the same ones the whole network has (merging again would hide via points).
    if reach_m is not None:
        near = nx.single_source_dijkstra_path_length(H, start, cutoff=reach_m, weight="length_m")
        H.remove_nodes_from([n for n in list(H.nodes) if n not in near])
        changed = True
        while changed:
            changed = False
            for n in [n for n in H.nodes if n != start and H.degree(n) <= 1]:
                H.remove_node(n)
                lg.pruned += 1
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


def _adjacency(H) -> dict:
    """Each node's (neighbour, key, length) list, cheapest first, built once per loop graph (the search runs
    thousands of shortest-path searches on the same graph, and a plain list beats networkx's dict-of-dicts
    with a weight callable several times over)."""
    stamp = len(H)  # O(1); the loop graph is never changed once the search starts (number_of_edges is O(E))
    cached = H.graph.get("_search_adj")
    if cached is None or cached[0] != stamp:
        adj = {u: [] for u in H.nodes}
        for u, v, k, d in H.edges(keys=True, data=True):
            adj[u].append((v, k, d["length_m"]))
            if u != v:
                adj[v].append((u, k, d["length_m"]))
        for lst in adj.values():
            lst.sort(key=lambda t: (t[2], t[1]))
        H.graph["_search_adj"] = (stamp, adj)
    return H.graph["_search_adj"][1]


def _dijkstra(H, source, used=frozenset(), forbidden=frozenset(), cutoff=None):
    """Shortest paths from source, hiding used edge keys and forbidden nodes; with `cutoff`, only to nodes at
    most that far (a leg can't be longer). Exact; ties break by node id, the same on every platform."""
    if source in forbidden:
        return {source: 0.0}, {source: [source]}
    adj = _adjacency(H)
    limit = None if cutoff is None else cutoff + 1e-6  # equal-length ties stay in
    dist, pred, done = {source: 0.0}, {source: None}, set()
    heap = [(0.0, source)]
    while heap:
        d, u = heappop(heap)
        if u in done:
            continue
        done.add(u)
        for v, k, w in adj[u]:
            if v in forbidden or k in used:
                continue
            nd = d + w
            if limit is not None and nd > limit:
                break  # the list is sorted by length: nothing cheaper follows
            if v not in dist or nd < dist[v]:
                dist[v] = nd
                pred[v] = u
                heappush(heap, (nd, v))
    return dist, _Paths(pred)


class _Paths(dict):
    """Shortest paths as a dict node -> node list, built from predecessors only when asked for (copying a path
    on every relaxation is what made the search slow on big networks)."""

    def __init__(self, pred: dict):
        super().__init__()
        self._pred = pred

    def __contains__(self, v):
        return v in self._pred

    def __getitem__(self, v):
        if v not in self._pred:
            raise KeyError(v)
        path, at = [], v
        while at is not None:
            path.append(at)
            at = self._pred[at]
        return path[::-1]

    def get(self, v, default=None):
        return self[v] if v in self._pred else default

    def __iter__(self):
        return iter(self._pred)

    def __len__(self):
        return len(self._pred)

    def keys(self):
        return self._pred.keys()


def _steps(H, nodes, used):
    """Super-edge steps along a node path, choosing the shortest unused key between neighbours."""
    adj = _adjacency(H)  # each node's edges cheapest first: the first unused one to b is the shortest
    steps = []
    for a, b in zip(nodes, nodes[1:]):
        k = next(k for v, k, _ in adj[a] if v == b and k not in used)
        steps.append((a, b, k))
    return steps


def waypoint_loops(H, start, lo, hi, forbidden=frozenset(), deadline=None, report=None, tick=None, clock=time.time):
    """Edge-disjoint loops through `start` built from one or two via junctions, length in [lo, hi].

    `tick(loops)` is called before each via junction with the loops found so far (for early results)."""
    forbidden = set(forbidden)
    dist_s, paths_s = _dijkstra(H, start, forbidden=forbidden, cutoff=hi / 2)
    apsp = {}
    loops, seen = [], set()
    # A ring with no junction on it but the start merges into one self-loop edge: that edge is a loop by itself.
    for u, _, k, d in sorted(nx.selfloop_edges(H, keys=True, data=True), key=lambda e: e[2]):
        if u == start and lo <= d["length_m"] <= hi:
            seen.add(frozenset([k]))
            loops.append(([(start, start, k)], d["length_m"]))
    nodes = [n for n in sorted(dist_s, key=dist_s.get) if n != start and n not in forbidden and 2 * dist_s[n] <= hi]
    for i, a in enumerate(nodes):
        if tick is not None:
            tick(loops)
        if deadline and clock() > deadline:
            if report is not None:
                report["truncated"] = True
            break
        leg1 = _steps(H, paths_s[a], set())
        used1 = {k for *_, k in leg1}
        # One via: straight back from a on unused edges.
        dist_a, paths_a = _dijkstra(H, a, used=used1, forbidden=forbidden, cutoff=hi - dist_s[a])
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
                dist_b, paths_b = _dijkstra(H, back_from, used=used2, forbidden=forbidden,
                                            cutoff=hi - dist_s[a] - dist_a[b])
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
           max_seconds: float = MAX_SECONDS, evaluate=None, avoid=(), must_visit=(), direction: str | None = None,
           on_snapshot=None, first_s: float = FIRST_RESULTS_S, clock=time.time, keep=None, through: int | None = None,
           max_detour_m: float = MAX_DETOUR_M, finish: int | None = None) -> dict:
    """Find, score, and pick candidates.

    Trips are enumerated a family at a time (loops, then the cheap out-and-backs and one-ways, then lollipops,
    the slow family) and each family is scored and day-planned (`evaluate`) as soon as it is found, best first.
    Only the clock stops any of it; then the report says so, and a trip never planned in full is never offered.

    `evaluate(candidate)` may add penalty (e.g. a day plan's). `avoid`: names/way ids to exclude.
    `must_visit`: dicts {"node": n} or {"name": trail}; a node off the loop gets an out-and-back detour
    from the nearest point of the loop (at most `max_detour_m` one way). `direction`: "cw" or "ccw".
    `on_snapshot(report)`: called once, `first_s` into the search (by `clock`), with the trips found so far
    scored and picked the same way (`report["partial"]` True); not called if the search ends sooner.
    `keep(candidate)`: only candidates it accepts are picked (a destination's range and reach). `through`: the
    node out-and-backs must pass (a destination). `finish`: where one-way trips end.
    """
    t0 = clock()
    deadline = t0 + max_seconds
    excluded = avoided_keys(tg, avoid) if avoid else set()
    lg = loop_graph(tg, start, no_roads, excluded, reach_m=max_m * (1 + WIDEN) / 2)  # the widened retry fits too
    H = lg.graph
    report = {"loop_graph": {"nodes": H.number_of_nodes(), "edges": H.number_of_edges(), "pruned": lg.pruned,
                             "merged": lg.merged, "reach_m": lg.reach_m}, "loops_found": 0, "lollipops_found": 0,
              "out_and_backs_found": 0, "one_ways_found": 0, "stems_tried": 0, "via_pairs_tried": 0, "truncated": False,
              "widened": False, "avoided_edges": len(excluded), "must_visit_notes": [], "notes": [], "partial": False}
    musts = _MustVisits(tg, must_visit, no_roads, excluded, max_detour_m) if must_visit else None
    mid = (min_m + max_m) / 2
    order = lambda c: (c.penalty, abs(c.length_m - mid), c.length_m)  # noqa: E731
    evaluated: list[Candidate] = []  # scored and, with `evaluate`, day-planned: the only trips that can be offered
    totals = {"found": 0, "kept": 0, "detoured": 0}

    def prepare(cands: list[Candidate], limit: int | None = None, register: bool = True) -> list[Candidate]:
        """Must-visits, the destination's filter, direction and base penalty, then day plans best first until
        the clock (or `limit` of them, for the first look). Registered trips join `evaluated`."""
        if register:
            totals["found"] += len(cands)
        if musts is not None:
            cands = musts.apply(cands)
        if keep is not None:
            cands = [c for c in cands if keep(c)]
        if direction:  # only trips that come back to the start have a way round; one way and out and back don't
            cands = [c if c.kind not in ("loop", "lollipop") or orientation(c.route) == direction else _reversed(tg, c)
                     for c in cands]
        else:  # a loop walked against a one-way trail is walked the other way round when that helps
            cands = [_signed_way_round(tg, c) if c.kind in ("loop", "lollipop") else c for c in cands]
        if register:
            totals["kept"] += len(cands)
            totals["detoured"] += sum(1 for c in cands if c.detour_keys)
        for c in cands:
            c.penalty = penalty(c.length_m, c.road_m, c.last_resort_m, c.repeated_m, min_m, max_m, weights)
        cands.sort(key=order)
        done = []
        for c in cands:
            if limit is not None and len(done) >= limit:
                break
            if evaluate is not None:
                if limit is None and clock() > deadline:
                    report["truncated"] = True
                    break
                c.penalty += evaluate(c)
            done.append(c)
        if register:
            evaluated.extend(done)
        return done

    def pick(cands: list[Candidate]) -> list[Candidate]:
        chosen = []
        for c in sorted(cands, key=order):
            if all(c.overlap(o) < max_overlap for o in chosen):
                chosen.append(c)
            if len(chosen) == count:
                break
        return chosen

    snap = {"due": clock() + first_s, "done": on_snapshot is None}

    def ticker(make):
        """A waypoint_loops tick: once it's time, score what's found so far and hand it over."""
        def tick(loops):
            if snap["done"] or clock() < snap["due"]:
                return
            snap["done"] = True
            fresh = prepare([make(steps) for steps, _ in loops], limit=SNAPSHOT_EVALUATED, register=False)
            rep = dict(report, must_visit_notes=[], notes=list(report["notes"]), partial=True,
                       evaluated=(len(evaluated) + len(fresh)) if evaluate is not None else 0, scored=totals["kept"])
            rep["candidates"] = pick(evaluated + fresh)
            rep["seconds"] = clock() - t0
            on_snapshot(rep)
        return tick

    def collect(lo: float, hi: float) -> int:
        """Enumerate every family in [lo, hi], preparing each as it comes. Returns how many trips were found."""
        n, loops_n = 0, 0
        if "loop" in kinds:
            tick = ticker(lambda steps: _candidate(tg, H, "loop", start, steps, []))
            loops = [_candidate(tg, H, "loop", start, steps, [])
                     for steps, _ in waypoint_loops(H, start, lo, hi, deadline=deadline, report=report, tick=tick, clock=clock)]
            loops_n = len(loops)
            report["loops_found"] += loops_n
            n += loops_n
            prepare(loops)
        # The cheap shapes next (a few shortest-path runs each), so if the clock runs out it only ever cuts the
        # slow lollipop search, which comes last.
        if "out_and_back" in kinds:
            obs = out_and_backs(tg, start, lo, hi, no_roads, excluded, through=through, deadline=deadline, report=report,
                                clock=clock)
            if through is not None and must_visit:
                # a destination on a spur: a longer out-and-back can also go past the spur's junction and take
                # the spur as a side trip (must_visit adds it)
                walked = {tuple(c.keys) for c in obs}
                obs += [c for c in out_and_backs(tg, start, lo, hi, no_roads, excluded, deadline=deadline, report=report,
                                                 clock=clock) if tuple(c.keys) not in walked]
            report["out_and_backs_found"] += len(obs)
            n += len(obs)
            prepare(obs)
        if "one_way" in kinds and finish is not None:
            ows = one_ways(tg, start, finish, lo, hi, no_roads, excluded, through=through, deadline=deadline, report=report,
                           clock=clock)
            report["one_ways_found"] += len(ows)
            n += len(ows)
            prepare(ows)
        if "lollipop" in kinds and (loops_n < count or "loop" not in kinds):
            dist, paths = _dijkstra(H, start)
            stems = [v for v in sorted(dist, key=dist.get) if v != start and 2 * dist[v] < hi and H.degree(v) >= 3]
            for v in _spread_out(tg, stems):  # every junction in reach, spread out first; only the clock stops it
                if deadline and clock() > deadline:
                    report["truncated"] = True
                    break
                report["stems_tried"] += 1
                stem = _steps(H, paths[v], set())
                forbidden = set(paths[v][:-1])

                def make(steps, stem=stem):
                    return _candidate(tg, H, "lollipop", start, stem + steps + [(b, a, k) for a, b, k in stem[::-1]], stem)

                loops = waypoint_loops(H, v, lo - 2 * dist[v], hi - 2 * dist[v], forbidden, deadline, report,
                                       tick=ticker(make), clock=clock)
                report["lollipops_found"] += len(loops)
                n += len(loops)
                prepare([make(steps) for steps, _ in loops])
        return n

    found = collect(min_m, max_m)
    if not found and not report["truncated"]:
        report["widened"] = True
        found = collect(min_m * (1 - WIDEN), max_m * (1 + WIDEN))
    if not found:
        if H.degree(start) < 2 and "lollipop" not in kinds:
            report["notes"].append("With these settings the start sits on a dead-end spur, so no loop can pass through it; "
                                   "a lollipop (out-and-back stem, then a loop) could. Try --type any.")
        elif "lollipop" not in kinds:
            report["notes"].append("No loop through the start fits even a 25% wider range. Try --type any, a different "
                                   "range, or fewer --avoid/--no-roads restrictions.")
        else:
            report["notes"].append("No trip of this kind fits even a 25% wider range with these restrictions.")
    snap["done"] = True
    if musts is not None:
        report["must_visit_notes"] += musts.notes(start, totals)
    if report["truncated"]:
        report["notes"].append("The search hit its time budget, so some trips were never examined or planned in full.")
    report.update(candidates=pick(evaluated), evaluated=len(evaluated) if evaluate is not None else 0,
                  scored=totals["kept"], seconds=clock() - t0)
    return report


STEM_SPACING_M = 400.0  # a first pass over stems at least this far apart (straight line), then the rest


def _spread_out(tg, nodes: list[int]) -> list[int]:
    """`nodes` (nearest first) reordered so a first pass visits ones at least STEM_SPACING_M apart, then the rest
    in their old order: a tangle of junctions by the trailhead no longer eats the whole time budget before a
    junction up the trail gets its turn. Nothing is dropped."""
    G = tg.graph
    first, rest = [], []
    for v in nodes:
        x, y = G.nodes[v]["x"], G.nodes[v]["y"]
        if all(math.hypot(x - G.nodes[u]["x"], y - G.nodes[u]["y"]) >= STEM_SPACING_M for u in first):
            first.append(v)
        else:
            rest.append(v)
    return first + rest


def out_and_back_to(tg, start: int, node: int, no_roads: bool = False, exclude_keys=frozenset()) -> Candidate | None:
    """The shortest out and back from start to one node (one shortest path, doubled), or None if unreachable."""
    try:
        out = shortest_route(tg, start, node, no_roads, exclude_keys=exclude_keys)
    except ValueError:
        return None
    keys, path = [e["key"] for e in out.edges], list(out.nodes)
    route = walk(tg, keys + keys[::-1], path + path[::-1][1:])
    route.notes.append(f"Out and back, {out.length_m / 1609.344:.1f} mi each way.")
    lengths = {e["key"]: e["length_m"] for e in route.edges}
    return Candidate(kind="out_and_back", start=start, keys=keys + keys[::-1], nodes=path + path[::-1][1:],
                     stem_keys=list(keys), route=route, length_m=route.length_m, road_m=route.road_m,
                     last_resort_m=route.last_resort_m, repeated_m=out.length_m, lengths=lengths, turnaround=node)


# --- out-and-back ---------------------------------------------------------------------------------------



def out_and_backs(tg, start: int, lo: float, hi: float, no_roads: bool = False, exclude_keys=frozenset(),
                  through: int | None = None, deadline=None, report=None, clock=time.time) -> list[Candidate]:
    """Walk out to a turnaround and back the same way, length in [lo, hi]. Turnarounds are trail ends and
    junctions (from the full graph: spurs count here). With `through`, only turnarounds whose way out passes
    `through`, or `through` itself. The way out is the preferred route (roads cost more), as for a stem."""
    R = routing_graph(tg, no_roads, exclude_keys=exclude_keys)
    if start not in R:
        return []
    G = tg.graph
    _, paths = nx.single_source_dijkstra(R, start, weight="w")
    found = []
    for x, path in paths.items():
        if x == start or not (G.degree(x) == 1 or G.degree(x) >= 3) and x != through:
            continue
        if through is not None and through not in path:
            continue
        keys = [R[a][b]["key"] for a, b in zip(path, path[1:])]
        one_way = sum(G[a][b][k]["length_m"] for (a, b), k in zip(zip(path, path[1:]), keys))
        if lo <= 2 * one_way <= hi:
            found.append((abs(2 * one_way - (lo + hi) / 2), x, path, keys, one_way))
    found.sort(key=lambda f: (f[0], f[1]))
    out = []
    for _, x, path, keys, one_way in found:  # closest to the range's middle first; only the clock stops it
        if deadline and clock() > deadline:
            if report is not None:
                report["truncated"] = True
            break
        route = walk(tg, keys + keys[::-1], path + path[::-1][1:])
        route.notes.append(f"Out and back, {one_way / 1609.344:.1f} mi each way.")
        lengths = {e["key"]: e["length_m"] for e in route.edges}
        out.append(Candidate(kind="out_and_back", start=start, keys=keys + keys[::-1], nodes=path + path[::-1][1:],
                             stem_keys=list(keys), route=route, length_m=route.length_m, road_m=route.road_m,
                             last_resort_m=route.last_resort_m, repeated_m=one_way, lengths=lengths, turnaround=x))
    return out


# --- one way --------------------------------------------------------------------------------------------

LAST_RESORT_CAP = 10.0  # routing costs can be up to this times the distance (last-resort roads)


def _path_keys(R, path):
    return [R[a][b]["key"] for a, b in zip(path, path[1:])]


def _one_way_candidate(tg, start, finish, keys, nodes) -> Candidate:
    route = walk(tg, keys, nodes)
    G = tg.graph
    straight = math.hypot(G.nodes[start]["x"] - G.nodes[finish]["x"], G.nodes[start]["y"] - G.nodes[finish]["y"])
    route.notes.append(f"One way: start and finish are {straight / 1609.344:.1f} mi apart in a straight line. "
                       "Shuttle drive: unknown.")
    lengths = {e["key"]: e["length_m"] for e in route.edges}
    seen, twice = set(), []
    for k in keys:
        if k in seen and k not in twice:
            twice.append(k)
        seen.add(k)
    return Candidate(kind="one_way", start=start, keys=keys, nodes=nodes, stem_keys=[], route=route,
                     detour_keys=twice, length_m=route.length_m, road_m=route.road_m, last_resort_m=route.last_resort_m,
                     repeated_m=sum(lengths[k] for k in twice), lengths=lengths, finish=finish)


def one_ways(tg, start: int, finish: int, lo: float, hi: float, no_roads: bool = False, exclude_keys=frozenset(),
             through: int | None = None, deadline=None, report=None, clock=time.time) -> list[Candidate]:
    """From start to finish, length in [lo, hi]: the preferred route, the next best way round each stretch of it,
    and other ways through one junction (the second leg avoids the first leg's trails). With `through`:
    start -> through -> finish; a `through` on a spur is walked out and back (those edges are the candidate's
    detour_keys)."""
    R = routing_graph(tg, no_roads, exclude_keys=exclude_keys)
    if start not in R or finish not in R:
        return []
    G = tg.graph

    def real(path):
        return sum(G[a][b][R[a][b]["key"]]["length_m"] for a, b in zip(path, path[1:]))

    try:
        if through is not None:
            p1 = nx.shortest_path(R, start, through, weight="w")
            p2 = nx.shortest_path(R, through, finish, weight="w")
            nodes, keys = p1 + p2[1:], _path_keys(R, p1) + _path_keys(R, p2)
            c = _one_way_candidate(tg, start, finish, keys, nodes)
            return [c] if lo <= c.length_m <= hi else []
        best = nx.shortest_path(R, start, finish, weight="w")
    except nx.NetworkXNoPath:
        return []
    out, seen = [], set()
    if lo <= real(best) <= hi:
        out.append(_one_way_candidate(tg, start, finish, _path_keys(R, best), best))
    seen.add(tuple(_path_keys(R, best)))

    def alt_edge(a, b):
        """Another trail joining the same two junctions (the routing graph keeps only the lightest), or None."""
        others = [(edge_weight(d), k) for k, d in G[a][b].items() if k != R[a][b]["key"] and k not in exclude_keys
                  and not (no_roads and d["is_road"])]
        return min(others) if others else None

    def path_keys(path, swapped):
        return [swapped[k] if (k := R[x][y]["key"]) in swapped else k for x, y in zip(path, path[1:])]

    def length(path, swapped):
        return sum(G[x][y][k]["length_m"] for (x, y), k in zip(zip(path, path[1:]), path_keys(path, swapped)))

    # Other ways round: the next best way with one stretch of the best route blocked (a parallel trail takes a
    # blocked one's place), every stretch in turn. No graph copies: the block is a weight.
    for a, b in zip(best, best[1:]):
        if deadline and clock() > deadline:
            if report is not None:
                report["truncated"] = True
            return out
        alt = alt_edge(a, b)
        swapped = {R[a][b]["key"]: alt[1]} if alt else {}

        def blocked(u, v, d, a=a, b=b, alt=alt):
            return (alt[0] if alt else None) if {u, v} == {a, b} else d["w"]
        try:
            path = nx.shortest_path(R, start, finish, weight=blocked)
        except nx.NetworkXNoPath:
            continue
        keys = path_keys(path, swapped)
        if tuple(keys) in seen:
            continue
        seen.add(tuple(keys))
        if lo <= length(path, swapped) <= hi:
            out.append(_one_way_candidate(tg, start, finish, keys, path))
    # ...and ways through one other junction: out to it, then to the finish avoiding the first leg's trails.
    dist_s, paths_s = nx.single_source_dijkstra(R, start, weight="w")
    dist_f = nx.single_source_dijkstra_path_length(R, finish, weight="w")
    mid = (lo + hi) / 2
    vias = sorted((a for a in dist_s if a not in (start, finish) and a in dist_f and G.degree(a) >= 3
                   and dist_s[a] + dist_f[a] <= hi * LAST_RESORT_CAP), key=lambda a: (abs(dist_s[a] + dist_f[a] - mid), a))
    for a in vias:  # closest to the range's middle first; only the clock stops it
        if deadline and clock() > deadline:
            if report is not None:
                report["truncated"] = True
            break
        leg1 = paths_s[a]
        first = {frozenset(e) for e in zip(leg1, leg1[1:])}

        def avoiding(u, v, d, first=first):
            return None if frozenset((u, v)) in first else d["w"]
        try:
            leg2 = nx.shortest_path(R, a, finish, weight=avoiding)
        except nx.NetworkXNoPath:
            continue
        nodes = leg1 + leg2[1:]
        keys = path_keys(nodes, {})
        if tuple(keys) in seen or len(set(nodes)) != len(nodes):  # no repeats: a walk that crosses itself isn't one
            continue
        seen.add(tuple(keys))
        if lo <= length(nodes, {}) <= hi:
            out.append(_one_way_candidate(tg, start, finish, keys, nodes))
    return out


def _candidate(tg, H, kind, start, super_steps, stem_steps) -> Candidate:
    keys, nodes = _expand(H, super_steps)
    stem_keys, _ = _expand(H, stem_steps) if stem_steps else ([], [])
    route = walk(tg, keys, nodes)
    lengths = {e["key"]: e["length_m"] for e in route.edges}
    return Candidate(kind=kind, start=start, keys=keys, nodes=nodes, stem_keys=stem_keys, route=route,
                     length_m=route.length_m, road_m=route.road_m, last_resort_m=route.last_resort_m,
                     repeated_m=sum(lengths[k] for k in stem_keys), lengths=lengths)


def _signed_way_round(tg, c: Candidate) -> Candidate:
    """The same loop, turned round if that walks fewer one-way trails against their sign."""
    wrong = len(against_one_way(c.route))
    if not wrong:
        return c
    other = _reversed(tg, c)
    return other if len(against_one_way(other.route)) < wrong else c


def _reversed(tg, c: Candidate) -> Candidate:
    """The same walk in the opposite direction."""
    route = walk(tg, c.keys[::-1], c.nodes[::-1])
    return Candidate(kind=c.kind, start=c.start, keys=c.keys[::-1], nodes=c.nodes[::-1], stem_keys=c.stem_keys,
                     route=route, detour_keys=c.detour_keys, length_m=c.length_m, road_m=c.road_m,
                     last_resort_m=c.last_resort_m, repeated_m=c.repeated_m, lengths=c.lengths,
                     turnaround=c.turnaround, finish=c.finish)


class _MustVisits:
    """The must-visit spots, with the paths to them worked out once; `apply` keeps the candidates that pass
    every spot, adding an out-and-back detour where needed."""

    def __init__(self, tg, must_visit, no_roads, excluded, max_detour_m=MAX_DETOUR_M):
        self.tg, self.specs, self.max_detour_m = tg, list(must_visit), max_detour_m
        self.R = routing_graph(tg, no_roads, exclude_keys=excluded)
        self.reach = {}  # node -> (dist from v to every node, paths)
        for m in self.specs:
            if "node" in m and m["node"] in self.R:
                self.reach[m["node"]] = nx.single_source_dijkstra(self.R, m["node"], weight="w")

    def apply(self, found) -> list[Candidate]:
        kept = []
        for c in found:
            ok = True
            for m in self.specs:
                if "name" in m:
                    if not any((e["name"] or "").lower() == m["name"].lower() for e in c.route.edges):
                        ok = False
                        break
                    continue
                v = m["node"]
                if v in c.route.nodes:
                    continue
                if v not in self.reach or c.start not in self.reach[v][0]:
                    ok = False
                    break
                dist, paths = self.reach[v]
                on_route = [n for n in set(c.route.nodes) if n in dist]
                if not on_route:
                    ok = False
                    break
                j = min(on_route, key=dist.get)
                if dist[j] > self.max_detour_m:
                    ok = False
                    break
                c = _with_detour(self.tg, c, j, paths[j][::-1], self.R)  # path from j out to v
            if ok:
                kept.append(c)
        return kept

    def notes(self, start: int, totals: dict) -> list[str]:
        out = []
        for m in self.specs:
            if m.get("internal"):
                continue
            label = m.get("name") or f"node {m['node']}"
            if "node" in m and (m["node"] not in self.reach or start not in self.reach[m["node"]][0]):
                out.append(f"must-visit {label}: not reachable from the start (not in the graph, in another piece of "
                           "the network, or cut off by --avoid/--no-roads)")
            out.append(f"must-visit {label}: {totals['kept']} of {totals['found']} loops can include it"
                       f" ({totals['detoured']} with an out-and-back detour)")
        return out


def _with_detour(tg, c: Candidate, j: int, path_nodes: list[int], R) -> Candidate:
    """Insert an out-and-back along path_nodes (j ... v) at the first visit of j."""
    keys_out = [R[a][b]["key"] for a, b in zip(path_nodes, path_nodes[1:])]
    i = c.nodes.index(j)
    keys = c.keys[:i] + keys_out + keys_out[::-1] + c.keys[i:]
    nodes = c.nodes[:i + 1] + path_nodes[1:] + path_nodes[::-1][1:] + c.nodes[i + 1:]
    route = walk(tg, keys, nodes)
    lengths = {e["key"]: e["length_m"] for e in route.edges}
    detour_m = sum(lengths[k] for k in keys_out)
    return Candidate(kind=c.kind, start=c.start, keys=keys, nodes=nodes, stem_keys=c.stem_keys, route=route,
                     detour_keys=c.detour_keys + keys_out, length_m=route.length_m, road_m=route.road_m,
                     last_resort_m=route.last_resort_m, repeated_m=c.repeated_m + detour_m, lengths=lengths,
                     turnaround=c.turnaround, finish=c.finish)


def find_candidates(tg, start: int, min_m: float, max_m: float, **kw) -> list[Candidate]:
    return search(tg, start, min_m, max_m, **kw)["candidates"]
