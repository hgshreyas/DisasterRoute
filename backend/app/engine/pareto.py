"""Bi-objective routing: the trade-off between travel time and hazard exposure.

pareto_routes() returns every route that no other route beats on BOTH time and risk
(the Pareto front). The UI can then show "fastest", "safest", and a balanced pick,
instead of hiding the trade-off inside one weighted cost.

Method: multi-label A* (label-setting).
  * a label is (time, risk) for one partial path;
  * a label is discarded if another label at the same node is no worse on both objectives;
  * ALT lower bounds on time prune labels that cannot finish within the detour budget
    or cannot beat a route already found at the target.
With eps_time = eps_risk = 0 the result is the exact Pareto front.
Positive eps values give a smaller, approximate front (faster on a large city).
"""
from __future__ import annotations

from heapq import heappop, heappush

from .algorithms import Landmarks, PathResult, _finish, alt_astar, dijkstra
from .csr import INF


def pareto_routes(g, s, t, landmarks: Landmarks | None = None, state=None,
                  detour: float = 1.5, eps_time: float = 0.0, eps_risk: float = 0.0,
                  max_labels: int = 40, max_expansions: int = 3_000_000,
                  n_active: int = 4) -> list[PathResult]:
    st = state or g.state
    base = (alt_astar(g, s, t, landmarks, 0.0, st, n_active) if landmarks
            else dijkstra(g, s, t, 0.0, st))
    if base is None:
        return []
    if s == t:
        return [base]

    lb = landmarks.lower_bound_fn(s, t, n_active) if landmarks else None

    def h(v: int) -> float:
        return lb(v, t) if lb else 0.0

    tmax = base.time * detour
    bt, mult, risk, head = g.base_time, st.mult, st.risk, g.head

    L_t, L_r, L_node, L_par, L_edge, L_dead = [0.0], [0.0], [s], [-1], [-1], [False]
    node_labels: dict[int, list[int]] = {s: [0]}
    heap = [(h(s), 0.0, 0)]
    expansions = 0

    while heap:
        _, _, i = heappop(heap)
        if L_dead[i]:
            continue
        u = L_node[i]
        if u == t:
            continue                       # target labels are collected from node_labels
        expansions += 1
        if expansions > max_expansions:
            break
        ti, ri = L_t[i], L_r[i]
        front = node_labels.get(t, ())
        for e in range(g.indptr[u], g.indptr[u + 1]):
            m = mult[e]
            if m == INF:
                continue
            nt = ti + bt[e] * m
            nr = ri + risk[e]
            v = head[e]
            hv = h(v)
            if nt + hv > tmax + 1e-9:
                continue
            bound_t = nt + hv
            if any(L_t[f] <= bound_t and L_r[f] <= nr for f in front):
                continue                   # a finished route already beats every completion
            lst = node_labels.get(v, [])
            if any(L_t[j] <= nt + eps_time and L_r[j] <= nr + eps_risk for j in lst):
                continue
            if len(lst) >= max_labels:
                continue
            keep = []
            for j in lst:
                if nt <= L_t[j] and nr <= L_r[j]:
                    L_dead[j] = True
                else:
                    keep.append(j)
            nid = len(L_t)
            L_t.append(nt); L_r.append(nr); L_node.append(v)
            L_par.append(i); L_edge.append(e); L_dead.append(False)
            keep.append(nid)
            node_labels[v] = keep
            heappush(heap, (nt + hv, nr, nid))

    results = []
    for i in node_labels.get(t, []):
        if L_dead[i]:
            continue
        edges = []
        j = i
        while L_par[j] != -1:
            edges.append(L_edge[j])
            j = L_par[j]
        edges.reverse()
        results.append(_finish(g, st, edges, s, expansions, "pareto", 0.0))
    results.sort(key=lambda r: (r.time, r.risk))
    return results


def pick_balanced(front: list[PathResult], w_time: float = 0.5) -> PathResult | None:
    """Pick one route from a Pareto front by normalised weighted distance to the ideal."""
    if not front:
        return None
    t0, t1 = min(r.time for r in front), max(r.time for r in front)
    r0, r1 = min(r.risk for r in front), max(r.risk for r in front)
    dt, dr = (t1 - t0) or 1.0, (r1 - r0) or 1.0
    return min(front, key=lambda r: w_time * (r.time - t0) / dt + (1 - w_time) * (r.risk - r0) / dr)
