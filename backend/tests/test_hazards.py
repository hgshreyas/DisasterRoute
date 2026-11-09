"""Step 3 tests: exposure geometry, scenario rules, time-dependent flood, baseline-vs-hazard.

Run from backend/:   python tests/test_hazards.py
Needs numpy, networkx, scipy only.
"""
import math
import random
import sys
import tempfile
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import networkx as nx
import numpy as np

import make_scenarios as MS
import test_engine as TE
from app.engine import INF, Landmarks, td_alt_astar
from app.exposure import EdgeGeometry, LocalProjection, polygon_exposure
from app.graph import RoadGraph
from app.hazards import build_state
from app.routing import compare_scenario, run_algo, time_dependent_route


# ------------------------------------------------------------------ 1. geometry vs analytic
def _geom(polylines):
    coords, offs, = [], [0]
    for pl in polylines:
        coords += pl
        offs.append(len(coords))
    return EdgeGeometry(np.array(coords, float), np.array(offs), np.ones(len(polylines)))


def _circle_xy(r, n=180):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([r * np.cos(a), r * np.sin(a)])


def test_exposure_analytic():
    eg = _geom([
        [(-1000, 0), (1000, 0)],                  # through the centre
        [(-1000, 500), (1000, 500)],              # off-centre chord
        [(3000, 3000), (4000, 3000)],             # far away
        [(0, 0), (1000, 0)],                      # starts inside the polygon
        [(-1000, -1000), (-500, 0), (1000, 0)],   # polyline bending in, then crossing
    ])
    idx, ex = polygon_exposure(eg, _circle_xy(600))
    got = dict(zip(idx.tolist(), ex.tolist()))
    assert math.isclose(got[0], 1200, rel_tol=2e-3), got[0]
    assert math.isclose(got[1], 2 * math.sqrt(600**2 - 500**2), rel_tol=2e-3), got[1]
    assert 2 not in got
    assert math.isclose(got[3], 600, rel_tol=2e-3), got[3]
    # polyline: from (-500,0) to (1000,0) the inside part is x in [-600,600] ∩ [-500,1000]
    # plus the first leg's brief clip of the circle near (-500,0)
    first_leg = got[4]
    assert first_leg > 1100 * 0.99, first_leg

    sq = np.array([(-100, -100), (100, -100), (100, 100), (-100, 100)], float)
    idx, ex = polygon_exposure(eg, sq)
    got = dict(zip(idx.tolist(), ex.tolist()))
    assert math.isclose(got[0], 200, rel_tol=1e-9)
    assert 1 not in got and 2 not in got
    assert math.isclose(got[3], 100, rel_tol=1e-9)
    print("  exposure geometry matches analytic chord lengths")


# ------------------------------------------------------------------ synthetic city around Silk Board
LAT0, LON0, STEP, N = 12.900, 77.595, 0.002, 30


def synthetic_city():
    G = nx.MultiDiGraph()
    for i in range(N):
        for j in range(N):
            G.add_node(i * N + j, y=LAT0 + i * STEP, x=LON0 + j * STEP)
    arterial_row = 9                                  # lat 12.918, passes ~35 m from Silk Board
    for i in range(N):
        for j in range(N):
            a = i * N + j
            for b, along_row in (([a + 1], True) if j < N - 1 else ([], False),) + \
                                (([a + N], False) if i < N - 1 else ([], False),):
                for bb in b:
                    length = 220.0 if along_row else 222.0
                    speed = 60.0 if (along_row and i == arterial_row) else 20.0
                    tt = length / (speed / 3.6)
                    G.add_edge(a, bb, length=length, travel_time=tt, highway="residential")
                    G.add_edge(bb, a, length=length, travel_time=tt, highway="residential")
    return G


def write_scenarios(folder: Path) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for name, fn in [("flood", MS.flood), ("flood_rising", MS.flood_rising),
                     ("wildfire", MS.wildfire), ("landslide", MS.landslide),
                     ("earthquake", MS.earthquake)]:
        (folder / f"{name}.geojson").write_text(json.dumps(fn()), encoding="utf-8")
        names.append(name)
    return names


def _midpoints_m(rg):
    g = rg.csr
    proj = rg.proj
    ax, ay = proj.xy([g.lat[t] for t in g.tail], [g.lon[t] for t in g.tail])
    bx, by = proj.xy([g.lat[h] for h in g.head], [g.lon[h] for h in g.head])
    return (ax + bx) / 2, (ay + by) / 2


def test_flood_rules(rg):
    st, summary = rg.scenario("flood")
    g, proj = rg.csr, rg.proj
    mx, my = _midpoints_m(rg)
    sites = [(proj.xy(lat, lon), r) for _, lat, lon, r in
             __import__("app.scenario_sites", fromlist=["x"]).FLOOD_SITES]

    def dist_to(site_xy):
        return np.hypot(mx - site_xy[0], my - site_xy[1])

    closed = [e for e in range(g.m) if st.mult[e] == INF]
    assert closed, "high-severity core should close some roads"
    for e in closed:
        assert any(dist_to(xy)[e] <= 0.3 * r + 3 for xy, r in sites), "closure outside a high core"

    near_any = np.min([dist_to(xy) - r for xy, r in sites], axis=0)   # <0 inside some outer circle
    for e in range(g.m):
        assert st.mult[e] >= 1.0 and st.risk[e] >= 0.0
        if near_any[e] > 125:       # midpoint > half an edge (111 m) outside every footprint
            assert st.mult[e] == 1.0 and st.risk[e] == 0.0, e
        assert st.risk[e] <= g.length[e] * 1.0001
    pen = sum(1 for e in range(g.m) if 1.0 < st.mult[e] < INF)
    assert pen > 0
    print(f"  flood: {len(closed)} closed, {pen} slowed, summary={summary['_totals']}")


def test_all_scenarios_build(rg, names):
    for n in names:
        st, summary = rg.scenario(n)
        assert all(m >= 1.0 for m in st.mult)
        assert all(om >= m for om, m in zip(st.onset_mult, st.mult)), "onset_mult must be >= mult"
        print(f"  {n:13s} {summary['_totals']}")
    assert rg.scenario("earthquake")[1]["_totals"]["closed_edges"] > 0
    assert rg.scenario("flood_rising")[1]["_totals"]["scheduled_edges"] > 0
    # sites outside this synthetic city must have no effect
    assert rg.scenario("wildfire")[1]["_totals"]["closed_edges"] == 0


def test_time_dependent(rg):
    g = rg.csr
    st, _ = rg.scenario("flood_rising")
    lm = rg.landmarks
    rng = random.Random(1)
    checked = 0
    for _ in range(40):
        s, t = rng.randrange(g.n), rng.randrange(g.n)
        res = td_alt_astar(g, s, t, lm, 0.0, st)
        ref = TE._td_reference(g, st, s, t, 0.0)
        assert (res is None) == (ref is None)
        if res:
            assert math.isclose(res.extra["arrival"], ref, rel_tol=1e-9, abs_tol=1e-9)
            late = td_alt_astar(g, s, t, lm, 3600.0, st)
            assert late.extra["arrival"] >= res.extra["arrival"] - 1e-9, "FIFO violated"
            checked += 1
    print(f"  time-dependent flood: {checked} routes match reference and respect FIFO")


def test_baseline_vs_hazard(rg):
    st, _ = rg.scenario("flood")
    s, _ = rg.nearest_node(12.9180, 77.5960)
    t, _ = rg.nearest_node(12.9180, 77.6500)
    out = compare_scenario(rg, s, t, st, lam=0.5, detour=2.5)
    base = out["baseline"]["under_hazard"]
    aware = out["hazard_aware"]
    assert base["blocked"], "the straight arterial should run into the closed flood core"
    assert aware is not None and aware["risk"] >= 0
    # the hazard-aware route never uses a closed edge
    res = run_algo(rg, "bidir_alt", s, t, 0.5, st)
    assert all(st.mult[e] != INF for e in res.edges)
    ref = run_algo(rg, "dijkstra", s, t, 0.5, st)
    assert math.isclose(res.cost, ref.cost, rel_tol=1e-9)
    print(f"  baseline {out['baseline']['travel_time_s']}s is BLOCKED under flood "
          f"({base['blocked_edges']} closed edges); hazard-aware {aware['travel_time_s']}s "
          f"(+{aware['travel_time_s'] - out['baseline']['travel_time_s']:.0f}s), "
          f"risk={aware['risk']}")
    print(f"  Pareto front: {[(r['travel_time_s'], r['risk']) for r in out['pareto_front']]}")


def test_replan(rg):
    from app.routing import replan
    s, _ = rg.nearest_node(12.9180, 77.5960)
    t, _ = rg.nearest_node(12.9180, 77.6500)
    for name in ("flood", "wildfire", "landslide", "earthquake"):
        st, _ = rg.scenario(name)
        for lam in (0.0, 0.5):
            out = replan(rg, s, t, None, st, lam)
            assert out, name
            assert out["same_cost_as_recompute"], (name, lam)
            print(f"  {name:10s} lam={lam}: rerouted={out['rerouted']!s:5} changed={out['changed_roads']:5d} "
                  f"D*Lite {out['dstar_repair']['expanded']:4d} nodes {out['dstar_repair']['ms']}ms | "
                  f"recompute {out['recompute']['expanded']:4d} nodes {out['recompute']['ms']}ms")
    # many trips on a grid with lots of equal costs (ties) - this once exposed a D* Lite stopping bug
    rng = random.Random(5)
    for name in ("flood", "earthquake", "flood_rising"):
        st, _ = rg.scenario(name)
        for _ in range(150):
            a, b = rng.randrange(rg.csr.n), rng.randrange(rg.csr.n)
            out = replan(rg, a, b, None, st, rng.choice((0.0, 0.5)))
            assert not out or out["same_cost_as_recompute"], (name, a, b)
    # undoing the hazard goes back to the original route
    st, _ = rg.scenario("flood")
    back = replan(rg, s, t, st, None, 0.5)
    assert back["same_cost_as_recompute"]
    # nothing changes -> nothing to repair
    same = replan(rg, s, t, st, st, 0.5)
    assert same["changed_roads"] == 0 and same["dstar_repair"]["expanded"] == 0 and not same["rerouted"]


if __name__ == "__main__":
    print("test_exposure_analytic")
    test_exposure_analytic()
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "scenarios"
        names = write_scenarios(folder)
        G = synthetic_city()
        probe = RoadGraph(G, None, folder)
        lm_path = Path(d) / "lm.npz"
        Landmarks.build(probe.csr, k=6, seed=0).save(lm_path, probe.csr)
        rg = RoadGraph(G, lm_path, folder)
        print(f"synthetic city: {rg.csr.n} nodes, {rg.csr.m} edges")
        print("test_flood_rules");           test_flood_rules(rg)
        print("test_all_scenarios_build");   test_all_scenarios_build(rg, names)
        print("test_time_dependent");        test_time_dependent(rg)
        print("test_baseline_vs_hazard");    test_baseline_vs_hazard(rg)
        print("test_replan");                test_replan(rg)
    print("ALL HAZARD TESTS PASSED")
