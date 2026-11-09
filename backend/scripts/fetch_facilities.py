"""Fetch hospitals (and candidate shelters) for Bengaluru from OpenStreetMap.

    python scripts/fetch_facilities.py

Writes data/facilities.json:  [{"id", "name", "kind": "hospital"|"shelter", "lat", "lon",
                               "opening_hours"?, "emergency"?}, ...]
Needs internet.  Then run scripts/prune_facilities.py to drop clinics and unsuitable shelters.  Shelters are community centres and stadiums found in OSM; REVIEW the list and
delete anything unsuitable (OSM is volunteer-edited, so names and coverage vary).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import osmnx as ox  # noqa: E402

from app.config import DATA_DIR, PLACE_NAME  # noqa: E402

QUERIES = [
    ("hospital", {"amenity": "hospital"}),
    ("shelter", {"amenity": "community_centre"}),
    ("shelter", {"leisure": "stadium"}),
]


def centroids(gdf):
    """Centroids computed in a metric CRS, returned as (lat, lon)."""
    proj = gdf.to_crs(32643)
    c = proj.geometry.centroid
    ll = c.set_crs(32643).to_crs(4326)
    return ll.y.values, ll.x.values


def main() -> None:
    out, seen = [], set()
    for kind, tags in QUERIES:
        print(f"Fetching {kind} {tags} ...")
        try:
            gdf = ox.features_from_place(PLACE_NAME, tags)
        except Exception as exc:  # e.g. no features match
            print(f"  skipped: {exc}")
            continue
        lats, lons = centroids(gdf)
        names = gdf["name"] if "name" in gdf.columns else [None] * len(gdf)
        hours = gdf["opening_hours"] if "opening_hours" in gdf.columns else [None] * len(gdf)
        emerg = gdf["emergency"] if "emergency" in gdf.columns else [None] * len(gdf)
        extra = {k: (gdf[k] if k in gdf.columns else [None] * len(gdf))
                 for k in ("healthcare", "beds", "capacity", "operator:type", "healthcare:speciality")}
        areas = gdf.to_crs(32643).geometry.area.values          # square metres (0 for points)
        for i, ((osm_key, _), name, oh, em, lat, lon) in enumerate(zip(gdf.index, names, hours, emerg, lats, lons)):
            key = (kind, round(lat, 5), round(lon, 5))
            if key in seen:
                continue
            seen.add(key)
            label = name if isinstance(name, str) and name else f"Unnamed {kind}"
            row = {"id": f"{kind}-{len(out)}", "name": label, "kind": kind,
                   "lat": round(float(lat), 6), "lon": round(float(lon), 6)}
            if isinstance(oh, str) and oh.strip():
                row["opening_hours"] = oh.strip()
            if isinstance(em, str) and em.strip():
                row["emergency"] = em.strip()
            for k, col in extra.items():                       # tags used by scripts/prune_facilities.py
                v = col.iloc[i] if hasattr(col, "iloc") else col[i]
                if isinstance(v, str) and v.strip():
                    row[k.replace(":", "_")] = v.strip()
            if areas[i] > 0:
                row["area_m2"] = round(float(areas[i]))
            out.append(row)
        print(f"  total so far: {len(out)}")

    path = DATA_DIR / "facilities.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    n_h = sum(1 for f in out if f["kind"] == "hospital")
    print(f"Saved {path}: {n_h} hospitals, {len(out) - n_h} candidate shelters")
    from app.opening_hours import is_open
    from datetime import datetime
    with_h = [f for f in out if f.get("opening_hours")]
    readable = [f for f in with_h if is_open(f["opening_hours"], datetime(2026, 1, 5, 12, 0)) is not None]
    print(f"  opening hours listed for {len(with_h)} of {len(out)} (readable: {len(readable)}); "
          f"emergency=yes on {sum(1 for f in out if f.get('emergency') == 'yes')}")


if __name__ == "__main__":
    main()
