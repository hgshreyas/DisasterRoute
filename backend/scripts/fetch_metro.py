"""Download the Namma Metro lines and stations from OpenStreetMap and save data/metro.json.

    python scripts/fetch_metro.py             download and save
    python scripts/fetch_metro.py --reparse   rebuild metro.json from the saved data/metro_raw.json (no download)

Asks the public Overpass API (the OpenStreetMap service osmnx also uses) for the metro route
relations, keeps the stops in running order, and saves them for app/transit.py.

CHECK THE OUTPUT: it lists every line with its station count and end stations.  Compare with the
real network.  If a line is missing or has odd stops, OpenStreetMap's tagging differs from what
app/transit.py expects: send me the printed summary.  The raw answer is saved to data/metro_raw.json.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import transit
from app.config import DATA_DIR, METRO_PATH

ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def overpass(query: str) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for url in ENDPOINTS:
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "DisasterRoute-student-project/1.0"})
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:                  # try the next mirror
            last = exc
            print(f"  {url} failed: {exc}")
            time.sleep(2)
    raise SystemExit(f"Could not reach Overpass: {last}")


def main():
    raw = DATA_DIR / "metro_raw.json"
    if "--reparse" in sys.argv:
        print(f"Reading {raw} (no download) ...")
        data = json.loads(raw.read_text(encoding="utf-8"))
    else:
        print("Asking OpenStreetMap for the metro routes ...")
        data = overpass(transit.OVERPASS_QUERY)
        raw.write_text(json.dumps(data), encoding="utf-8")
    n_rel = sum(1 for e in data.get("elements", []) if e.get("type") == "relation")
    print(f"  {n_rel} route relations, {len(data.get('elements', []))} elements in all")
    net = transit.parse_overpass(data)
    if not net["lines"]:
        raise SystemExit("No metro line with two or more stops could be read from the answer.")
    METRO_PATH.write_text(json.dumps(net, ensure_ascii=False), encoding="utf-8")
    n = sum(len(l["stations"]) for l in net["lines"])
    print(f"Saved {METRO_PATH}: {len(net['lines'])} lines, {n} station stops")
    for ln in net["lines"]:
        sts = ln["stations"]
        unnamed = sum(1 for s in sts if s["name"].startswith("Station "))
        print(f"  {ln['name']:<28} {len(sts):>3} stations: {sts[0]['name']} ... {sts[-1]['name']}"
              + (f"   ({unnamed} without a name in OpenStreetMap)" if unnamed else ""))
        gaps = [(transit.haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]), a["name"], b["name"]) for a, b in zip(sts, sts[1:])]
        far = [g for g in gaps if g[0] > 3500]
        for g, a, b in far:                           # neighbouring Namma Metro stations are 1-3 km apart
            print(f"    WARNING: {a} -> {b} is {g / 1000:.1f} km apart; a stop may be wrong")
    loaded = transit.MetroNetwork(net)
    inter = sorted({loaded.station(x)["name"] for grp in loaded.by_complex.values()
                    if len({loaded.nodes[x][0] for x in grp}) > 1 for x in grp})
    print(f"  interchanges found: {', '.join(inter) if inter else 'none'}")


if __name__ == "__main__":
    main()
