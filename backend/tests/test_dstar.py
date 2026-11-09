"""D* Lite must give the same route cost as planning from scratch after ANY sequence of cost changes.
Run from backend/:  python tests/test_dstar.py
"""
import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_hazards as TH
from app.engine import INF, DStarLite, Landmarks, changed_edges, dijkstra
from app.graph import RoadGraph


def same(a, b):
    return (a == INF and b == INF) or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def check_path(g, state, lam, p, start, goal, cost):
    assert p.nodes[0] == start and p.nodes[-1] == goal, (p.nodes[0], p.nodes[-1], start, goal)
    for e, u, v in zip(p.edges, p.nodes, p.nodes[1:]):
        assert g.tail[e] == u and g.head[e] == v, "path is not contiguous"
    total = sum(g.base_time[e] * state.mult[e] + lam * state.risk[e] for e in p.edges)
    assert same(total, cost), (total, cost)


def mutate(g, state, rng):
    """A random batch of hazard changes: close, slow, add exposure, or restore roads."""
    new = state.copy()
    for e in rng.sample(range(g.m), rng.randint(1, 40)):
        r = rng.random()
        if r < 0.25:
            new.mult[e] = INF
        elif r < 0.55:
            new.mult[e] = 1.0 + rng.random() * 5
            new.risk[e] = rng.random() * 50
        elif r < 0.7:
            new.risk[e] = rng.random() * 100
        else:                                                  # hazard goes away
            new.mult[e], new.risk[e] = 1.0, 0.0
    return new


def main():
    with tempfile.TemporaryDirectory() as d:
        rg = RoadGraph(TH.synthetic_city(), None, Path(d))
        g = rg.csr
        lm = Landmarks.build(g, k=6, seed=0)
        rng = random.Random(7)
        n = g.n
        print(f"  synthetic city: {n} nodes, {g.m} roads")

        # ---- 1. first search equals Dijkstra, with and without landmarks
        for lam in (0.0, 0.5):
            for use_lm in (True, False):
                for _ in range(15):
                    s, t = rng.randrange(n), rng.randrange(n)
                    base = g.fresh_state()
                    ds = DStarLite(g, base, s, t, lam, lm if use_lm else None)
                    ds.compute()
                    ref = dijkstra(g, s, t, lam, base)
                    assert same(ds.cost(), ref.cost if ref else INF), (s, t, ds.cost(), ref and ref.cost)
                    if ref and s != t:
                        check_path(g, base, lam, ds.path(), s, t, ds.cost())
        print("  first search = Dijkstra (with and without landmarks)")

        # ---- 2. repeated changes: closures, slowdowns, exposure, restorations
        rounds = ratio_n = 0
        ratio_sum = 0.0
        for lam in (0.0, 0.5):
            for _ in range(6):
                s, t = rng.randrange(n), rng.randrange(n)
                if s == t:
                    continue
                state = g.fresh_state()
                ds = DStarLite(g, state, s, t, lam, lm)
                ds.compute()
                for _ in range(25):
                    new = mutate(g, state, rng)
                    stats = ds.update_costs(new, changed_edges(state, new))
                    state = new
                    ref = dijkstra(g, s, t, lam, state)
                    assert same(ds.cost(), ref.cost if ref else INF), (s, t, lam, ds.cost(), ref and ref.cost)
                    p = ds.path()
                    assert (p is None) == (ref is None)
                    if p:
                        check_path(g, state, lam, p, s, t, ds.cost())
                    if ref:
                        ratio_sum += stats["expanded"] / max(1, ref.settled)
                        ratio_n += 1
                    rounds += 1
        print(f"  {rounds} random change batches all gave Dijkstra's cost; "
              f"D* Lite expanded {100 * ratio_sum / ratio_n:.0f}% as many nodes as a fresh Dijkstra on average")

        # ---- 3. goal cut off, then reachable again
        s, t = 0, n - 1
        state = g.fresh_state()
        ds = DStarLite(g, state, s, t, 0.5, lm)
        ds.compute()
        into_goal = [g.r_edge[k] for k in range(g.r_indptr[t], g.r_indptr[t + 1])]
        cut = state.copy()
        for e in into_goal:
            cut.mult[e] = INF
        ds.update_costs(cut)
        assert ds.cost() == INF and ds.path() is None and dijkstra(g, s, t, 0.5, cut) is None
        ds.update_costs(state)                                   # roads reopen
        assert same(ds.cost(), dijkstra(g, s, t, 0.5, state).cost)
        print("  goal cut off -> unreachable; reopened -> route found again")

        # ---- 4. the vehicle moves along the route while hazards change
        checked = 0
        for _ in range(6):
            s, t = rng.randrange(n), rng.randrange(n)
            if s == t:
                continue
            state = g.fresh_state()
            ds = DStarLite(g, state, s, t, 0.5, lm)
            ds.compute()
            for _ in range(12):
                p = ds.path()
                if p is None or len(p.nodes) < 3:
                    break
                ds.move_to(p.nodes[min(len(p.nodes) - 1, rng.randint(1, 4))])
                new = mutate(g, state, rng)
                ds.update_costs(new, changed_edges(state, new))
                state = new
                ref = dijkstra(g, state, ds.start, t, 0.5) if False else dijkstra(g, ds.start, t, 0.5, state)
                assert same(ds.cost(), ref.cost if ref else INF), (ds.start, t, ds.cost(), ref and ref.cost)
                checked += 1
        print(f"  moving start: {checked} replans from the vehicle's new position matched Dijkstra")

        # ---- 5. a small local change is cheap; no change costs almost nothing
        s, t = 0, n - 1
        state = g.fresh_state()
        ds = DStarLite(g, state, s, t, 0.5, lm)
        first = ds.compute()["expanded"]
        same_state = state.copy()
        assert ds.update_costs(same_state)["expanded"] == 0
        p = ds.path()
        local = state.copy()
        local.mult[p.edges[len(p.edges) // 2]] = INF              # close one road on the route
        redo = ds.update_costs(local)["expanded"]
        fresh = dijkstra(g, s, t, 0.5, local).settled
        assert same(ds.cost(), dijkstra(g, s, t, 0.5, local).cost)
        print(f"  close one road on the route: first search {first} nodes, repair {redo}, fresh Dijkstra {fresh}")
        assert redo < fresh
    print("ALL D* LITE TESTS PASSED")


if __name__ == "__main__":
    main()
