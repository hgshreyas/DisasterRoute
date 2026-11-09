"""Glue test: NetworkX graph -> RoadGraph -> routing helpers -> GeoJSON, without osmnx/fastapi.

Run from backend/:   python -I tests/test_glue.py
"""
import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import networkx as nx

from app.engine import INF, Landmarks
from app.graph import RoadGraph
from app.routing import ALGOS, pareto_alternatives, run_algo, to_dict


def grid_graph(rows=14, cols=14, seed=0):
    rng = random.Random(seed)
    G = nx.MultiDiGraph()
    for i in range(rows):
        for j in range(cols):
            G.add_node(i * cols + j, y=12.90 + i * 0.004, x=77.55 + j * 0.004)
    for i in range(rows):
        for j in range(cols):
            a = i * cols + j
            for b in ([a + 1] if j < cols - 1 else []) + ([a + cols] if i < rows - 1 else []):
                length = 400.0
                tt = length / (rng.uniform(15, 50) / 3.6)
                G.add_edge(a, b, length=length, travel_time=tt)
                G.add_edge(b, a, length=length, travel_time=tt * rng.uniform(0.9, 1.1))
    G.add_edge(0, 1, length=450.0, travel_time=999.0)   # slower parallel edge: must be ignored
    return G


def main():
    G = grid_graph()
    with tempfile.TemporaryDirectory() as d:
        # build + save + reload landmarks, exactly as the scripts do
        tmp = Path(d) / "lm.npz"
        probe = RoadGraph(G)
        Landmarks.build(probe.csr, k=6, seed=0).save(tmp, probe.csr)
        rg = RoadGraph(G, tmp)
    assert rg.landmarks is not None and len(rg.landmarks.ids) == 6
    g = rg.csr
    print(f"graph: {g.n} nodes, {g.m} edges (parallel edge collapsed: {g.m == G.number_of_edges() - 1})")

    s, s_d = rg.nearest_node(12.9001, 77.5502)
    t, t_d = rg.nearest_node(12.9519, 77.6019)
    assert s_d < 100 and t_d < 100

    results = {a: run_algo(rg, a, s, t) for a in ALGOS}
    base = results["dijkstra"].cost
    for a, r in results.items():
        assert math.isclose(r.cost, base, rel_tol=1e-9), (a, r.cost, base)
        d = to_dict(rg, r)
        assert d["geometry"]["type"] == "LineString" and len(d["geometry"]["coordinates"]) >= 2
        print(f"  {a:15s} time={d['travel_time_s']:8.1f}s length={d['length_m']:8.1f}m settled={d['settled_nodes']}")

    # Hazard scenario: a flood band across the middle of the direct corridor.
    st = g.state
    flooded = []
    for e in range(g.m):
        la = (g.lat[g.tail[e]] + g.lat[g.head[e]]) / 2
        lo = (g.lon[g.tail[e]] + g.lon[g.head[e]]) / 2
        if 12.930 < la < 12.940 and 77.560 < lo < 77.585:
            flooded.append(e)
    st.penalize(flooded, mult=1.0, risk=300.0)       # exposed but not slower: forces a real trade-off
    st.close(flooded[:3])                            # a few confirmed closures
    print(f"  flooded edges: {len(flooded)}, closed: {st.n_closed()}")

    fast = run_algo(rg, "bidir_alt", s, t, lam=0.0)
    safe = run_algo(rg, "bidir_alt", s, t, lam=0.5)
    ref = run_algo(rg, "dijkstra", s, t, lam=0.5)
    assert math.isclose(safe.cost, ref.cost, rel_tol=1e-9)
    assert all(st.mult[e] != INF for e in fast.edges + safe.edges)
    assert safe.risk <= fast.risk + 1e-9 and safe.time >= fast.time - 1e-9
    assert fast.risk > 0, "scenario should make the fastest route cross the flood zone"
    assert safe.risk < fast.risk, "risk-aware route should reduce exposure"
    print(f"  fastest: {fast.time:.0f}s risk={fast.risk:.0f} | risk-aware(lam=.5): {safe.time:.0f}s risk={safe.risk:.0f}")

    front, best = pareto_alternatives(rg, s, t, detour=1.6)
    assert front and best in front
    assert len(front) >= 2, "expected a genuine time-vs-exposure trade-off"
    print(f"  Pareto front has {len(front)} route(s):")
    for r in front:
        print(f"    time={r.time:7.1f}s risk={r.risk:7.1f}")
    print("GLUE TEST PASSED")


if __name__ == "__main__":
    main()
