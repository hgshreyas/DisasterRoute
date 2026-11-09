"""Parallel first requests must load the graph and build each scenario once.   Run from backend/:  python tests/test_concurrency.py"""
import sys, time, types, threading, tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
B = Path(__file__).resolve().parent.parent
for p in (B, B/"tests"): sys.path.insert(0, str(p))
import test_hazards as TH
calls = []
fake = types.ModuleType("osmnx")
def load_graphml(path):
    calls.append(threading.get_ident()); time.sleep(1.0); return TH.synthetic_city()   # a real load takes a minute
fake.load_graphml = load_graphml
sys.modules["osmnx"] = fake
import app.graph as G
tmp = Path(tempfile.mkdtemp()); (tmp/"g.graphml").write_text("x")
G.GRAPH_PATH = tmp/"g.graphml"; G.LANDMARKS_PATH = tmp/"none.npz"
with ThreadPoolExecutor(6) as ex:                       # the page fires /modes, /scenarios, /metro, /route ... at once
    rgs = list(ex.map(lambda _: G.get_graph(), range(6)))
print("graph loads:", len(calls), "| distinct graph objects:", len({id(r) for r in rgs}))
assert len(calls) == 1 and len({id(r) for r in rgs}) == 1, "parallel first requests must share one graph load"
# scenario builds
rg = rgs[0]; n = []
orig = G.build_state
def slow_build(*a, **k):
    n.append(1); time.sleep(0.5); return orig(*a, **k)
G.build_state = slow_build
folder = Path(tempfile.mkdtemp()); TH.write_scenarios(folder); rg.scenario_dir = folder
with ThreadPoolExecutor(6) as ex:
    list(ex.map(lambda _: rg.scenario("flood"), range(6)))
print("scenario builds for one scenario requested 6 times at once:", len(n))
assert len(n) == 1, "a scenario must be built once"
print("CONCURRENCY TEST PASSED")
