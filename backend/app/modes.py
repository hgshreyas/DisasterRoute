"""How long a trip really takes by car, bike or bus in Bengaluru.

The road graph gives a free-flow time per road (OSM speed limits or class defaults, about 36 km/h
on typical trips).  Real Bengaluru traffic is far slower, so a trip is rescaled:

        trip time = (graph time x hazard multipliers) x k  +  fixed wait

where k = REF_FREEFLOW_KMH / (typical speed for that mode and traffic level).  Uniform scaling does
not change WHICH route is best, so the routing engine and its A* bounds are untouched; only the
reported times (and time cut-offs) change.

Speeds (km/h), where they come from:
  car   normal 16.6 = TomTom Traffic Index 2025, Bengaluru: 36 min 9 s per 10 km, all-day average.
        peak   13.9 = same report, average rush-hour speed (13.2 evening, 14.6 morning).
        light  23   = ASSUMPTION for night / early-morning traffic.
  bike  ASSUMPTION: about 20% faster than a car in congestion (two-wheelers filter through traffic).
  bus   peak   12   = BMTC average speed reported in 2018 (old figure; treat as indicative).
        normal 14, light 18 = ASSUMPTIONS.  fixed 12 min = ASSUMED average wait plus walks to and from stops.
These are estimates for planning, not timetables or live traffic.  Edit the tables below to tune them.
"""
from __future__ import annotations

from dataclasses import dataclass

REF_FREEFLOW_KMH = 35.8          # measured on the Bengaluru graph: scripts/calibrate_speed.py, 150 trips 3-12 km apart

TRAFFIC = {
    "light": "Light (night, early morning)",
    "normal": "Normal (all-day average)",
    "peak": "Peak (rush hour)",
}

MODES = {
    "car":  {"label": "Car", "speeds": {"light": 23.0, "normal": 16.6, "peak": 13.9}, "fixed_min": 0.0},
    "bike": {"label": "Bike or scooter", "speeds": {"light": 26.0, "normal": 20.0, "peak": 17.0}, "fixed_min": 0.0},
    "bus":  {"label": "Bus (BMTC)", "speeds": {"light": 18.0, "normal": 14.0, "peak": 12.0}, "fixed_min": 12.0},
}


@dataclass(frozen=True)
class TravelModel:
    mode: str
    traffic: str
    speed_kmh: float
    k: float
    fixed_s: float

    def trip(self, free_flow_s: float) -> float:
        """Real trip seconds for a route whose graph time is `free_flow_s`."""
        return free_flow_s * self.k + self.fixed_s


def get_model(mode: str = "car", traffic: str = "normal") -> TravelModel:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}")
    if traffic not in TRAFFIC:
        raise ValueError(f"traffic must be one of {list(TRAFFIC)}")
    sp = MODES[mode]["speeds"][traffic]
    return TravelModel(mode, traffic, sp, REF_FREEFLOW_KMH / sp, MODES[mode]["fixed_min"] * 60.0)


def scale_times(obj, tm: TravelModel):
    """Rescale every complete-route "travel_time_s" inside a response (dicts and lists, in place)."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "travel_time_s" and isinstance(val, (int, float)):
                obj[key] = round(tm.trip(val), 1)
            else:
                scale_times(val, tm)
    elif isinstance(obj, list):
        for item in obj:
            scale_times(item, tm)
    return obj


def describe() -> dict:
    return {"reference_free_flow_kmh": REF_FREEFLOW_KMH, "traffic": TRAFFIC,
            "modes": {m: {"label": v["label"], "speeds_kmh": v["speeds"], "fixed_min": v["fixed_min"]}
                      for m, v in MODES.items()}}
