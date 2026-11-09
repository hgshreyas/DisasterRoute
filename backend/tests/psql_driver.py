"""Test-only stand-in for psycopg, backed by the real `psql` command line client.

The sandbox that built this project cannot pip-install psycopg, but it does have a real PostgreSQL
server.  This driver sends each statement to that server through psql, so tests/test_db.py checks the
project's SQL against real PostgreSQL.  It is NOT used by the app (the app uses psycopg).

Limits (fine for tests): every statement is its own transaction, and only the %s placeholder is handled.
"""
import datetime as dt
import json
import subprocess


def _literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


class _Obj(list):
    """A JSON object parsed with its key order kept."""


def _plain(v):
    if isinstance(v, _Obj):
        return {k: _plain(x) for k, x in v}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class Connection:
    def __init__(self, args):
        self.args = args

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _psql(self, sql: str) -> str:
        p = subprocess.run(["psql", *self.args, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"], input=sql,
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip())
        return p.stdout

    def execute(self, sql: str, params=None):
        if params is None:                                   # a script (the schema): run as is
            self._psql(sql)
            return _Cursor([])
        parts = sql.split("%s")
        assert len(parts) == len(params) + 1, (sql, params)
        text = parts[0] + "".join(_literal(p) + q for p, q in zip(params, parts[1:]))
        if text.lstrip().upper().startswith("SELECT") or " RETURNING " in text.upper():
            wrapped = f"WITH q AS ({text}) SELECT coalesce(json_agg(row_to_json(q)), '[]'::json) FROM q"
            rows = json.loads(self._psql(wrapped).strip(), object_pairs_hook=_Obj)
            out = []
            for r in rows:
                vals = []
                for k, v in r:
                    v = _plain(v)
                    if isinstance(v, str) and k.endswith("_at"):
                        v = dt.datetime.fromisoformat(v)
                    vals.append(v)
                out.append(tuple(vals))
            return _Cursor(out)
        self._psql(text)
        return _Cursor([])


def connect_factory(host_dir: str, port: int, user: str, dbname: str):
    args = ["-h", host_dir, "-p", str(port), "-U", user, "-d", dbname]
    return lambda: Connection(args)
