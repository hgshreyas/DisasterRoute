"""Remove clinics and unsuitable shelters from data/facilities.json, and list what was removed.

    python scripts/prune_facilities.py            # writes data/facilities.json, keeps facilities_full.json
    python scripts/prune_facilities.py --dry-run  # only print what would be removed

Rules (all visible below; edit them if you disagree):
  hospitals  keep: emergency=yes, or beds listed, or a name that says hospital / medical college / institute ...
             drop: emergency=no, healthcare=clinic/doctor/dentist/..., clinic-type names (clinic, dental,
                   diagnostic, scan, lab, eye care, physio, veterinary ...), and unnamed ones
             A name with both "hospital" and a clinic word (e.g. "Dental Hospital") is kept; the word
             hospital wins unless emergency=no.
  shelters   keep: named community centres / stadiums of at least MIN_SHELTER_M2 (points of unknown size stay)
             drop: club, gym, swimming, academy, temple ... names, unnamed ones, tiny buildings, and
                   duplicates within 150 m (the larger / named one is kept)
Works on files made by an older fetch_facilities.py too (it then relies on names and emergency=yes only).
Remaining facilities still snap to the road network when the app loads them.
"""
import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIN_SHELTER_M2 = 300
DEDUPE_M = 150

HOSPITAL_WORDS = ("hospital", "medical college", "institute of medical", "medical centre", "medical center",
                  "nimhans", "aiims", "trauma", "maternity home", "nursing home", "multispeciality",
                  "multi speciality", "super speciality", "superspeciality", "general hospital")
# Specialist centres without the word "hospital"; kept only if the name has no clinic word.
SPECIALIST_WORDS = ("chest diseases", "tuberculosis", "cancer", "oncology", "cardiac", "cardiology", "cardio",
                    "neuro", "kidney", "heart centre", "heart center", "netralaya", "nethralaya", "hrudayalaya")
CLINIC_WORDS = ("clinic", "dental", "dentist", "diagnostic", "polyclinic", "scan", "laborator", " lab",
                "eye care", "eye centre", "eye center", "physio", "veterinary", "pet ", "vet ", "homoeo",
                "homeo", "ayurved", "yoga", "wellness", "skin", "hair", "cosmetic", "fertility", "dialysis",
                "pharmacy", "medical store", "blood bank", "x-ray", "xray")
CLINIC_TAGS = ("clinic", "doctor", "doctors", "dentist", "laboratory", "pharmacy", "physiotherapist",
               "alternative", "optometrist")
SHELTER_BAD = ("club", "gym", "swimming", "aquatic", "academy", "temple", "mandir", "church", "mosque",
               "masjid", "bar ", "resort", "cricket ground", "turf", "mall", "hotel", "banquet", "kalyana")


def _has(name: str, words) -> bool:
    n = f" {name.lower()} "
    return any(w in n for w in words)


def classify(f: dict) -> tuple[bool, str]:
    """(keep?, reason) for one facility row (without the distance-based dedupe)."""
    name = (f.get("name") or "").strip()
    unnamed = not name or name.lower().startswith("unnamed")
    if f["kind"] == "hospital":
        em = (f.get("emergency") or "").lower()
        if em == "no":
            return False, "emergency=no"
        if em == "yes":
            return True, "emergency=yes"
        if (f.get("healthcare") or "").lower() in CLINIC_TAGS:
            return False, f"healthcare={f['healthcare']}"
        if unnamed:
            return False, "unnamed"
        if f.get("beds"):
            return True, "beds listed"
        if _has(name, HOSPITAL_WORDS):
            return True, "hospital name"
        if _has(name, CLINIC_WORDS):
            return False, "clinic-type name"
        if _has(name, SPECIALIST_WORDS):
            return True, "specialist centre name"
        return False, "no sign of a hospital in name or tags"
    if unnamed:
        return False, "unnamed"
    if _has(name, SHELTER_BAD):
        return False, "not a public shelter (name)"
    area = f.get("area_m2")
    if area is not None and area < MIN_SHELTER_M2:
        return False, f"only {area} m2"
    return True, "ok"


def _dist_m(a, b):
    dy = (a["lat"] - b["lat"]) * 111_320
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def dedupe(rows: list[dict], kind: str) -> tuple[list[dict], list[dict]]:
    """Within DEDUPE_M keep one: the one with the biggest known area, then any named one."""
    rank = sorted(rows, key=lambda f: (-(f.get("area_m2") or 0), f["id"]))
    kept, dropped = [], []
    for f in rank:
        if any(_dist_m(f, k) < DEDUPE_M for k in kept):
            dropped.append(f)
        else:
            kept.append(f)
    return kept, dropped


def prune(rows: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    kept, removed = [], []
    for f in rows:
        ok, why = classify(f)
        (kept if ok else removed).append(f if ok else (f, why))
    shelters = [f for f in kept if f["kind"] == "shelter"]
    others = [f for f in kept if f["kind"] != "shelter"]
    s_keep, s_drop = dedupe(shelters, "shelter")
    removed += [(f, f"duplicate within {DEDUPE_M} m") for f in s_drop]
    h_keep, h_drop = dedupe(others, "hospital")     # separate buildings of one hospital campus
    removed += [(f, f"same campus as another hospital (within {DEDUPE_M} m)") for f in h_drop]
    final = sorted(s_keep + h_keep, key=lambda f: f["id"])
    return final, removed


def main(dry_run: bool = False) -> None:
    from collections import Counter
    from app.config import DATA_DIR
    path = DATA_DIR / "facilities.json"
    full = DATA_DIR / "facilities_full.json"
    src = full if full.exists() else path          # always prune from the untouched original
    rows = json.loads(src.read_text(encoding="utf-8"))
    final, removed = prune(rows)
    by = Counter((f["kind"], why) for f, why in removed)
    print(f"read {len(rows)} facilities from {src.name}: "
          f"{sum(1 for f in rows if f['kind'] == 'hospital')} hospitals, "
          f"{sum(1 for f in rows if f['kind'] == 'shelter')} shelters")
    print("removed, by reason:")
    for (kind, why), n in by.most_common():
        print(f"  {n:5d}  {kind:9s} {why}")
    print("sample of removed names:")
    for f, why in removed[:15]:
        print(f"    [{f['kind']}] {f.get('name')}  ({why})")
    print("sample of kept hospitals:")
    for f in [x for x in final if x["kind"] == "hospital"][:10]:
        print(f"    {f['name']}")
    print(f"result: {sum(1 for f in final if f['kind'] == 'hospital')} hospitals, "
          f"{sum(1 for f in final if f['kind'] == 'shelter')} shelters")
    if dry_run:
        print("dry run: nothing written")
        return
    if not full.exists():
        shutil.copyfile(path, full)
    path.write_text(json.dumps(final, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path} (original kept as {full.name}; restart the server to load it)")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
