"""Compact graph (CSR layout) plus mutable hazard state for the routing engine.

Why not NetworkX for routing? Whole-Bengaluru has hundreds of thousands of edges. Plain
Python lists indexed by integer ids are several times faster than NetworkX dict-of-dicts,
and let us keep hazard data (closures, penalties, risk) in flat arrays.

Edge cost under hazards (everything else in the engine uses this one formula):

    time(e)  = base_time[e] * mult[e]          mult >= 1, mult = inf means CLOSED
    cost(e)  = time(e) + lam * risk[e]         lam >= 0 (seconds per unit of exposure)

Because mult >= 1 and risk >= 0, hazards can only INCREASE costs. That keeps the ALT
landmark bounds (computed once on clean base_time) valid for every scenario.
"""
from __future__ import annotations

import math

import numpy as np

INF = math.inf


class HazardState:
    """Per-edge hazard data. One instance = one scenario."""

    __slots__ = ("mult", "risk", "onset", "onset_mult")

    def __init__(self, m: int):
        self.mult = [1.0] * m          # static travel-time multiplier (inf = closed)
        self.risk = [0.0] * m          # exposure score (e.g. weighted exposed metres)
        self.onset = [INF] * m         # time (s) after which the hazard escalates
        self.onset_mult = [1.0] * m    # multiplier in force from `onset` onward (>= mult)

    def copy(self) -> "HazardState":
        new = HazardState(0)
        new.mult = self.mult[:]
        new.risk = self.risk[:]
        new.onset = self.onset[:]
        new.onset_mult = self.onset_mult[:]
        return new

    def close(self, edges) -> None:
        for e in edges:
            self.mult[e] = INF
            self.onset_mult[e] = INF

    def penalize(self, edges, mult: float = 1.0, risk: float = 0.0) -> None:
        """Hazards only ever make an edge worse; repeated calls keep the worst value."""
        mult = max(1.0, float(mult))
        for e in edges:
            if mult > self.mult[e]:
                self.mult[e] = mult
            if risk > self.risk[e]:
                self.risk[e] = risk
            if self.onset_mult[e] < self.mult[e]:
                self.onset_mult[e] = self.mult[e]

    def schedule(self, edges, onset_s: float, mult: float = INF) -> None:
        """From `onset_s` seconds after departure, the edge gets multiplier `mult`."""
        for e in edges:
            if onset_s < self.onset[e]:
                self.onset[e] = float(onset_s)
            self.onset_mult[e] = max(self.onset_mult[e], mult, self.mult[e])

    def n_closed(self) -> int:
        return sum(1 for x in self.mult if x == INF)


class CSRGraph:
    def __init__(self, n, tails, heads, base_time, length=None, node_ids=None,
                 lat=None, lon=None, edge_ref=None):
        tails = np.asarray(tails, dtype=np.int64)
        heads = np.asarray(heads, dtype=np.int64)
        bt = np.asarray(base_time, dtype=np.float64)
        m = len(tails)

        order = np.argsort(tails, kind="stable")
        tails, heads, bt = tails[order], heads[order], np.maximum(bt[order], 1e-6)
        length = np.zeros(m) if length is None else np.asarray(length, dtype=np.float64)[order]
        self.edge_ref = None if edge_ref is None else [edge_ref[i] for i in order]

        indptr = np.zeros(n + 1, dtype=np.int64)
        indptr[1:] = np.cumsum(np.bincount(tails, minlength=n))

        r_order = np.argsort(heads, kind="stable")
        r_indptr = np.zeros(n + 1, dtype=np.int64)
        r_indptr[1:] = np.cumsum(np.bincount(heads, minlength=n))

        self.n, self.m = n, m
        self.indptr = indptr.tolist()
        self.head = heads.tolist()
        self.tail = tails.tolist()
        self.base_time = bt.tolist()
        self.length = length.tolist()
        self.r_indptr = r_indptr.tolist()
        self.r_edge = r_order.tolist()          # reverse slot -> forward edge index
        self.r_tail = tails[r_order].tolist()   # reverse slot -> tail of that edge
        self.ident = list(range(m))             # forward slot -> edge index (identity)

        self.node_ids = list(node_ids) if node_ids is not None else list(range(n))
        self.lat = None if lat is None else [float(x) for x in lat]
        self.lon = None if lon is None else [float(x) for x in lon]
        self.state = HazardState(m)
        self._edge_lookup = None

    # ------------------------------------------------------------------ builders
    @classmethod
    def from_networkx(cls, G) -> "CSRGraph":
        node_ids = list(G.nodes)
        index = {nid: i for i, nid in enumerate(node_ids)}
        best: dict[tuple[int, int], tuple[float, float, tuple]] = {}
        for u, v, key, data in G.edges(keys=True, data=True):
            if u == v:
                continue
            tt = float(data["travel_time"])
            k = (index[u], index[v])
            cur = best.get(k)
            if cur is None or tt < cur[0]:
                best[k] = (tt, float(data.get("length", 0.0)), (u, v, key))
        tails = [k[0] for k in best]
        heads = [k[1] for k in best]
        vals = list(best.values())
        return cls(
            n=len(node_ids), tails=tails, heads=heads,
            base_time=[x[0] for x in vals], length=[x[1] for x in vals],
            node_ids=node_ids,
            lat=[G.nodes[n]["y"] for n in node_ids],
            lon=[G.nodes[n]["x"] for n in node_ids],
            edge_ref=[x[2] for x in vals],
        )

    # ------------------------------------------------------------------ helpers
    def edge_between(self, u: int, v: int) -> int | None:
        if self._edge_lookup is None:
            self._edge_lookup = {(self.tail[e], self.head[e]): e for e in range(self.m)}
        return self._edge_lookup.get((u, v))

    def fresh_state(self) -> HazardState:
        return HazardState(self.m)
