"""Opening-hours reader tests.   Run from backend/:  python tests/test_hours.py"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.opening_hours import is_open

MON = datetime(2026, 11, 9, 0, 0)          # 2026-11-09 is a Monday


def at(day_offset, h, m=0):
    return MON.replace(day=9 + day_offset, hour=h, minute=m)


def main():
    assert is_open("24/7", at(3, 3)) is True
    assert is_open("Mo-Fr 09:00-17:00", at(0, 10)) is True
    assert is_open("Mo-Fr 09:00-17:00", at(0, 17)) is False          # end is exclusive
    assert is_open("Mo-Fr 09:00-17:00", at(5, 10)) is False          # Saturday
    assert is_open("Mo-Sa 08:00-20:00; Su off", at(6, 12)) is False  # Sunday off
    assert is_open("Mo-Sa 08:00-20:00; Su off", at(2, 12)) is True
    assert is_open("Mo,We,Fr 10:00-14:00,16:00-20:00", at(2, 15)) is False   # gap between spans
    assert is_open("Mo,We,Fr 10:00-14:00,16:00-20:00", at(2, 17)) is True
    assert is_open("Mo,We,Fr 10:00-14:00,16:00-20:00", at(1, 11)) is False   # Tuesday not listed
    assert is_open("09:00-21:00", at(4, 20, 30)) is True                     # no days = every day
    assert is_open("Mo-Su 22:00-06:00", at(0, 23)) is True                   # overnight, evening part
    assert is_open("Mo-Su 22:00-06:00", at(1, 5)) is True                    # overnight, morning part
    assert is_open("Mo-Su 22:00-06:00", at(1, 7)) is False
    assert is_open("Mo-Fr 08:00-12:00; We off", at(2, 9)) is False           # later rule overrides
    assert is_open("Sa-Mo 10:00-12:00", at(6, 11)) is True                   # day range wrapping the week
    # not understood -> unknown, never a guess
    for odd in ("sunrise-sunset", "PH off", "Mo-Fr 09:00-17:00; PH off", "Jan-Mar 09:00-17:00", "by appointment", "", None, "Mo 25:00-26:00"):
        assert is_open(odd, at(0, 10)) is None, odd
    print("ALL OPENING-HOURS TESTS PASSED")


if __name__ == "__main__":
    main()
