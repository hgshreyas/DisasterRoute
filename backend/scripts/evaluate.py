"""Evaluation: hazard-blind baseline vs the proposed hazard-aware routing, per hazard scenario.

    python scripts/evaluate.py                       # flood, wildfire, landslide, earthquake
    python scripts/evaluate.py --trips 500 --threshold 10 --sweep 0,0.5,2,5 --scenarios flood,custom

For random trips 3-12 km apart (same trips in every scenario) it measures
  1. hazard exposure   - baseline route (shortest time on normal roads, then driven under the hazard)
                         vs hazard-aware route; share of trips whose baseline route is blocked/exposed.
  2. travel-time cost  - extra minutes the hazard-aware route takes (car, normal traffic) compared with
                         the baseline's normal-conditions time.
  3. rerouting speed   - D* Lite repair vs fresh bidirectional ALT, from normal conditions to the scenario.
  4. facility access   - share of the road network within 30 min of a hospital / shelter, before vs after.
Results are printed and saved to results/evaluation.md and results/evaluation.json.
"""
import argparse
import json
import math
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ("flood", "wildfire", "landslide", "earthquake")


def _km(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def _med(xs):
    return st.median(xs) if xs else float("nan")


def _mean(xs):
    return st.fmean(xs) if xs else float("nan")


def pick_trips(g, n, lo_km, hi_km, seed):
    rng, out, tries = random.Random(seed), [], 0
    while len(out) < n and tries < n * 300:
        tries += 1
        s, t = rng.randrange(g.n), rng.randrange(g.n)
        if lo_km <= _km(g.lat[s], g.lon[s], g.lat[t], g.lon[t]) <= hi_km:
            out.append((s, t))
    return out


def evaluate_scenario(rg, name, trips, lam, facs, thr=30.0):
    from app.accessibility import compare_coverage
    from app.modes import get_model
    from app.routing import evaluate_edges, replan, run_algo
    g = rg.csr
    car = get_model("car", "normal")
    clean = g.fresh_state()
    state = rg.scenario(name)[0]
    n = blocked = exposed_trips = unreachable = 0
    risk_b, risk_a, extra_min, extra_pct, d_nodes, f_nodes, d_ms, f_ms, same = [], [], [], [], [], [], [], [], 0
    for s, t in trips:
        base = run_algo(rg, "bidir_alt", s, t, 0.0, clean)
        if base is None:
            continue
        n += 1
        ev = evaluate_edges(rg, state, base.edges)
        hit = ev["blocked"] or ev["exposed_edges"] > 0
        aware = run_algo(rg, "bidir_alt", s, t, lam, state)
        if ev["blocked"]:
            blocked += 1
        if aware is None:
            unreachable += 1
        if hit:
            exposed_trips += 1
            if aware is not None:
                if not ev["blocked"]:
                    risk_b.append(ev["risk"])
                    risk_a.append(aware.risk)
                extra_min.append((car.trip(aware.time) - car.trip(base.time)) / 60.0)
                extra_pct.append(100.0 * (aware.time / base.time - 1.0))
        out = replan(rg, s, t, None, state, lam)
        if out:
            d_nodes.append(out["dstar_repair"]["expanded"]); f_nodes.append(out["recompute"]["expanded"])
            d_ms.append(out["dstar_repair"]["ms"]); f_ms.append(out["recompute"]["ms"])
            same += out["same_cost_as_recompute"]
    row = {
        "scenario": name, "trips": n,
        "baseline_blocked_pct": round(100 * blocked / n, 1) if n else None,
        "baseline_hit_pct": round(100 * exposed_trips / n, 1) if n else None,
        "aware_unreachable_pct": round(100 * unreachable / n, 1) if n else None,
        "affected_trips": exposed_trips,
        "mean_exposure_baseline": round(_mean(risk_b), 2), "mean_exposure_aware": round(_mean(risk_a), 2),
        "exposure_reduction_pct": round(100 * (1 - sum(risk_a) / sum(risk_b)), 1) if risk_b and sum(risk_b) > 0 else None,
        "median_extra_min": round(_med(extra_min), 2), "mean_extra_min": round(_mean(extra_min), 2),
        "median_extra_pct": round(_med(extra_pct), 1),
        "dstar_nodes": _med(d_nodes), "fresh_nodes": _med(f_nodes),
        "dstar_ms": round(_med(d_ms), 2), "fresh_ms": round(_med(f_ms), 2),
        "replan_same_cost": f"{same}/{len(d_nodes)}",
    }
    for kind in ("hospital", "shelter"):
        fl = [f for f in facs if f.kind == kind]
        if not fl:
            continue
        graph_min = max(0.1, (thr * 60.0 - car.fixed_s) / car.k / 60.0)   # real minutes -> free-flow graph minutes
        c = compare_coverage(rg, fl, state, graph_min)
        row[f"{kind}_pct_normal"] = c["baseline"]["pct_within_threshold"]
        row[f"{kind}_pct_scenario"] = c["scenario"]["pct_within_threshold"]
        row[f"{kind}_cut_off"] = len(c["newly_cut_off"])
    return row


def sweep_scenario(rg, name, trips, lams):
    """Time-versus-exposure trade-off: the same affected trips routed with different lambda."""
    from app.modes import get_model
    from app.routing import evaluate_edges, run_algo
    g = rg.csr
    car = get_model("car", "normal")
    clean, state = g.fresh_state(), rg.scenario(name)[0]
    affected = []
    for s, t in trips:
        base = run_algo(rg, "bidir_alt", s, t, 0.0, clean)
        if base is None:
            continue
        ev = evaluate_edges(rg, state, base.edges)
        if (ev["blocked"] or ev["exposed_edges"] > 0):
            affected.append((s, t, base, ev))
    rows = []
    for lam in lams:
        risk, extra, lost = [], [], 0
        for s, t, base, ev in affected:
            r = run_algo(rg, "bidir_alt", s, t, lam, state)
            if r is None:
                lost += 1
                continue
            risk.append(r.risk)
            extra.append((car.trip(r.time) - car.trip(base.time)) / 60.0)
        rows.append({"scenario": name, "lam": lam, "affected": len(affected),
                     "mean_exposure": round(_mean(risk), 2), "mean_extra_min": round(_mean(extra), 2),
                     "unreachable": lost})
    return rows


def markdown(rows, lam, n_trips, thr=30.0, sweep=None):
    f = lambda v: "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else v
    L = [f"# Evaluation: hazard-blind baseline vs hazard-aware routing\n",
         f"{n_trips} random trips 3-12 km apart (same trips in every scenario), lambda = {lam}. "
         "Times are car, normal traffic. Exposure = metres of road inside hazard zones weighted by severity.\n",
         "## 1. Hazard exposure\n",
         "| Scenario | Trips | Baseline route blocked | Baseline route hit by hazard | Hazard-aware unreachable | Mean exposure, baseline | Mean exposure, aware | Reduction |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {r['trips']} | {f(r['baseline_blocked_pct'])}% | {f(r['baseline_hit_pct'])}% | "
                 f"{f(r['aware_unreachable_pct'])}% | {f(r['mean_exposure_baseline'])} | {f(r['mean_exposure_aware'])} | "
                 f"{f(r['exposure_reduction_pct'])}{'%' if r['exposure_reduction_pct'] is not None else ''} |")
    L += ["\nExposure columns use trips whose baseline route is hit but not blocked (a blocked route cannot be driven).\n",
          "## 2. Travel-time trade-off (trips whose baseline route was hit)\n",
          "| Scenario | Affected trips | Median extra minutes | Mean extra minutes | Median extra % (graph time) |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {r['affected_trips']} | {f(r['median_extra_min'])} | {f(r['mean_extra_min'])} | {f(r['median_extra_pct'])} |")
    L += ["\n## 3. Rerouting speed (normal conditions to scenario, median per trip)\n",
          "| Scenario | D* Lite nodes | Fresh ALT nodes | D* Lite ms | Fresh ALT ms | Same cost |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {f(r['dstar_nodes'])} | {f(r['fresh_nodes'])} | {r['dstar_ms']} | {r['fresh_ms']} | {r['replan_same_cost']} |")
    L += [f"\n## 4. Facility access (share of road network within {thr:g} real car minutes of a facility)\n",
          "| Scenario | Hospitals normal | Hospitals in scenario | Hospitals cut off | Shelters normal | Shelters in scenario | Shelters cut off |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scenario']} | {f(r.get('hospital_pct_normal'))}% | {f(r.get('hospital_pct_scenario'))}% | {f(r.get('hospital_cut_off'))} | "
                 f"{f(r.get('shelter_pct_normal'))}% | {f(r.get('shelter_pct_scenario'))}% | {f(r.get('shelter_cut_off'))} |")
    if sweep:
        L += ["\n## 5. Time versus exposure as lambda grows (trips whose baseline route was hit or blocked)\n",
              "| Scenario | lambda | Affected trips | Mean exposure | Mean extra minutes | Unreachable |", "|---|---|---|---|---|---|"]
        for r in sweep:
            L.append(f"| {r['scenario']} | {r['lam']:g} | {r['affected']} | {f(r['mean_exposure'])} | {f(r['mean_extra_min'])} | {r['unreachable']} |")
    return "\n".join(L) + "\n"


def main(rg=None, trips=60, lo_km=3.0, hi_km=12.0, lam=0.5, scenarios=None, seed=11, out_dir=None, thr=30.0, sweep=None, save_db=False):
    if rg is None:
        from app.graph import get_graph
        rg = get_graph()
    names = scenarios or [s for s in DEFAULT if s in rg.scenario_names()]
    try:
        facs = rg.facilities()
    except FileNotFoundError:
        facs = []
    pairs = pick_trips(rg.csr, trips, lo_km, hi_km, seed)
    rows = []
    for name in names:
        print(f"evaluating {name} ...", flush=True)
        rows.append(evaluate_scenario(rg, name, pairs, lam, facs, thr))
    sw = []
    for name in names if sweep else []:
        print(f"lambda sweep {name} ...", flush=True)
        sw += sweep_scenario(rg, name, pairs, sweep)
    md = markdown(rows, lam, len(pairs), thr, sw)
    print("\n" + md)
    out = Path(out_dir) if out_dir else ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "evaluation.md").write_text(md, encoding="utf-8")
    (out / "evaluation.json").write_text(json.dumps({"scenarios": rows, "lambda_sweep": sw}, indent=2), encoding="utf-8")
    print(f"saved {out / 'evaluation.md'} and evaluation.json")
    if save_db:
        from app import db
        st = db.get_store()
        if st is None:
            print("--save-db: no database configured (set DATABASE_URL and install psycopg); results kept in files only")
        else:
            run_id = st.save_evaluation({"trips": len(pairs), "lam": lam, "threshold_min": thr, "seed": seed,
                                         "scenarios": names}, rows, sw)
            print(f"saved to PostgreSQL as evaluation run {run_id}  (GET /results/evaluations/{run_id})")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trips", type=int, default=60)
    ap.add_argument("--lam", type=float, default=0.5)
    ap.add_argument("--scenarios", default="")
    ap.add_argument("--save-db", action="store_true", help="also store the results in PostgreSQL")
    ap.add_argument("--sweep", default="", help="comma list of lambdas for the trade-off table, e.g. 0,0.5,2,5")
    ap.add_argument("--threshold", type=float, default=30.0, help="facility-access threshold, minutes")
    a = ap.parse_args()
    main(trips=a.trips, lam=a.lam, scenarios=[x for x in a.scenarios.split(",") if x] or None, thr=a.threshold, save_db=a.save_db,
         sweep=[float(x) for x in a.sweep.split(",") if x] or None)
