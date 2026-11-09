"""Download the whole-Bengaluru drive network once and cache it as GraphML.

Run from the backend/ folder:
    python scripts/download_graph.py

Needs internet. Expect several minutes and a large file (hundreds of MB of RAM).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import osmnx as ox  # noqa: E402

from app.config import DEFAULT_SPEEDS_KMH, GRAPH_PATH, PLACE_NAME  # noqa: E402


def main() -> None:
    ox.settings.use_cache = True
    ox.settings.log_console = True

    t0 = time.time()
    print(f"Downloading drive network for: {PLACE_NAME}")
    G = ox.graph_from_place(PLACE_NAME, network_type="drive", simplify=True)
    print(f"Downloaded: {len(G.nodes):,} nodes, {len(G.edges):,} edges "
          f"in {time.time() - t0:.0f}s")

    # Speeds (km/h) and travel times (seconds) on every edge.
    # hwy_speeds fills in missing maxspeed tags by road class.
    G = ox.add_edge_speeds(G, hwy_speeds=DEFAULT_SPEEDS_KMH)
    G = ox.add_edge_travel_times(G)

    # Keep the largest strongly connected component so every node can reach every other.
    G = ox.truncate.largest_component(G, strongly=True)
    print(f"After keeping the largest strongly connected component: "
          f"{len(G.nodes):,} nodes, {len(G.edges):,} edges")

    ox.save_graphml(G, GRAPH_PATH)
    print(f"Saved to {GRAPH_PATH}")


if __name__ == "__main__":
    main()
