"""Advanced shortest-path algorithms for hazard-aware routing.

Implemented here (all on the CSR graph from csr.py):

  dijkstra                 heap Dijkstra with early exit, cutoff, forward or reverse
  bidirectional_dijkstra   two searches that meet in the middle
  Landmarks                ALT preprocessing (A*, Landmarks, Triangle inequality)
  alt_astar                A* guided by landmark lower bounds
  bidirectional_alt        consistent bidirectional A* (average-potential method)
  td_alt_astar             time-dependent earliest-arrival A* (hazards that start later)
  multi_source_dijkstra    one search from many sources (facility accessibility)

Cost of an edge is  base_time*mult + lam*risk  (see csr.py). Closed edges have mult = inf.
Landmark bounds use CLEAN base_time, so they stay admissible under any hazard scenario
(hazards never decrease an edge's cost).
"""
from __future__ import annotations

import heapq
import random
from array import array
from dataclasses import dataclass, field
from heapq import heappop, heappush

import numpy as np

from .csr import INF, CSRGraph, HazardState


# --------------------------------------------------------------------------- results
@dataclass
class PathResult:
    cost: float            # time + lam * risk
    time: float            # seconds
    risk: float            # summed exposure
    nodes: list            # CSR node indices
    edges: list            # CSR edge indices
    settled: int           # nodes settled (a measure of search effort)
    algo: str
    extra: dict = field(default_factory=dict)


def _finish(g: CSRGraph, st: HazardState, edges: list, s: int, settled: int,
            algo: str, lam: float = 0.0) -> PathResult:
    bt = g.base_time
    time = sum(bt[e] * st.mult[e] for e in edges)
    risk = sum(st.risk[e] for e in edges)
    nodes = [s] + [g.head[e] for e in edges]
    return PathResult(time + lam * risk, time, risk, nodes, edges, settled, algo)


def _edges_from_pred(g: CSRGraph, pred: dict, s: int, t: int) -> list:
    edges = []
    v = t
    while v != s:
        e = pred[v]
        edges.append(e)
        v = g.tail[e]
    edges.reverse()
    return edges


# --------------------------------------------------------------------------- Dijkstra
def dijkstra_tree(g: CSRGraph, s: int, t: int | None = None, lam: float = 0.0,
                  state: HazardState | None = None, cutoff: float = INF,
                  reverse: bool = False):
    """Return (dist, pred, settled). Stops early when t is settled or cost exceeds cutoff.

    reverse=True searches the reversed graph: dist[v] = cost of the best path v -> s.
    pred[v] is the edge used to reach v (forward: edge into v; reverse: edge out of v).
    """
    st = state or g.state
    bt, mult, risk = g.base_time, st.mult, st.risk
    if reverse:
        ptr, nbr, eid = g.r_indptr, g.r_tail, g.r_edge
    else:
        ptr, nbr, eid = g.indptr, g.head, g.ident

    dist = {s: 0.0}
    pred: dict[int, int] = {}
    done: set[int] = set()
    heap = [(0.0, s)]
    while heap:
        d, u = heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u == t or d > cutoff:
            break
        for k in range(ptr[u], ptr[u + 1]):
            e = eid[k]
            nd = d + bt[e] * mult[e] + lam * risk[e]
            v = nbr[k]
            if nd < dist.get(v, INF):
                dist[v] = nd
                pred[v] = e
                heappush(heap, (nd, v))
    return dist, pred, len(done)


def dijkstra(g, s, t, lam=0.0, state=None) -> PathResult | None:
    st = state or g.state
    if s == t:
        return _finish(g, st, [], s, 1, "dijkstra", lam)
    dist, pred, settled = dijkstra_tree(g, s, t, lam, st)
    if t not in pred:
        return None
    return _finish(g, st, _edges_from_pred(g, pred, s, t), s, settled, "dijkstra", lam)


# --------------------------------------------------------------------------- bidirectional Dijkstra
def bidirectional_dijkstra(g, s, t, lam=0.0, state=None) -> PathResult | None:
    st = state or g.state
    if s == t:
        return _finish(g, st, [], s, 1, "bidir_dijkstra", lam)
    bt, mult, risk = g.base_time, st.mult, st.risk
    head, tail = g.head, g.tail

    df, db = {s: 0.0}, {t: 0.0}
    pre_f: dict[int, int] = {}
    pre_b: dict[int, int] = {}
    hf, hb = [(0.0, s)], [(0.0, t)]
    sf: set[int] = set()
    sb: set[int] = set()
    mu, meet = INF, None

    while True:
        while hf and hf[0][1] in sf:
            heappop(hf)
        while hb and hb[0][1] in sb:
            heappop(hb)
        if not hf or not hb or hf[0][0] + hb[0][0] >= mu:
            break
        if len(hf) <= len(hb):
            d, u = heappop(hf)
            sf.add(u)
            for e in range(g.indptr[u], g.indptr[u + 1]):
                nd = d + bt[e] * mult[e] + lam * risk[e]
                v = head[e]
                if nd < df.get(v, INF):
                    df[v] = nd
                    pre_f[v] = e
                    heappush(hf, (nd, v))
                    if v in db and nd + db[v] < mu:
                        mu, meet = nd + db[v], v
        else:
            d, u = heappop(hb)
            sb.add(u)
            for k in range(g.r_indptr[u], g.r_indptr[u + 1]):
                e = g.r_edge[k]
                nd = d + bt[e] * mult[e] + lam * risk[e]
                w = g.r_tail[k]
                if nd < db.get(w, INF):
                    db[w] = nd
                    pre_b[w] = e
                    heappush(hb, (nd, w))
                    if w in df and nd + df[w] < mu:
                        mu, meet = nd + df[w], w

    if meet is None:
        return None
    edges = _edges_from_pred(g, pre_f, s, meet)
    v = meet
    while v != t:
        e = pre_b[v]
        edges.append(e)
        v = head[e]
    return _finish(g, st, edges, s, len(sf) + len(sb), "bidir_dijkstra", lam)


# --------------------------------------------------------------------------- ALT landmarks
def _full_dijkstra_base(g: CSRGraph, src: int, reverse: bool) -> array:
    """Distances over the CLEAN graph (base_time only, no hazards)."""
    bt = g.base_time
    if reverse:
        ptr, nbr, eid = g.r_indptr, g.r_tail, g.r_edge
    else:
        ptr, nbr, eid = g.indptr, g.head, g.ident
    dist = array("d", [INF]) * g.n
    dist[src] = 0.0
    heap = [(0.0, src)]
    while heap:
        d, u = heappop(heap)
        if d > dist[u]:
            continue
        for k in range(ptr[u], ptr[u + 1]):
            nd = d + bt[eid[k]]
            v = nbr[k]
            if nd < dist[v]:
                dist[v] = nd
                heappush(heap, (nd, v))
    return dist


class Landmarks:
    """Distances from/to a handful of landmark nodes, used for A* lower bounds.

    For any u, v:   dist(u, v) >= d_from[L][v] - d_from[L][u]
                    dist(u, v) >= d_to[L][u]   - d_to[L][v]
    """

    def __init__(self, ids: list[int], d_from: list[array], d_to: list[array]):
        self.ids, self.d_from, self.d_to = ids, d_from, d_to

    @classmethod
    def build(cls, g: CSRGraph, k: int = 8, seed: int = 0) -> "Landmarks":
        """Farthest-point selection: each new landmark is far from all earlier ones.

        Requires a strongly connected graph (the download script keeps the largest SCC).
        """
        rng = random.Random(seed)
        first = _full_dijkstra_base(g, rng.randrange(g.n), reverse=False)
        if any(x == INF for x in first):
            raise ValueError("Graph is not strongly connected; keep the largest SCC first.")
        lm = max(range(g.n), key=first.__getitem__)

        ids, d_from, d_to = [], [], []
        closest = [INF] * g.n                      # distance to nearest chosen landmark
        for _ in range(min(k, g.n)):
            f = _full_dijkstra_base(g, lm, reverse=False)
            r = _full_dijkstra_base(g, lm, reverse=True)
            ids.append(lm)
            d_from.append(f)
            d_to.append(r)
            for v in range(g.n):
                x = min(f[v], r[v])
                if x < closest[v]:
                    closest[v] = x
            lm = max(range(g.n), key=closest.__getitem__)
        return cls(ids, d_from, d_to)

    # -- persistence (preprocessing for the whole city is slow, so cache it)
    def save(self, path, g: CSRGraph) -> None:
        np.savez_compressed(
            path, n=g.n, m=g.m, ids=np.array(self.ids),
            d_from=np.array([np.frombuffer(a, dtype=np.float64) for a in self.d_from]),
            d_to=np.array([np.frombuffer(a, dtype=np.float64) for a in self.d_to]),
        )

    @classmethod
    def load(cls, path, g: CSRGraph) -> "Landmarks":
        z = np.load(path)
        if int(z["n"]) != g.n or int(z["m"]) != g.m:
            raise ValueError("Landmark file does not match this graph; rebuild it.")
        d_from = [array("d", row.tolist()) for row in z["d_from"]]
        d_to = [array("d", row.tolist()) for row in z["d_to"]]
        return cls([int(i) for i in z["ids"]], d_from, d_to)

    # -- bounds
    def _active(self, s: int, t: int, n_active: int) -> list[int]:
        scores = []
        for i in range(len(self.ids)):
            f, to = self.d_from[i], self.d_to[i]
            scores.append((max(f[t] - f[s], to[s] - to[t]), i))
        scores.sort(reverse=True)
        return [i for _, i in scores[:n_active]]

    def lower_bound_fn(self, s: int, t: int, n_active: int = 4):
        """Return lb(u, v) built from the landmarks that are tightest for this s-t pair."""
        act = self._active(s, t, n_active)
        fs = [self.d_from[i] for i in act]
        ts = [self.d_to[i] for i in act]

        def lb(u: int, v: int) -> float:
            best = 0.0
            for f, to in zip(fs, ts):
                a = f[v] - f[u]
                if a > best:
                    best = a
                b = to[u] - to[v]
                if b > best:
                    best = b
            return best

        return lb


# --------------------------------------------------------------------------- ALT A*
def alt_astar(g, s, t, landmarks: Landmarks, lam=0.0, state=None, n_active=4) -> PathResult | None:
    st = state or g.state
    if s == t:
        return _finish(g, st, [], s, 1, "alt_astar", lam)
    bt, mult, risk = g.base_time, st.mult, st.risk
    head = g.head
    lb = landmarks.lower_bound_fn(s, t, n_active)

    dist = {s: 0.0}
    pred: dict[int, int] = {}
    done: set[int] = set()
    heap = [(lb(s, t), 0.0, s)]
    while heap:
        _, d, u = heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u == t:
            break
        for e in range(g.indptr[u], g.indptr[u + 1]):
            nd = d + bt[e] * mult[e] + lam * risk[e]
            v = head[e]
            if nd < dist.get(v, INF):
                dist[v] = nd
                pred[v] = e
                heappush(heap, (nd + lb(v, t), nd, v))
    if t not in pred:
        return None
    return _finish(g, st, _edges_from_pred(g, pred, s, t), s, len(done), "alt_astar", lam)


# --------------------------------------------------------------------------- bidirectional ALT
def bidirectional_alt(g, s, t, landmarks: Landmarks, lam=0.0, state=None, n_active=4) -> PathResult | None:
    """Consistent bidirectional A* (Goldberg-Harrelson average potentials).

    Forward potential  pf(v) = (h_t(v) - h_s(v)) / 2,  backward potential = -pf.
    Both searches then run Dijkstra on the SAME reduced costs, so the usual
    bidirectional stopping rule applies:  stop when  key_f + key_b >= mu.
    """
    st = state or g.state
    if s == t:
        return _finish(g, st, [], s, 1, "bidir_alt", lam)
    bt, mult, risk = g.base_time, st.mult, st.risk
    head = g.head
    lb = landmarks.lower_bound_fn(s, t, n_active)

    memo: dict[int, float] = {}

    def pf(v: int) -> float:
        x = memo.get(v)
        if x is None:
            x = 0.5 * (lb(v, t) - lb(s, v))
            memo[v] = x
        return x

    df, db = {s: 0.0}, {t: 0.0}
    pre_f: dict[int, int] = {}
    pre_b: dict[int, int] = {}
    hf, hb = [(pf(s), 0.0, s)], [(-pf(t), 0.0, t)]
    sf: set[int] = set()
    sb: set[int] = set()
    mu, meet = INF, None

    while True:
        while hf and hf[0][2] in sf:
            heappop(hf)
        while hb and hb[0][2] in sb:
            heappop(hb)
        if not hf or not hb or hf[0][0] + hb[0][0] >= mu:
            break
        if len(hf) <= len(hb):
            _, d, u = heappop(hf)
            sf.add(u)
            for e in range(g.indptr[u], g.indptr[u + 1]):
                nd = d + bt[e] * mult[e] + lam * risk[e]
                v = head[e]
                if nd < df.get(v, INF):
                    df[v] = nd
                    pre_f[v] = e
                    heappush(hf, (nd + pf(v), nd, v))
                    if v in db and nd + db[v] < mu:
                        mu, meet = nd + db[v], v
        else:
            _, d, u = heappop(hb)
            sb.add(u)
            for k in range(g.r_indptr[u], g.r_indptr[u + 1]):
                e = g.r_edge[k]
                nd = d + bt[e] * mult[e] + lam * risk[e]
                w = g.r_tail[k]
                if nd < db.get(w, INF):
                    db[w] = nd
                    pre_b[w] = e
                    heappush(hb, (nd - pf(w), nd, w))
                    if w in df and nd + df[w] < mu:
                        mu, meet = nd + df[w], w

    if meet is None:
        return None
    edges = _edges_from_pred(g, pre_f, s, meet)
    v = meet
    while v != t:
        e = pre_b[v]
        edges.append(e)
        v = head[e]
    return _finish(g, st, edges, s, len(sf) + len(sb), "bidir_alt", lam)


# --------------------------------------------------------------------------- time-dependent A*
def td_alt_astar(g, s, t, landmarks: Landmarks, depart: float = 0.0, state=None,
                 n_active=4) -> PathResult | None:
    """Earliest-arrival route when hazards escalate over time.

    An edge entered at clock time tau has multiplier  onset_mult[e] if tau >= onset[e]
    else mult[e].  Hazards only escalate (onset_mult >= mult), so a later arrival is never
    better (FIFO) and plain label-setting A* is exact.  The clock advances by travel time
    only; risk is not part of this objective (use pareto_routes / lam for that).
    """
    st = state or g.state
    if s == t:
        res = _finish(g, st, [], s, 1, "td_alt", 0.0)
        res.extra["arrival"] = depart
        return res
    bt, mult = g.base_time, st.mult
    onset, onset_mult = st.onset, st.onset_mult
    head = g.head
    lb = landmarks.lower_bound_fn(s, t, n_active)

    arr = {s: depart}
    pred: dict[int, int] = {}
    done: set[int] = set()
    heap = [(depart + lb(s, t), depart, s)]
    while heap:
        _, a, u = heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u == t:
            break
        for e in range(g.indptr[u], g.indptr[u + 1]):
            m = onset_mult[e] if a >= onset[e] else mult[e]
            na = a + bt[e] * m
            v = head[e]
            if na < arr.get(v, INF):
                arr[v] = na
                pred[v] = e
                heappush(heap, (na + lb(v, t), na, v))
    if t not in pred:
        return None
    edges = _edges_from_pred(g, pred, s, t)
    res = _finish(g, st, edges, s, len(done), "td_alt", 0.0)
    res.time = arr[t] - depart          # true elapsed time with escalation applied
    res.cost = res.time
    res.extra["arrival"] = arr[t]
    return res


# --------------------------------------------------------------------------- multi-source
def multi_source_dijkstra(g, sources: list[int], lam=0.0, state=None, reverse=False,
                          cutoff: float = INF):
    """Dijkstra from many sources at once.

    reverse=True with facility nodes as sources gives, for EVERY node v, the cost of
    the cheapest trip v -> nearest facility, in a single sweep.
    Returns (dist, label): label[v] = index into `sources` of the closest source.
    """
    st = state or g.state
    bt, mult, risk = g.base_time, st.mult, st.risk
    if reverse:
        ptr, nbr, eid = g.r_indptr, g.r_tail, g.r_edge
    else:
        ptr, nbr, eid = g.indptr, g.head, g.ident

    best: dict[int, float] = {}
    heap = []
    for i, src in enumerate(sources):
        if 0.0 < best.get(src, INF):
            best[src] = 0.0
            heap.append((0.0, src, i))
    heapq.heapify(heap)
    dist: dict[int, float] = {}
    label: dict[int, int] = {}
    while heap:
        d, u, lab = heappop(heap)
        if u in dist:
            continue
        if d > cutoff:
            break
        dist[u] = d
        label[u] = lab
        for k in range(ptr[u], ptr[u + 1]):
            e = eid[k]
            nd = d + bt[e] * mult[e] + lam * risk[e]
            v = nbr[k]
            if v not in dist and nd < best.get(v, INF):
                best[v] = nd
                heappush(heap, (nd, v, lab))
    return dist, label
