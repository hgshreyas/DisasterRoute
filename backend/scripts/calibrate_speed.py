"""Measure the typical trip speed on the road graph, to calibrate app/modes.py.

    python scripts/calibrate_speed.py

Picks random origin-destination pairs 3-12 km apart (straight line), routes each on the graph's own
free-flow times, and reports the average speed over those trips.  Put the printed value into
REF_FREEFLOW_KMH in app/modes.py.  (/health's mean_free_flow_kmh averages every road segment, so it is
pulled down by the many slow residential streets; trips use faster main roads.)
"""
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _km(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def main(rg=None, pairs=150, lo_km=3.0, hi_km=12.0, seed=1):
    from app.routing import run_algo
    if rg is None:
        from app.graph import get_graph
        rg = get_graph()
    g = rg.csr
    rng = random.Random(seed)
    speeds, tot_len, tot_t, tries = [], 0.0, 0.0, 0
    while len(speeds) < pairs and tries < pairs * 200:
        tries += 1
        s, t = rng.randrange(g.n), rng.randrange(g.n)
        if not lo_km <= _km(g.lat[s], g.lon[s], g.lat[t], g.lon[t]) <= hi_km:
            continue
        r = run_algo(rg, "bidir_alt", s, t, 0.0, g.fresh_state())
        if r is None or r.time <= 0:
            continue
        length = sum(g.length[e] for e in r.edges)
        speeds.append(length / r.time * 3.6)
        tot_len += length
        tot_t += r.time
    if not speeds:
        print("Could not find any trips; is the graph loaded?")
        return None
    mean = 3.6 * tot_len / tot_t
    print(f"{len(speeds)} trips, {lo_km:g}-{hi_km:g} km apart: average speed {mean:.1f} km/h "
          f"(median {statistics.median(speeds):.1f}, min {min(speeds):.1f}, max {max(speeds):.1f})")
    print(f"Set REF_FREEFLOW_KMH = {mean:.1f} in app/modes.py  (it is {__import__('app.modes', fromlist=['x']).REF_FREEFLOW_KMH} now)")
    return mean


if __name__ == "__main__":
    main()
