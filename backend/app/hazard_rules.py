"""How each hazard changes a road edge. Pure functions, no geometry libraries.

Every rule returns  (mult, risk, closed)  for one edge:
  mult    travel-time multiplier >= 1  (the road is slower)
  risk    exposure score, in "weighted exposed metres"
  closed  True if the edge is removed from routing

frac = share of the edge's length inside the hazard (0..1].
All thresholds and weights are PROPOSED design values (see design notes), meant to be tuned.
"""
from __future__ import annotations

SEVERITY = {"low": 0.3, "medium": 0.6, "high": 1.0}

# Smoke: speed factor on the exposed part, and exposure weight
SMOKE_SPEED = {"light": 0.85, "moderate": 0.6, "heavy": 0.35}
SMOKE_WEIGHT = {"light": 0.3, "moderate": 0.6, "heavy": 1.0}

# Landslide vulnerability by road class (major roads are better built)
ROAD_VULN = {
    "residential": 1.0, "unclassified": 1.0, "living_street": 1.0, "service": 1.0,
    "tertiary": 0.8, "secondary": 0.6, "primary": 0.4, "trunk": 0.3, "motorway": 0.3,
}

FLOOD_CLOSE_FRAC = 0.6          # high-severity flood covering >= 60% of an edge closes it
LANDSLIDE_CLOSE_SCORE = 0.7     # frac * susceptibility * vulnerability >= this closes the edge
PARTIAL_DAMAGE_MULT = 2.0       # partially damaged link: half speed

HAZARDS = ("flood", "smoke", "fire_hotspot", "landslide", "damage")


def normalize_highway(h) -> str:
    """OSM 'highway' can be a list, or carry a '_link' suffix."""
    if isinstance(h, (list, tuple)):
        h = h[0] if h else "residential"
    h = str(h or "residential")
    return h[:-5] if h.endswith("_link") else h


def _level(table: dict, props: dict, key: str) -> float:
    value = props.get(key)
    if value not in table:
        raise ValueError(f"feature {props.get('name')!r}: '{key}' must be one of {sorted(table)}, got {value!r}")
    return table[value]


def edge_effect(hazard: str, props: dict, frac: float, length_m: float, highway: str):
    """Return (mult, risk, closed) for one edge touched by one hazard feature."""
    frac = max(0.0, min(1.0, frac))

    if hazard == "flood":
        expo = frac * _level(SEVERITY, props, "severity")
        closed = props.get("severity") == "high" and frac >= FLOOD_CLOSE_FRAC
        return 1.0 + expo, expo * length_m, closed

    if hazard == "smoke":
        sf = _level(SMOKE_SPEED, props, "intensity")
        w = _level(SMOKE_WEIGHT, props, "intensity")
        return 1.0 + frac * (1.0 / sf - 1.0), frac * w * length_m, False

    if hazard == "fire_hotspot":
        return 1.0, length_m, True

    if hazard == "landslide":
        score = frac * _level(SEVERITY, props, "susceptibility") * ROAD_VULN.get(highway, 0.8)
        return 1.0 + score, score * length_m, score >= LANDSLIDE_CLOSE_SCORE

    if hazard == "damage":
        state = props.get("state")
        if state == "closed":
            return 1.0, length_m, True
        if state == "partial":
            return PARTIAL_DAMAGE_MULT, 0.5 * length_m, False
        raise ValueError(f"feature {props.get('name')!r}: 'state' must be 'closed' or 'partial', got {state!r}")

    raise ValueError(f"unknown hazard type {hazard!r}; expected one of {HAZARDS}")
