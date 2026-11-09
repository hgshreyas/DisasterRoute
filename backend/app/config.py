"""Central configuration for DisasterRoute."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Whole-Bengaluru drivable road graph, cached after the first download.
PLACE_NAME = "Bengaluru, Karnataka, India"
GRAPH_PATH = DATA_DIR / "bengaluru_drive.graphml"
LANDMARKS_PATH = DATA_DIR / "bengaluru_landmarks.npz"
SCENARIO_DIR = DATA_DIR / "scenarios"
SCENARIO_DIR.mkdir(exist_ok=True)
FACILITIES_PATH = DATA_DIR / "facilities.json"
METRO_PATH = DATA_DIR / "metro.json"
FRONTEND_DIR = BASE_DIR / "frontend"


def _load_dotenv() -> None:
    """Read KEY=value lines from backend/.env into the environment (real environment variables win)."""
    path = BASE_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()
# PostgreSQL connection string. Unset = the app runs from files only, without a database.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

# Highest speed we ever assume (km/h). Used to keep the A* heuristic admissible.
MAX_SPEED_KMH = 100.0

# Fallback speeds (km/h) per OSM highway class when OSM has no maxspeed tag.
# Bengaluru traffic is slow, so these are deliberately conservative.
DEFAULT_SPEEDS_KMH = {
    "motorway": 60,
    "trunk": 45,
    "primary": 35,
    "secondary": 30,
    "tertiary": 25,
    "residential": 20,
    "unclassified": 20,
    "living_street": 10,
    "service": 15,
}
