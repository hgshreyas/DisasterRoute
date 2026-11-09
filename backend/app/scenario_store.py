"""Create, replace and delete hazard scenarios at runtime (what the UI's "add hazard" uses).

Scenarios live as GeoJSON files in data/scenarios/. Files made through this module carry
"created_by": "user"; the built-in demo scenarios do not, and are protected from
overwrite and deletion.
"""
from __future__ import annotations

import json
import re

from .hazards import build_state, normalize_features

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
RESERVED = {"none"}


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def scenario_details(rg) -> list[dict]:
    out = []
    for name in rg.scenario_names():
        try:
            user = _read(rg.scenario_dir / f"{name}.geojson").get("created_by") == "user"
        except (OSError, ValueError):
            user = False
        out.append({"name": name, "user_created": user})
    return out


def create_scenario(rg, name: str, items: list, overwrite: bool = False) -> dict:
    """Validate, apply, and save a scenario. Nothing is saved if anything fails.

    Raises ValueError (bad input), FileExistsError (name taken, overwrite=False) or
    PermissionError (a built-in scenario).
    """
    if not NAME_RE.match(name or "") or name in RESERVED:
        raise ValueError("name must be 1-40 chars: lowercase letters, digits, '_' or '-' "
                         "(start with a letter or digit), and not 'none'")
    feats = normalize_features(items)
    path = rg.scenario_dir / f"{name}.geojson"
    if path.exists():
        if _read(path).get("created_by") != "user":
            raise PermissionError(f"'{name}' is a built-in scenario; choose another name")
        if not overwrite:
            raise FileExistsError(f"'{name}' already exists; send overwrite=true to replace it")

    fc = {"type": "FeatureCollection", "name": name, "created_by": "user", "features": feats}
    state, summary = build_state(rg.csr, rg.egeom, rg.proj, fc)     # may raise: before saving
    rg.scenario_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc), encoding="utf-8")
    rg._states[name] = (state, summary)
    return {"name": name, "summary": summary, "geojson": fc}


def delete_scenario(rg, name: str) -> None:
    path = rg.scenario_dir / f"{name}.geojson"
    if not NAME_RE.match(name or "") or not path.exists():
        raise KeyError(name)
    if _read(path).get("created_by") != "user":
        raise PermissionError(f"'{name}' is a built-in scenario and cannot be deleted")
    path.unlink()
    rg._states.pop(name, None)
