"""A/B: one camera sortie (haynes, seed 3, 1 Scout x 1 sortie, as live-nav check 2) with the render gate on
and off, in memory. Pass: every steps.npz array identical. Writes only the result json beside this file
(the run ground is built under data/terrain/run_ground if this run id has none yet)."""
import sys, os, time, json, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import sim.campaign_camera as CC
from sim.campaign import Campaign
from sim.live_runs import SEEDS, settings_for, ranges_for
site = sys.argv[1] if len(sys.argv) > 1 else "haynes"
res = {}
arrs = {}
for gate in (True, False):
    CC.GATE_ON = gate
    s = settings_for(site, 1, 1)
    t = time.perf_counter()
    c = Campaign(SEEDS[site], s, ranges_for(s)).run()
    wall = time.perf_counter() - t
    arrs[gate] = {k: np.asarray(v) for k, v in c.step_arrays.items()}
    res["gate_on" if gate else "gate_off"] = dict(run_id=c.run_id, wall_s=round(wall, 1), setup_s=round(c.rig.t_setup, 1),
                                                  steps=c.rig.steps, frames=dict(c.rig.frames), s_per_step=c.rig.s_per_step())
    print(res, flush=True)
a, b = arrs[True], arrs[False]
diff = [k for k in sorted(set(a) | set(b)) if k not in a or k not in b or a[k].shape != b[k].shape
        or not np.array_equal(a[k], b[k], equal_nan=a[k].dtype.kind == "f")]
res["arrays"] = len(a); res["arrays_differing"] = diff
json.dump(res, open(os.path.join(os.path.dirname(__file__), f"gate_ab_{site}.json"), "w"), indent=1)
print("IDENTICAL" if not diff else f"DIFFER: {diff}")
