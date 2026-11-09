"""Rerouting-speed benchmark: D* Lite repair versus planning again from scratch.

    python scripts/bench_replan.py            (about 1-3 minutes on the Bengaluru graph)

For random trips 3-12 km apart it starts from normal conditions, then applies
  * each saved scenario (flood, wildfire, landslide, earthquake, ...), and
  * "one road on the planned route closes"  (the live-rerouting case),
and reports, per case, the median search effort (nodes expanded) and time of
  D* Lite repair   vs   a fresh bidirectional ALT search,
plus the share of trips where both gave the same cost (must be 100%) and where the route changed.
Finding which roads changed costs O(roads) and is reported on its own line.
"""
import math
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _km(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def trips(g, n, lo_km, hi_km, rng):
    out, tries = [], 0
    while len(out) < n and tries < n * 300:
        tries += 1
        s, t = rng.randrange(g.n), rng.randrange(g.n)
        if lo_km <= _km(g.lat[s], g.lon[s], g.lat[t], g.lon[t]) <= hi_km:
            out.append((s, t))
    return out


def live_drive(rg, pairs, lam, closures=4):
    """A vehicle drives its route; every ~20% of the way a road AHEAD on its route closes.

    D* Lite keeps one search alive (start moves, only the closed road is reported as changed);
    the alternative plans from the vehicle's current position with a fresh bidirectional ALT search.
    """
    from time import perf_counter
    from app.engine import DStarLite, bidirectional_alt
    g = rg.csr
    d_ms, f_ms, d_nodes, f_nodes, ok, total = [], [], [], [], 0, 0
    for s, t in pairs:
        state = g.fresh_state()
        ds = DStarLite(g, state, s, t, lam, rg.landmarks)
        ds.compute()
        path = ds.path()
        if path is None or len(path.edges) < 12:
            continue
        for _ in range(closures):
            path = ds.path()
            if path is None or len(path.edges) < 8:
                break
            step = max(1, len(path.edges) // 5)
            ds.move_to(path.nodes[step])                              # vehicle advances
            ahead = path.edges[step:]
            if len(ahead) < 3:
                break
            e = ahead[min(len(ahead) - 1, max(1, len(ahead) // 4))]  # a road shortly ahead closes
            state = rg.with_closures(state, [e])
            t0 = perf_counter(); r = ds.update_costs(state, [e]); dm = (perf_counter() - t0) * 1000
            t0 = perf_counter(); fr = bidirectional_alt(g, ds.start, t, rg.landmarks, lam, state); fm = (perf_counter() - t0) * 1000
            dp = ds.path()
            total += 1
            same = (fr is None and dp is None) or (fr is not None and dp is not None and abs(fr.cost - dp.cost) <= 1e-6 * (1 + fr.cost))
            ok += same
            d_ms.append(dm); f_ms.append(fm); d_nodes.append(r["expanded"]); f_nodes.append(fr.settled if fr else 0)
            if dp is None:
                break
    if total:
        print(f"\nVehicle driving with {closures} successive closures ahead ({total} replans over {len(d_ms)} updates)")
        print(f"  D* Lite  : median {st.median(d_nodes):.0f} nodes, {st.median(d_ms):.1f} ms, mean {st.fmean(d_ms):.1f} ms")
        print(f"  fresh ALT: median {st.median(f_nodes):.0f} nodes, {st.median(f_ms):.1f} ms, mean {st.fmean(f_ms):.1f} ms")
        print(f"  same cost as fresh search: {ok}/{total}")


def main(rg=None, n=40, lo_km=3.0, hi_km=12.0, lam=0.5, seed=7):
    from app.routing import replan, run_algo
    if rg is None:
        from app.graph import get_graph
        rg = get_graph()
    g = rg.csr
    rng = random.Random(seed)
    pairs = trips(g, n, lo_km, hi_km, rng)
    cases = [(name, rg.scenario(name)[0]) for name in rg.scenario_names()]
    rows = []
    for name, state in cases + [("one road on route closes", None)]:
        eff_d, eff_f, ms_d, ms_f, det, same, rer, used = [], [], [], [], [], 0, 0, 0
        for s, t in pairs:
            after = state
            if state is None:                              # close the middle road of the current best route
                r = run_algo(rg, "bidir_alt", s, t, lam, g.fresh_state())
                if r is None or len(r.edges) < 3:
                    continue
                after = rg.with_closures(None, [r.edges[len(r.edges) // 2]])
            out = replan(rg, s, t, None, after, lam)
            if not out:
                continue
            used += 1
            eff_d.append(out["dstar_repair"]["expanded"]); eff_f.append(out["recompute"]["expanded"])
            ms_d.append(out["dstar_repair"]["ms"]); ms_f.append(out["recompute"]["ms"])
            det.append(out["change_detection_ms"])
            same += out["same_cost_as_recompute"]; rer += out["rerouted"]
        if used:
            rows.append((name, used, st.median(eff_d), st.median(eff_f), st.median(ms_d), st.median(ms_f),
                         st.median(det), same, rer))
    print(f"\n{'case':28s}{'trips':>6s}{'D* nodes':>10s}{'fresh nodes':>12s}{'D* ms':>9s}{'fresh ms':>10s}{'same cost':>11s}{'rerouted':>10s}")
    for name, used, nd, nf, md, mf, dt, same, rer in rows:
        print(f"{name:28s}{used:6d}{nd:10.0f}{nf:12.0f}{md:9.1f}{mf:10.1f}{same:>8d}/{used:<3d}{rer:>7d}/{used}")
    if rows:
        print(f"\nChange detection (comparing two states, O(roads)): median {st.median(r[6] for r in rows):.1f} ms. "
              "A live system is told which roads changed, so this is excluded from the D* column.")
    live_drive(rg, pairs[:25], lam)
    bad = [r for r in rows if r[7] != r[1]]
    print("All repaired costs equal the fresh search." if not bad else f"MISMATCH in: {[r[0] for r in bad]}")


if __name__ == "__main__":
    main()
