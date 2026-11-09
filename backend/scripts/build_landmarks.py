"""Precompute ALT landmarks for the cached Bengaluru graph (run once after download_graph.py).

    python scripts/build_landmarks.py            # default 8 landmarks
    python scripts/build_landmarks.py 12

Each landmark needs two full-graph Dijkstra sweeps, so expect several minutes.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import osmnx as ox  # noqa: E402

from app.config import GRAPH_PATH, LANDMARKS_PATH  # noqa: E402
from app.engine import CSRGraph, Landmarks  # noqa: E402


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    print(f"Loading {GRAPH_PATH} ...")
    g = CSRGraph.from_networkx(ox.load_graphml(GRAPH_PATH))
    print(f"CSR graph: {g.n:,} nodes, {g.m:,} edges. Building {k} landmarks ...")
    t0 = time.time()
    lm = Landmarks.build(g, k=k, seed=0)
    lm.save(LANDMARKS_PATH, g)
    print(f"Saved {LANDMARKS_PATH} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
