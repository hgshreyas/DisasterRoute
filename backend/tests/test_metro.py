"""Metro tests: OpenStreetMap parsing, interchanges, journeys, hazards.  Run from backend/:  python tests/test_metro.py

Also provides fake_metro(rg), a small two-line network on the synthetic city, used by frontend_smoke.py.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_hazards as TH
from app import transit
from app.engine import INF
from app.graph import RoadGraph


def fake_metro(rg=None):
    """Line A (west-east) and Line B (south-north) meeting at 'Junction', on the synthetic city (12.900-12.958 N, 77.595-77.653 E)."""
    return {"lines": [
        {"name": "Line A", "ref": "A", "colour": "#7c3aed", "stations": [
            {"name": "West End", "lat": 12.9215, "lon": 77.6000}, {"name": "Midtown", "lat": 12.9205, "lon": 77.6125},
            {"name": "Junction", "lat": 12.9200, "lon": 77.6250}, {"name": "East Gate", "lat": 12.9200, "lon": 77.6400}]},
        {"name": "Line B", "ref": "B", "colour": "#16a34a", "stations": [
            {"name": "South Park", "lat": 12.9085, "lon": 77.6255}, {"name": "Lake View", "lat": 12.9140, "lon": 77.6252},
            {"name": "Junction", "lat": 12.9202, "lon": 77.6252}, {"name": "North Hill", "lat": 12.9300, "lon": 77.6250}]},
    ]}


def fake_overpass():
    """Shaped like an Overpass answer: two lines, both directions, platform members, a bus route, a way member."""
    els = []

    def node(i, lat, lon, **tags):
        els.append({"type": "node", "id": i, "lat": lat, "lon": lon, **({"tags": tags} if tags else {})})
        return i
    purple = [node(1, 12.92, 77.600, name="Alpha"), node(2, 12.92, 77.612, name="Bravo"), node(3, 12.92, 77.625, name="Interchange (Purple Line)"),
              node(4, 12.92, 77.640, name="Delta"), node(6, 12.92, 77.655, name="Namma Metro - Reach 4")]
    node(7, 12.9201, 77.6551, name="Eastern End", railway="station")          # the real name sits on a nearby station node
    green = [node(11, 12.908, 77.6255, name="Foxtrot"), node(12, 12.9140, 77.6252), node(13, 12.9201, 77.6251, name="Interchange (Green Line)"),
             node(14, 12.930, 77.625, name="Hotel"), node(15, 12.940, 77.625, name="Namma Metro - Reach 9")]
    node(20, 12.9201, 77.6249, name="Interchange platform")
    # a second stop node of the same station, a few metres away (entry and exit stops)
    purple_with_pair = purple[:3] + [node(5, 12.92002, 77.62502, name="Interchange")] + purple[3:]

    def rel(rid, stops, name, ref, colour, route="subway", rev=False):
        seq = stops[::-1] if rev else stops
        members = [{"type": "node", "ref": s, "role": "stop"} for s in seq]
        members += [{"type": "node", "ref": 20, "role": "platform"}, {"type": "way", "ref": 9, "role": ""}]
        if ref == "G":                                              # a viaduct wrongly listed as the last "stop"
            members.append({"type": "way", "ref": 30, "role": "stop"})
        els.append({"type": "relation", "id": rid, "tags": {"route": route, "name": name, "ref": ref, "colour": colour}, "members": members})
    els.append({"type": "way", "id": 30, "nodes": [1, 2], "tags": {"railway": "subway", "name": "Namma Metro - Reach 4", "bridge": "viaduct"}})
    els.append({"type": "way", "id": 31, "nodes": [3, 4], "tags": {"railway": "station", "name": "Zulu Terminal", "building": "train_station"}})
    rel(1, purple_with_pair, "Purple Line (Alpha -> Eastern End)", "P", "#7c3aed")
    rel(2, purple[:4], "Purple Line (Eastern End -> Alpha)", "P", "#7c3aed", rev=True)            # shorter variant, other direction
    rel(3, green, "Green Line: Foxtrot => Hotel", "G", "#16a34a")
    rel(4, green, "Green Line: Hotel => Foxtrot", "G", "#16a34a", rev=True)
    rel(5, purple[:2], "Bus 500", "500", "#ff0000", route="bus")
    return {"elements": els}


def main():
    # ---- parsing
    assert "skel" not in transit.OVERPASS_QUERY, "'out skel' drops tags and with them the station names"
    net = transit.parse_overpass(fake_overpass())
    assert [l["name"] for l in net["lines"]] == ["Green Line", "Purple Line"], net["lines"]      # direction text removed
    purple = [l for l in net["lines"] if l["name"] == "Purple Line"][0]
    assert [s["name"] for s in purple["stations"]] == ["Alpha", "Bravo", "Interchange", "Delta", "Eastern End"], purple   # pair merged, platform ignored,
    #                                                    line suffix removed, "Reach 4" replaced by the real name nearby
    green = [l for l in net["lines"] if l["name"] == "Green Line"][0]
    assert [s["name"] for s in green["stations"]] == ["Foxtrot", "Station 2", "Interchange", "Hotel", "Station 5"], green   # unnamed or mislabelled stops with no named station nearby keep a label
    assert transit.parse_overpass({"elements": []}) == {"lines": []}
    assert green["stations"][-1]["name"] == "Station 5"                  # the viaduct "stop" did not become a station
    zulu = transit.parse_overpass({"elements": [
        {"type": "node", "id": 1, "lat": 12.92, "lon": 77.60, "tags": {"name": "Yankee"}},
        {"type": "node", "id": 3, "lat": 12.92, "lon": 77.62}, {"type": "node", "id": 4, "lat": 12.92, "lon": 77.622},
        {"type": "way", "id": 31, "nodes": [3, 4], "tags": {"railway": "station", "name": "Zulu Terminal"}},
        {"type": "relation", "id": 9, "tags": {"route": "subway", "name": "X Line", "ref": "X"},
         "members": [{"type": "node", "ref": 1, "role": "stop"}, {"type": "way", "ref": 31, "role": "stop"}]}]})
    assert [s["name"] for s in zulu["lines"][0]["stations"]] == ["Yankee", "Zulu Terminal"], zulu     # a station building is accepted
    assert all(l["name"] != "Bus 500" for l in net["lines"])
    print("  parsed:", {l["name"]: len(l["stations"]) for l in net["lines"]})

    # ---- interchange detection
    mn = transit.MetroNetwork(net)
    groups = [g for g in mn.by_complex.values() if len({mn.nodes[x][0] for x in g}) > 1]
    assert len(groups) == 1 and {mn.station(x)["name"] for x in groups[0]} == {"Interchange"}, groups
    print("  interchange found: Interchange (two lines)")

    with tempfile.TemporaryDirectory() as d:
        rg = RoadGraph(TH.synthetic_city(), None, Path(d))
        mn = transit.MetroNetwork(fake_metro(), rg)
        assert all(r is not None for r in mn.road_node)
        node = lambda lat, lon: rg.nearest_node(lat, lon)[0]

        # ---- a journey that needs an interchange
        s, t = node(12.920, 77.599), node(12.908, 77.627)
        j = transit.journey(rg, mn, s, t)
        assert j["ok"], j
        types = [l["type"] for l in j["legs"]]
        assert types == ["walk", "wait", "metro", "transfer", "wait", "metro", "walk"], types
        assert [l["line"] for l in j["legs"] if l["type"] == "metro"] == ["Line A", "Line B"]
        parts = sum(l["minutes"] for l in j["legs"])
        assert abs(parts - j["total_min"]) < 0.6, (parts, j["total_min"])                  # legs add up to the total
        assert all(l["geometry"]["coordinates"] for l in j["legs"] if l["type"] in ("metro", "walk"))
        print("  journey with interchange: %.1f min (%s)" % (j["total_min"], ", ".join(types)))

        # ---- headway depends on traffic; peak means shorter waits
        peak = transit.journey(rg, mn, s, t, traffic="peak")
        light = transit.journey(rg, mn, s, t, traffic="light")
        assert peak["total_s"] < j["total_s"] < light["total_s"], (peak["total_s"], j["total_s"], light["total_s"])

        # ---- one line, no interchange
        j2 = transit.journey(rg, mn, node(12.920, 77.599), node(12.920, 77.641))
        assert j2["ok"] and [l["type"] for l in j2["legs"]].count("transfer") == 0, j2

        # ---- no station within a 30 minute walk
        far = transit.journey(rg, mn, node(12.900, 77.595), t)
        assert not far["ok"] and "walk of the start" in far["reason"], far

        # ---- hazard rules: which zones close a station
        la, lo = 12.9200, 77.6250

        def zone(kind, sev=None, onset=0, state=None):
            props = {"hazard": kind, "onset_min": onset, **({"severity": sev} if sev else {}), **({"state": state} if state else {})}
            ring = [[lo + dx, la + dy] for dx, dy in [(-.002, -.002), (.002, -.002), (.002, .002), (-.002, .002), (-.002, -.002)]]
            return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": props,
                                                              "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
        junction = {x for x in range(len(mn.nodes)) if mn.station(x)["name"] == "Junction"}
        for kind, sev, onset, state, closes in [
                ("flood", "high", 0, None, True), ("flood", "medium", 0, None, False), ("flood", "low", 0, None, False),
                ("wildfire_hotspot", None, 0, None, True), ("landslide_block", None, 0, None, True),
                ("quake_damage", None, 0, "closed", True), ("quake_damage", None, 0, "partial", False),
                ("wildfire", "high", 0, None, False), ("landslide", "high", 0, None, False), ("flood", "high", 20, None, False)]:
            got = transit.closed_stations(mn, zone(kind, sev, onset, state)) == junction
            assert got == closes, (kind, sev, onset, state, got)
        assert transit.closed_stations(mn, None) == set()
        shut = zone("flood", "high")
        blocked = transit.journey(rg, mn, s, t, geojson=shut)
        assert not blocked["ok"] and "Junction" in blocked["closed_stations"], blocked
        one_side = transit.journey(rg, mn, node(12.920, 77.599), node(12.9205, 77.6125), geojson=shut)
        assert one_side["ok"], one_side                                                     # Line A west of the junction still runs
        print("  hazard rules ok; closed interchange cuts the A to B journey:", blocked["reason"])

        # ---- roads: closed roads stop the walk, slowed roads lengthen it
        away = next(i for i in range(rg.csr.n) if 500 < transit.haversine_m(12.9215, 77.6000, rg.csr.lat[i], rg.csr.lon[i]) < 900)
        closed_roads = rg.csr.fresh_state()
        for e in range(rg.csr.m):
            closed_roads.mult[e] = INF
        nowalk = transit.journey(rg, mn, away, t, closed_roads)
        assert not nowalk["ok"] and "walk" in nowalk["reason"], nowalk
        slow = rg.csr.fresh_state()
        for e in range(rg.csr.m):
            slow.mult[e] = 3.0
        base = transit.journey(rg, mn, away, t)
        slowed = transit.journey(rg, mn, away, t, slow)
        assert base["ok"] and (not slowed["ok"] or slowed["legs"][0]["minutes"] > base["legs"][0]["minutes"]), (base, slowed)
        print("  closed roads stop the walk, slowed roads lengthen it")

        # ---- the file round trip the app uses
        path = Path(d) / "metro.json"
        path.write_text(json.dumps(fake_metro()), encoding="utf-8")
        assert len(transit.load_network(path, rg).lines) == 2
        path.write_text(json.dumps({"lines": []}), encoding="utf-8")
        try:
            transit.load_network(path, rg)
            raise AssertionError("an empty network should be rejected")
        except ValueError:
            pass
    print("ALL METRO TESTS PASSED")


if __name__ == "__main__":
    main()
