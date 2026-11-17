"""Scene.first_person with sim.scene.GPU_SHADE on vs off, 50 check-2 poses: Vision.rims, pixels, depth, time."""
import sys, os, time, json, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import sim.scene as SC
from sim.terrain import load_terrain
from sim.vision import Vision
rid = "w00003-c44d133e"
st = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz"); a = st["S1-01.step"]
nr = np.diff(st["S1-01.rim_o"])
poses = sorted(set(list(np.flatnonzero(nr > 0)) + list(np.linspace(0, len(a) - 1, 44).astype(int))))[:50]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
sc = SC.Scene(h, cr, x0, y0, cell, display="off")
same = 0; pts = [0, 0]; pmax = 0; dmax = 0.0; tg = []; tc = []
for i in poses:
    pose = tuple(float(v) for v in a[i, 1:4])
    out = {}
    for g in (True, False):
        SC.GPU_SHADE = g; sc.set_rover("cam", pose)
        t = time.perf_counter(); out[g] = sc.first_person("cam"); (tg if g else tc).append(time.perf_counter() - t)
    (ig, dg), (ic, dc) = out[True], out[False]
    rg = Vision(sc, np.random.default_rng(int(i))).rims(ig, dg); rc = Vision(sc, np.random.default_rng(int(i))).rims(ic, dc)
    same += int(len(rg) == len(rc) and (len(rc) == 0 or np.allclose(rg, rc, atol=1e-6))); pts[0] += len(rg); pts[1] += len(rc)
    pmax = max(pmax, int(np.abs(ig.astype(int) - ic.astype(int)).max()))
    dmax = max(dmax, float(np.nanmax(np.abs(dg - dc))) if np.isfinite(dc).any() else 0.0)
    assert np.array_equal(np.isnan(dg), np.isnan(dc))
res = dict(poses=len(poses), gpu_used=bool(sc._gpu), rims_identical=same, rim_pts_gpu=pts[0], rim_pts_numpy=pts[1],
           px_max=pmax, depth_maxdiff=dmax, gpu_ms_median=round(1e3 * np.median(tg[2:]), 1), numpy_ms_median=round(1e3 * np.median(tc[2:]), 1))
print(json.dumps(res)); json.dump(res, open(os.path.join(os.path.dirname(__file__), "scene_gpu_check.json"), "w"), indent=1)
