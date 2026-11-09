"""Write data/scenario_preview.html: a self-contained map of every scenario for checking.

    python scripts/preview_map.py     then open data/scenario_preview.html in a browser

Needs internet in the browser (Leaflet and map tiles load from CDNs).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR  # noqa: E402

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>DisasterRoute scenario preview</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{height:100%;margin:0} .note{position:absolute;z-index:999;bottom:8px;left:8px;
background:#fffd;padding:6px 10px;font:12px sans-serif;border-radius:4px;max-width:340px}</style>
</head><body><div id="map"></div>
<div class="note">SIMULATED hazards, approximate positions. Check each circle against the real place,
then edit app/scenario_sites.py.</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA__;
const map = L.map('map').setView([12.97, 77.59], 11);
// Several key-free basemaps: use the radio buttons (top right) if one fails to load.
// tile.openstreetmap.org blocks pages opened from disk (file://) with "403 Access blocked";
// it works when the page is served over http (see the note in the README / chat).
const bases = {
  'Esri street map': L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    {maxZoom: 19, attribution: 'Tiles &copy; Esri'}),
  'OpenTopoMap': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    {maxZoom: 17, subdomains: 'abc',
     attribution: '&copy; OpenStreetMap contributors, SRTM | OpenTopoMap'}),
  'OpenStreetMap (only when served over http://localhost)': L.tileLayer(
    'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}),
};
bases['Esri street map'].addTo(map);
const COLORS = {flood: '#1f77b4', wildfire: '#d95f02', wildfire_hotspot: '#b30000',
  landslide: '#8c6d31', landslide_block: '#5b3a00', quake_damage: '#7b2d8e'};
const OPACITY = {low: 0.15, medium: 0.3, high: 0.5, light: 0.15, moderate: 0.3, heavy: 0.5};
const layers = {};
for (const [name, fc] of Object.entries(DATA)) {
  layers[name] = L.geoJSON(fc, {
    style: f => { const p = f.properties; return {color: COLORS[p.hazard] || '#333', weight: 1,
      fillColor: COLORS[p.hazard] || '#333',
      fillOpacity: OPACITY[p.severity || p.intensity] || 0.4}; },
    onEachFeature: (f, l) => { const p = f.properties;
      l.bindPopup(`<b>${p.name}</b><br>${p.hazard} ${p.severity || p.intensity || p.state || ''}` +
        `<br>onset: ${p.onset_min} min`); }
  });
}
const first = Object.keys(layers)[0]; layers[first].addTo(map);
L.control.layers(bases, layers, {collapsed: false}).addTo(map);   // scenarios = tick boxes
</script></body></html>
"""


def main() -> None:
    folder = DATA_DIR / "scenarios"
    data = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.geojson"))}
    if not data:
        sys.exit("No scenarios found. Run: python scripts/make_scenarios.py")
    out = DATA_DIR / "scenario_preview.html"
    out.write_text(HTML.replace("__DATA__", json.dumps(data)), encoding="utf-8")
    print(f"wrote {out}  (open it in your browser)")


if __name__ == "__main__":
    main()
