"""Step 7 tests: facility coverage, per-origin access, nearest-edge lookup, closures.

Run from backend/:   python tests/test_access.py
"""
import json
import math
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import networkx as nx

import test_hazards as TH
from app.accessibility import compare_coverage, coverage, load_facilities, origin_access
from app.engine import INF, Landmarks
from app.graph import RoadGraph


def make_facilities(rg, folder: Path, seed=3, n=6):
    rng = random.Random(seed)
    g = rg.csr
    rows = []
    for i, node in enumerate(rng.sample(range(g.n), n)):
        kind = "hospital" if i < 4 else "shelter"
        rows.append({"id": f"{kind}-{i}", "name": f"{kind.title()} {i}", "kind": kind,
                     "lat": g.lat[node], "lon": g.lon[node]})
    path = folder / "facilities.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return load_facilities(rg, path)


def ref_graph(g, st):
    G = nx.DiGraph()
    G.add_nodes_from(range(g.n))
    for e in range(g.m):
        if st.mult[e] != INF:
            G.add_edge(g.tail[e], g.head[e], w=g.base_time[e] * st.mult[e])
    return G


def test_coverage_matches_reference(rg, facs):
    g = rg.csr
    st, _ = rg.scenario("flood")
    thr_min = 6.0
    for state in (None, st):
        sta = state or g.fresh_state()
        G = ref_graph(g, sta)
        G.add_node("S")
        for f in facs:
            G.add_edge(f.node, "S", w=0.0)      # reversed graph trick: use reverse + super source
        Gr = G.reverse(copy=True)
        ref = nx.single_source_dijkstra_path_length(Gr, "S", weight="w")
        ref.pop("S", None)
        within_ref = sum(1 for d in ref.values() if d <= thr_min * 60)
        cov = coverage(rg, facs, state, thr_min)
        assert cov["nodes_within_threshold"] == within_ref, (cov["nodes_within_threshold"], within_ref)
        assert cov["nodes_cannot_reach_any"] == g.n - len(ref)
        assert sum(cov["catchment_nodes"].values()) == within_ref
    print("  coverage matches a networkx reference (clean and flooded)")


def test_isolated_facility(rg, facs):
    g = rg.csr
    victim = facs[0]
    inbound = [g.r_edge[k] for k in range(g.r_indptr[victim.node], g.r_indptr[victim.node + 1])]
    st = g.fresh_state()
    st.close(inbound)
    out = compare_coverage(rg, facs, st, threshold_min=10)
    cut = {c["id"] for c in out["newly_cut_off"]}
    assert victim.id in cut, "facility with every inbound road closed must be flagged cut off"
    row = next(c for c in out["facilities"] if c["id"] == victim.id)
    assert row["catchment_scenario"] <= 1 < row["catchment_baseline"]
    assert out["pct_points_lost"] >= 0
    print(f"  isolated facility flagged; city coverage {out['baseline']['pct_within_threshold']}% -> "
          f"{out['scenario']['pct_within_threshold']}%")


def test_origin_access(rg, facs):
    g = rg.csr
    st, _ = rg.scenario("flood")
    rng = random.Random(9)
    G = ref_graph(g, st)
    for _ in range(6):
        o = rng.randrange(g.n)
        rows = origin_access(rg, o, facs, st, max_minutes=240)
        by_id = {r["id"]: r for r in rows}
        for f in facs:
            try:
                ref = nx.dijkstra_path_length(G, o, f.node, weight="w") / 60.0
            except nx.NetworkXNoPath:
                ref = None
            got = by_id[f.id]["minutes_scenario"]
            if ref is None:
                assert got is None and not by_id[f.id]["reachable"]
            else:
                assert got is not None and math.isclose(got, ref, abs_tol=0.01), (got, ref)
        assert [r["minutes_scenario"] is None for r in rows] == sorted(
            r["minutes_scenario"] is None for r in rows), "reachable rows should come first"
    print("  per-origin facility times match a networkx reference")


def test_nearest_edges_and_closures(rg):
    g = rg.csr
    e = 1234
    a, b = g.tail[e], g.head[e]
    lat = (g.lat[a] + g.lat[b]) / 2
    lon = (g.lon[a] + g.lon[b]) / 2
    near = rg.edges_near(lat, lon, 50, 4)
    assert near and near[0]["distance_m"] < 1.0
    assert near[0]["edge"] in (e, g.edge_between(b, a)), near[0]
    ids = near[0]["close_ids"]
    assert len(ids) == 2, "both directions should be offered for closing"

    base_state, _ = rg.scenario("flood")
    before = base_state.n_closed()
    st = rg.with_closures(base_state, ids)
    assert base_state.n_closed() == before, "closing must not modify the cached scenario"
    assert all(st.mult[x] == INF for x in ids)

    # a route that would use the closed road must avoid it
    from app.routing import run_algo
    s, t = a, b
    res = run_algo(rg, "bidir_alt", s, t, 0.0, st)
    assert res is not None and all(st.mult[x] != INF for x in res.edges)
    assert res.time >= g.base_time[ids[0]] - 1e-9
    try:
        rg.with_closures(None, [g.m + 5])
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range edge id should raise")
    print(f"  edges_near finds the clicked road ({near[0]['distance_m']} m), closure is a copy, "
          f"detour {res.time:.0f}s vs direct {g.base_time[ids[0]]:.0f}s")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "scenarios"
        TH.write_scenarios(folder)
        G = TH.synthetic_city()
        probe = RoadGraph(G, None, folder)
        lm = Path(d) / "lm.npz"
        Landmarks.build(probe.csr, k=6, seed=0).save(lm, probe.csr)
        rg = RoadGraph(G, lm, folder)
        facs = make_facilities(rg, Path(d))
        print(f"synthetic city: {rg.csr.n} nodes; {len(facs)} facilities")
        print("test_coverage_matches_reference");   test_coverage_matches_reference(rg, facs)
        print("test_isolated_facility");            test_isolated_facility(rg, facs)
        print("test_origin_access");                test_origin_access(rg, facs)
        print("test_nearest_edges_and_closures");   test_nearest_edges_and_closures(rg)
    print("ALL ACCESS TESTS PASSED")
