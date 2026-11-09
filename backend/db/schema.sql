-- DisasterRoute schema. Plain PostgreSQL (no PostGIS needed): hazard geometry is stored as GeoJSON.
-- Safe to run more than once.

CREATE TABLE IF NOT EXISTS scenarios (
    name        TEXT PRIMARY KEY,
    created_by  TEXT NOT NULL DEFAULT 'builtin' CHECK (created_by IN ('builtin', 'user')),
    geojson     JSONB NOT NULL,                       -- the whole FeatureCollection, as the engine reads it
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hazard_zones (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario    TEXT NOT NULL REFERENCES scenarios(name) ON DELETE CASCADE,
    hazard      TEXT NOT NULL,                        -- flood | wildfire | wildfire_hotspot | landslide | landslide_block | quake_damage
    level       TEXT,                                 -- severity / intensity / state, whichever the hazard uses
    onset_min   DOUBLE PRECISION NOT NULL DEFAULT 0,  -- minutes after departure until it takes effect
    name        TEXT,
    geometry    JSONB NOT NULL,                       -- GeoJSON geometry (EPSG:4326)
    properties  JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS hazard_zones_scenario_idx ON hazard_zones (scenario);

CREATE TABLE IF NOT EXISTS facilities (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('hospital', 'shelter')),
    lat            DOUBLE PRECISION NOT NULL,
    lon            DOUBLE PRECISION NOT NULL,
    opening_hours  TEXT,                              -- raw OpenStreetMap tag, if listed
    emergency      TEXT,
    properties     JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS facilities_kind_idx ON facilities (kind);

-- One row per /compare-scenario request: how the normal route fared against the hazard-aware one.
CREATE TABLE IF NOT EXISTS route_queries (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    scenario          TEXT,                           -- not a foreign key: scenarios can be deleted later
    mode              TEXT NOT NULL,
    traffic           TEXT NOT NULL,
    lam               DOUBLE PRECISION,
    origin_lat        DOUBLE PRECISION NOT NULL,
    origin_lon        DOUBLE PRECISION NOT NULL,
    dest_lat          DOUBLE PRECISION NOT NULL,
    dest_lon          DOUBLE PRECISION NOT NULL,
    baseline_s        DOUBLE PRECISION,               -- normal route, normal conditions (real seconds for the mode)
    baseline_blocked  BOOLEAN,
    baseline_risk     DOUBLE PRECISION,               -- exposure when driven under the hazard
    aware_s           DOUBLE PRECISION,
    aware_risk        DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS route_queries_created_idx ON route_queries (created_at DESC);

-- A run of scripts/evaluate.py and its per-scenario results.
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    trips          INTEGER NOT NULL,
    lam            DOUBLE PRECISION NOT NULL,
    threshold_min  DOUBLE PRECISION NOT NULL,
    params         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id    BIGINT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    scenario  TEXT NOT NULL,
    metrics   JSONB NOT NULL,                         -- exposure, time trade-off, rerouting speed, facility access
    UNIQUE (run_id, scenario)
);
