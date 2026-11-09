"""Namma Metro journeys: walk to a station, wait, ride, transfer, walk out.

DATA: data/metro.json, made by scripts/fetch_metro.py from OpenStreetMap:
    {"lines": [{"name", "ref", "colour", "stations": [{"name", "lat", "lon"}, ...]}]}   stations in order.

MODEL (every number is an assumption unless a source is named):
  walking   4.5 km/h along the road graph, at most 30 min at each end (hazard slow-downs and closed roads apply).
  waiting   half the headway at the first boarding and at each transfer. Headway 10 / 7 / 5 min for light /
            normal / peak traffic (Wikipedia gives 3-15 min across the network).
  riding    straight-line distance between neighbouring stations x 1.1 at 35 km/h (Wikipedia's average
            speed for Namma Metro, stops included).  The drawn track is a straight line between stations.
  transfer  5 min walk between two lines at the same station (stations of different lines within
            150 m, or with the same name, are one interchange).
  hazards   PROPOSED rule: a station inside a closing hazard zone (flood high, wildfire hotspot, landslide
            block, quake damage "closed") cannot be boarded or left; trains still pass through it.
            Hazards that start later (onset > 0) are not considered.
Not modelled: timetables, first/last train, fares, crowding, the metro being shut down by a quake.
"""
from __future__ import annotations

import heapq
import json
import math
import re
from pathlib import Path

from .engine import INF, dijkstra_tree

WALK_KMH = 4.5
WALK_MS = WALK_KMH / 3.6
MAX_WALK_MIN = 30.0
METRO_KMH = 35.0
CURVE = 1.1
TRANSFER_MIN = 5.0
INTERCHANGE_M = 150.0
HEADWAY_MIN = {"light": 10.0, "normal": 7.0, "peak": 5.0}
DEFAULT_COLOUR = "#7c3aed"


def haversine_m(la1, lo1, la2, lo2) -> float:
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742000.0 * math.asin(math.sqrt(a))


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().replace("metro station", "").replace("station", ""))


# ------------------------------------------------------------------ OpenStreetMap (Overpass) JSON -> metro.json
OVERPASS_QUERY = """[out:json][timeout:180];
(
  relation["route"~"^(subway|light_rail|train)$"]["network"~"Namma|BMRCL|Bangalore Metro|Bengaluru Metro",i](12.70,77.30,13.30,77.90);
  relation["route"~"^(subway|light_rail)$"]["name"~"Metro|Purple Line|Green Line|Yellow Line|Pink Line|Blue Line",i](12.70,77.30,13.30,77.90);
);
out body;
>;
out body;
"""


def parse_overpass(data: dict) -> dict:
    """Turn an Overpass JSON answer (route relations plus their member nodes and ways) into metro.json."""
    els = data.get("elements", [])
    nodes = {e["id"]: e for e in els if e.get("type") == "node"}
    ways = {e["id"]: e for e in els if e.get("type") == "way"}

    def point(member):
        if member.get("type") == "node" and member["ref"] in nodes:
            n = nodes[member["ref"]]
            return n["lat"], n["lon"], (n.get("tags") or {}).get("name")
        if member.get("type") == "way" and member["ref"] in ways:
            w = ways[member["ref"]]
            wt = w.get("tags") or {}
            if not (wt.get("railway") in ("station", "halt") or wt.get("public_transport") in ("station", "platform", "stop_position")
                    or wt.get("building") == "train_station"):
                return None                       # a track or viaduct mistakenly given the role "stop" is not a station
            pts = [nodes[i] for i in w.get("nodes", []) if i in nodes]
            if pts:
                return (sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts),
                        (w.get("tags") or {}).get("name"))
        return None

    named = [n for n in nodes.values() if (n.get("tags") or {}).get("name")
             and ((n.get("tags") or {}).get("railway") == "station" or (n.get("tags") or {}).get("public_transport") == "station")]

    def tidy(name: str) -> str:
        """'Majestic (Green Line)' -> 'Majestic': the line suffix is not part of the station's name."""
        return re.sub(r"\s*\([^)]*\bline\)\s*$", "", name or "", flags=re.I).strip()

    def usable(name: str | None) -> bool:
        return bool(name) and not re.search(r"^namma metro\b|\breach\s*\d", name, re.I)   # construction labels, not stations

    def stop_name(name: str | None, la: float, lo: float) -> str | None:
        if usable(name):
            return tidy(name)
        near = min(named, key=lambda n: haversine_m(la, lo, n["lat"], n["lon"]), default=None)
        if near is not None and haversine_m(la, lo, near["lat"], near["lon"]) <= 300:
            return tidy(near["tags"]["name"])
        return None

    best: dict[str, dict] = {}
    for rel in (e for e in els if e.get("type") == "relation"):
        tags = rel.get("tags") or {}
        if tags.get("route") not in ("subway", "light_rail", "train"):
            continue
        stops = []
        for m in rel.get("members", []):
            role = m.get("role") or ""
            if not role.startswith("stop"):
                continue
            p = point(m)
            if p is None:
                continue
            la, lo, nm = p
            if stops and haversine_m(stops[-1]["lat"], stops[-1]["lon"], la, lo) < 100:
                continue                                           # entry and exit stops of one station
            stops.append({"name": stop_name(nm, la, lo) or f"Station {len(stops) + 1}", "lat": round(la, 6), "lon": round(lo, 6)})
        if len(stops) < 2:
            continue
        label = re.split(r"\s+\(|:", tags.get("name", ""))[0].strip()          # 'Purple Line (A -> B)' -> 'Purple Line'
        key = tags.get("ref") or tags.get("colour") or label or str(rel.get("id"))
        cur = best.get(key)
        if cur is None or len(stops) > len(cur["stations"]):          # keep the longer of the two directions
            best[key] = {"name": label or key, "ref": tags.get("ref", ""),
                         "colour": tags.get("colour") or DEFAULT_COLOUR, "stations": stops}
    return {"lines": sorted(best.values(), key=lambda l: l["name"])}


# ------------------------------------------------------------------ the network
class MetroNetwork:
    def __init__(self, data: dict, rg=None, max_snap_m: float = 500.0):
        self.lines = data["lines"]
        self.nodes: list[tuple[int, int]] = []
        self.index: dict[tuple[int, int], int] = {}
        for li, ln in enumerate(self.lines):
            for si, st in enumerate(ln["stations"]):
                self.index[(li, si)] = len(self.nodes)
                self.nodes.append((li, si))
        n = len(self.nodes)
        self.adj: list[list[tuple[int, float]]] = [[] for _ in range(n)]
        for li, ln in enumerate(self.lines):
            sts = ln["stations"]
            for si in range(len(sts) - 1):
                a, b = self.index[(li, si)], self.index[(li, si + 1)]
                d = haversine_m(sts[si]["lat"], sts[si]["lon"], sts[si + 1]["lat"], sts[si + 1]["lon"]) * CURVE
                t = d / (METRO_KMH / 3.6)
                self.adj[a].append((b, t))
                self.adj[b].append((a, t))
        # interchanges: nodes of different lines at the same place
        self.complex = list(range(n))

        def find(x):
            while self.complex[x] != x:
                self.complex[x] = self.complex[self.complex[x]]
                x = self.complex[x]
            return x

        for a in range(n):
            for b in range(a + 1, n):
                (la, _), (lb, _) = self.nodes[a], self.nodes[b]
                if la == lb:
                    continue
                sa, sb = self.station(a), self.station(b)
                if (haversine_m(sa["lat"], sa["lon"], sb["lat"], sb["lon"]) <= INTERCHANGE_M
                        or (_norm(sa["name"]) and _norm(sa["name"]) == _norm(sb["name"]))):
                    self.complex[find(a)] = find(b)
        self.by_complex: dict[int, list[int]] = {}
        for x in range(n):
            self.by_complex.setdefault(find(x), []).append(x)
        self.complex = [find(x) for x in range(n)]
        # road node for each station
        self.road_node: list[int | None] = [None] * n
        if rg is not None:
            for x in range(n):
                st = self.station(x)
                node, d = rg.nearest_node(st["lat"], st["lon"])
                self.road_node[x] = node if d <= max_snap_m else None

    def station(self, node: int) -> dict:
        li, si = self.nodes[node]
        return self.lines[li]["stations"][si]

    def line_of(self, node: int) -> dict:
        return self.lines[self.nodes[node][0]]

    # -------------------------------------------------------------- planning
    def plan(self, access: dict[int, float], egress: dict[int, float], headway_min: float,
             closed: set[int] | frozenset = frozenset()):
        """Cheapest walk + wait + ride (+ transfers) + walk, as (total_seconds, node_path) or None."""
        half = headway_min * 30.0
        dist: dict[int, float] = {}
        prev: dict[int, int | None] = {}
        heap = []
        for x, a in access.items():
            if x not in closed:
                dist[x] = a + half
                prev[x] = None
                heap.append((a + half, x))
        heapq.heapify(heap)
        done: set[int] = set()
        while heap:
            d, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            for v, t in self.adj[u]:
                if d + t < dist.get(v, INF):
                    dist[v], prev[v] = d + t, u
                    heapq.heappush(heap, (d + t, v))
            if u not in closed:
                for v in self.by_complex[self.complex[u]]:
                    if v != u and self.nodes[v][0] != self.nodes[u][0] and v not in closed:
                        nd = d + TRANSFER_MIN * 60.0 + half
                        if nd < dist.get(v, INF):
                            dist[v], prev[v] = nd, u
                            heapq.heappush(heap, (nd, v))
        best, best_node = INF, None
        for x, e in egress.items():
            if x in dist and x not in closed and dist[x] + e < best:
                best, best_node = dist[x] + e, x
        if best_node is None:
            return None
        path, x = [], best_node
        while x is not None:
            path.append(x)
            x = prev[x]
        return best, path[::-1]


# ------------------------------------------------------------------ hazards and walking
def _in_ring(lat: float, lon: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _closing(props: dict) -> bool:
    k = props.get("hazard")
    return ((k == "flood" and props.get("severity") == "high") or k in ("wildfire_hotspot", "landslide_block")
            or (k == "quake_damage" and props.get("state", "closed") == "closed"))


def closed_stations(net: MetroNetwork, geojson: dict | None) -> set[int]:
    """Station nodes inside a closing hazard zone that is already in force (onset 0)."""
    out: set[int] = set()
    if not geojson:
        return out
    for f in geojson.get("features", []):
        pr = f.get("properties") or {}
        if not _closing(pr) or float(pr.get("onset_min", 0) or 0) > 0:
            continue
        g = f.get("geometry") or {}
        polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
        for x in range(len(net.nodes)):
            st = net.station(x)
            if any(_in_ring(st["lat"], st["lon"], p[0]) and not any(_in_ring(st["lat"], st["lon"], h) for h in p[1:])
                   for p in polys):
                out.add(x)
    return out


def walk_state(g, state=None):
    """A hazard state whose edge costs are WALKING seconds (hazard slow-downs kept, closures kept)."""
    base = state.mult if state is not None else [1.0] * g.m
    st = g.fresh_state()
    st.mult = [INF if m == INF else (g.length[e] / WALK_MS) / max(g.base_time[e], 1e-9) * m
               for e, m in enumerate(base)]
    return st


def _walk_geometry(rg, pred, src, node, reverse=False):
    g = rg.csr
    edges = []
    v = node
    while v != src:
        e = pred[v]
        edges.append(e)
        v = g.tail[e]
    edges = edges[::-1]
    geo = rg.route_geometry(edges) if edges else {"type": "LineString", "coordinates": [[g.lon[src], g.lat[src]]] * 2}
    if reverse:
        geo = {"type": "LineString", "coordinates": geo["coordinates"][::-1]}
    return geo, sum(g.length[e] for e in edges)


def journey(rg, net: MetroNetwork, s: int, t: int, state=None, traffic: str = "normal",
            geojson: dict | None = None) -> dict:
    """Best metro journey between road nodes s and t. Always returns a dict; ok=False explains why not."""
    g = rg.csr
    cutoff = MAX_WALK_MIN * 60.0
    walk = walk_state(g, state)
    dist_o, pred_o, _ = dijkstra_tree(g, s, None, 0.0, walk, cutoff)
    dist_d, pred_d, _ = dijkstra_tree(g, t, None, 0.0, walk, cutoff)
    closed = closed_stations(net, geojson)
    closed_names = sorted({net.station(x)["name"] for x in closed})

    def fail(reason: str) -> dict:
        return {"ok": False, "reason": reason, "closed_stations": closed_names}

    access = {x: dist_o[r] for x, r in enumerate(net.road_node) if r is not None and r in dist_o and dist_o[r] <= cutoff}
    egress = {x: dist_d[r] for x, r in enumerate(net.road_node) if r is not None and r in dist_d and dist_d[r] <= cutoff}
    if not access:
        return fail(f"No metro station within a {MAX_WALK_MIN:.0f} minute walk of the start.")
    if not egress:
        return fail(f"No metro station within a {MAX_WALK_MIN:.0f} minute walk of the destination.")
    if all(x in closed for x in access):
        return fail("Every station near the start is closed by a hazard.")
    if all(x in closed for x in egress):
        return fail("Every station near the destination is closed by a hazard.")
    headway = HEADWAY_MIN.get(traffic, HEADWAY_MIN["normal"])
    plan = net.plan(access, egress, headway, closed)
    if plan is None:
        return fail("No open metro connection between the stations near the two points.")
    total, path = plan

    legs = []
    first, last = path[0], path[-1]
    geo, length = _walk_geometry(rg, pred_o, s, net.road_node[first])
    legs.append({"type": "walk", "to": net.station(first)["name"], "minutes": round(access[first] / 60, 1),
                 "length_m": round(length), "geometry": geo})
    legs.append({"type": "wait", "at": net.station(first)["name"], "line": net.line_of(first)["name"],
                 "minutes": round(headway / 2, 1)})
    i = 0
    while i < len(path) - 1:
        a, b = path[i], path[i + 1]
        if net.nodes[a][0] != net.nodes[b][0]:                       # transfer
            legs.append({"type": "transfer", "at": net.station(a)["name"], "minutes": TRANSFER_MIN,
                         "from_line": net.line_of(a)["name"], "to_line": net.line_of(b)["name"]})
            legs.append({"type": "wait", "at": net.station(b)["name"], "line": net.line_of(b)["name"],
                         "minutes": round(headway / 2, 1)})
            i += 1
            continue
        j = i
        secs = 0.0
        while j < len(path) - 1 and net.nodes[path[j]][0] == net.nodes[path[j + 1]][0]:
            secs += next(t_ for v, t_ in net.adj[path[j]] if v == path[j + 1])
            j += 1
        ln = net.line_of(path[i])
        legs.append({"type": "metro", "line": ln["name"], "colour": ln["colour"],
                     "from": net.station(path[i])["name"], "to": net.station(path[j])["name"],
                     "stops": j - i, "minutes": round(secs / 60, 1),
                     "geometry": {"type": "LineString",
                                  "coordinates": [[net.station(x)["lon"], net.station(x)["lat"]] for x in path[i:j + 1]]}})
        i = j
    geo, length = _walk_geometry(rg, pred_d, t, net.road_node[last], reverse=True)
    legs.append({"type": "walk", "to": "your destination", "from": net.station(last)["name"],
                 "minutes": round(egress[last] / 60, 1), "length_m": round(length), "geometry": geo})
    return {"ok": True, "total_s": round(total, 1), "total_min": round(total / 60, 1), "headway_min": headway,
            "legs": legs, "closed_stations": closed_names}


def load_network(path: Path, rg) -> MetroNetwork:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("lines"):
        raise ValueError("metro.json has no lines")
    return MetroNetwork(data, rg)
