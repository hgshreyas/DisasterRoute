"""Facility accessibility: which hospitals and shelters can people still reach?

Two views, both from the routing engine:

  coverage()      city-wide. ONE reverse multi-source Dijkstra from every facility gives each
                  road node its nearest facility and travel time. Run it on clean conditions
                  and again under a scenario, then compare.
  origin_access() from one chosen point: travel time to every facility, or "unreachable".

Network nodes stand in for where people are (a proxy: node density is not population).
Travel time here is time only (base_time * multiplier); closed roads cannot be used.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .engine import INF, dijkstra_tree, multi_source_dijkstra


@dataclass
class Facility:
    id: str
    name: str
    kind: str          # "hospital" | "shelter"
    lat: float
    lon: float
    node: int          # CSR node the facility snaps to
    snap_m: float
    opening_hours: str | None = None     # raw OSM tag, if known
    emergency: str | None = None         # OSM emergency=yes/no, if known


def load_facilities(rg, path: Path, max_snap_m: float = 500.0) -> list[Facility]:
    """Read facilities.json and snap each facility to the road network."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[Facility] = []
    for r in rows:
        node, d = rg.nearest_node(r["lat"], r["lon"])
        if d > max_snap_m:
            continue                      # outside the cached road network
        out.append(Facility(r["id"], r["name"], r["kind"], r["lat"], r["lon"], node, d,
                            r.get("opening_hours"), r.get("emergency")))
    return out


def coverage(rg, facilities: list[Facility], state=None, threshold_min: float = 30.0) -> dict:
    """City-wide accessibility under one hazard state (None = normal conditions)."""
    g = rg.csr
    thr = threshold_min * 60.0
    if not facilities:
        return {"facilities": 0}
    dist, label = multi_source_dijkstra(g, [f.node for f in facilities], 0.0, state, reverse=True)
    within = [d for d in dist.values() if d <= thr]
    served = Counter(label[v] for v, d in dist.items() if d <= thr)
    return {
        "threshold_min": threshold_min,
        "nodes_total": g.n,
        "nodes_within_threshold": len(within),
        "pct_within_threshold": round(100.0 * len(within) / g.n, 2),
        "nodes_cannot_reach_any": g.n - len(dist),
        "mean_time_to_nearest_min": round(sum(dist.values()) / max(1, len(dist)) / 60.0, 2),
        "catchment_nodes": {facilities[i].id: served.get(i, 0) for i in range(len(facilities))},
    }


def compare_coverage(rg, facilities: list[Facility], state, threshold_min: float = 30.0) -> dict:
    """Baseline vs scenario coverage plus which facilities lost their catchment."""
    base = coverage(rg, facilities, None, threshold_min)
    scen = coverage(rg, facilities, state, threshold_min)
    changes = []
    for f in facilities:
        b = base["catchment_nodes"][f.id]
        s = scen["catchment_nodes"][f.id]
        changes.append({
            "id": f.id, "name": f.name, "kind": f.kind, "lat": f.lat, "lon": f.lon,
            "catchment_baseline": b, "catchment_scenario": s,
            "cut_off": s <= 1,            # only its own node is within reach
            "was_cut_off": b <= 1,
        })
    changes.sort(key=lambda c: c["catchment_scenario"] - c["catchment_baseline"])
    return {
        "baseline": {k: v for k, v in base.items() if k != "catchment_nodes"},
        "scenario": {k: v for k, v in scen.items() if k != "catchment_nodes"},
        "pct_points_lost": round(base["pct_within_threshold"] - scen["pct_within_threshold"], 2),
        "newly_cut_off": [c for c in changes if c["cut_off"] and not c["was_cut_off"]],
        "facilities": changes,
    }


def origin_access(rg, origin_node: int, facilities: list[Facility], state=None,
                  max_minutes: float = 90.0) -> list[dict]:
    """Travel time from one origin to every facility (None when unreachable), nearest first."""
    g = rg.csr
    cutoff = max_minutes * 60.0
    base_d, _, _ = dijkstra_tree(g, origin_node, None, 0.0, None, cutoff)
    scen_d, _, _ = dijkstra_tree(g, origin_node, None, 0.0, state, cutoff)
    rows = []
    for f in facilities:
        b = base_d.get(f.node)
        s = scen_d.get(f.node)
        if b is not None and b > cutoff:
            b = None
        if s is not None and s > cutoff:
            s = None
        rows.append({
            "id": f.id, "name": f.name, "kind": f.kind, "lat": f.lat, "lon": f.lon,
            "minutes_baseline": None if b is None else round(b / 60.0, 2),
            "minutes_scenario": None if s is None else round(s / 60.0, 2),
            "reachable": s is not None,
            "extra_minutes": None if (b is None or s is None) else round((s - b) / 60.0, 2),
        })
    rows.sort(key=lambda r: (r["minutes_scenario"] is None, r["minutes_scenario"] or 0.0))
    return rows
