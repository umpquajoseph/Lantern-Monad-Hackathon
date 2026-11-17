"""Lidar.scan on the GPU vs numpy, 300 consecutive check-2 poses: ranges and points identical (same noise seed), time."""
import sys, os, time, json, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import sim.lidar as LI
from sim.scene import Scene
from sim.terrain import load_terrain
rid = "w00003-c44d133e"
a = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz")["S1-01.step"]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
sc = Scene(h, cr, x0, y0, cell, display="off")
eq = 0; nan_eq = 0; maxd = 0.0; T = {True: [], False: []}; hz_eq = 0
for k, i in enumerate(range(300, 600)):
    pose = tuple(float(v) for v in a[i, 1:4]); out = {}
    for g in (True, False):
        LI.GPU_LIDAR = g; lid = LI.Lidar(sc, np.random.default_rng(i))
        t = time.perf_counter(); pts, rng = lid.scan(pose); T[g].append(time.perf_counter() - t)
        out[g] = (pts, rng, lid.hazards(pts))
    (pg, rg, hg), (pc, rc, hc) = out[True], out[False]
    nan_eq += int(np.array_equal(np.isnan(rg), np.isnan(rc)))
    eq += int(np.array_equal(rg, rc, equal_nan=True))
    hz_eq += int(np.array_equal(hg[0], hc[0]) and hg[1] == hc[1])
    if np.isfinite(rc).any(): maxd = max(maxd, float(np.nanmax(np.abs(rg - rc))))
res = dict(poses=300, gpu_used=bool(sc.gpu()), ranges_identical=eq, nan_pattern_identical=nan_eq, hazards_identical=hz_eq,
           range_maxdiff_m=maxd, gpu_ms_median=round(1e3 * np.median(T[True][3:]), 2), numpy_ms_median=round(1e3 * np.median(T[False][3:]), 2))
print(json.dumps(res)); json.dump(res, open(os.path.join(os.path.dirname(__file__), "lidar_gpu_check.json"), "w"), indent=1)
