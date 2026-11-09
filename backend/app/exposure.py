"""Geometry for hazard exposure: how much of each road edge lies inside a hazard polygon.

Pure numpy (no shapely needed).  Work happens in a local metric projection around
Bengaluru, which is accurate to well under 1% across the city.

Typical use:
    proj = LocalProjection()
    eg   = EdgeGeometry.from_graph(csr, G, proj)       # once, ~tens of seconds for the city
    idx, exposed_m = polygon_exposure(eg, ring_xy)      # per hazard polygon
"""
from __future__ import annotations

import math

import numpy as np

# Road-class vulnerability to slope failure (used by the landslide rule): major
# roads are better engineered, small roads fail first.
ROAD_VULN = {
    "motorway": 0.3, "motorway_link": 0.3, "trunk": 0.3, "trunk_link": 0.3,
    "primary": 0.4, "primary_link": 0.4,
    "secondary": 0.6, "secondary_link": 0.6,
    "tertiary": 0.8, "tertiary_link": 0.8,
}


class LocalProjection:
    """Equirectangular projection (metres) around a reference point."""

    def __init__(self, lat0: float = 12.97, lon0: float = 77.59):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110540.0

    def xy(self, lat, lon):
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def ring_xy(self, lonlat_ring) -> np.ndarray:
        arr = np.asarray(lonlat_ring, dtype=float)
        x, y = self.xy(arr[:, 1], arr[:, 0])
        return np.column_stack([x, y])


class EdgeGeometry:
    """Ragged polyline storage for every CSR edge, in projected metres."""

    def __init__(self, coords: np.ndarray, offsets: np.ndarray, road_weight: np.ndarray):
        self.coords = coords                       # (K, 2)
        self.offsets = offsets                     # (m + 1,)
        self.road_weight = road_weight             # (m,) landslide vulnerability
        starts = offsets[:-1]
        self.minx = np.minimum.reduceat(coords[:, 0], starts)
        self.maxx = np.maximum.reduceat(coords[:, 0], starts)
        self.miny = np.minimum.reduceat(coords[:, 1], starts)
        self.maxy = np.maximum.reduceat(coords[:, 1], starts)

        seg = np.hypot(np.diff(coords[:, 0]), np.diff(coords[:, 1]))
        seg[offsets[1:-1] - 1] = 0.0               # segments that straddle two edges
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.length = np.maximum(cum[offsets[1:] - 1] - cum[offsets[:-1]], 1e-6)

    @property
    def m(self) -> int:
        return len(self.offsets) - 1

    @classmethod
    def from_graph(cls, csr, G, proj: LocalProjection) -> "EdgeGeometry":
        """Use OSM edge geometry when present, else a straight line between the endpoints."""
        lons: list[float] = []
        lats: list[float] = []
        offsets = [0]
        weights = []
        for e in range(csr.m):
            if csr.edge_ref is not None:
                u, v, key = csr.edge_ref[e]
                data = G[u][v][key]
                geom = data.get("geometry")
                hw = data.get("highway")
                if isinstance(hw, list):
                    hw = hw[0] if hw else None
                weights.append(ROAD_VULN.get(hw, 1.0))
            else:
                geom = None
                weights.append(1.0)
            if geom is not None:
                for x, y in geom.coords:
                    lons.append(x)
                    lats.append(y)
            else:
                a, b = csr.tail[e], csr.head[e]
                lons += [csr.lon[a], csr.lon[b]]
                lats += [csr.lat[a], csr.lat[b]]
            offsets.append(len(lons))
        x, y = proj.xy(lats, lons)
        return cls(np.column_stack([x, y]), np.asarray(offsets, dtype=np.int64),
                   np.asarray(weights, dtype=float))


# --------------------------------------------------------------------------- core geometry
def _inside(points: np.ndarray, C: np.ndarray, D: np.ndarray) -> np.ndarray:
    """Ray-casting point-in-polygon for many points; polygon edges are C[i] -> D[i]."""
    x = points[:, 0][:, None]
    y = points[:, 1][:, None]
    x1, y1 = C[:, 0][None, :], C[:, 1][None, :]
    x2, y2 = D[:, 0][None, :], D[:, 1][None, :]
    cond = (y1 > y) != (y2 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    return ((cond & (x < xint)).sum(axis=1) % 2) == 1


def _segment_inside_length(a: np.ndarray, b: np.ndarray, C: np.ndarray, D: np.ndarray) -> float:
    """Length (m) of segment a-b that lies inside the polygon C->D."""
    r = b - a
    L = math.hypot(r[0], r[1])
    if L < 1e-9:
        return 0.0
    s = D - C
    qp = C - a
    den = r[0] * s[:, 1] - r[1] * s[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[:, 0] * s[:, 1] - qp[:, 1] * s[:, 0]) / den
        u = (qp[:, 0] * r[1] - qp[:, 1] * r[0]) / den
    ok = (np.abs(den) > 1e-12) & (t > 0) & (t < 1) & (u >= 0) & (u < 1)
    ts = np.concatenate(([0.0], np.sort(t[ok]), [1.0]))
    keep = np.diff(ts) > 1e-12
    if not keep.any():
        return 0.0
    mids = (ts[:-1] + ts[1:]) / 2
    pts = a[None, :] + mids[:, None] * r[None, :]
    inside = _inside(pts, C, D) & keep
    return float((np.diff(ts)[inside]).sum() * L)


def _point_segment_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    denom = float(ab @ ab)
    t = 0.0 if denom < 1e-12 else min(1.0, max(0.0, float((p - a) @ ab) / denom))
    q = a + t * ab
    return float(math.hypot(p[0] - q[0], p[1] - q[1]))


def nearest_edges(eg: EdgeGeometry, x: float, y: float, max_m: float = 150.0,
                  k: int = 3) -> list[tuple[int, float]]:
    """The k closest edges to a point (projected metres) within max_m, as (edge, metres)."""
    cand = np.nonzero((eg.maxx >= x - max_m) & (eg.minx <= x + max_m) &
                      (eg.maxy >= y - max_m) & (eg.miny <= y + max_m))[0]
    p = np.array([x, y], dtype=float)
    scored = []
    for e in cand:
        pts = eg.coords[eg.offsets[e]:eg.offsets[e + 1]]
        d = min(_point_segment_dist(p, a, b) for a, b in zip(pts[:-1], pts[1:]))
        if d <= max_m:
            scored.append((int(e), d))
    scored.sort(key=lambda r: r[1])
    return scored[:k]


def polygon_exposure(eg: EdgeGeometry, ring: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Edges that touch the polygon and the metres of each edge inside it."""
    ring = np.asarray(ring, dtype=float)
    if np.allclose(ring[0], ring[-1]):
        ring = ring[:-1]
    C, D = ring, np.roll(ring, -1, axis=0)
    x0, y0 = ring.min(axis=0)
    x1, y1 = ring.max(axis=0)
    cand = np.nonzero((eg.maxx >= x0) & (eg.minx <= x1) & (eg.maxy >= y0) & (eg.miny <= y1))[0]

    idx: list[int] = []
    exposed: list[float] = []
    for e in cand:
        pts = eg.coords[eg.offsets[e]:eg.offsets[e + 1]]
        total = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            total += _segment_inside_length(a, b, C, D)
        if total > 1e-6:
            idx.append(int(e))
            exposed.append(min(total, float(eg.length[e])))
    return np.asarray(idx, dtype=np.int64), np.asarray(exposed, dtype=float)
