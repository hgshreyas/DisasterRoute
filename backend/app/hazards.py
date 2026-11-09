"""Turn a hazard scenario (GeoJSON) into per-edge hazard state for the routing engine.

A scenario is a GeoJSON FeatureCollection of Polygon / MultiPolygon features. Each
feature's properties say what it is. Your own uploaded zones work too, as long as they
use the same properties:

  hazard      flood | wildfire | wildfire_hotspot | landslide | landslide_block | quake_damage
  severity    low | medium | high               (flood, landslide)
  intensity   light | moderate | heavy          (wildfire smoke)
  state       closed | partial                  (quake_damage)
  onset_min   minutes after departure when the feature takes effect (default 0)

Rules (PROPOSED values from the design notes; tune the constants below):
  flood      mult = 1 + ALPHA_FLOOD * f * sev;  risk = exposed_m * sev;
             closed if sev is high and the exposed fraction f >= FLOOD_CLOSE_FRACTION
  wildfire   smoke slows traffic: mult = 1 + f * (1/speed_factor - 1); risk = exposed_m * w
             hotspot polygons close every road they touch
  landslide  mult = 1 + ALPHA_LANDSLIDE * f * susceptibility * road_vulnerability
             landslide_block polygons close every road they touch
  quake      closed -> removed from the network; partial -> speed x0.5

Where several features overlap an edge, the worst value wins (never summed), and hazards only
ever increase an edge's cost.  This is what keeps the landmark bounds valid (see engine/csr.py).
Features with onset_min > 0 become scheduled (time-dependent) changes instead of static ones.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .engine import INF, CSRGraph, HazardState
from .exposure import EdgeGeometry, LocalProjection, polygon_exposure

SEVERITY = {"low": 0.3, "medium": 0.6, "high": 1.0}
SMOKE_SPEED = {"light": 0.85, "moderate": 0.60, "heavy": 0.35}
SMOKE_RISK = {"light": 0.3, "moderate": 0.6, "heavy": 1.0}
ALPHA_FLOOD = 5.0
FLOOD_CLOSE_FRACTION = 0.6
ALPHA_LANDSLIDE = 2.0
QUAKE_PARTIAL_MULT = 2.0
QUAKE_PARTIAL_RISK = 0.5


KINDS = ("flood", "wildfire", "wildfire_hotspot", "landslide", "landslide_block", "quake_damage")
MAX_FEATURES = 200
MAX_RADIUS_M = 25000.0
MAX_ONSET_MIN = 24 * 60


def circle_ring(lat: float, lon: float, radius_m: float, n: int = 48) -> list:
    """GeoJSON ring (lon, lat), closed, approximating a circle."""
    ring = []
    for i in range(n):
        a = 2 * math.pi * i / n
        dlat = radius_m * math.sin(a) / 110540.0
        dlon = radius_m * math.cos(a) / (111320.0 * math.cos(math.radians(lat)))
        ring.append([round(lon + dlon, 6), round(lat + dlat, 6)])
    ring.append(ring[0])
    return ring


def _num(x, what: str, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number")
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{what} must be between {lo} and {hi}")
    return v


def validate_feature(feat: dict, i: int) -> dict:
    """Check one GeoJSON feature and fill defaults. Raises ValueError with a clear message."""
    where = f"feature {i + 1}"
    props = dict(feat.get("properties") or {})
    kind = props.get("hazard")
    if kind not in KINDS:
        raise ValueError(f"{where}: 'hazard' must be one of {list(KINDS)}")
    if kind in ("flood", "landslide") and props.get("severity") not in SEVERITY:
        raise ValueError(f"{where}: {kind} needs 'severity' in {list(SEVERITY)}")
    if kind == "wildfire" and props.get("intensity") not in SMOKE_SPEED:
        raise ValueError(f"{where}: wildfire needs 'intensity' in {list(SMOKE_SPEED)}")
    if kind == "quake_damage":
        props.setdefault("state", "closed")
        if props["state"] not in ("closed", "partial"):
            raise ValueError(f"{where}: quake_damage 'state' must be closed or partial")
    props["onset_min"] = _num(props.get("onset_min", 0), f"{where}: onset_min", 0, MAX_ONSET_MIN)
    props.setdefault("name", f"{kind} {i + 1}")
    props["simulated"] = True

    geom = feat.get("geometry") or {}
    if geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"{where}: geometry must be a Polygon or MultiPolygon")
    for ring in _rings(geom):
        if not isinstance(ring, list) or len(ring) < 4:
            raise ValueError(f"{where}: a polygon ring needs at least 4 points (closed)")
        for pt in ring:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                raise ValueError(f"{where}: coordinates must be [lon, lat] pairs")
            _num(pt[0], f"{where}: longitude", -180, 180)
            _num(pt[1], f"{where}: latitude", -90, 90)
    return {"type": "Feature", "properties": props, "geometry": geom}


def normalize_features(items: list) -> list:
    """Accept GeoJSON features or a circle shorthand, validate, and return GeoJSON features.

    Circle shorthand:  {"shape": "circle", "lat": 12.93, "lon": 77.62, "radius_m": 600,
                        "hazard": "flood", "severity": "high", "name": "My flood", "onset_min": 0}
    """
    if not isinstance(items, list) or not items:
        raise ValueError("provide a non-empty list of hazards")
    if len(items) > MAX_FEATURES:
        raise ValueError(f"at most {MAX_FEATURES} hazards per scenario")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"feature {i + 1}: must be an object")
        if it.get("type") == "Feature":
            feat = it
        else:
            if it.get("shape", "circle") != "circle":
                raise ValueError(f"feature {i + 1}: shorthand supports shape 'circle' only; "
                                 "send a GeoJSON Feature for other shapes")
            lat = _num(it.get("lat"), f"feature {i + 1}: lat", -90, 90)
            lon = _num(it.get("lon"), f"feature {i + 1}: lon", -180, 180)
            rad = _num(it.get("radius_m"), f"feature {i + 1}: radius_m", 10, MAX_RADIUS_M)
            props = {k: v for k, v in it.items() if k not in ("shape", "lat", "lon", "radius_m")}
            feat = {"type": "Feature", "properties": props,
                    "geometry": {"type": "Polygon", "coordinates": [circle_ring(lat, lon, rad)]}}
        out.append(validate_feature(feat, i))
    return out


def _rings(geometry: dict) -> list:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [poly[0] for poly in geometry["coordinates"]]
    raise ValueError(f"Unsupported geometry type {geometry['type']!r} (use Polygon/MultiPolygon)")


def _effects(kind: str, props: dict, frac, exposed, eg: EdgeGeometry, idx):
    """Return per-edge (mult, risk, close_mask) arrays for one feature."""
    import numpy as np

    n = len(idx)
    ones = np.ones(n)
    zeros = np.zeros(n)
    nope = np.zeros(n, dtype=bool)
    if kind == "flood":
        sev = SEVERITY[props["severity"]]
        close = (frac >= FLOOD_CLOSE_FRACTION) if sev >= 1.0 else nope
        return 1 + ALPHA_FLOOD * frac * sev, exposed * sev, close
    if kind == "wildfire":
        lvl = props["intensity"]
        return 1 + frac * (1 / SMOKE_SPEED[lvl] - 1), exposed * SMOKE_RISK[lvl], nope
    if kind in ("wildfire_hotspot", "landslide_block"):
        return ones, zeros, np.ones(n, dtype=bool)
    if kind == "landslide":
        susc = SEVERITY[props["severity"]]
        vuln = eg.road_weight[idx]
        return 1 + ALPHA_LANDSLIDE * frac * susc * vuln, exposed * susc * vuln, nope
    if kind == "quake_damage":
        if props.get("state", "closed") == "closed":
            return ones, zeros, np.ones(n, dtype=bool)
        return ones * QUAKE_PARTIAL_MULT, exposed * QUAKE_PARTIAL_RISK, nope
    raise ValueError(f"Unknown hazard kind {kind!r}")


def build_state(csr: CSRGraph, eg: EdgeGeometry, proj: LocalProjection,
                fc: dict) -> tuple[HazardState, dict]:
    """Apply every feature of a FeatureCollection; return (state, summary)."""
    st = HazardState(csr.m)
    summary: dict[str, dict] = {}

    for feat in fc["features"]:
        props = feat.get("properties", {})
        kind = props["hazard"]
        onset_min = float(props.get("onset_min", 0))
        entry = summary.setdefault(kind, {"features": 0, "edges_touched": 0, "closed": 0,
                                          "scheduled": 0})
        entry["features"] += 1

        for ring in _rings(feat["geometry"]):
            idx, exposed = polygon_exposure(eg, proj.ring_xy(ring))
            if len(idx) == 0:
                continue
            frac = (exposed / eg.length[idx]).clip(0.0, 1.0)
            mult, risk, close = _effects(kind, props, frac, exposed, eg, idx)
            entry["edges_touched"] += len(idx)

            for e, m, r, c in zip(idx.tolist(), mult.tolist(), risk.tolist(), close.tolist()):
                if onset_min > 0:
                    st.schedule([e], onset_min * 60.0, INF if c else m)
                    entry["scheduled"] += 1
                elif c:
                    st.close([e])
                    entry["closed"] += 1
                else:
                    st.penalize([e], mult=m, risk=r)

    summary["_totals"] = {
        "closed_edges": st.n_closed(),
        "penalized_edges": sum(1 for x in st.mult if 1.0 < x < INF),
        "risky_edges": sum(1 for x in st.risk if x > 0),
        "scheduled_edges": sum(1 for x in st.onset if x < INF),
    }
    return st, summary


def load_scenario_file(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        fc = json.load(f)
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"{path} is not a GeoJSON FeatureCollection")
    return fc
