"""Generate the simulated hazard scenarios as GeoJSON files in data/scenarios/.

    python scripts/make_scenarios.py

Outputs: flood.geojson, flood_rising.geojson (grows over time), wildfire.geojson,
landslide.geojson, earthquake.geojson.  Needs nothing but the standard library.
Edit app/scenario_sites.py to move or resize hazards, then run again.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import scenario_sites as S  # noqa: E402
from app.config import DATA_DIR  # noqa: E402
from app.hazards import circle_ring  # noqa: E402

SCENARIO_DIR = DATA_DIR / "scenarios"


circle = circle_ring    # shared with the runtime hazard API (app/hazards.py)


def feature(ring: list, **props) -> dict:
    props["simulated"] = True
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def collection(name: str, features: list) -> dict:
    return {"type": "FeatureCollection", "name": name, "features": features}


def flood() -> dict:
    feats = []
    for name, lat, lon, r in S.FLOOD_SITES:
        for sev, scale in (("low", 1.0), ("medium", 0.6), ("high", 0.3)):
            feats.append(feature(circle(lat, lon, r * scale), hazard="flood", severity=sev,
                                 name=name, onset_min=0))
    return collection("flood", feats)


def flood_rising() -> dict:
    """Same sites, but the water spreads: worse zones switch on at 30 and 60 minutes."""
    feats = []
    for name, lat, lon, r in S.FLOOD_SITES:
        stages = [  # (onset_min, severity, radius scale)
            (0, "medium", 0.35),
            (30, "high", 0.35), (30, "medium", 0.65),
            (60, "high", 0.65), (60, "low", 1.0),
        ]
        for onset, sev, scale in stages:
            feats.append(feature(circle(lat, lon, r * scale), hazard="flood", severity=sev,
                                 name=name, onset_min=onset))
    return collection("flood_rising", feats)


def wildfire() -> dict:
    name, lat, lon, r = S.WILDFIRE["hotspot"]
    feats = [feature(circle(lat, lon, r), hazard="wildfire_hotspot", name=name, onset_min=0)]
    pname, plat, plon = S.WILDFIRE["smoke"]
    for lvl in ("light", "moderate", "heavy"):
        feats.append(feature(circle(plat, plon, S.WILDFIRE["smoke_radii_m"][lvl]),
                             hazard="wildfire", intensity=lvl, name=pname, onset_min=0))
    return collection("wildfire", feats)


def landslide() -> dict:
    feats = []
    for name, lat, lon, r in S.LANDSLIDE_SITES:
        feats.append(feature(circle(lat, lon, r), hazard="landslide", severity="medium",
                             name=name, onset_min=0))
        feats.append(feature(circle(lat, lon, r * 0.5), hazard="landslide", severity="high",
                             name=name, onset_min=0))
    for name, lat, lon, r in S.LANDSLIDE_BLOCKS:
        feats.append(feature(circle(lat, lon, r), hazard="landslide_block", name=name,
                             onset_min=0))
    return collection("landslide", feats)


def earthquake() -> dict:
    feats = [feature(circle(lat, lon, r), hazard="quake_damage", state=state, name=name,
                     onset_min=0)
             for name, lat, lon, r, state in S.QUAKE_DAMAGE]
    return collection("earthquake", feats)


def main() -> None:
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in [("flood", flood), ("flood_rising", flood_rising), ("wildfire", wildfire),
                     ("landslide", landslide), ("earthquake", earthquake)]:
        fc = fn()
        path = SCENARIO_DIR / f"{name}.geojson"
        path.write_text(json.dumps(fc, indent=1), encoding="utf-8")
        print(f"wrote {path}  ({len(fc['features'])} features)")


if __name__ == "__main__":
    main()
