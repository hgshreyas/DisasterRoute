"""DisasterRoute API (Steps 3 and 7: hazard scenarios, closures, facility accessibility)."""
import json
import time
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .accessibility import compare_coverage, origin_access
from .config import FRONTEND_DIR, METRO_PATH
from .graph import get_graph
from . import transit
from .modes import describe as describe_modes, get_model, scale_times
from .opening_hours import is_open
from .scenario_store import create_scenario, delete_scenario, scenario_details
from . import db
from .routing import (ALGOS, NeedLandmarks, compare_scenario, pareto_alternatives, replan, run_algo,
                      time_dependent_route, to_dict)

app = FastAPI(title="DisasterRoute API", version="0.4.0")

# The React dev server runs on a different port, so allow it during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CLOSED_DOC = "comma-separated edge ids to close on top of the scenario (from /edges/near)"

# The map page. Served from the same address as the API (http://127.0.0.1:8000/app/), which
# also means OpenStreetMap map tiles load without being blocked.
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


def _graph():
    try:
        return get_graph()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _snap(rg, lat1, lon1, lat2, lon2):
    s, s_d = rg.nearest_node(lat1, lon1)
    t, t_d = rg.nearest_node(lat2, lon2)
    # Reject points far outside the cached area (e.g. a click in another city).
    if max(s_d, t_d) > 1000:
        raise HTTPException(status_code=400, detail="Point is more than 1 km from the road network.")
    return s, t


def _metro(rg):
    """The metro network, loaded and snapped to the roads on first use."""
    with rg._lock:                                            # parallel requests must not each load it
        net = getattr(rg, "_metro_net", None)
        if net is None:
            if not METRO_PATH.exists():
                raise HTTPException(status_code=503, detail=f"{METRO_PATH} not found. Run: python scripts/fetch_metro.py")
            try:
                net = transit.load_network(METRO_PATH, rg)
            except (ValueError, KeyError, OSError) as exc:
                raise HTTPException(status_code=503, detail=f"metro.json is unreadable: {exc}")
            rg._metro_net = net
        return net


def _model(mode: str, traffic: str):
    if mode == "metro":
        raise HTTPException(status_code=400, detail="Metro trips use /route/metro; other endpoints take car, bike or bus.")
    try:
        return get_model(mode, traffic)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


MODE_Q = Query("car", description="car | bike | bus: rescales times to real Bengaluru speeds (see /modes)")
TRAFFIC_Q = Query("normal", description="light | normal | peak")


def _state(rg, scenario: str | None, closed: str | None = None):
    """Hazard state = scenario (None = normal) plus any extra closed roads."""
    state = None
    if scenario and scenario != "none":
        try:
            state = rg.scenario(scenario)[0]
        except KeyError:
            raise HTTPException(status_code=404, detail=(
                f"Unknown scenario {scenario!r}. Available: {rg.scenario_names()}. "
                "Create them with: python scripts/make_scenarios.py"))
    if closed:
        try:
            ids = [int(x) for x in closed.split(",") if x.strip()]
            state = rg.with_closures(state, ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Bad 'closed' list: {exc}")
    return state


def _facilities(rg, kind: str):
    try:
        facs = rg.facilities()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return facs if kind == "all" else [f for f in facs if f.kind == kind]


@app.get("/health")
def health():
    try:
        rg = get_graph()
    except FileNotFoundError as exc:
        return {"status": "graph_missing", "detail": str(exc)}
    return {
        "status": "ok",
        "nodes": rg.csr.n,
        "edges": rg.csr.m,
        "landmarks": len(rg.landmarks.ids) if rg.landmarks else 0,
        "scenarios": rg.scenario_names(),
        "mean_free_flow_kmh": round(3.6 * sum(rg.csr.length) / max(1e-9, sum(rg.csr.base_time)), 1),
    }


@app.get("/modes")
def modes():
    """Transport modes, traffic levels and the speeds behind the time estimates."""
    return describe_modes()


@app.get("/metro")
def metro():
    """Lines and stations, for drawing the network."""
    net = _metro(_graph())
    return {"lines": [{"name": l["name"], "colour": l["colour"],
                       "stations": [{"name": s["name"], "lat": s["lat"], "lon": s["lon"]} for s in l["stations"]]}
                      for l in net.lines]}


# ----------------------------------------------------------------------------- scenarios
class ScenarioIn(BaseModel):
    name: str
    features: list[dict]          # GeoJSON features, or circle shorthand (see app/hazards.py)
    overwrite: bool = False


@app.get("/scenarios")
def scenarios():
    rg = _graph()
    return {"scenarios": rg.scenario_names(), "details": scenario_details(rg)}


@app.post("/scenarios", status_code=201)
def create_scenario_endpoint(body: ScenarioIn):
    """Create (or replace) a hazard scenario from zones drawn or entered by the user.

    Example body:
      {"name": "my-flood",
       "features": [{"shape": "circle", "lat": 12.9177, "lon": 77.6226, "radius_m": 500,
                     "hazard": "flood", "severity": "high", "name": "Silk Board"}]}
    The scenario can be used immediately: /route?...&scenario=my-flood
    """
    rg = _graph()
    try:
        result = create_scenario(rg, body.name, body.features, body.overwrite)
        db.safe("save_scenario", body.name, result["geojson"])      # copy into PostgreSQL if configured
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.delete("/scenarios/{name}")
def delete_scenario_endpoint(name: str):
    rg = _graph()
    try:
        delete_scenario(rg, name)
        db.safe("delete_scenario", name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {name!r}")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"deleted": name}


@app.get("/scenarios/{name}")
def scenario_detail(name: str):
    """Hazard polygons (GeoJSON, for the map overlay) plus how many roads they affect.

    The first call for each scenario builds the edge geometry for the whole city, which
    can take a minute. Later calls are instant.
    """
    rg = _graph()
    _state(rg, name)                     # raises 404 if unknown; builds + caches the state
    _, summary = rg.scenario(name)
    path = rg.scenario_dir / f"{name}.geojson"
    return {"name": name, "summary": summary,
            "geojson": json.loads(path.read_text(encoding="utf-8"))}


# ----------------------------------------------------------------------------- closures
@app.get("/edges/near")
def edges_near(lat: float, lon: float, radius_m: float = Query(150, gt=0, le=500),
               k: int = Query(3, ge=1, le=10)):
    """Roads near a clicked point. Pass `close_ids` of the chosen road as `closed=` elsewhere."""
    rg = _graph()
    out = rg.edges_near(lat, lon, radius_m, k)
    if not out:
        raise HTTPException(status_code=404, detail="No road within that distance.")
    return {"edges": out}


# ----------------------------------------------------------------------------- routing
@app.get("/route")
def route(
    origin_lat: float = Query(..., ge=-90, le=90),
    origin_lon: float = Query(..., ge=-180, le=180),
    dest_lat: float = Query(..., ge=-90, le=90),
    dest_lon: float = Query(..., ge=-180, le=180),
    algo: str = Query("bidir_alt", pattern="^(dijkstra|bidir_dijkstra|alt|bidir_alt)$"),
    lam: float = Query(0.0, ge=0, description="seconds of penalty per unit of hazard exposure"),
    scenario: str | None = Query(None, description="scenario name, e.g. flood"),
    closed: str | None = Query(None, description=CLOSED_DOC),
    mode: str = MODE_Q, traffic: str = TRAFFIC_Q,
):
    tm = _model(mode, traffic)
    rg = _graph()
    state = _state(rg, scenario, closed)
    s, t = _snap(rg, origin_lat, origin_lon, dest_lat, dest_lon)
    try:
        res = run_algo(rg, algo, s, t, lam, state)
    except NeedLandmarks as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if res is None:
        raise HTTPException(status_code=404, detail="No route between these points.")
    return scale_times(to_dict(rg, res), tm)


@app.get("/route/metro")
def route_metro(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    scenario: str | None = None,
    closed: str | None = Query(None, description=CLOSED_DOC),
    traffic: str = TRAFFIC_Q,
):
    """Walk to a station, ride (with transfers), walk out. Stations inside closing hazards are unusable."""
    _model("car", traffic)                              # validates the traffic level
    rg = _graph()
    net = _metro(rg)
    state = _state(rg, scenario, closed)
    s, t = _snap(rg, origin_lat, origin_lon, dest_lat, dest_lon)
    geojson = None
    if scenario and scenario != "none":
        geojson = json.loads((rg.scenario_dir / f"{scenario}.geojson").read_text(encoding="utf-8"))
    return transit.journey(rg, net, s, t, state, traffic, geojson)


@app.get("/route/time-dependent")
def route_time_dependent(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    scenario: str = Query("flood_rising"),
    depart_min: float = Query(0.0, ge=0, description="minutes after the scenario starts"),
    closed: str | None = Query(None, description=CLOSED_DOC),
    mode: str = MODE_Q, traffic: str = TRAFFIC_Q,
):
    """Earliest-arrival route when hazards escalate over time."""
    tm = _model(mode, traffic)
    rg = _graph()
    state = _state(rg, scenario, closed)
    s, t = _snap(rg, origin_lat, origin_lon, dest_lat, dest_lon)
    # Hazards start at fixed clock times, but the engine counts free-flow seconds. Express the
    # start times, and the departure (including any bus wait), in free-flow seconds.
    st = state.copy() if state is not None else rg.csr.fresh_state()
    st.onset = [o / tm.k for o in st.onset]
    try:
        res = time_dependent_route(rg, s, t, st, (depart_min * 60.0 + tm.fixed_s) / tm.k / 60.0)
    except NeedLandmarks as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if res is None:
        raise HTTPException(status_code=404, detail="No route between these points.")
    out = scale_times(to_dict(rg, res), tm)
    out["depart_min"] = depart_min
    out["arrival_min"] = round(depart_min + out["travel_time_s"] / 60.0, 2)
    return out


@app.get("/route/alternatives")
def alternatives(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    detour: float = Query(1.5, ge=1.0, le=5.0, description="max time vs fastest route"),
    scenario: str | None = None,
    closed: str | None = Query(None, description=CLOSED_DOC),
    mode: str = MODE_Q, traffic: str = TRAFFIC_Q,
):
    """Pareto front of time vs hazard exposure, plus a balanced pick."""
    tm = _model(mode, traffic)
    rg = _graph()
    state = _state(rg, scenario, closed)
    s, t = _snap(rg, origin_lat, origin_lon, dest_lat, dest_lon)
    front, best = pareto_alternatives(rg, s, t, detour, state)
    if not front:
        raise HTTPException(status_code=404, detail="No route between these points.")
    return scale_times({
        "fastest": to_dict(rg, front[0]),
        "balanced": to_dict(rg, best),
        "lowest_exposure": to_dict(rg, min(front, key=lambda r: (r.risk, r.time))),
        "front": [to_dict(rg, r, with_geometry=False) for r in front],
    }, tm)


@app.get("/compare-scenario")
def compare(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    scenario: str = Query(..., description="scenario name, e.g. flood"),
    lam: float = Query(0.5, ge=0),
    detour: float = Query(1.5, ge=1.0, le=5.0),
    closed: str | None = Query(None, description=CLOSED_DOC),
    mode: str = MODE_Q, traffic: str = TRAFFIC_Q,
):
    """The project's core comparison: the normal route versus hazard-aware routes."""
    tm = _model(mode, traffic)
    rg = _graph()
    state = _state(rg, scenario, closed)
    s, t = _snap(rg, origin_lat, origin_lon, dest_lat, dest_lon)
    try:
        out = compare_scenario(rg, s, t, state, lam, detour)
    except NeedLandmarks as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not out:
        raise HTTPException(status_code=404, detail="No route between these points.")
    out["scenario"] = scenario
    out["model"] = {"mode": tm.mode, "traffic": tm.traffic, "speed_kmh": tm.speed_kmh}
    scale_times(out, tm)
    base, aware = out["baseline"], out.get("hazard_aware")
    db.safe_background("log_query", scenario=scenario, mode=tm.mode, traffic=tm.traffic, lam=lam,
                       origin=(origin_lat, origin_lon), dest=(dest_lat, dest_lon),
                       baseline_s=base["travel_time_s"], baseline_blocked=base["under_hazard"]["blocked"],
                       baseline_risk=base["under_hazard"]["risk"],
                       aware_s=aware["travel_time_s"] if aware else None,
                       aware_risk=aware["risk"] if aware else None)
    return out


@app.get("/replan")
def replan_endpoint(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    scenario_before: str | None = None, scenario_after: str | None = None,
    closed_before: str | None = Query(None, description=CLOSED_DOC),
    closed_after: str | None = Query(None, description=CLOSED_DOC),
    lam: float = Query(0.5, ge=0),
    mode: str = MODE_Q, traffic: str = TRAFFIC_Q,
):
    """Hazards change after a route was planned: repair it with D* Lite and compare with recomputing."""
    tm = _model(mode, traffic)
    rg = _graph()
    before = _state(rg, scenario_before, closed_before)
    after = _state(rg, scenario_after, closed_after)
    s, t = _snap(rg, origin_lat, origin_lon, dest_lat, dest_lon)
    try:
        out = replan(rg, s, t, before, after, lam)
    except NeedLandmarks as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not out:
        raise HTTPException(status_code=404, detail="No route between these points before the change.")
    out["model"] = {"mode": tm.mode, "traffic": tm.traffic, "speed_kmh": tm.speed_kmh}
    return scale_times(out, tm)


# ----------------------------------------------------------------------------- database (optional)
@app.get("/db/status")
def db_status():
    """Whether PostgreSQL is configured and reachable, with row counts."""
    st = db.get_store()
    if st is None:
        return {"enabled": False, "detail": "Set DATABASE_URL (see README) and install psycopg to enable storage."}
    try:
        return {"enabled": True, "connected": True,
                "rows": {t: st.count(t) for t in ("scenarios", "hazard_zones", "facilities", "route_queries",
                                                  "evaluation_runs", "evaluation_results")}}
    except Exception as exc:
        return {"enabled": True, "connected": False, "detail": str(exc)}


def _need_db():
    st = db.get_store()
    if st is None:
        raise HTTPException(status_code=503, detail="No database configured. Set DATABASE_URL (see README).")
    return st


@app.get("/results/evaluations")
def evaluations(limit: int = Query(20, ge=1, le=200)):
    st = _need_db()
    try:
        return {"runs": st.list_evaluations(limit)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")


@app.get("/results/evaluations/{run_id}")
def evaluation(run_id: int):
    st = _need_db()
    try:
        out = st.get_evaluation(run_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")
    if out is None:
        raise HTTPException(status_code=404, detail=f"No evaluation run {run_id}")
    return out


@app.get("/results/queries")
def route_queries(limit: int = Query(50, ge=1, le=500)):
    st = _need_db()
    try:
        return {"queries": st.recent_queries(limit)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")


@app.get("/compare-algorithms")
def compare_algorithms(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    lam: float = Query(0.0, ge=0),
    scenario: str | None = None,
    closed: str | None = Query(None, description=CLOSED_DOC),
):
    """Run every algorithm on the same query: same cost, different search effort/time."""
    rg = _graph()
    state = _state(rg, scenario, closed)
    s, t = _snap(rg, origin_lat, origin_lon, dest_lat, dest_lon)
    rows = []
    for algo in ALGOS:
        t0 = time.perf_counter()
        try:
            res = run_algo(rg, algo, s, t, lam, state)
        except NeedLandmarks:
            rows.append({"algo": algo, "error": "landmarks not built"})
            continue
        ms = (time.perf_counter() - t0) * 1000
        if res is None:
            raise HTTPException(status_code=404, detail="No route between these points.")
        row = to_dict(rg, res, with_geometry=False)
        row["runtime_ms"] = round(ms, 1)
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- accessibility
@app.get("/facilities")
def facilities(kind: str = Query("all", pattern="^(all|hospital|shelter)$")):
    rg = _graph()
    facs = _facilities(rg, kind)
    return {"count": len(facs),
            "facilities": [{"id": f.id, "name": f.name, "kind": f.kind, "lat": f.lat,
                            "lon": f.lon, "snap_m": round(f.snap_m, 1)} for f in facs]}


@app.get("/accessibility")
def accessibility(
    scenario: str | None = None,
    closed: str | None = Query(None, description=CLOSED_DOC),
    kind: str = Query("hospital", pattern="^(all|hospital|shelter)$"),
    threshold_min: float = Query(30.0, gt=0, le=240),
    mode: str = MODE_Q, traffic: str = TRAFFIC_Q,
):
    """City-wide: how much of the road network can reach a facility, before vs after."""
    tm = _model(mode, traffic)
    rg = _graph()
    state = _state(rg, scenario, closed)
    facs = _facilities(rg, kind)
    # the threshold is real minutes for this mode; the engine counts free-flow time
    out = compare_coverage(rg, facs, state, max(0.1, (threshold_min * 60.0 - tm.fixed_s) / tm.k / 60.0))
    for part in ("baseline", "scenario"):
        out[part]["threshold_min"] = threshold_min
        if "mean_time_to_nearest_min" in out[part]:
            out[part]["mean_time_to_nearest_min"] = round(out[part]["mean_time_to_nearest_min"] * tm.k, 2)
    out.update({"scenario_name": scenario, "kind": kind})
    return out


@app.get("/accessibility/origin")
def accessibility_origin(
    lat: float, lon: float,
    scenario: str | None = None,
    closed: str | None = Query(None, description=CLOSED_DOC),
    kind: str = Query("all", pattern="^(all|hospital|shelter)$"),
    max_minutes: float = Query(60.0, gt=0, le=240),
    limit: int = Query(15, ge=1, le=200),
    at: str | None = Query(None, description="local date-time YYYY-MM-DDTHH:MM you set off; hours are checked at arrival"),
    open_only: bool = Query(False, description="drop facilities known to be closed on arrival"),
    mode: str = MODE_Q, traffic: str = TRAFFIC_Q,
):
    """From one point: which facilities are still reachable, and how much slower."""
    rg = _graph()
    state = _state(rg, scenario, closed)
    facs = _facilities(rg, kind)
    s, d = rg.nearest_node(lat, lon)
    if d > 1000:
        raise HTTPException(status_code=400, detail="Point is more than 1 km from the road network.")
    tm = _model(mode, traffic)
    rows = origin_access(rg, s, facs, state, max(1.0, (max_minutes * 60.0 - tm.fixed_s) / tm.k / 60.0))
    for r in rows:                                   # free-flow minutes -> real minutes for this mode
        for key in ("minutes_baseline", "minutes_scenario"):
            if r[key] is not None:
                r[key] = round(r[key] * tm.k + tm.fixed_s / 60.0, 2)
        if r["extra_minutes"] is not None:
            r["extra_minutes"] = round(r["extra_minutes"] * tm.k, 2)
    if at:
        try:
            start = datetime.fromisoformat(at)
        except ValueError:
            raise HTTPException(status_code=400, detail="'at' must look like 2026-11-08T21:30")
        by_id = {f.id: f for f in facs}
        for r in rows:
            f = by_id[r["id"]]
            r["hours"] = f.opening_hours
            if r["minutes_scenario"] is None:
                r["open_status"] = "unknown"
                continue
            arrive = start + timedelta(minutes=r["minutes_scenario"])
            o = is_open(f.opening_hours, arrive)
            if o is True:
                r["open_status"] = "open"
            elif o is False:
                r["open_status"] = "closed"
            elif f.kind == "hospital" and f.emergency == "yes":
                r["open_status"] = "assumed_open"      # emergency department, hours not listed
            else:
                r["open_status"] = "unknown"
        if open_only:
            rows = [r for r in rows if r["open_status"] != "closed"]
    return {"origin_node": s, "reachable": sum(1 for r in rows if r["reachable"]),
            "total": len(rows), "facilities": rows[:limit]}
