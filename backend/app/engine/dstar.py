"""D* Lite (Koenig and Likhachev, 2002): replanning that reuses the previous search.

When a hazard appears or grows, some road costs change.  Planning again from scratch repeats work
for the whole area, although most of the old search is still valid.  D* Lite keeps its distance
estimates and repairs only the part of the graph the cost changes affect.

How it works (searching BACKWARD from the goal, so the start can move without restarting):
  g[u]    best known cost from u to the goal
  rhs[u]  one-step look-ahead:  min over roads (u,v) of  cost(u,v) + g[v]   (rhs[goal] = 0)
  u is "inconsistent" when g[u] != rhs[u]; inconsistent nodes wait in a priority queue ordered by
      key(u) = ( min(g,rhs)[u] + h(start,u) + km ,  min(g,rhs)[u] )
  and the search repairs them until the start is consistent.  `km` grows when the start moves, so
  old queue entries stay valid without being re-keyed.

Edge cost is the project's usual  base_time*mult + lam*risk  (closed road: mult = inf).
Heuristic h(start,u) = landmark (ALT) lower bound on the cost from start to u.  It is admissible and
consistent because costs never go below clean base_time, so it stays valid whatever the hazards do,
including a hazard being removed again.
"""
from __future__ import annotations

from heapq import heappop, heappush

from .algorithms import Landmarks, PathResult
from .csr import INF, CSRGraph, HazardState


def changed_edges(old: HazardState, new: HazardState) -> list[int]:
    """Roads whose cost inputs differ between two states (cost depends on mult and risk only)."""
    om, nm, orr, nr = old.mult, new.mult, old.risk, new.risk
    return [e for e in range(len(om)) if om[e] != nm[e] or orr[e] != nr[e]]


class DStarLite:
    def __init__(self, g: CSRGraph, state: HazardState, start: int, goal: int, lam: float = 0.0,
                 landmarks: Landmarks | None = None, n_active: int = 4):
        self.g_graph, self.state, self.lam = g, state, lam
        self.start, self.last, self.goal = start, start, goal
        self.km = 0.0
        self.g: dict[int, float] = {}
        self.rhs: dict[int, float] = {goal: 0.0}
        self.heap: list[tuple[float, float, int]] = []
        self.queued: dict[int, tuple[float, float]] = {}
        self.expanded = 0                 # nodes made consistent (the search effort)
        self.pops = 0                     # queue pops, including re-keyings
        if landmarks is not None:
            self.lb = landmarks.lower_bound_fn(start, goal, n_active)
        else:
            self.lb = None
        self._insert(goal)

    # ------------------------------------------------------------------ pieces
    def _h(self, u: int) -> float:
        return 0.0 if self.lb is None else self.lb(self.start, u)

    def _key(self, u: int) -> tuple[float, float]:
        m = min(self.g.get(u, INF), self.rhs.get(u, INF))
        if m == INF:
            return (INF, INF)
        return (m + self._h(u) + self.km, m)

    def _insert(self, u: int) -> None:
        k = self._key(u)
        self.queued[u] = k
        heappush(self.heap, (k[0], k[1], u))

    @staticmethod
    def _before(top: tuple[float, float], start_key: tuple[float, float]) -> bool:
        """Should the search keep going: is the best queued key not later than the start's key?

        Ties in the first component count as "keep going".  Landmark bounds are often exactly tight and
        road costs often repeat, so many keys tie with the start's up to rounding error; the heap orders
        them by raw float values, so deciding a tie by the second component could stop the search while a
        node that ties (within rounding) but sorts later still needs repairing.
        """
        if start_key[0] == INF:
            return top[0] != INF
        return top[0] <= start_key[0] + 1e-9 * (1.0 + abs(start_key[0]))

    def _top(self) -> tuple[float, float]:
        h, q = self.heap, self.queued
        while h and q.get(h[0][2]) != (h[0][0], h[0][1]):
            heappop(h)                                    # entry was removed or re-keyed
        return (h[0][0], h[0][1]) if h else (INF, INF)

    def _update(self, u: int) -> None:
        """Recompute rhs[u] from its successors; queue u if it is now inconsistent."""
        g = self.g_graph
        if u != self.goal:
            bt, mult, risk, lam = g.base_time, self.state.mult, self.state.risk, self.lam
            gg, head = self.g, g.head
            best = INF
            for e in range(g.indptr[u], g.indptr[u + 1]):
                v = head[e]
                val = bt[e] * mult[e] + lam * risk[e] + gg.get(v, INF)
                if val < best:
                    best = val
            self.rhs[u] = best
        self.queued.pop(u, None)
        if self.g.get(u, INF) != self.rhs.get(u, INF):
            self._insert(u)

    def _predecessors(self, u: int):
        g = self.g_graph
        for k in range(g.r_indptr[u], g.r_indptr[u + 1]):
            yield g.r_tail[k]

    # ------------------------------------------------------------------ search
    def compute(self) -> dict:
        """Repair the search until the start is consistent. Returns the effort spent in this call."""
        e0, p0 = self.expanded, self.pops
        s = self.start
        while True:
            top = self._top()
            if not (self._before(top, self._key(s)) or self.rhs.get(s, INF) != self.g.get(s, INF)):
                break
            k1, k2, u = heappop(self.heap)
            self.pops += 1
            if self.queued.get(u) != (k1, k2):
                continue
            k_new = self._key(u)
            if (k1, k2) < k_new:                          # key is stale (km grew): re-queue with the right one
                self._insert(u)
                continue
            self.queued.pop(u, None)
            self.expanded += 1
            if self.g.get(u, INF) > self.rhs.get(u, INF):     # better than thought: lock it in, tell predecessors
                self.g[u] = self.rhs[u]
                for p in self._predecessors(u):
                    self._update(p)
            else:                                             # worse than thought: forget g and re-examine
                self.g[u] = INF
                self._update(u)
                for p in self._predecessors(u):
                    self._update(p)
        return {"expanded": self.expanded - e0, "pops": self.pops - p0}

    def update_costs(self, new_state: HazardState, changed: list[int] | None = None) -> dict:
        """Switch to `new_state` and repair. `changed` = roads whose cost differs (found by comparison if omitted)."""
        if changed is None:
            changed = changed_edges(self.state, new_state)
        self.state = new_state
        tails = self.g_graph.tail
        for u in {tails[e] for e in changed}:
            self._update(u)
        out = self.compute()
        out["changed_edges"] = len(changed)
        return out

    def move_to(self, node: int) -> None:
        """The vehicle has reached `node` on its way; later repairs plan from here."""
        if self.lb is not None:
            self.km += self.lb(self.last, node)
        self.start = self.last = node

    # ------------------------------------------------------------------ result
    def cost(self) -> float:
        return self.g.get(self.start, INF)

    def path(self) -> PathResult | None:
        """Best route from the current start, by following g downhill; None if the goal is unreachable."""
        if self.cost() == INF:
            return None
        g = self.g_graph
        bt, mult, risk, lam = g.base_time, self.state.mult, self.state.risk, self.lam
        u, nodes, edges = self.start, [self.start], []
        for _ in range(g.n):
            if u == self.goal:
                break
            best, be = INF, -1
            for e in range(g.indptr[u], g.indptr[u + 1]):
                val = bt[e] * mult[e] + lam * risk[e] + self.g.get(g.head[e], INF)
                if val < best:
                    best, be = val, e
            if be < 0 or best == INF:
                return None
            edges.append(be)
            u = g.head[be]
            nodes.append(u)
        else:
            return None
        time = sum(bt[e] * mult[e] for e in edges)
        rk = sum(risk[e] for e in edges)
        return PathResult(time + lam * rk, time, rk, nodes, edges, self.expanded, "dstar_lite")
