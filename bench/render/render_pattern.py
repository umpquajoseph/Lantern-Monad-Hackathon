"""Replay a sortie's rendered frames in order (the gate's pattern: jumps between frames): time set_rover,
GL+z-read and GPU shade per frame. Poses from a saved sortie npz (argv[1])."""
import sys, os, time, math, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dataclasses import replace
import sim.campaign_camera as CC
from sim.campaign import Campaign
from sim.live_runs import SEEDS, settings_for, ranges_for
from sim.scene import Scene, HFOV_DEG
from sim.terrain import load_terrain, DATA
a = np.load(sys.argv[1])["S1-01.step"]
s = settings_for("haynes", 1, 1); c = Campaign(SEEDS["haynes"], s, ranges_for(s))
cr = c.world.craters
from scipy.spatial import cKDTree
tree = cKDTree(cr[:, :2]); rmax = cr[:, 2].max() / 2
def gate(x, y, hd):
    idx = tree.query_ball_point((x, y), CC.GATE_RANGE_M + rmax)
    if not idx: return False
    cc = cr[idx]; R = cc[:, 2] / 2; dc = np.hypot(cc[:, 0] - x, cc[:, 1] - y); near = dc - R <= CC.GATE_RANGE_M
    if np.any(near & (dc <= R)): return True
    ang = np.abs((np.arctan2(cc[:, 1] - y, cc[:, 0] - x) - hd + math.pi) % (2 * math.pi) - math.pi)
    return bool(np.any(near & (ang <= math.radians(HFOV_DEG / 2 + CC.GATE_MARGIN_DEG) + np.arcsin(np.minimum(1, R / np.maximum(dc, 1e-6))))))
steps = [k for k in range(len(a)) if gate(*a[k, 1:4])]
import glob
gp = sorted(glob.glob(str(DATA / "terrain" / "run_ground" / f"{c.run_id}.npz")))[0]
h, crg, x0, y0, cell = load_terrain(gp); sc = Scene(h, crg, x0, y0, cell, display="off")
T = []
for k in steps:
    t0 = time.perf_counter(); sc.set_rover("cam", tuple(float(v) for v in a[k, 1:4])); t1 = time.perf_counter()
    sc.first_person("cam"); t2 = time.perf_counter()
    T.append((t1 - t0, sc.timings["fp_render_ms"] / 1e3, sc.timings["fp_shade_ms"] / 1e3, t2 - t1))
T = np.array(T[3:]) * 1e3
print(f"{len(steps)} gated frames of {len(a)} steps")
for name, col in zip(("set_rover", "GL+zread", "shade", "first_person"), T.T):
    print(f"{name:12s} median {np.median(col):6.1f}  mean {col.mean():6.1f}  p90 {np.percentile(col, 90):6.1f} ms  total {col.sum()/1e3:5.1f} s")
