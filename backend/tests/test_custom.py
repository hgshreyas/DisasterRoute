"""Runtime hazard creation: a user-made hazard must take effect immediately and safely.

Run from backend/:   python tests/test_custom.py
"""
import math
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import test_hazards as TH
from app.engine import INF, Landmarks
from app.graph import RoadGraph
from app.routing import compare_scenario, run_algo
from app.scenario_store import create_scenario, delete_scenario, scenario_details


def expect(exc_type, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc_type as e:
        return str(e)
    raise AssertionError(f"expected {exc_type.__name__}")


def main():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "scenarios"
        TH.write_scenarios(folder)                      # the built-in demo scenarios
        G = TH.synthetic_city()
        probe = RoadGraph(G, None, folder)
        lm = Path(d) / "lm.npz"
        Landmarks.build(probe.csr, k=6, seed=0).save(lm, probe.csr)
        rg = RoadGraph(G, lm, folder)
        g = rg.csr

        # 1. Create a hazard that did not exist before, via the circle shorthand.
        out = create_scenario(rg, "my-flood", [
            {"shape": "circle", "lat": 12.9180, "lon": 77.6226, "radius_m": 400,
             "hazard": "flood", "severity": "high", "name": "Test flood"}])
        st, summary = rg.scenario("my-flood")
        assert summary["_totals"]["closed_edges"] > 0, summary
        assert out["geojson"]["created_by"] == "user"
        assert "my-flood" in rg.scenario_names()
        print(f"  created 'my-flood': {summary['_totals']}")

        # 2. It changes routing immediately: the arterial through it is now blocked.
        s, _ = rg.nearest_node(12.9180, 77.5960)
        t, _ = rg.nearest_node(12.9180, 77.6500)
        cmp_ = compare_scenario(rg, s, t, st, lam=0.5, detour=3.0)
        assert cmp_["baseline"]["under_hazard"]["blocked"]
        aware = run_algo(rg, "bidir_alt", s, t, 0.5, st)
        assert all(st.mult[e] != INF for e in aware.edges)
        print(f"  routing reacts: baseline blocked, detour {cmp_['hazard_aware']['travel_time_s']}s")

        # 3. A polygon given as GeoJSON works too, and time-dependent onset is kept.
        ring = [[77.600, 12.905], [77.612, 12.905], [77.612, 12.915], [77.600, 12.915], [77.600, 12.905]]
        out2 = create_scenario(rg, "poly-rising", [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
             "properties": {"hazard": "flood", "severity": "high", "onset_min": 20}}])
        assert out2["summary"]["_totals"]["scheduled_edges"] > 0
        assert out2["summary"]["_totals"]["closed_edges"] == 0     # nothing closed until t=20min

        # 4. Validation: every bad input is rejected with a clear message, nothing saved.
        before = set(rg.scenario_names())
        bad = [
            ("bad name", [{"shape": "circle", "lat": 12.9, "lon": 77.6, "radius_m": 100,
                           "hazard": "flood", "severity": "high"}]),
            ("ok-name", []),
            ("ok-name", [{"shape": "circle", "lat": 12.9, "lon": 77.6, "radius_m": 100,
                          "hazard": "tornado"}]),
            ("ok-name", [{"shape": "circle", "lat": 12.9, "lon": 77.6, "radius_m": 100,
                          "hazard": "flood", "severity": "extreme"}]),
            ("ok-name", [{"shape": "circle", "lat": 12.9, "lon": 77.6, "radius_m": 99999999,
                          "hazard": "flood", "severity": "low"}]),
            ("ok-name", [{"shape": "circle", "lat": 95, "lon": 77.6, "radius_m": 100,
                          "hazard": "flood", "severity": "low"}]),
            ("ok-name", [{"shape": "square", "lat": 12.9, "lon": 77.6, "radius_m": 100,
                          "hazard": "flood", "severity": "low"}]),
            ("ok-name", [{"shape": "circle", "lat": 12.9, "lon": 77.6, "radius_m": 100,
                          "hazard": "wildfire"}]),
            ("ok-name", [{"shape": "circle", "lat": 12.9, "lon": 77.6, "radius_m": 100,
                          "hazard": "flood", "severity": "low", "onset_min": -5}]),
            ("none", [{"shape": "circle", "lat": 12.9, "lon": 77.6, "radius_m": 100,
                       "hazard": "flood", "severity": "low"}]),
        ]
        for name, items in bad:
            expect(ValueError, create_scenario, rg, name, items)
        assert set(rg.scenario_names()) == before, "failed requests must not save anything"
        print(f"  {len(bad)} invalid requests rejected, nothing saved")

        # 5. Name clashes: built-ins are protected; user scenarios need overwrite=True.
        good = [{"shape": "circle", "lat": 12.918, "lon": 77.62, "radius_m": 300,
                 "hazard": "flood", "severity": "medium"}]
        expect(PermissionError, create_scenario, rg, "flood", good, True)
        expect(FileExistsError, create_scenario, rg, "my-flood", good)
        create_scenario(rg, "my-flood", good, overwrite=True)
        assert rg.scenario("my-flood")[1]["_totals"]["closed_edges"] == 0   # medium: slows, no closure

        # 6. Delete: user scenarios yes, built-ins no.
        expect(PermissionError, delete_scenario, rg, "flood")
        delete_scenario(rg, "my-flood")
        assert "my-flood" not in rg.scenario_names()
        expect(KeyError, delete_scenario, rg, "my-flood")
        expect(KeyError, rg.scenario, "my-flood")
        details = {x["name"]: x["user_created"] for x in scenario_details(rg)}
        assert details["flood"] is False and details["poly-rising"] is True
        print("  built-ins protected; user scenarios can be replaced and deleted")

        # 7. Saved file is reloaded correctly by a fresh RoadGraph (survives a restart).
        rg2 = RoadGraph(G, lm, folder)
        assert math.isclose(rg2.scenario("poly-rising")[1]["_totals"]["scheduled_edges"],
                            out2["summary"]["_totals"]["scheduled_edges"])
    print("ALL CUSTOM-HAZARD TESTS PASSED")


if __name__ == "__main__":
    main()
