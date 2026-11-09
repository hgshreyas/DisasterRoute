"""Load the cached road graph, build the CSR routing graph, and snap points to nodes."""
from __future__ import annotations

import math
import threading

import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from .accessibility import load_facilities
from .config import FACILITIES_PATH, GRAPH_PATH, LANDMARKS_PATH, SCENARIO_DIR
from .engine import CSRGraph, HazardState, Landmarks
from .exposure import EdgeGeometry, LocalProjection, nearest_edges
from .hazards import build_state, load_scenario_file


class RoadGraph:
    """NetworkX graph (for geometry/attributes) + CSR graph (for fast routing)."""

    def __init__(self, G: nx.MultiDiGraph, landmarks_path=None, scenario_dir=SCENARIO_DIR):
        self.G = G
        self.csr = CSRGraph.from_networkx(G)
        self.landmarks: Landmarks | None = None
        if landmarks_path is not None and landmarks_path.exists():
            self.landmarks = Landmarks.load(landmarks_path, self.csr)

        self.proj = LocalProjection()
        self.scenario_dir = scenario_dir
        self._egeom: EdgeGeometry | None = None
        self._states: dict[str, tuple[HazardState, dict]] = {}
        self._facilities: list | None = None
        self._lock = threading.RLock()          # the API serves requests in parallel threads: build each thing once

        lats = np.array(self.csr.lat)
        lons = np.array(self.csr.lon)
        self._lat0 = float(lats.mean())
        # Equirectangular projection is accurate enough for snapping inside one city.
        self._tree = cKDTree(self._to_xy(lats, lons))

    def _to_xy(self, lat, lon):
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        x = np.radians(lon) * math.cos(math.radians(self._lat0)) * 6371000.0
        y = np.radians(lat) * 6371000.0
        return np.column_stack([x, y])

    def nearest_node(self, lat: float, lon: float) -> tuple[int, float]:
        """Return (CSR node index, snap distance in metres)."""
        dist, idx = self._tree.query(self._to_xy([lat], [lon])[0])
        return int(idx), float(dist)

    # ---------------------------------------------------------------- scenarios
    @property
    def egeom(self) -> EdgeGeometry:
        """Edge polylines in metres. Built on first use (tens of seconds for the whole city)."""
        if self._egeom is None:
            with self._lock:
                if self._egeom is None:
                    self._egeom = EdgeGeometry.from_graph(self.csr, self.G, self.proj)
        return self._egeom

    def scenario_names(self) -> list[str]:
        return sorted(p.stem for p in self.scenario_dir.glob("*.geojson"))

    def scenario(self, name: str) -> tuple[HazardState, dict]:
        """(state, summary) for a scenario file; computed once, then cached."""
        if name not in self._states:
            with self._lock:
                if name not in self._states:
                    path = self.scenario_dir / f"{name}.geojson"
                    if not path.exists():
                        raise KeyError(name)
                    fc = load_scenario_file(path)
                    self._states[name] = build_state(self.csr, self.egeom, self.proj, fc)
        return self._states[name]

    def clear_scenarios(self) -> None:
        self._states.clear()

    # ---------------------------------------------------------------- closures and edges
    def with_closures(self, state: HazardState | None, edge_ids: list[int]) -> HazardState:
        """A COPY of `state` (or of normal conditions) with extra roads closed.

        The cached scenario state is never modified, so closures from one request cannot
        leak into another.
        """
        for e in edge_ids:
            if not 0 <= e < self.csr.m:
                raise ValueError(f"edge id {e} out of range")
        st = state.copy() if state is not None else self.csr.fresh_state()
        st.close(edge_ids)
        return st

    def edge_info(self, e: int) -> dict:
        u, v, key = self.csr.edge_ref[e]
        data = self.G[u][v][key]
        name, hw = data.get("name"), data.get("highway")
        if isinstance(name, list):
            name = ", ".join(map(str, name))
        if isinstance(hw, list):
            hw = hw[0] if hw else None
        return {"edge": e, "name": name, "highway": hw,
                "length_m": round(self.csr.length[e], 1)}

    def edges_near(self, lat: float, lon: float, max_m: float = 150.0, k: int = 3) -> list[dict]:
        """Roads near a clicked point, with the opposite-direction edge so both can be closed."""
        x, y = self.proj.xy(lat, lon)
        out = []
        for e, d in nearest_edges(self.egeom, float(x), float(y), max_m, k):
            info = self.edge_info(e)
            rev = self.csr.edge_between(self.csr.head[e], self.csr.tail[e])
            info.update({"distance_m": round(d, 1), "reverse_edge": rev,
                         "close_ids": [e] if rev is None else [e, rev],
                         "geometry": self.route_geometry([e])})
            out.append(info)
        return out

    # ---------------------------------------------------------------- facilities
    def facilities(self) -> list:
        if self._facilities is None:
            with self._lock:
                if self._facilities is None:
                    if not FACILITIES_PATH.exists():
                        raise FileNotFoundError(
                            f"{FACILITIES_PATH} not found. Run: python scripts/fetch_facilities.py")
                    self._facilities = load_facilities(self, FACILITIES_PATH)
        return self._facilities

    def route_geometry(self, edges: list[int]) -> dict:
        """GeoJSON LineString (lon, lat) for a list of CSR edge indices."""
        coords: list[list[float]] = []
        for e in edges:
            u, v, key = self.csr.edge_ref[e]
            geom = self.G[u][v][key].get("geometry")
            if geom is not None:
                seg = [[x, y] for x, y in geom.coords]
            else:
                seg = [[self.G.nodes[u]["x"], self.G.nodes[u]["y"]],
                       [self.G.nodes[v]["x"], self.G.nodes[v]["y"]]]
            coords.extend(seg if not coords else seg[1:])
        return {"type": "LineString", "coordinates": coords}


_GRAPH: RoadGraph | None = None
_GRAPH_LOCK = threading.Lock()


def get_graph() -> RoadGraph:
    """The city graph, loaded once. Parallel first requests wait for the single load instead of each starting one."""
    global _GRAPH
    if _GRAPH is None:
        with _GRAPH_LOCK:
            if _GRAPH is None:
                import osmnx as ox  # imported here so unit tests can run without osmnx

                if not GRAPH_PATH.exists():
                    raise FileNotFoundError(
                        f"{GRAPH_PATH} not found. Run: python scripts/download_graph.py"
                    )
                _GRAPH = RoadGraph(ox.load_graphml(GRAPH_PATH), LANDMARKS_PATH)
    return _GRAPH
