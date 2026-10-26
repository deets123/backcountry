"""Follow a saved route on newer trail data, edge by edge, on the same OSM ways. Never routes around a gap.

A saved candidate keeps the OSM nodes and graph edge keys it walked (`route`). On a newer cut, each step from
node u to node v along way W is found again using only W's edges, which also covers W being split by a new
junction. If a node or the way is gone, or W no longer joins u and v, the way is reported missing and there is
no route (CLAUDE.md rule 1: report the gap, never draw a connecting line).
"""

import networkx as nx

from backcountry.route import walk


def _way_of(key: str) -> int:
    return int(key.split(":")[0])


def retrace(tg, saved_candidate: dict) -> dict:
    """{"ok": True, "route", "keys", "nodes"} or {"ok": False, "missing": [{"source", "name"}]}."""
    rec = saved_candidate.get("route")
    if not rec or not rec.get("nodes") or len(rec.get("keys") or []) != len(rec["nodes"]) - 1:
        return {"ok": False, "missing": [], "reason": "saved before routes were recorded"}
    names = {src: seg["label"] for seg in saved_candidate.get("segments") or [] for src in seg.get("sources", [])}
    G = tg.graph
    by_way: dict[int, nx.Graph] = {}
    missing, keys, nodes = [], [], [rec["nodes"][0]]

    def way_graph(w: int) -> nx.Graph:
        if w not in by_way:
            H = nx.Graph()
            for u, v, k, d in G.edges(keys=True, data=True):
                if d["way_id"] == w and (not H.has_edge(u, v) or d["length_m"] < H[u][v]["length_m"]):
                    H.add_edge(u, v, key=k, length_m=d["length_m"])
            by_way[w] = H
        return by_way[w]

    for (u, v), k in zip(zip(rec["nodes"], rec["nodes"][1:]), rec["keys"]):
        w = _way_of(k)
        if G.has_edge(u, v, k) and G[u][v][k]["way_id"] == w:
            keys.append(k)
            nodes.append(v)
            continue
        H = way_graph(w)
        try:
            path = nx.shortest_path(H, u, v, weight="length_m") if u in H and v in H else None
        except nx.NetworkXNoPath:
            path = None
        if path is None or len(path) < 2:
            src = f"osm:way/{w}"
            if src not in [m["source"] for m in missing]:
                missing.append({"source": src, "name": names.get(src)})
            continue
        for a, b in zip(path, path[1:]):
            keys.append(H[a][b]["key"])
            nodes.append(b)
    if missing:
        return {"ok": False, "missing": missing}
    return {"ok": True, "route": walk(tg, keys, nodes), "keys": keys, "nodes": nodes}


# --- what changed, in plain words (no raw OSM ids in user text) ------------------------------------------

M_PER_MI = 1609.344
WATER_KINDS = ("spring", "drinking_water", "water", "stream crossing", "river crossing")


def _mi(m) -> str:
    return f"{m / M_PER_MI:.1f} mi"


def _ft(v) -> str:
    return "unknown" if v is None else f"{round(v):,} ft"


def _lvl(v) -> str:
    return (v or "unknown").capitalize()


def _day_level(d: dict):
    return (d.get("rating") or {}).get("level")


def _labels(c: dict) -> dict:
    return {src: seg["label"] for seg in c.get("segments") or [] for src in seg.get("sources", [])}


def _water(c: dict) -> dict:
    return {f["source"]: f.get("name") or f["kind"].replace("_", " ") for f in c.get("features") or [] if f["kind"] in WATER_KINDS}


def changes(old: dict, new: dict) -> list[str]:
    """What following the same route on newer data changed, as short lines for the user to review."""
    out = []
    if _mi(old["length_m"]) != _mi(new["length_m"]):
        out.append(f"Total: {_mi(old['length_m'])} → {_mi(new['length_m'])}")
    og, ng = old.get("gain_ft"), new.get("gain_ft")
    if (og is None) != (ng is None) or (og is not None and abs(og - ng) >= 10):
        out.append(f"Climbing: {_ft(og)} → {_ft(ng)}")
    if old.get("level") != new.get("level"):
        out.append(f"Trip level: {_lvl(old.get('level'))} → {_lvl(new.get('level'))}")
    od, nd = (old.get("plan") or {}).get("days") or [], (new.get("plan") or {}).get("days") or []
    if len(od) != len(nd):
        out.append(f"Days: {len(od)} → {len(nd)}")
    for i, (a, b) in enumerate(zip(od, nd), 1):
        a_mi, b_mi = _mi(a["end_m"] - a["start_m"]), _mi(b["end_m"] - b["start_m"])
        if a_mi != b_mi or _day_level(a) != _day_level(b):
            out.append(f"Day {i}: {a_mi}, {_lvl(_day_level(a))} → {b_mi}, {_lvl(_day_level(b))}")
    oc, nc = (old.get("plan") or {}).get("camps") or [], (new.get("plan") or {}).get("camps") or []
    for i, (a, b) in enumerate(zip(oc, nc), 1):
        moved = (b["dist_m"] - a["dist_m"]) / M_PER_MI
        if abs(moved) >= 0.05:
            out.append(f"Camp {i} moves {abs(moved):.1f} mi {'later' if moved > 0 else 'earlier'} along the route")
    ow, nw = _water(old), _water(new)
    out += [f"Water on the map, now along the route: {nw[s]}" for s in nw if s not in ow]
    out += [f"Water on the map, no longer along the route: {ow[s]}" for s in ow if s not in nw]
    ol, nl = _labels(old), _labels(new)
    renamed = {(ol[s], nl[s]) for s in ol if s in nl and ol[s] != nl[s]}
    out += [f"Trail renamed: {a} → {b}" for a, b in sorted(renamed)]
    return out or ["No changes to this trip."]
