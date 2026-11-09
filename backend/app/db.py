"""PostgreSQL storage: scenarios, hazard zones, facilities, route comparisons and evaluation results.

The database is OPTIONAL. The routing engine still reads scenarios and facilities from files in data/;
with DATABASE_URL set, this module also keeps copies in PostgreSQL and records results there.  Nothing
here may break a routing request: callers use the *_safe helpers, which log a warning and carry on if
the database is down.

All SQL uses %s placeholders, and JSON goes in as text cast with ::jsonb, so it runs on psycopg 3
(pip install "psycopg[binary]") and on any driver with the same conventions.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger("disasterroute.db")

LEVEL_KEYS = ("severity", "intensity", "state")


def _default_connect() -> Callable:
    """Return connect() for DATABASE_URL, or None when there is no URL or no driver."""
    from .config import DATABASE_URL
    if not DATABASE_URL:
        return None
    try:
        import psycopg
    except ImportError:
        log.warning('DATABASE_URL is set but psycopg is not installed: pip install "psycopg[binary]"')
        return None
    return lambda: psycopg.connect(DATABASE_URL, connect_timeout=5)


class Store:
    def __init__(self, connect: Callable):
        self._connect = connect

    # ------------------------------------------------------------------ plumbing
    def _run(self, statements: list[tuple[str, tuple]], fetch: bool = False):
        """Run statements in ONE transaction; return the rows of the last one if fetch."""
        with self._connect() as conn:
            cur = None
            for sql, params in statements:
                cur = conn.execute(sql, params)
            return cur.fetchall() if fetch and cur is not None else None

    def init_schema(self, schema_path: Path | None = None) -> None:
        from .config import SCHEMA_PATH
        sql = Path(schema_path or SCHEMA_PATH).read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.execute(sql)

    def reset(self) -> None:
        """Drop every table this app owns. Used by `db_init.py --reset --yes`."""
        self._run([("DROP TABLE IF EXISTS evaluation_results, evaluation_runs, route_queries, "
                    "facilities, hazard_zones, scenarios CASCADE", ())])

    # ------------------------------------------------------------------ scenarios
    @staticmethod
    def _zone_rows(name: str, fc: dict) -> list[tuple[str, tuple]]:
        rows = []
        for f in fc.get("features", []):
            p = f.get("properties", {}) or {}
            level = next((str(p[k]) for k in LEVEL_KEYS if p.get(k) is not None), None)
            rows.append((
                "INSERT INTO hazard_zones (scenario, hazard, level, onset_min, name, geometry, properties) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                (name, str(p.get("hazard", "unknown")), level, float(p.get("onset_min", 0) or 0),
                 p.get("name"), json.dumps(f.get("geometry")), json.dumps(p))))
        return rows

    def save_scenario(self, name: str, fc: dict) -> None:
        created_by = "user" if fc.get("created_by") == "user" else "builtin"
        self._run([
            ("INSERT INTO scenarios (name, created_by, geojson) VALUES (%s, %s, %s::jsonb) "
             "ON CONFLICT (name) DO UPDATE SET created_by = EXCLUDED.created_by, geojson = EXCLUDED.geojson, "
             "updated_at = now()", (name, created_by, json.dumps(fc))),
            ("DELETE FROM hazard_zones WHERE scenario = %s", (name,)),
            *self._zone_rows(name, fc),
        ])

    def delete_scenario(self, name: str) -> bool:
        rows = self._run([("DELETE FROM scenarios WHERE name = %s RETURNING name", (name,))], fetch=True)
        return bool(rows)

    def list_scenarios(self) -> list[dict]:
        rows = self._run([(
            "SELECT s.name, s.created_by, s.updated_at, count(z.id) FROM scenarios s "
            "LEFT JOIN hazard_zones z ON z.scenario = s.name GROUP BY s.name ORDER BY s.name", ())], fetch=True)
        return [{"name": r[0], "created_by": r[1], "updated_at": r[2].isoformat(), "zones": r[3]} for r in rows]

    def sync_scenarios(self, folder: Path) -> int:
        n = 0
        for path in sorted(Path(folder).glob("*.geojson")):
            self.save_scenario(path.stem, json.loads(path.read_text(encoding="utf-8")))
            n += 1
        return n

    # ------------------------------------------------------------------ facilities
    def sync_facilities(self, path: Path) -> int:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {"id", "name", "kind", "lat", "lon", "opening_hours", "emergency"}
        stmts: list[tuple[str, tuple]] = [("DELETE FROM facilities", ())]
        for r in rows:
            stmts.append((
                "INSERT INTO facilities (id, name, kind, lat, lon, opening_hours, emergency, properties) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (r["id"], r["name"], r["kind"], r["lat"], r["lon"], r.get("opening_hours"), r.get("emergency"),
                 json.dumps({k: v for k, v in r.items() if k not in known}))))
        self._run(stmts)
        return len(rows)

    def count(self, table: str) -> int:
        if table not in {"scenarios", "hazard_zones", "facilities", "route_queries", "evaluation_runs",
                         "evaluation_results"}:
            raise ValueError(table)
        return self._run([(f"SELECT count(*) FROM {table}", ())], fetch=True)[0][0]

    # ------------------------------------------------------------------ results
    def log_query(self, *, scenario, mode, traffic, lam, origin, dest, baseline_s, baseline_blocked,
                  baseline_risk, aware_s, aware_risk) -> None:
        self._run([(
            "INSERT INTO route_queries (scenario, mode, traffic, lam, origin_lat, origin_lon, dest_lat, dest_lon, "
            "baseline_s, baseline_blocked, baseline_risk, aware_s, aware_risk) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (scenario, mode, traffic, lam, origin[0], origin[1], dest[0], dest[1],
             baseline_s, baseline_blocked, baseline_risk, aware_s, aware_risk))])

    def recent_queries(self, limit: int = 50) -> list[dict]:
        rows = self._run([(
            "SELECT id, created_at, scenario, mode, traffic, lam, origin_lat, origin_lon, dest_lat, dest_lon, "
            "baseline_s, baseline_blocked, baseline_risk, aware_s, aware_risk "
            "FROM route_queries ORDER BY id DESC LIMIT %s", (int(limit),))], fetch=True)
        keys = ("id", "created_at", "scenario", "mode", "traffic", "lam", "origin_lat", "origin_lon", "dest_lat",
                "dest_lon", "baseline_s", "baseline_blocked", "baseline_risk", "aware_s", "aware_risk")
        out = [dict(zip(keys, r)) for r in rows]
        for o in out:
            o["created_at"] = o["created_at"].isoformat()
        return out

    def save_evaluation(self, params: dict, rows: list[dict], sweep: list[dict] | None = None) -> int:
        """Store one evaluate.py run. `rows` = one metrics dict per scenario (each has a 'scenario' key)."""
        with self._connect() as conn:
            run_id = conn.execute(
                "INSERT INTO evaluation_runs (trips, lam, threshold_min, params) VALUES (%s, %s, %s, %s::jsonb) "
                "RETURNING id", (params["trips"], params["lam"], params["threshold_min"],
                                 json.dumps({**params, "lambda_sweep": sweep or []}))).fetchone()[0]
            for r in rows:
                conn.execute("INSERT INTO evaluation_results (run_id, scenario, metrics) VALUES (%s, %s, %s::jsonb)",
                             (run_id, r["scenario"], json.dumps(r)))
        return run_id

    def list_evaluations(self, limit: int = 20) -> list[dict]:
        rows = self._run([(
            "SELECT r.id, r.created_at, r.trips, r.lam, r.threshold_min, count(x.id) FROM evaluation_runs r "
            "LEFT JOIN evaluation_results x ON x.run_id = r.id GROUP BY r.id ORDER BY r.id DESC LIMIT %s",
            (int(limit),))], fetch=True)
        return [{"id": r[0], "created_at": r[1].isoformat(), "trips": r[2], "lam": r[3], "threshold_min": r[4],
                 "scenarios": r[5]} for r in rows]

    def get_evaluation(self, run_id: int) -> dict | None:
        head = self._run([("SELECT id, created_at, trips, lam, threshold_min, params FROM evaluation_runs "
                           "WHERE id = %s", (run_id,))], fetch=True)
        if not head:
            return None
        h = head[0]
        res = self._run([("SELECT scenario, metrics FROM evaluation_results WHERE run_id = %s ORDER BY scenario",
                          (run_id,))], fetch=True)
        parse = lambda v: json.loads(v) if isinstance(v, str) else v        # psycopg returns JSONB already parsed
        return {"id": h[0], "created_at": h[1].isoformat(), "trips": h[2], "lam": h[3], "threshold_min": h[4],
                "params": parse(h[5]), "results": [parse(r[1]) for r in res]}


# ---------------------------------------------------------------------- module-level access
_store: Store | None = None
_checked = False
_lock = threading.Lock()


def get_store() -> Store | None:
    """The shared Store, or None when no database is configured."""
    global _store, _checked
    if not _checked:
        with _lock:
            if not _checked:
                connect = _default_connect()
                _store = Store(connect) if connect else None
                _checked = True
    return _store


def set_store(store: Store | None) -> None:
    """For tests: install (or clear) the store."""
    global _store, _checked
    _store, _checked = store, True


def safe(fn_name: str, *args, **kwargs):
    """Call a Store method if a database is configured; log and return None on any failure."""
    st = get_store()
    if st is None:
        return None
    try:
        return getattr(st, fn_name)(*args, **kwargs)
    except Exception as exc:                      # a database problem must never break routing
        log.warning("database call %s failed: %s", fn_name, exc)
        return None


def safe_background(fn_name: str, *args, **kwargs) -> None:
    """Like safe(), on a short-lived thread so the request does not wait for the insert."""
    if get_store() is None:
        return
    threading.Thread(target=safe, args=(fn_name, *args), kwargs=kwargs, daemon=True).start()
