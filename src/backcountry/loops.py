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

from backcountry.route import Route, routing_graph, walk
from backcountry.scoring import Weights, overlap, penalty

MAX_SECONDS = 60.0  # search time budget; the report says when it was hit
MAX_DETOUR_M = 1609.0  # a must-visit spot can be reached by an out-and-back of at most this (one way)
MAX_STEMS = 8  # nearest junctions tried as lollipop stems
WIDEN = 0.25  # if nothing fits the range, look this much wider and report the deviation
MAX_EVALUATED = 40  # only the best this many (by distance, roads, repeats) get day plans: they're the slow part
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
    kind: str  # "loop", "lollipop" or "out_and_back"
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


def _dijkstra(H, source, used=frozenset(), forbidden=frozenset(), cutoff=None):
    """Shortest paths from source; with `cutoff`, only to nodes at most that far (a leg can't be longer)."""
    cutoff = None if cutoff is None else cutoff + 1e-6  # equal-length ties stay in
    return nx.single_source_dijkstra(H, source, cutoff=cutoff, weight=_weight(set(used), set(forbidden)))


def _steps(H, nodes, used):
    """Super-edge steps along a node path, choosing the shortest unused key between neighbours."""
    steps = []
    for a, b in zip(nodes, nodes[1:]):
        k = min((k for k in H[a][b] if k not in used), key=lambda k: H[a][b][k]["length_m"])
        steps.append((a, b, k))
    return steps


def waypoint_loops(H, start, lo, hi, forbidden=frozenset(), deadline=None, report=None, tick=None):
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
        if deadline and time.time() > deadline:
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
           on_snapshot=None, first_s: float = FIRST_RESULTS_S, clock=time.time) -> dict:
    """Find, score, and pick candidates.

    `evaluate(candidate)` may add penalty (e.g. a day plan's). `avoid`: names/way ids to exclude.
    `must_visit`: dicts {"node": n} or {"name": trail}; a node off the loop gets an out-and-back detour
    from the nearest point of the loop (at most MAX_DETOUR_M one way). `direction`: "cw" or "ccw".
    `on_snapshot(report)`: called once, `first_s` into the search (by `clock`), with the trips found so far
    scored and picked the same way (`report["partial"]` True); not called if the search ends sooner.
    """
    t0 = time.time()
    deadline = t0 + max_seconds
    excluded = avoided_keys(tg, avoid) if avoid else set()
    lg = loop_graph(tg, start, no_roads, excluded, reach_m=max_m * (1 + WIDEN) / 2)  # the widened retry fits too
    H = lg.graph
    report = {"loop_graph": {"nodes": H.number_of_nodes(), "edges": H.number_of_edges(), "pruned": lg.pruned,
                             "merged": lg.merged, "reach_m": lg.reach_m}, "loops_found": 0, "lollipops_found": 0,
              "out_and_backs_found": 0, "stems_tried": 0, "via_pairs_tried": 0, "truncated": False, "widened": False,
              "avoided_edges": len(excluded), "must_visit_notes": [], "notes": [], "partial": False}

    def choose(found: list[Candidate], rep: dict) -> list[Candidate]:
        if must_visit:
            found = _apply_must_visit(tg, found, must_visit, no_roads, excluded, rep)
        if direction:
            found = [c if orientation(c.route) == direction else _reversed(tg, c) for c in found]
        scored = len(found)
        mid = (min_m + max_m) / 2
        order = lambda c: (c.penalty, abs(c.length_m - mid), c.length_m)  # noqa: E731
        for c in found:
            c.penalty = penalty(c.length_m, c.road_m, c.last_resort_m, c.repeated_m, min_m, max_m, weights)
        if evaluate is not None:
            found.sort(key=order)
            # the best few dozen, plus enough different-looking ones that the final pick still has variety
            pool, spread = found[:MAX_EVALUATED], []
            for c in found:
                if len(spread) == 3 * count:
                    break
                if all(c.overlap(o) < max_overlap for o in spread):
                    spread.append(c)
            found = pool + [c for c in spread if c not in pool]
            for c in found:
                c.penalty += evaluate(c)
        rep["evaluated"] = len(found) if evaluate is not None else 0
        found.sort(key=order)
        chosen = []
        for c in found:
            if all(c.overlap(o) < max_overlap for o in chosen):
                chosen.append(c)
            if len(chosen) == count:
                break
        rep["scored"] = scored
        return chosen

    snap = {"due": clock() + first_s, "done": on_snapshot is None}
    so_far: list[Candidate] = []  # candidates from finished waypoint searches

    def ticker(make):
        """A waypoint_loops tick: once it's time, score what's found so far and hand it over."""
        def tick(loops):
            if snap["done"] or clock() < snap["due"]:
                return
            snap["done"] = True
            rep = dict(report, must_visit_notes=[], notes=list(report["notes"]), partial=True)
            rep["candidates"] = choose(list(so_far) + [make(steps) for steps, _ in loops], rep)
            rep["seconds"] = time.time() - t0
            on_snapshot(rep)
        return tick

    def collect(lo: float, hi: float) -> list[Candidate]:
        found = []
        if "loop" in kinds:
            tick = ticker(lambda steps: _candidate(tg, H, "loop", start, steps, []))
            for steps, _ in waypoint_loops(H, start, lo, hi, deadline=deadline, report=report, tick=tick):
                found.append(_candidate(tg, H, "loop", start, steps, []))
            so_far.extend(found)
            report["loops_found"] += len(found)
        if "lollipop" in kinds and (len(found) < count or "loop" not in kinds):
            dist, paths = _dijkstra(H, start)
            stems = [v for v in sorted(dist, key=dist.get) if v != start and 2 * dist[v] < hi and H.degree(v) >= 3]
            for v in stems[:MAX_STEMS]:
                report["stems_tried"] += 1
                stem = _steps(H, paths[v], set())
                forbidden = set(paths[v][:-1])

                def make(steps, stem=stem):
                    return _candidate(tg, H, "lollipop", start, stem + steps + [(b, a, k) for a, b, k in stem[::-1]], stem)

                loops = waypoint_loops(H, v, lo - 2 * dist[v], hi - 2 * dist[v], forbidden, deadline, report,
                                       tick=ticker(make))
                report["lollipops_found"] += len(loops)
                for steps, _ in loops:
                    c = make(steps)
                    found.append(c)
                    so_far.append(c)
        if "out_and_back" in kinds:
            obs = out_and_backs(tg, start, lo, hi, no_roads, excluded, deadline=deadline, report=report)
            report["out_and_backs_found"] += len(obs)
            found += obs
            so_far.extend(obs)
        return found

    found = collect(min_m, max_m)
    if not found and not report["truncated"]:
        report["widened"] = True
        so_far.clear()
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
    chosen = choose(found, report)
    report.update(candidates=chosen, seconds=time.time() - t0)
    return report


# --- out-and-back ---------------------------------------------------------------------------------------

MAX_OUT_AND_BACKS = 200  # turnarounds made into candidates, closest to the range's middle first


def out_and_backs(tg, start: int, lo: float, hi: float, no_roads: bool = False, exclude_keys=frozenset(),
                  through: int | None = None, deadline=None, report=None) -> list[Candidate]:
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
    for _, x, path, keys, one_way in found[:MAX_OUT_AND_BACKS]:
        if deadline and time.time() > deadline:
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


def _candidate(tg, H, kind, start, super_steps, stem_steps) -> Candidate:
    keys, nodes = _expand(H, super_steps)
    stem_keys, _ = _expand(H, stem_steps) if stem_steps else ([], [])
    route = walk(tg, keys, nodes)
    lengths = {e["key"]: e["length_m"] for e in route.edges}
    return Candidate(kind=kind, start=start, keys=keys, nodes=nodes, stem_keys=stem_keys, route=route,
                     length_m=route.length_m, road_m=route.road_m, last_resort_m=route.last_resort_m,
                     repeated_m=sum(lengths[k] for k in stem_keys), lengths=lengths)


def _reversed(tg, c: Candidate) -> Candidate:
    """The same walk in the opposite direction."""
    route = walk(tg, c.keys[::-1], c.nodes[::-1])
    return Candidate(kind=c.kind, start=c.start, keys=c.keys[::-1], nodes=c.nodes[::-1], stem_keys=c.stem_keys,
                     route=route, detour_keys=c.detour_keys, length_m=c.length_m, road_m=c.road_m,
                     last_resort_m=c.last_resort_m, repeated_m=c.repeated_m, lengths=c.lengths)


def _apply_must_visit(tg, found, must_visit, no_roads, excluded, report) -> list[Candidate]:
    """Keep candidates that pass every must-visit spot, adding an out-and-back detour where needed."""
    R = routing_graph(tg, no_roads, exclude_keys=excluded)
    reach = {}  # node -> (dist from v to every node, paths)
    for m in must_visit:
        if "node" in m and m["node"] in R:
            reach[m["node"]] = nx.single_source_dijkstra(R, m["node"], weight="w")
    kept = []
    for c in found:
        ok = True
        for m in must_visit:
            if "name" in m:
                if not any((e["name"] or "").lower() == m["name"].lower() for e in c.route.edges):
                    ok = False
                    break
                continue
            v = m["node"]
            if v in c.route.nodes:
                continue
            if v not in reach or c.start not in reach[v][0]:
                ok = False
                break
            dist, paths = reach[v]
            on_route = [n for n in set(c.route.nodes) if n in dist]
            if not on_route:
                ok = False
                break
            j = min(on_route, key=dist.get)
            if dist[j] > MAX_DETOUR_M:
                ok = False
                break
            c = _with_detour(tg, c, j, paths[j][::-1], R)  # path from j out to v
        if ok:
            kept.append(c)
    start = found[0].start if found else None
    for m in must_visit:
        label = m.get("name") or f"node {m['node']}"
        if "node" in m and (m["node"] not in reach or (start is not None and start not in reach[m["node"]][0])):
            report["must_visit_notes"].append(f"must-visit {label}: not reachable from the start (not in the graph, "
                                              "in another piece of the network, or cut off by --avoid/--no-roads)")
        n_ok = sum(1 for c in kept)
        report["must_visit_notes"].append(f"must-visit {label}: {n_ok} of {len(found)} loops can include it"
                                          + f" ({sum(1 for c in kept if c.detour_keys)} with an out-and-back detour)")
    return kept


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
                     last_resort_m=route.last_resort_m, repeated_m=c.repeated_m + detour_m, lengths=lengths)


def find_candidates(tg, start: int, min_m: float, max_m: float, **kw) -> list[Candidate]:
    return search(tg, start, min_m, max_m, **kw)["candidates"]
