"""Create the PostgreSQL tables and load the scenarios and facilities from data/.

    python scripts/db_init.py                 # create tables (if missing) and load scenarios + facilities
    python scripts/db_init.py --reset --yes   # DROP all DisasterRoute tables first (asks you to confirm with --yes)

Needs DATABASE_URL (in backend/.env or the environment) and:  pip install "psycopg[binary]"
Safe to run again: scenarios and facilities are replaced by what is in data/.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv) -> int:
    from app import db
    from app.config import DATABASE_URL, FACILITIES_PATH, SCENARIO_DIR
    if not DATABASE_URL:
        print("DATABASE_URL is not set. Copy .env.example to .env and edit it, e.g.\n"
              "  DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/disasterroute")
        return 1
    st = db.get_store()
    if st is None:
        print('The database driver is missing:  pip install "psycopg[binary]"')
        return 1
    try:
        if "--reset" in argv:
            if "--yes" not in argv:
                print("--reset deletes every DisasterRoute table. Add --yes to confirm.")
                return 1
            st.reset()
            print("dropped existing tables")
        st.init_schema()
        print("tables ready")
        print(f"scenarios loaded: {st.sync_scenarios(SCENARIO_DIR)}")
        if FACILITIES_PATH.exists():
            print(f"facilities loaded: {st.sync_facilities(FACILITIES_PATH)}")
        else:
            print("no data/facilities.json yet (run scripts/fetch_facilities.py), skipped")
        for t in ("scenarios", "hazard_zones", "facilities", "route_queries", "evaluation_runs"):
            print(f"  {t:16s} {st.count(t)} rows")
    except Exception as exc:
        print(f"Database error: {exc}\nCheck that PostgreSQL is running, the database exists, and DATABASE_URL is right.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
