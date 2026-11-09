"""Tiny reader for the common forms of the OpenStreetMap `opening_hours` tag.

Understands:  "24/7"   "Mo-Fr 09:00-17:00"   "Mo-Sa 08:00-20:00; Su off"
              "Mo,We,Fr 10:00-14:00,16:00-20:00"   "09:00-21:00" (every day)   overnight spans (22:00-06:00)
Anything else (public holidays, sunrise/sunset, months, week numbers, free text) gives None
("unknown"), never a guess.  Later rules override earlier ones for the days they name,
as in the OSM syntax.
"""
from __future__ import annotations

import re
from datetime import datetime

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_TIME = r"\d{1,2}:\d{2}"
_RANGE = re.compile(rf"^({_TIME})-({_TIME})$")
_DAYPART = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$")


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m < 60):
        raise ValueError(hhmm)
    return h * 60 + m


def _days(part: str) -> set[int]:
    out: set[int] = set()
    for tok in part.split(","):
        m = _DAYPART.match(tok.strip())
        if not m:
            raise ValueError(tok)
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        out.update(range(a, b + 1) if a <= b else list(range(a, 7)) + list(range(0, b + 1)))
    return out


def parse(spec: str) -> dict[int, list[tuple[int, int]]] | None:
    """weekday (0=Mon) -> list of (start_min, end_min); end may exceed 1440 for overnight spans."""
    if not isinstance(spec, str) or not spec.strip():
        return None
    table: dict[int, list[tuple[int, int]]] = {d: [] for d in range(7)}
    try:
        for rule in (r.strip() for r in spec.split(";") if r.strip()):
            if rule == "24/7":
                days, spans = set(range(7)), [(0, 1440)]
            else:
                m = re.match(r"^((?:Mo|Tu|We|Th|Fr|Sa|Su)[A-Za-z,\-]*)?\s*(.*)$", rule)
                dpart, rest = (m.group(1) or "").strip(), m.group(2).strip()
                days = _days(dpart) if dpart else set(range(7))
                if rest in ("off", "closed"):
                    spans = []
                elif rest == "24/7":
                    spans = [(0, 1440)]
                else:
                    spans = []
                    for tok in rest.split(","):
                        r = _RANGE.match(tok.strip())
                        if not r:
                            return None
                        a, b = _mins(r.group(1)), _mins(r.group(2))
                        spans.append((a, b if b > a else b + 1440))      # overnight
            for d in days:
                table[d] = list(spans)
    except ValueError:
        return None
    return table


def is_open(spec: str | None, when: datetime) -> bool | None:
    """True / False, or None when the tag is missing or cannot be read reliably."""
    table = parse(spec) if spec else None
    if table is None:
        return None
    now = when.hour * 60 + when.minute
    wd = when.weekday()
    for a, b in table[wd]:
        if a <= now < b:
            return True
    for a, b in table[(wd - 1) % 7]:               # yesterday's overnight span still running
        if b > 1440 and now < b - 1440:
            return True
    return False
