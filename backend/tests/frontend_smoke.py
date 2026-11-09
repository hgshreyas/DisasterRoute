"""End-to-end smoke test of the map page: real app/main.py (through a tiny FastAPI stand-in),
real routing/hazard code on a synthetic city, real frontend/index.html in headless Chromium.

Leaflet itself is replaced by tests/leaflet_stub.js (the sandbox cannot download it), so this checks
the page's logic, API calls and what it asks the map to draw, NOT how the map looks.

Run from backend/:   python tests/frontend_smoke.py     (needs playwright + chromium)
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import fake_fastapi

fake_fastapi.install()

import uvicorn
from playwright.sync_api import sync_playwright

import app.graph as graph_mod
import app.main as M
import test_access as TA
import test_hazards as TH
import test_metro as TM
from app.engine import Landmarks
from app.modes import REF_FREEFLOW_KMH as REF
from app.graph import RoadGraph

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
STUB = (HERE / "leaflet_stub.js").read_text(encoding="utf-8")


def start_server(rg, facilities_path):
    M.get_graph = lambda: rg
    graph_mod.FACILITIES_PATH = facilities_path
    server = uvicorn.Server(uvicorn.Config(fake_fastapi.build_asgi(M.app), host="127.0.0.1",
                                           port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("server did not start")


def ev(page, js):
    return page.evaluate(js)


def wait_idle(page):
    page.wait_for_function("document.getElementById('spinner').hidden === true", timeout=20000)


def main():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d) / "scenarios"
        TH.write_scenarios(folder)
        G = TH.synthetic_city()
        probe = RoadGraph(G, None, folder)
        lm = Path(d) / "lm.npz"
        Landmarks.build(probe.csr, k=6, seed=0).save(lm, probe.csr)
        rg = RoadGraph(G, lm, folder)
        TA.make_facilities(rg, Path(d))
        fpath = Path(d) / "facilities.json"
        rows = json.loads(fpath.read_text(encoding="utf-8"))
        extra = {"hospital-0": {"opening_hours": "24/7"},
                 "hospital-1": {"opening_hours": "Mo-Su 00:00-01:00"},      # closed at midday
                 "hospital-2": {"emergency": "yes"},                       # no hours, emergency dept
                 "shelter-4": {"opening_hours": "Mo-Su 08:00-20:00"}}
        for r in rows:
            r.update(extra.get(r["id"], {}))
        fpath.write_text(json.dumps(rows), encoding="utf-8")
        (Path(d) / "metro.json").write_text(json.dumps(TM.fake_metro(rg)), encoding="utf-8")
        M.METRO_PATH = Path(d) / "metro.json"
        server = start_server(rg, Path(d) / "facilities.json")

        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["geolocation"],
                                      geolocation={"latitude": 12.9180, "longitude": 77.6000, "accuracy": 20})
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
            page.route("**/unpkg.com/**leaflet.js", lambda r: r.fulfill(body=STUB, content_type="application/javascript"))
            page.route("**/unpkg.com/**leaflet.css", lambda r: r.fulfill(body="", content_type="text/css"))

            def nominatim(route):
                q = route.request.url
                pt = ("12.9177", "77.5960") if "Silk" in q else ("12.9177", "77.6500")
                body = [{"display_name": ("Silk Board Junction" if "Silk" in q else "Far Place") + ", Bengaluru, Karnataka, India",
                         "lat": pt[0], "lon": pt[1]}]
                route.fulfill(body=json.dumps(body), content_type="application/json",
                              headers={"access-control-allow-origin": "*"})
            page.route("**/nominatim.openstreetmap.org/**", nominatim)

            # 0. root redirects to the page
            page.goto(BASE + "/")
            assert page.url.endswith("/app/"), page.url
            page.wait_for_function("document.querySelectorAll('#scenario option').length >= 6")
            names = ev(page, "[...document.querySelectorAll('#scenario option')].map(o => o.value)")
            assert "flood" in names and "flood_rising" in names, names
            print("  page loads, scenario list:", names)

            # 1. choosing a scenario draws its polygons and shows a summary
            page.select_option("#scenario", "flood")
            page.wait_for_function("document.getElementById('scenarioInfo').textContent.length > 0")
            wait_idle(page)
            assert ev(page, "hazardLayer.items.length") >= 1
            info = page.inner_text("#scenarioInfo")
            assert "closed" in info, info
            print("  scenario:", info)

            # 1b. type start and destination instead of clicking
            page.fill("#qFrom", "Silk Board"); page.click("#goFrom")
            page.click("#geoListA button")
            assert ev(page, "$('goFrom').parentElement.nextElementSibling.id") == "geoListA"      # list sits under Start
            assert ev(page, "$('goTo').parentElement.nextElementSibling.id") == "geoListB"        # and under Destination
            assert abs(ev(page, "S.origin.lat") - 12.9177) < 1e-6 and ev(page, "S.dest") is None
            page.fill("#qTo", "Far Place"); page.press("#qTo", "Enter")
            page.wait_for_selector("#geoListB button")
            assert page.is_visible("#geoListB") and not page.is_visible("#geoListA")
            page.click("#geoListB button")
            page.wait_for_selector("#results .card"); wait_idle(page)
            assert ev(page, "pinLayer.items.length") == 2
            print("  typed start and destination ->", page.inner_text("#qFrom"), "|", ev(page, "$('qTo').value"))
            page.fill("#qFrom", "ab"); page.click("#goFrom")
            page.wait_for_selector("#error:not([hidden])")
            assert "3 letters" in page.inner_text("#error")

            # 1c. facilities near the destination, filtered by opening hours at the arrival time
            page.wait_for_function("document.querySelectorAll('#nearDest .row2').length > 0", timeout=20000)
            monday_noon = "2026-11-09T12:00"
            api = ("/accessibility/origin?lat=12.9177&lon=77.6500&scenario=none&max_minutes=60&limit=60"
                   "&kind=hospital&at=" + monday_noon)
            only = page.evaluate(f"fetch('{api}&open_only=true').then(r => r.json())")["facilities"]
            allr = page.evaluate(f"fetch('{api}&open_only=false').then(r => r.json())")["facilities"]
            st_ = {r["id"]: r["open_status"] for r in allr}
            assert st_["hospital-0"] == "open", st_
            assert st_["hospital-1"] == "closed", st_
            assert st_["hospital-2"] == "assumed_open", st_
            assert st_["hospital-3"] == "unknown", st_
            assert all(r["open_status"] != "closed" for r in only) and "hospital-1" not in [r["id"] for r in only]
            bad = page.evaluate("fetch('/accessibility/origin?lat=12.9&lon=77.6&at=nonsense').then(r => r.status)")
            assert bad == 400, bad
            errors[:] = [e for e in errors if "status of 400" not in e]      # that 400 was deliberate
            page.fill("#needAt", monday_noon)
            page.wait_for_function("document.getElementById('nearDest').textContent.includes('Open when you arrive')", timeout=20000)
            txt = page.inner_text("#nearDest")
            assert "Closed when you arrive" not in txt and "Opening hours not listed" in txt, txt
            page.click("#nearDest button[data-near]")
            page.wait_for_function("nearLayer.items.length >= 2", timeout=20000)
            assert "#0d9488" in ev(page, "nearLayer.items.map(i => i.style.color)")
            print("  near destination: hours filter ok, route to facility drawn")

            # 1c2. traffic follows the clock when set to Auto (the default)
            assert page.input_value("#traffic") == "auto"
            cases = {(2026, 10, 9, 9, 0): "peak", (2026, 10, 9, 13, 0): "normal", (2026, 10, 9, 18, 30): "peak",
                     (2026, 10, 9, 21, 0): "normal", (2026, 10, 9, 11, 0): "normal", (2026, 10, 10, 23, 0): "light",
                     (2026, 10, 10, 5, 59): "light", (2026, 10, 14, 18, 0): "normal",     # Saturday evening
                     (2026, 10, 15, 9, 0): "normal"}                                       # Sunday morning
            for (y, mo, d_, h, mi), want in cases.items():
                got = ev(page, f"trafficAt(new Date({y}, {mo}, {d_}, {h}, {mi}))")
                assert got == want, ((y, mo, d_, h, mi), got, want)
            urls = []
            page.on("request", lambda r: urls.append(r.url) if "/accessibility/origin" in r.url else None)
            page.fill("#needAt", "2026-11-09T09:00")
            page.wait_for_function("document.getElementById('nearDest').textContent.length > 0"); wait_idle(page)
            page.fill("#needAt", "2026-11-09T13:00"); wait_idle(page)
            page.wait_for_timeout(500)
            assert any("traffic=peak" in u for u in urls) and any("traffic=normal" in u for u in urls), urls[-3:]
            assert "detected from the current time" in page.inner_text("#modeNote")
            print("  traffic auto-detected from time: Mon 09:00 peak, Mon 13:00 normal, night light")

            # 1d. travel mode and traffic rescale times to realistic speeds
            q = "origin_lat=12.9177&origin_lon=77.5960&dest_lat=12.9177&dest_lon=77.6500&scenario=none&lam=0"
            def route(extra):
                return page.evaluate(f"fetch('/route?{q}&{extra}').then(r => r.json())")
            light, normal, peak = (route(f"mode=car&traffic={t}")["travel_time_s"] for t in ("light", "normal", "peak"))
            bike, bus = route("mode=bike&traffic=normal")["travel_time_s"], route("mode=bus&traffic=normal")["travel_time_s"]
            ff = light / (REF / 23.0)                                   # graph (free-flow) seconds
            assert abs(normal - ff * REF / 16.6) < 1.0 and abs(peak - ff * REF / 13.9) < 1.0, (ff, normal, peak)
            assert light < normal < peak and bike < normal, (light, normal, peak, bike)
            assert abs(bus - (ff * REF / 14.0 + 720.0)) < 1.0, (bus, ff)      # 12 min wait and walking added
            assert abs(route("")["travel_time_s"] - normal) < 1.0                # car at normal traffic is the default
            td = page.evaluate("fetch('/route/time-dependent?" + q + "&scenario=flood_rising&mode=bus&depart_min=0').then(r => r.json())")
            assert td["travel_time_s"] >= 720 and abs(td["arrival_min"] - td["travel_time_s"] / 60) < 0.02, td
            acc = page.evaluate("fetch('/accessibility/origin?lat=12.9177&lon=77.6500&kind=hospital&max_minutes=240&limit=3&mode=car&traffic=light').then(r => r.json())")["facilities"][0]
            acc2 = page.evaluate("fetch('/accessibility/origin?lat=12.9177&lon=77.6500&kind=hospital&max_minutes=240&limit=3&mode=bus&traffic=normal').then(r => r.json())")["facilities"][0]
            assert acc["id"] == acc2["id"] and acc2["minutes_scenario"] > acc["minutes_scenario"] + 11, (acc, acc2)
            assert page.evaluate("fetch('/route?" + q + "&mode=metro').then(r => r.status)") == 400
            errors[:] = [e for e in errors if "status of 400" not in e]
            before = page.inner_text("#results")
            page.select_option("#mode", "bus"); page.select_option("#traffic", "peak")
            page.wait_for_function("document.getElementById('modeNote').textContent.includes('BMTC')")
            wait_idle(page)
            assert page.inner_text("#results") != before
            assert "door to door" in page.inner_text("#results")
            print(f"  modes: car light/normal/peak = {light/60:.1f}/{normal/60:.1f}/{peak/60:.1f} min, bike {bike/60:.1f}, bus {bus/60:.1f}")
            page.select_option("#mode", "car"); page.select_option("#traffic", "normal"); wait_idle(page)

            # 1e. metro journeys
            page.select_option("#scenario", "none"); wait_idle(page)
            lines = page.evaluate("fetch('/metro').then(r => r.json())")["lines"]
            assert [l["name"] for l in lines] == ["Line A", "Line B"]
            page.check("#showMetro"); wait_idle(page)
            page.select_option("#mode", "metro")
            assert "Wikipedia" in page.inner_text("#modeNote")
            page.evaluate("S.origin = {lat: 12.920, lng: 77.599}; S.dest = {lat: 12.908, lng: 77.627}; drawPins(); findRoutes();")
            page.wait_for_function("document.getElementById('results').textContent.includes('Metro journey')", timeout=20000)
            wait_idle(page)
            txt = page.inner_text("#results")
            assert "Change from Line A to Line B" in txt and "Wait about" in txt and "By car for comparison" in txt, txt
            cols = ev(page, "routeLayer.items.map(i => i.style && i.style.color)")
            assert "#7c3aed" in cols and "#16a34a" in cols and "#6b7280" in cols, cols
            page.wait_for_function("document.querySelectorAll('#nearDest .row2').length > 0", timeout=20000)
            assert "by car" in page.inner_text("#nearDest")
            print("  metro journey:", txt.split("\n")[1], "|", [l for l in txt.split("\n") if l.startswith("Change")][0])
            page.evaluate("S.origin = {lat: 12.900, lng: 77.595}; drawPins(); findRoutes();")   # corner: no station within a 30 min walk
            page.wait_for_function("document.getElementById('results').textContent.includes('No metro station')", timeout=20000)
            wait_idle(page)
            assert page.evaluate("fetch('/route/metro?origin_lat=12.92&origin_lon=77.6&dest_lat=12.91&dest_lon=77.62&traffic=bad').then(r => r.status)") == 400
            errors[:] = [e for e in errors if "status of 400" not in e]
            flood = page.evaluate("fetch('/route/metro?origin_lat=12.920&origin_lon=77.599&dest_lat=12.908&dest_lon=77.627&scenario=flood').then(r => r.json())")
            print("  metro under the flood scenario: ok =", flood["ok"], "| stations closed:", flood.get("closed_stations"))
            made = page.evaluate("""fetch('/scenarios', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: 'zz-station', overwrite: true, features: [{shape: 'circle', lat: 12.920, lon: 77.625,
                radius_m: 150, hazard: 'flood', severity: 'high', name: 'on the interchange'}]})}).then(r => r.status)""")
            assert made == 201, made
            hit = page.evaluate("fetch('/route/metro?origin_lat=12.920&origin_lon=77.599&dest_lat=12.908&dest_lon=77.627&scenario=zz-station').then(r => r.json())")
            assert "Junction" in hit["closed_stations"], hit
            assert not hit["ok"] or not any(l["type"] == "transfer" and l["at"] == "Junction" for l in hit["legs"]), hit
            print("  station inside a closing hazard is closed; journey now:", "none" if not hit["ok"] else f"{hit['total_min']} min")
            page.evaluate("fetch('/scenarios/zz-station', {method: 'DELETE'})")
            page.select_option("#mode", "car"); wait_idle(page)

            # 2. example route: the normal route is blocked, a hazard-aware route is drawn
            page.click("#example")
            page.wait_for_selector("#results .card")
            wait_idle(page)
            txt = page.inner_text("#results")
            assert "BLOCKED" in txt and "Hazard-aware route" in txt, txt
            colors = ev(page, "routeLayer.items.map(i => i.style.color)")
            assert "#2563eb" in colors and "#16a34a" in colors, colors
            assert ev(page, "pinLayer.items.length") == 2
            green_before = ev(page, "routeLayer.items.find(i => i.style.color === '#16a34a').ll")
            print("  routes drawn:", colors, "| normal route reported BLOCKED")

            # 3. close a road ON the hazard-aware route: list updates, route changes
            page.click("#modes button[data-mode=close]")
            i = len(green_before) // 2
            mid = [(green_before[i][0] + green_before[i + 1][0]) / 2, (green_before[i][1] + green_before[i + 1][1]) / 2]
            page.evaluate("([la, lo]) => __map.handlers.click({latlng: {lat: la, lng: lo}})", mid)
            page.wait_for_selector("#closedList li")
            wait_idle(page)
            assert ev(page, "closeLayer.items.length") == 1
            green_after = ev(page, "(routeLayer.items.find(i => i.style.color === '#16a34a') || {}).ll || null")
            assert green_after is not None and green_after != green_before, "route should change after closing a road on it"
            print("  closed a road:", page.inner_text("#closedList"), "| route re-planned")
            page.click("#closedList button")
            wait_idle(page)
            assert page.locator("#closedList li").count() == 0 and ev(page, "closeLayer.items.length") == 0

            # 4. create hazards from the map (nothing hard-coded): flood, then quake damage
            page.click("#modes button[data-mode=hazard]")
            assert page.is_visible("#hazardForm")
            page.select_option("#hType", "flood"); page.select_option("#hLevel", "high")
            page.fill("#hRadius", "400")
            page.evaluate("__map.handlers.click({latlng: {lat: 12.9180, lng: 77.6226}})")
            page.wait_for_function("document.getElementById('scenario').value === 'custom'")
            wait_idle(page)
            page.select_option("#hType", "quake")
            assert not page.is_visible("#hLevel") or page.input_value("#hLevel") == "closed"
            page.evaluate("__map.handlers.click({latlng: {lat: 12.9300, lng: 77.6300}})")
            page.wait_for_function("document.getElementById('scenarioInfo').textContent.includes('closed')")
            wait_idle(page)
            saved = page.evaluate("fetch('/scenarios/custom').then(r => r.json())")
            kinds = [f["properties"]["hazard"] for f in saved["geojson"]["features"]]
            assert kinds == ["flood", "quake_damage"], kinds
            assert saved["summary"]["_totals"]["closed_edges"] > 0
            print("  hazards created in the UI and saved:", kinds, saved["summary"]["_totals"])

            # 5. a bad hazard is rejected with a message instead of silently failing
            page.fill("#hRadius", "999999")
            page.evaluate("__map.handlers.click({latlng: {lat: 12.93, lng: 77.62}})")
            page.wait_for_selector("#error:not([hidden])")
            assert "Radius" in page.inner_text("#error") or "radius" in page.inner_text("#error")
            page.fill("#hRadius", "300")

            # 6. facilities + accessibility
            page.check("#showFac")
            page.wait_for_function("facLayer.items.length > 0")
            n_fac = ev(page, "facLayer.items.length")
            # facilities inside a hazard zone are flagged (flood scenario is selected here? use a known zone)
            page.select_option("#scenario", "flood"); wait_idle(page)
            cen = ev(page, "(() => { const r = S.hazGeo.features[0].geometry.coordinates[0];"
                           " return [r.reduce((a, c) => a + c[1], 0) / r.length, r.reduce((a, c) => a + c[0], 0) / r.length]; })()")
            assert ev(page, f"hazardAt({cen[0]}, {cen[1]})") is not None, "centre of a flood zone should be inside it"
            assert ev(page, "hazardAt(0, 0)") is None
            assert "facilities lie inside a hazard zone" in page.inner_text("#facNote"), page.inner_text("#facNote")
            print("  facility hazard flag:", page.inner_text("#facNote"))
            page.click("#accessBtn")
            page.wait_for_function("document.getElementById('access').textContent.includes('%')")
            acc = page.inner_text("#access")
            assert "undefined" not in acc and "NaN" not in acc, acc
            print(f"  facilities drawn: {n_fac}; coverage text: {acc[:110]}...")

            # 6b. live location as the start
            page.click("#useLoc")
            try:
                page.wait_for_function("document.getElementById('locNote').textContent.includes('Start set')", timeout=5000)
            except Exception:
                print("DEBUG note:", ev(page, "$('locNote').textContent"), "| geo:", ev(page, "!!navigator.geolocation"))
                raise
            assert abs(ev(page, "S.origin.lat") - 12.9180) < 1e-6 and page.input_value("#qFrom") == "My location"
            ctx.set_geolocation({"latitude": 12.9180, "longitude": 77.6050, "accuracy": 20})
            page.check("#followLoc")
            try:
                page.wait_for_function("Math.abs(S.origin.lng - 77.6050) < 1e-6", timeout=5000)
            except Exception:
                print("DEBUG follow:", ev(page, "JSON.stringify(S.origin)"), ev(page, "$('locNote').textContent"))
                raise
            page.uncheck("#followLoc")
            ctx.set_geolocation({"latitude": 28.61, "longitude": 77.21, "accuracy": 20})      # Delhi
            page.click("#useLoc")
            try:
                page.wait_for_function("document.getElementById('locNote').textContent.includes('outside Bengaluru')", timeout=5000)
            except Exception:
                print("DEBUG outside:", ev(page, "JSON.stringify(S.origin)"), ev(page, "$('locNote').textContent"))
                raise
            assert abs(ev(page, "S.origin.lng") - 77.6050) < 1e-6, "a position outside the city must not replace the start"
            print("  live location: start set, follow updated it, outside-city fix rejected")
            wait_idle(page)

            # 7. spreading flood: time-dependent route appears
            page.select_option("#scenario", "flood_rising")
            page.wait_for_function("!document.getElementById('depRow').hidden")
            wait_idle(page)
            page.select_option("#depart", "30")
            page.wait_for_function("document.getElementById('results').textContent.includes('Time-dependent')")
            wait_idle(page)
            assert "#ea580c" in ev(page, "routeLayer.items.map(i => i.style.color)")
            print("  time-dependent route drawn for flood_rising")
            page.wait_for_function("document.getElementById('results').textContent.includes('Rerouting after the change')")
            note = page.inner_text("#results").split("Rerouting after the change")[1][:200]
            assert "undefined" not in note and "NaN" not in note, note
            print("  replan note:", " ".join(note.split())[:150])

            # 8. delete my hazards
            page.click("#hClear")
            page.wait_for_function("document.getElementById('scenario').value === 'none'")
            wait_idle(page)
            assert "custom" not in page.evaluate("fetch('/scenarios').then(r => r.json())")["scenarios"]

            # 9. an API failure is shown to the user
            page.evaluate("M = null")
            page.select_option("#scenario", "none")
            wait_idle(page)

            page.screenshot(path=str(HERE / "frontend_smoke_panel.png"), clip={"x": 0, "y": 0, "width": 380, "height": 900})
            browser.close()
        server.should_exit = True

        assert not errors, "browser errors:\n" + "\n".join(errors)
    print("FRONTEND SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
