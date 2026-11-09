"""The API with PostgreSQL attached: scenario create/delete are mirrored, route comparisons are logged,
results are served; and with no database everything else still works.

Run from backend/:   python tests/test_db_api.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fake_fastapi

fake_fastapi.install()
import uvicorn

import app.main as M
import test_db as TDB
import test_hazards as TH
from app import db
from app.engine import Landmarks
from app.graph import RoadGraph

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"


def call(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_for(fn, what, secs=10):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + what)


def main():
    bindir = TDB.find_bin()
    if not bindir:
        print("SKIPPED: no PostgreSQL server binaries found")
        return
    srv = TDB.Server(bindir)
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "scenarios"
        TH.write_scenarios(folder)
        G = TH.synthetic_city()
        probe = RoadGraph(G, None, folder)
        lm = Path(d) / "lm.npz"
        Landmarks.build(probe.csr, k=6, seed=0).save(lm, probe.csr)
        rg = RoadGraph(G, lm, folder)
        M.get_graph = lambda: rg
        server = uvicorn.Server(uvicorn.Config(fake_fastapi.build_asgi(M.app), host="127.0.0.1", port=PORT, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        q = urllib.parse.urlencode({"origin_lat": 12.9180, "origin_lon": 77.5960, "dest_lat": 12.9180, "dest_lon": 77.6500,
                                    "scenario": "flood", "lam": 0.5, "detour": 2.5, "mode": "car", "traffic": "normal"})
        try:
            # ---- no database configured
            db.set_store(None)
            code, js = call("/db/status")
            assert code == 200 and js["enabled"] is False, js
            code, js = call("/compare-scenario?" + q)
            assert code == 200 and js["baseline"], js
            assert call("/results/queries")[0] == 503
            code, js = call("/scenarios", "POST", {"name": "nodb", "features": [
                {"shape": "circle", "lat": 12.918, "lon": 77.62, "radius_m": 300, "hazard": "flood", "severity": "high"}]})
            assert code == 201, js
            print("  without a database: routing and scenario creation work, results endpoints say 503")

            # ---- with PostgreSQL
            srv.start()
            st = db.Store(srv.connect())
            st.init_schema()
            db.set_store(st)
            code, js = call("/db/status")
            assert js["enabled"] and js["connected"] and js["rows"]["route_queries"] == 0, js
            code, js = call("/scenarios", "POST", {"name": "mine", "features": [
                {"shape": "circle", "lat": 12.918, "lon": 77.62, "radius_m": 300, "hazard": "flood", "severity": "high", "name": "Test"}]})
            assert code == 201
            assert any(s["name"] == "mine" and s["zones"] == 1 for s in st.list_scenarios()), st.list_scenarios()
            code, js = call("/compare-scenario?" + q)
            assert code == 200
            qs = wait_for(lambda: call("/results/queries")[1]["queries"], "the route query to be logged")
            row = qs[0]
            assert row["scenario"] == "flood" and row["mode"] == "car" and row["lam"] == 0.5
            assert abs(row["baseline_s"] - js["baseline"]["travel_time_s"]) < 0.11, (row, js["baseline"])
            assert row["baseline_blocked"] is True and abs(row["aware_s"] - js["hazard_aware"]["travel_time_s"]) < 0.11
            print(f"  scenario mirrored to PostgreSQL; comparison logged: baseline blocked={row['baseline_blocked']}, "
                  f"aware {row['aware_s']} s")
            code, js = call("/scenarios/mine", "DELETE")
            assert code == 200 and not any(s["name"] == "mine" for s in st.list_scenarios())
            rid = st.save_evaluation({"trips": 10, "lam": 0.5, "threshold_min": 10}, [{"scenario": "flood", "x": 1}])
            assert call("/results/evaluations")[1]["runs"][0]["id"] == rid
            assert call(f"/results/evaluations/{rid}")[1]["results"][0]["x"] == 1
            assert call("/results/evaluations/9999")[0] == 404
            print("  delete mirrored; evaluation results served by the API")

            # ---- database goes down mid-run
            db.set_store(db.Store(lambda: (_ for _ in ()).throw(ConnectionError("down"))))
            code, js = call("/compare-scenario?" + q)
            assert code == 200 and js["baseline"], "routing must not depend on the database"
            code, js = call("/db/status")
            assert js["enabled"] and js["connected"] is False, js
            assert call("/results/queries")[0] == 503
            print("  database down: routing still works, status reports not connected")
        finally:
            server.should_exit = True
            srv.stop()
            db.set_store(None)
    print("DATABASE API TEST PASSED")


if __name__ == "__main__":
    main()
