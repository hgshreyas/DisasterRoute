"""Tests for scripts/prune_facilities.py rules.  Run from backend/:  python tests/test_prune.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prune_facilities as P


def H(name, **kw):
    return {"id": name or "x", "name": name, "kind": "hospital", "lat": 12.9, "lon": 77.6, **kw}


def S(name, **kw):
    return {"id": name or "x", "name": name, "kind": "shelter", "lat": 12.9, "lon": 77.6, **kw}


def main():
    keep = lambda f: P.classify(f)[0]
    assert keep(H("Manipal Hospital"))
    assert keep(H("St. John's Medical College Hospital"))
    assert keep(H("Dental Hospital"))                     # 'hospital' wins over clinic words
    assert keep(H("Sunrise", emergency="yes"))
    assert keep(H("Sunrise", beds="40"))
    assert not keep(H("Sunrise Clinic"))
    assert not keep(H("City Dental Care"))
    assert not keep(H("Sunrise", healthcare="clinic"))
    assert not keep(H("Manipal Hospital", emergency="no"))
    assert not keep(H("Unnamed hospital"))
    assert not keep(H(None))
    assert not keep(H("Sri Sai Diagnostic Centre"))
    assert keep(S("Koramangala Indoor Stadium"))
    assert keep(S("Ward 5 Community Hall", area_m2=900))
    assert not keep(S("Ward 5 Community Hall", area_m2=80))
    assert not keep(S("Elite Sports Club"))
    assert not keep(S("Unnamed shelter"))
    rows = [S("A Hall", id="s1", lat=12.9000, lon=77.6000, area_m2=500),
            S("B Hall", id="s2", lat=12.9003, lon=77.6000, area_m2=900),   # 33 m from A: duplicate
            S("C Hall", id="s3", lat=12.9100, lon=77.6000, area_m2=500),
            H("Manipal Hospital", id="h1")]
    final, removed = P.prune(rows)
    ids = {f["id"] for f in final}
    assert ids == {"s2", "s3", "h1"}, ids
    assert [f["id"] for f, _ in removed] == ["s1"]
    print("PRUNE TESTS PASSED")


if __name__ == "__main__":
    main()
