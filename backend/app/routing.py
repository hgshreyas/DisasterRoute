"""Glue between the API and the routing engine."""
from __future__ import annotations

from time import perf_counter

from .engine import (INF, DStarLite, alt_astar, bidirectional_alt, bidirectional_dijkstra, dijkstra,
                     changed_edges, pareto_routes, pick_balanced, td_alt_astar)
from .graph import RoadGraph

ALGOS = ("dijkstra", "bidir_dijkstra", "alt", "bidir_alt")


class NeedLandmarks(RuntimeError):
    pass


def run_algo(rg: RoadGraph, algo: str, s: int, t: int, lam: float = 0.0, state=None):
    g = rg.csr
    if algo == "dijkstra":
        return dijkstra(g, s, t, lam, state)
    if algo == "bidir_dijkstra":
        return bidirectional_dijkstra(g, s, t, lam, state)
    if rg.landmarks is None:
        raise NeedLandmarks("Landmarks not built. Run: python scripts/build_landmarks.py")
    if algo == "alt":
        return alt_astar(g, s, t, rg.landmarks, lam, state)
    if algo == "bidir_alt":
        return bidirectional_alt(g, s, t, rg.landmarks, lam, state)
    raise ValueError(f"unknown algo {algo!r}")


def to_dict(rg: RoadGraph, res, with_geometry: bool = True) -> dict:
    g = rg.csr
    out = {
        "algo": res.algo,
        "travel_time_s": round(res.time, 1),
        "length_m": round(sum(g.length[e] for e in res.edges), 1),
        "risk": round(res.risk, 2),
        "cost": round(res.cost, 2),
        "settled_nodes": res.settled,
        "num_edges": len(res.edges),
    }
    if with_geometry:
        out["geometry"] = rg.route_geometry(res.edges)
    return out


def pareto_alternatives(rg: RoadGraph, s: int, t: int, detour: float = 1.5, state=None):
    front = pareto_routes(rg.csr, s, t, rg.landmarks, state, detour=detour,
                          eps_time=1.0, eps_risk=1.0)
    best = pick_balanced(front, 0.5)
    return front, best


def time_dependent_route(rg: RoadGraph, s: int, t: int, state, depart_min: float = 0.0):
    """Earliest-arrival route when a scenario escalates over time (e.g. flood_rising)."""
    if rg.landmarks is None:
        raise NeedLandmarks("Landmarks not built. Run: python scripts/build_landmarks.py")
    return td_alt_astar(rg.csr, s, t, rg.landmarks, depart_min * 60.0, state)


def evaluate_edges(rg: RoadGraph, state, edges: list[int]) -> dict:
    """How a FIXED route fares under a hazard state (used for the hazard-blind baseline)."""
    g = rg.csr
    blocked = [e for e in edges if state.mult[e] == INF]
    exposed = [e for e in edges if state.risk[e] > 0 or 1.0 < state.mult[e] < INF]
    return {
        "blocked": bool(blocked),
        "blocked_edges": len(blocked),
        "exposed_edges": len(exposed),
        "travel_time_s": None if blocked else round(
            sum(g.base_time[e] * state.mult[e] for e in edges), 1),
        "risk": round(sum(state.risk[e] for e in edges if state.mult[e] != INF), 2),
    }


def compare_scenario(rg: RoadGraph, s: int, t: int, state, lam: float = 0.5,
                     detour: float = 1.5) -> dict:
    """Hazard-blind route vs hazard-aware routes on the same scenario."""
    g = rg.csr
    clean = g.fresh_state()
    if state is None:                       # "no scenario": the aware route is just the normal one
        state = g.fresh_state()
    blind = run_algo(rg, "bidir_alt", s, t, 0.0, clean)
    if blind is None:
        return {}
    aware = run_algo(rg, "bidir_alt", s, t, lam, state)
    front, balanced = pareto_alternatives(rg, s, t, detour, state)

    out = {
        "baseline": {**to_dict(rg, blind), "normal_conditions": True,
                     "under_hazard": evaluate_edges(rg, state, blind.edges)},
        "hazard_aware": to_dict(rg, aware) if aware else None,
        "pareto_front": [to_dict(rg, r, with_geometry=False) for r in front],
        "balanced": to_dict(rg, balanced) if balanced else None,
    }
    if front:
        out["lowest_exposure"] = to_dict(rg, min(front, key=lambda r: (r.risk, r.time)))
    return out


def replan(rg: RoadGraph, s: int, t: int, before, after, lam: float = 0.5) -> dict:
    """The hazards change from `before` to `after` while a route is already planned.

    Compares two ways of getting the new best route:
      * D* Lite: keep the search made for `before` and repair only what the change affects;
      * recompute: run a fresh bidirectional ALT search on `after`.
    Both must reach the same cost; the difference is the work and time spent.  Finding which roads
    changed is a plain O(roads) comparison; it is timed separately because a live system is told
    which roads changed and would not need it.
    """
    g = rg.csr
    before = before if before is not None else g.fresh_state()
    after = after if after is not None else g.fresh_state()
    if rg.landmarks is None:
        raise NeedLandmarks("Landmarks not built. Run: python scripts/build_landmarks.py")
    ds = DStarLite(g, before, s, t, lam, rg.landmarks)
    t0 = perf_counter()
    first = ds.compute()
    t_first = perf_counter() - t0
    old_path = ds.path()
    if old_path is None:
        return {}
    t0 = perf_counter()
    changed = changed_edges(before, after)
    t_detect = perf_counter() - t0
    t0 = perf_counter()
    rep = ds.update_costs(after, changed)
    t_repair = perf_counter() - t0
    new_path = ds.path()
    t0 = perf_counter()
    fresh = bidirectional_alt(g, s, t, rg.landmarks, lam, after)
    t_fresh = perf_counter() - t0
    fc = fresh.cost if fresh else INF
    dc = new_path.cost if new_path else INF
    same = (fc == INF and dc == INF) or abs(fc - dc) <= 1e-6 * (1 + abs(fc))
    rerouted = bool(new_path) and (old_path.edges != new_path.edges)
    return {
        "old_route": to_dict(rg, old_path),
        "new_route": to_dict(rg, new_path) if new_path else None,
        "rerouted": rerouted,
        "changed_roads": len(changed),
        "initial_search": {"expanded": first["expanded"], "ms": round(t_first * 1000, 2)},
        "dstar_repair": {"expanded": rep["expanded"], "ms": round(t_repair * 1000, 2)},
        "recompute": {"expanded": fresh.settled if fresh else 0, "ms": round(t_fresh * 1000, 2)},
        "change_detection_ms": round(t_detect * 1000, 2),
        "same_cost_as_recompute": same,
        "speedup": round(t_fresh / t_repair, 2) if t_repair > 0 else None,
    }
