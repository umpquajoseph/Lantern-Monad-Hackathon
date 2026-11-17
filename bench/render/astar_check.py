"""Every SlopeMap.plan call of one camera sortie (haynes seed 3, 1x1) made twice, compiled (sim/astar_fast.py)
and with the Python loop; counts calls whose results differ. Then the steps arrays against an npz (argv[1])."""
import sys, os, time, json, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import sim.rover as R
from sim.campaign import Campaign
from sim.live_runs import SEEDS, settings_for, ranges_for
orig = R.SlopeMap.plan
stats = dict(calls=0, compiled=0, differ=0, t_py=0.0, t_c=0.0)
def both(self, *a, **k):
    R.ASTAR_COMPILED = False; t = time.perf_counter(); p = orig(self, *a, **k); stats["t_py"] += time.perf_counter() - t
    R.ASTAR_COMPILED = True; t = time.perf_counter(); c = orig(self, *a, **k); stats["t_c"] += time.perf_counter() - t
    stats["calls"] += 1; stats["compiled"] += k.get("cost") is not None
    if p != c: stats["differ"] += 1
    return c
R.SlopeMap.plan = both
s = settings_for("haynes", 1, 1); c = Campaign(SEEDS["haynes"], s, ranges_for(s)).run()
ref = np.load(sys.argv[1]); arr = {k: np.asarray(v) for k, v in c.step_arrays.items()}
diff = [k for k in sorted(set(ref.files) | set(arr)) if k not in arr or k not in ref.files or ref[k].shape != arr[k].shape
        or not np.array_equal(ref[k], arr[k], equal_nan=arr[k].dtype.kind == "f")]
stats.update(ms_py=round(stats.pop("t_py") / stats["calls"] * 1e3, 2), ms_compiled=round(stats.pop("t_c") / stats["calls"] * 1e3, 3),
             steps="IDENTICAL" if not diff else f"DIFFER: {diff}")
print(json.dumps(stats)); json.dump(stats, open(os.path.join(os.path.dirname(__file__), "astar_check.json"), "w"), indent=1)
