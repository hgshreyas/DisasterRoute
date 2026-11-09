# DisasterRoute

Hazard-aware routing for Bengaluru with a custom engine (not plain NetworkX shortest path).
All hazards are SIMULATED scenarios, not forecasts or real incident data.

Stack: custom routing engine (Dijkstra, bidirectional, ALT A*, time-dependent A*, Pareto search, D* Lite) on a CSR
graph, FastAPI, Leaflet map, OSMnx/NetworkX for data, GeoPandas/Shapely for hazard geometry, PostgreSQL for storage.

## Setup (on your machine)

```bash
cd disasterroute/backend
pip install -r requirements.txt
```

## 0. Check everything (no internet needed)

```bash
python tests/test_engine.py     # every algorithm vs an independent reference
python tests/test_glue.py       # graph -> routing -> GeoJSON on a small grid
python tests/test_hazards.py    # exposure geometry, scenario rules, time-dependent flood
python tests/test_access.py     # facility coverage, per-origin access, click-to-close
python tests/test_dstar.py      # D* Lite against Dijkstra
python tests/test_metro.py      # metro journey planning
python tests/test_prune.py      # hospital / shelter filtering
```

## 1. One-time data steps (internet needed)

```bash
python scripts/download_graph.py      # whole-Bengaluru road graph (you already did this)
python scripts/build_landmarks.py     # ALT landmarks (you already did this)
python scripts/make_scenarios.py      # 5 simulated scenarios -> data/scenarios/*.geojson
python scripts/preview_map.py         # then open data/scenario_preview.html in a browser
python scripts/fetch_facilities.py    # hospitals + candidate shelters from OpenStreetMap
python scripts/prune_facilities.py    # drop clinics and unsuitable shelters (keeps facilities_full.json)
python scripts/fetch_metro.py         # Namma Metro lines and stations
python scripts/calibrate_speed.py     # optional: re-measure REF_FREEFLOW_KMH for app/modes.py
```

Check the preview map: hazard positions are APPROXIMATE. Edit `app/scenario_sites.py`
(lat, lon, radius) and re-run `make_scenarios.py` + `preview_map.py` until each circle sits on
the right place.

## 2. Start the API

```bash
python -m uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000/app/** in your browser: this is the map. Steps:

1. Pick a scenario (flood, wildfire, landslide, earthquake) from the dropdown; its hazard zones are drawn.
2. In "Set route" mode click a start, then an end (or press "Load example").
3. You see the **normal route** (blue dashed, marked BLOCKED if it crosses a closed road) against the
   **hazard-aware route** (green), plus a lowest-exposure alternative (purple).
4. "Close a road" mode: click a road to close it and the routes re-plan.
5. "Add hazard" mode: choose a type and size, click the map to create your own hazard (saved as scenario `custom`).
   You can also **type** the start and destination (OpenStreetMap place search, Bengaluru only).
7. Under the routes, "Near your destination" lists the closest hospitals and shelters by travel time under the
   chosen scenario. Pick the time you will need them: facilities known to be closed then are hidden, those inside a
   hazard zone are listed last, and "Show route" draws the way from your destination. Opening hours come from
   OpenStreetMap, so re-run `python scripts/fetch_facilities.py` once to load them; many hospitals have none listed
   and are labelled "Opening hours not listed".
8. **Travel by** (car, bike or scooter, bus) and **Traffic** (light, normal, peak) rescale every time to realistic
   Bengaluru speeds: car 16.6 km/h all-day and 13.9 km/h in rush hour (TomTom Traffic Index 2025), bus about
   12 km/h in rush hour (BMTC, 2018) plus an assumed 12 min wait and walking. Bike and the light-traffic speeds are
   assumptions. Edit `app/modes.py` to tune them; `/modes` lists the values and `/health` shows the graph's own
   average speed. **Metro:** run `python scripts/fetch_metro.py` once. It downloads the Namma Metro lines and stations from
   OpenStreetMap into `data/metro.json` and prints every line with its station count, so check it against the real
   network. Then pick "Metro" under "Travel by": the page plans walk to a station, wait (half a train interval),
   ride, interchange (5 min) and walk out, and compares with going by car. Stations inside a high flood, a fire
   hotspot, a landslide block or quake damage are closed; closed roads stop the walk. Walking is 4.5 km/h, riding
   35 km/h and train intervals of 10, 7 and 5 minutes (light, normal, peak) are assumptions. There is no timetable.
6. "Use my current location" sets the start from your device (browser permission needed, Bengaluru only);
   "follow me" keeps updating it as you move.
9. Tick "Show hospitals/shelters" and press "Hospital coverage" to see which hospitals get cut off.

The map tiles and Leaflet need internet access. The first request that uses a scenario builds the edge geometry for the whole city (tens of
seconds to a minute); after that it is cached.

| Endpoint | What it does |
|----------|--------------|
| `/health` | graph size, landmark count, available scenarios |
| `/scenarios`, `/scenarios/flood` | list scenarios; polygons + how many roads each affects |
| `POST /scenarios` | CREATE your own hazard(s) at runtime; usable immediately (see below) |
| `DELETE /scenarios/{name}` | delete a scenario you created (built-in demos are protected) |
| `/route?...&scenario=flood&lam=0.5` | route under a scenario (`lam` = penalty per unit exposure) |
| `/compare-scenario?...&scenario=flood` | THE core comparison: normal route vs hazard-aware routes |
| `/route/time-dependent?...&scenario=flood_rising&depart_min=0` | earliest arrival as flooding spreads |
| `/route/alternatives?...&scenario=flood` | Pareto front of time vs exposure |
| `/compare-algorithms?...&scenario=flood` | all four algorithms on one query |
| `/edges/near?lat=..&lon=..` | roads near a clicked point; returns `close_ids` for the chosen road |
| any routing endpoint + `&closed=123,456` | the scenario plus extra closed roads (the "close a bridge" step) |
| `/facilities?kind=hospital` | hospitals / shelters snapped to the road network |
| `/accessibility?scenario=flood&kind=hospital&threshold_min=30` | city-wide coverage before vs after, plus facilities that got cut off |
| `/accessibility/origin?lat=..&lon=..&scenario=flood` | from one point: which facilities are still reachable and how much slower |
| `/replan?...&scenario_before=..&scenario_after=..` | hazards change after planning: D* Lite repair vs planning again |
| `/db/status`, `/results/evaluations`, `/results/queries` | PostgreSQL status, saved evaluation runs, logged route comparisons (needs the database) |
| `/route/metro?...`, `/metro`, `/modes` | metro journey, metro network, travel-mode speeds |

Sample query (Majestic to Koramangala): `origin_lat=12.9767&origin_lon=77.5713&dest_lat=12.9352&dest_lon=77.6245`

## Creating your own hazards (nothing is hard-coded)

The five demo scenarios come from `app/scenario_sites.py`, but the engine accepts any zone.
Open http://127.0.0.1:8000/docs, expand `POST /scenarios`, click "Try it out" and send:

```json
{
  "name": "my-flood",
  "features": [
    {"shape": "circle", "lat": 12.9177, "lon": 77.6226, "radius_m": 500,
     "hazard": "flood", "severity": "high", "name": "Silk Board"}
  ]
}
```

Then route with it: `/compare-scenario?...&scenario=my-flood`. Several hazards of different
types can go in one scenario. Instead of the circle shorthand you can send a GeoJSON
Polygon Feature (a zone drawn on a map) with the same properties. Properties:

- `hazard`: flood | wildfire | wildfire_hotspot | landslide | landslide_block | quake_damage
- `severity` low/medium/high (flood, landslide), `intensity` light/moderate/heavy (wildfire),
  `state` closed/partial (quake_damage), `onset_min` minutes until it takes effect (default 0)

Invalid input is rejected with a clear message and nothing is saved. Created scenarios are saved
in `data/scenarios/` and survive a restart. Reusing a name needs `"overwrite": true`.

## Scenario rules (PROPOSED values, tune in `app/hazards.py`)

| Hazard | Effect |
|--------|--------|
| flood | slower by `1 + 5*f*severity`; exposure = metres inside * severity; closed if high severity and >= 60% of the road is inside |
| wildfire smoke | speed factor 0.85 / 0.6 / 0.35 (light/moderate/heavy); fire hotspot closes roads |
| landslide | slower by `1 + 2*f*susceptibility*road_vulnerability`; blockage polygons close roads |
| earthquake | damaged bridge circles: closed (removed) or partial (speed x0.5) |
| `onset_min > 0` | the change switches on that many minutes after departure (time-dependent) |

You can also upload your own GeoJSON zones: Polygon features with the same properties
(`hazard`, `severity` / `intensity` / `state`, `onset_min`). Put the file in `data/scenarios/`.

## Layout

```
backend/app/engine/       CSR graph, Dijkstra/A* variants, Pareto search
backend/app/exposure.py   polygon-edge intersection (numpy only)
backend/app/hazards.py    scenario GeoJSON -> per-edge hazard state
backend/app/scenario_sites.py   WHERE the simulated hazards are (edit me)
backend/app/engine/dstar.py     D* Lite replanning
backend/app/modes.py, transit.py   travel modes and metro
backend/frontend/         the Leaflet map page (served at /app)
backend/scripts/          data download, evaluation and benchmark scripts
backend/results/          evaluation output (generated)
backend/db/schema.sql     PostgreSQL tables; backend/app/db.py is the storage layer
backend/tests/
```

## Rerouting with D* Lite

When a hazard appears or a road closes while a route is already planned, `app/engine/dstar.py` repairs the
old search instead of starting again (D* Lite, searching backward from the destination with landmark bounds).

- API: `GET /replan?origin_lat=..&origin_lon=..&dest_lat=..&dest_lon=..&scenario_before=..&scenario_after=..`
  (also `closed_before` / `closed_after`, `lam`, `mode`, `traffic`).  It returns the old and new route, the nodes
  searched and milliseconds for D* Lite repair and for a fresh bidirectional ALT search, and whether both gave
  the same cost.
- Map: change the scenario or close a road after finding a route and a "Rerouting after the change" card appears.
- Benchmark on the real city (the "rerouting speed" metric): `python scripts/bench_replan.py`
- Tests: `python tests/test_dstar.py` (randomised against Dijkstra).

Time-dependent (spreading) hazards are not handled by D* Lite; it replans on the conditions at one moment.

## PostgreSQL storage (optional)

The app works from files without a database. With PostgreSQL it also keeps scenarios and their hazard zones,
facilities, every route comparison (normal route vs hazard-aware route) and every evaluation run in tables
(`backend/db/schema.sql`; plain PostgreSQL, no PostGIS needed).

1. Install PostgreSQL (https://www.postgresql.org/download/windows/), remember the password you set for `postgres`.
2. Create the database: open "SQL Shell (psql)" and run `CREATE DATABASE disasterroute;`
3. In `backend/`: `pip install -r requirements.txt` (adds `psycopg`), copy `.env.example` to `.env` and put your
   password in `DATABASE_URL`.
4. `python scripts/db_init.py` creates the tables and loads `data/scenarios/*.geojson` and `data/facilities.json`
   (`--reset --yes` drops and recreates them).
5. Start the server as usual. Scenarios you create or delete on the map are mirrored, and each comparison is logged.
   `GET /db/status` shows row counts; `python scripts/evaluate.py --save-db` stores an evaluation run, readable at
   `GET /results/evaluations`.

If PostgreSQL is stopped, routing keeps working and the database calls are skipped with a warning.

## Evaluation (baseline vs proposed)

`python scripts/evaluate.py` runs the same random trips through flood, wildfire, landslide and earthquake and
writes `results/evaluation.md` / `.json`: hazard exposure, travel-time trade-off, rerouting speed (D* Lite vs
fresh search) and facility access.
Useful options: `--trips 500` (more trips), `--threshold 10` (facility access within 10 real car minutes),
`--sweep 0,0.5,2,5` (time-versus-exposure table as lambda grows), `--scenarios flood,custom`.

