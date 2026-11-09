"""Correctness tests: every engine algorithm is checked against an independent reference.

Run from backend/:   python -I tests/test_engine.py
Needs only numpy + networkx (no osmnx).
"""
import itertools
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import networkx as nx

from app.engine import (CSRGraph, HazardState, INF, Landmarks, alt_astar,
                        bidirectional_alt, bidirectional_dijkstra, dijkstra,
                        multi_source_dijkstra, pareto_routes, td_alt_astar)


def make_graph(n, extra, seed):
    """Strongly connected directed graph: a two-way ring plus random one/two-way chords."""
    rng = random.Random(seed)
    edges = {}
    for i in range(n):
        j = (i + 1) % n
        edges[(i, j)] = rng.uniform(1, 20)
        edges[(j, i)] = rng.uniform(1, 20)
    for _ in range(extra):
        a, b = rng.randrange(n), rng.randrange(n)
        if a != b:
            edges[(a, b)] = rng.uniform(1, 20)
            if rng.random() < 0.6:
                edges[(b, a)] = rng.uniform(1, 20)
    ks = list(edges)
    return CSRGraph(n, [k[0] for k in ks], [k[1] for k in ks], [edges[k] for k in ks])


def random_hazards(g, rng, p_close=0.04, p_pen=0.15):
    st = HazardState(g.m)
    for e in range(g.m):
        r = rng.random()
        if r < p_close:
            st.close([e])
        elif r < p_close + p_pen:
            st.penalize([e], mult=rng.uniform(1.2, 3.0), risk=rng.uniform(0, 50))
    return st


def reference_graph(g, st, lam):
    G = nx.DiGraph()
    G.add_nodes_from(range(g.n))
    for e in range(g.m):
        if st.mult[e] == INF:
            continue
        G.add_edge(g.tail[e], g.head[e],
                   w=g.base_time[e] * st.mult[e] + lam * st.risk[e])
    return G


def ref_cost(G, s, t):
    try:
        return nx.dijkstra_path_length(G, s, t, weight="w")
    except nx.NetworkXNoPath:
        return None


def check_path(g, res, st, lam, s, t):
    """The returned edge list must be a real s->t walk whose cost matches res.cost."""
    assert res.nodes[0] == s and res.nodes[-1] == t
    for a, b, e in zip(res.nodes[:-1], res.nodes[1:], res.edges):
        assert g.tail[e] == a and g.head[e] == b
        assert st.mult[e] != INF, "path uses a closed edge"
    c = sum(g.base_time[e] * st.mult[e] + lam * st.risk[e] for e in res.edges)
    assert math.isclose(c, res.cost, rel_tol=1e-9, abs_tol=1e-9), (c, res.cost)


def test_static_algorithms():
    total = 0
    for seed in range(6):
        g = make_graph(250, 400, seed)
        lm = Landmarks.build(g, k=6, seed=seed)
        rng = random.Random(100 + seed)
        for trial in range(40):
            st = random_hazards(g, rng)
            lam = rng.choice([0.0, 0.0, 0.3, 1.0])
            G = reference_graph(g, st, lam)
            s, t = rng.randrange(g.n), rng.randrange(g.n)
            ref = ref_cost(G, s, t)
            for name, fn in [
                ("dijkstra", lambda: dijkstra(g, s, t, lam, st)),
                ("bidir_dijkstra", lambda: bidirectional_dijkstra(g, s, t, lam, st)),
                ("alt_astar", lambda: alt_astar(g, s, t, lm, lam, st)),
                ("bidir_alt", lambda: bidirectional_alt(g, s, t, lm, lam, st)),
            ]:
                res = fn()
                if ref is None:
                    assert res is None, f"{name}: expected no path (seed={seed}, {s}->{t})"
                else:
                    assert res is not None, f"{name}: missed a path (seed={seed}, {s}->{t})"
                    assert math.isclose(res.cost, ref, rel_tol=1e-9, abs_tol=1e-9), \
                        f"{name}: {res.cost} != {ref} (seed={seed}, {s}->{t}, lam={lam})"
                    check_path(g, res, st, lam, s, t)
                    total += 1
    print(f"  static algorithms: {total} route checks passed")


def test_search_effort():
    """ALT variants should settle far fewer nodes than plain Dijkstra on a larger graph."""
    g = make_graph(6000, 9000, 7)
    lm = Landmarks.build(g, k=8, seed=1)
    rng = random.Random(5)
    eff = {"dijkstra": 0, "bidir_dijkstra": 0, "alt_astar": 0, "bidir_alt": 0}
    pairs = [(rng.randrange(g.n), rng.randrange(g.n)) for _ in range(25)]
    for s, t in pairs:
        eff["dijkstra"] += dijkstra(g, s, t).settled
        eff["bidir_dijkstra"] += bidirectional_dijkstra(g, s, t).settled
        eff["alt_astar"] += alt_astar(g, s, t, lm).settled
        eff["bidir_alt"] += bidirectional_alt(g, s, t, lm).settled
    print("  nodes settled over 25 queries (6000-node graph):")
    for k, v in eff.items():
        print(f"    {k:15s} {v:8d}")
    assert eff["alt_astar"] < eff["dijkstra"]


def test_time_dependent():
    total = 0
    for seed in range(5):
        g = make_graph(200, 350, 40 + seed)
        lm = Landmarks.build(g, k=5, seed=seed)
        rng = random.Random(seed)
        st = random_hazards(g, rng, p_close=0.02, p_pen=0.1)
        # Hazards that escalate later: some edges slow down or close after an onset time.
        for e in range(g.m):
            if rng.random() < 0.2:
                st.schedule([e], onset_s=rng.uniform(5, 60),
                            mult=rng.choice([INF, 2.5, 4.0]))
        for _ in range(40):
            s, t = rng.randrange(g.n), rng.randrange(g.n)
            depart = rng.choice([0.0, 10.0, 30.0])
            res = td_alt_astar(g, s, t, lm, depart, st)
            ref = _td_reference(g, st, s, t, depart)
            if ref is None:
                assert res is None
            else:
                assert res is not None
                assert math.isclose(res.extra["arrival"], ref, rel_tol=1e-9, abs_tol=1e-9), \
                    (res.extra["arrival"], ref)
                total += 1
    print(f"  time-dependent A*: {total} earliest-arrival checks passed")


def _td_reference(g, st, s, t, depart):
    """Independent plain time-dependent Dijkstra (no landmarks, different code)."""
    import heapq
    best = {s: depart}
    pq = [(depart, s)]
    while pq:
        a, u = heapq.heappop(pq)
        if a > best.get(u, INF):
            continue
        if u == t:
            return a
        for e in range(g.m):
            if g.tail[e] != u:
                continue
            m = st.onset_mult[e] if a >= st.onset[e] else st.mult[e]
            na = a + g.base_time[e] * m
            if na < best.get(g.head[e], INF):
                best[g.head[e]] = na
                heapq.heappush(pq, (na, g.head[e]))
    return None


def test_multi_source():
    total = 0
    for seed in range(5):
        g = make_graph(200, 300, 70 + seed)
        rng = random.Random(seed)
        st = random_hazards(g, rng)
        facilities = rng.sample(range(g.n), 5)
        G = reference_graph(g, st, 0.0).reverse(copy=True)
        for f in facilities:
            pass
        dist, label = multi_source_dijkstra(g, facilities, 0.0, st, reverse=True)
        # Reference: add a super source linked (cost 0) to every facility on the reversed graph.
        G.add_node("S")
        for f in facilities:
            G.add_edge("S", f, w=0.0)
        ref = nx.single_source_dijkstra_path_length(G, "S", weight="w")
        for v in range(g.n):
            if v in ref:
                assert v in dist and math.isclose(dist[v], ref[v], abs_tol=1e-9), (v, dist.get(v), ref[v])
                total += 1
            else:
                assert v not in dist
        # label must point at a facility that really achieves the distance
        for v, lab in list(label.items())[:30]:
            Gf = reference_graph(g, st, 0.0)
            assert math.isclose(nx.dijkstra_path_length(Gf, v, facilities[lab], weight="w"),
                                dist[v], abs_tol=1e-9)
    print(f"  multi-source Dijkstra: {total} node distances matched the reference")


def test_pareto_exact():
    """eps=0 and unlimited detour must reproduce the brute-force Pareto front."""
    checked = 0
    for seed in range(12):
        rng = random.Random(900 + seed)
        n = 9
        g = make_graph(n, 14, 500 + seed)
        st = HazardState(g.m)
        for e in range(g.m):
            st.risk[e] = rng.uniform(0, 30)
            if rng.random() < 0.2:
                st.mult[e] = rng.uniform(1.2, 2.5)
        lm = Landmarks.build(g, k=3, seed=seed)
        s, t = 0, n // 2
        front = pareto_routes(g, s, t, lm, st, detour=1e9, max_labels=10_000)

        G = nx.DiGraph()
        for e in range(g.m):
            G.add_edge(g.tail[e], g.head[e], e=e)
        cands = []
        for path in nx.all_simple_paths(G, s, t):
            es = [G[a][b]["e"] for a, b in zip(path[:-1], path[1:])]
            cands.append((sum(g.base_time[e] * st.mult[e] for e in es),
                          sum(st.risk[e] for e in es)))
        nd = [c for c in cands
              if not any(o[0] <= c[0] and o[1] <= c[1] and o != c for o in cands)]
        expect = sorted({(round(a, 6), round(b, 6)) for a, b in nd})
        got = sorted({(round(r.time, 6), round(r.risk, 6)) for r in front})
        assert expect == got, f"seed={seed}\nexpected {expect}\ngot      {got}"
        for r in front:
            check_path(g, r, st, 0.0, s, t)
        checked += 1
    print(f"  Pareto front: {checked} random graphs matched brute-force enumeration")


def test_pareto_pruning_and_budget():
    g = make_graph(400, 700, 3)
    rng = random.Random(3)
    st = HazardState(g.m)
    for e in range(g.m):
        st.risk[e] = rng.uniform(0, 40) if rng.random() < 0.3 else 0.0
    lm = Landmarks.build(g, k=6, seed=3)
    fastest = alt_astar(g, 0, 200, lm, 0.0, st)
    front = pareto_routes(g, 0, 200, lm, st, detour=1.4, eps_time=0.5, eps_risk=0.5)
    assert front, "front should not be empty"
    assert all(r.time <= fastest.time * 1.4 + 1e-6 for r in front)
    assert math.isclose(front[0].time, fastest.time, rel_tol=1e-9) or front[0].time >= fastest.time
    # no member dominates another
    for a, b in itertools.permutations(front, 2):
        assert not (a.time <= b.time and a.risk <= b.risk and (a.time, a.risk) != (b.time, b.risk))
    print(f"  Pareto with budget: {len(front)} routes, times "
          f"{front[0].time:.1f}..{front[-1].time:.1f}s, risks {front[0].risk:.1f}..{front[-1].risk:.1f}")


if __name__ == "__main__":
    for fn in [test_static_algorithms, test_search_effort, test_time_dependent,
               test_multi_source, test_pareto_exact, test_pareto_pruning_and_budget]:
        print(fn.__name__)
        fn()
    print("ALL ENGINE TESTS PASSED")
