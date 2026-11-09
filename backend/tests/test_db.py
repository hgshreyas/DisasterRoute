"""Database test against a REAL PostgreSQL server (started and stopped here).

Run from backend/:   python tests/test_db.py
Skips with a message if no PostgreSQL server binaries are available.  Uses tests/psql_driver.py in place of
psycopg, because the sandbox cannot install it; the SQL is the project's own and runs on real PostgreSQL.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db  # noqa: E402
import psql_driver  # noqa: E402

PORT = 54329
BIN_DIRS = ["/usr/lib/postgresql/%d/bin" % v for v in range(18, 11, -1)]


def find_bin():
    for d in BIN_DIRS:
        if os.path.exists(d + "/initdb"):
            return d
    exe = shutil.which("initdb")
    return str(Path(exe).parent) if exe else None


class Server:
    """A throwaway PostgreSQL cluster. As root it runs as a helper OS user, since PostgreSQL refuses root."""

    def __init__(self, bindir):
        self.bin = bindir
        self.dir = tempfile.mkdtemp(prefix="pgtest_")
        self.user = "pgtest" if os.geteuid() == 0 else os.environ.get("USER", "postgres")
        self.sock = self.dir
        self.prefix = ["runuser", "-u", self.user, "--"] if os.geteuid() == 0 else []

    def run(self, *cmd):
        return subprocess.run(self.prefix + list(cmd), capture_output=True, text=True)

    def start(self):
        if os.geteuid() == 0:
            os.chmod(self.dir, 0o777)
            shutil.chown(self.dir, user=self.user)
        data = self.dir + "/data"
        r = self.run(self.bin + "/initdb", "-D", data, "-A", "trust", "-U", self.user)
        if r.returncode:
            raise RuntimeError(r.stderr)
        r = self.run(self.bin + "/pg_ctl", "-D", data, "-o", f"-p {PORT} -k {self.sock} -c listen_addresses=''",
                     "-l", self.dir + "/log", "-w", "start")
        if r.returncode:
            raise RuntimeError(r.stderr + r.stdout)
        subprocess.run(["psql", "-h", self.sock, "-p", str(PORT), "-U", self.user, "-d", "postgres", "-Atc",
                        "CREATE DATABASE disasterroute"], check=True, capture_output=True)

    def stop(self):
        self.run(self.bin + "/pg_ctl", "-D", self.dir + "/data", "-m", "immediate", "stop")
        shutil.rmtree(self.dir, ignore_errors=True)

    def connect(self):
        return psql_driver.connect_factory(self.sock, PORT, self.user, "disasterroute")


def expect_error(fn, label):
    try:
        fn()
    except Exception:
        return
    raise AssertionError(f"{label}: expected the database to refuse this")


def run_tests(connect):
    st = db.Store(connect)
    st.init_schema()
    st.init_schema()                                              # safe to repeat
    print("  schema created twice without error")

    scen_dir = Path(__file__).resolve().parent.parent / "data" / "scenarios"
    n = st.sync_scenarios(scen_dir)
    files = sorted(scen_dir.glob("*.geojson"))
    n_feat = sum(len(json.loads(p.read_text())["features"]) for p in files)
    assert n == len(files) and st.count("scenarios") == n, (n, st.count("scenarios"))
    assert st.count("hazard_zones") == n_feat, (st.count("hazard_zones"), n_feat)
    st.sync_scenarios(scen_dir)                                   # re-running does not duplicate
    assert st.count("scenarios") == n and st.count("hazard_zones") == n_feat
    print(f"  {n} scenarios, {n_feat} hazard zones loaded; loading again changed nothing")

    fc = {"type": "FeatureCollection", "created_by": "user", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[77.6, 12.9], [77.61, 12.9], [77.61, 12.91], [77.6, 12.9]]]},
         "properties": {"hazard": "flood", "severity": "high", "onset_min": 15, "name": "Silk Board's underpass"}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.51, 12.9], [77.51, 12.91], [77.5, 12.9]]]},
         "properties": {"hazard": "quake_damage", "state": "closed"}}]}
    st.save_scenario("my-test", fc)
    rows = {s["name"]: s for s in st.list_scenarios()}
    assert rows["my-test"]["created_by"] == "user" and rows["my-test"]["zones"] == 2, rows["my-test"]
    fc["features"] = fc["features"][:1]
    st.save_scenario("my-test", fc)                               # replace: old zones must go
    assert {s["name"]: s for s in st.list_scenarios()}["my-test"]["zones"] == 1
    zone = st._run([("SELECT hazard, level, onset_min, name FROM hazard_zones WHERE scenario = %s", ("my-test",))], fetch=True)[0]
    assert zone == ("flood", "high", 15.0, "Silk Board's underpass"), zone
    assert st.delete_scenario("my-test") is True and st.delete_scenario("my-test") is False
    assert st._run([("SELECT count(*) FROM hazard_zones WHERE scenario = %s", ("my-test",))], fetch=True)[0][0] == 0
    print("  create / replace / delete scenario: zones follow (including a name with an apostrophe)")

    tmp = Path(tempfile.mkdtemp()) / "facilities.json"
    tmp.write_text(json.dumps([
        {"id": "hospital-0", "name": "St. John's Medical College Hospital", "kind": "hospital", "lat": 12.93, "lon": 77.62,
         "opening_hours": "24/7", "emergency": "yes", "beds": "1350"},
        {"id": "shelter-1", "name": "Community Hall", "kind": "shelter", "lat": 12.95, "lon": 77.6, "area_m2": 800}]))
    assert st.sync_facilities(tmp) == 2 and st.count("facilities") == 2
    assert st.sync_facilities(tmp) == 2 and st.count("facilities") == 2
    props = st._run([("SELECT properties FROM facilities WHERE id = %s", ("hospital-0",))], fetch=True)[0][0]
    assert props == {"beds": "1350"}, props
    bad = Path(tmp.parent / "bad.json"); bad.write_text(json.dumps([{"id": "x", "name": "x", "kind": "airport", "lat": 1, "lon": 1}]))
    expect_error(lambda: st.sync_facilities(bad), "facility kind check")
    print("  facilities stored with extra tags in JSON; a bad kind is refused")

    st.log_query(scenario="flood", mode="car", traffic="normal", lam=0.5, origin=(12.91, 77.59), dest=(12.93, 77.65),
                 baseline_s=900.5, baseline_blocked=True, baseline_risk=None, aware_s=1200.0, aware_risk=12.5)
    st.log_query(scenario=None, mode="bus", traffic="peak", lam=0.0, origin=(12.91, 77.59), dest=(12.93, 77.65),
                 baseline_s=2000.0, baseline_blocked=False, baseline_risk=0.0, aware_s=None, aware_risk=None)
    q = st.recent_queries(10)
    assert len(q) == 2 and q[0]["mode"] == "bus" and q[0]["aware_s"] is None and q[1]["baseline_blocked"] is True, q
    assert q[1]["scenario"] == "flood" and q[1]["baseline_s"] == 900.5
    print("  route queries logged and read back (newest first, nulls kept)")

    rows = [{"scenario": "flood", "trips": 500, "mean_exposure_aware": 72.36, "hospital_cut_off": 2},
            {"scenario": "wildfire", "trips": 500, "mean_exposure_aware": 807.27, "hospital_cut_off": 1}]
    rid = st.save_evaluation({"trips": 500, "lam": 0.5, "threshold_min": 10}, rows, sweep=[{"lam": 2, "scenario": "flood"}])
    ev = st.get_evaluation(rid)
    assert ev["trips"] == 500 and [r["scenario"] for r in ev["results"]] == ["flood", "wildfire"], ev
    assert ev["results"][0]["mean_exposure_aware"] == 72.36 and ev["params"]["lambda_sweep"][0]["lam"] == 2
    assert st.list_evaluations()[0]["scenarios"] == 2 and st.get_evaluation(99999) is None
    print("  evaluation run saved with per-scenario metrics and the lambda sweep")

    db.set_store(st)
    assert db.safe("count", "scenarios") == n
    db.set_store(db.Store(lambda: (_ for _ in ()).throw(ConnectionError("database is down"))))
    assert db.safe("count", "scenarios") is None                   # failure is swallowed
    db.set_store(None)
    assert db.safe("count", "scenarios") is None
    print("  database down or unset: safe() returns None instead of raising")

    st.reset()
    expect_error(lambda: st.count("scenarios"), "after reset")
    print("  reset drops the tables")


def main():
    bindir = find_bin()
    if not bindir or not shutil.which("psql"):
        print("SKIPPED: no PostgreSQL server binaries / psql found")
        return
    srv = Server(bindir)
    try:
        srv.start()
        run_tests(srv.connect())
    finally:
        srv.stop()
    print("DATABASE TESTS PASSED")


if __name__ == "__main__":
    main()
