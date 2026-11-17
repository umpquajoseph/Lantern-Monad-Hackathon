"""One camera sortie (haynes, seed 3, 1x1) in memory; saves its steps arrays to an npz (argv[1]) and prints wall time
and the rig's split. argv[2] (optional): an npz to compare with; prints IDENTICAL or the differing arrays."""
import sys, os, time, json, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from sim.campaign import Campaign
from sim.live_runs import SEEDS, settings_for, ranges_for
out = sys.argv[1]; site = "haynes"
s = settings_for(site, 1, 1)
t = time.perf_counter(); c = Campaign(SEEDS[site], s, ranges_for(s)).run(); wall = time.perf_counter() - t
arr = {k: np.asarray(v) for k, v in c.step_arrays.items()}
np.savez(out, **arr)
print(json.dumps(dict(wall_s=round(wall, 1), setup_s=round(c.rig.t_setup, 1), steps=c.rig.steps,
                      s_per_step_wall=round((wall - c.rig.t_setup) / c.rig.steps, 4), frames=c.rig.frames, split=c.rig.s_per_step())))
if len(sys.argv) > 2:
    ref = np.load(sys.argv[2])
    diff = [k for k in sorted(set(ref.files) | set(arr)) if k not in arr or k not in ref.files or ref[k].shape != arr[k].shape
            or not np.array_equal(ref[k], arr[k], equal_nan=arr[k].dtype.kind == "f")]
    print("IDENTICAL" if not diff else f"DIFFER: {diff}")
