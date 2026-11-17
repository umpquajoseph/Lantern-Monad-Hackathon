"""CuPy first_person vs numpy first_person on 50 check-2 poses: time, pixel diff, depth diff, Vision.rims."""
import sys, time, json, numpy as np, cupy as cp
sys.path.insert(0, r"C:\Monad-Hackathon-render"); sys.path.insert(0, r"C:\Users\umpqu\AppData\Local\Temp\claude\646ef6c61f8dd376\d2b0ef5b-4874-4ba2-92cf-a139a8382f89\scratchpad")
from sim.terrain import load_terrain
from sim.scene import Scene
from sim.vision import Vision
from gpu_fp import GpuShade
rid = "w00003-c44d133e"
st = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz"); a = st["S1-01.step"]
nr = np.diff(st["S1-01.rim_o"])
rim_steps = list(np.flatnonzero(nr > 0)); spread = list(np.linspace(0, len(a) - 1, 50 - len(rim_steps)).astype(int))
poses = sorted(set(rim_steps + spread))[:50]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
sc = Scene(h, cr, x0, y0, cell, display="off")
G = GpuShade(sc)
pose = lambda i: tuple(float(v) for v in a[i, 1:4])
sc.set_rover("cam", pose(poses[0])); G.first_person("cam"); G.first_person("cam")   # warm-up (kernel compile)
rows, tc, tg = [], [], []
for i in poses:
    sc.set_rover("cam", pose(i))
    t0 = time.perf_counter(); ic, dc = sc.first_person("cam"); tc.append(time.perf_counter() - t0)
    sc.set_rover("cam", pose(i))                      # drop cached CPU horizon maps; GPU builds its own
    tim = []
    ig, dg = G.first_person("cam", timings=tim); tg.append(tim[0])
    d = np.abs(ic[..., 0].astype(int) - ig[..., 0].astype(int))
    dd = np.abs(np.nan_to_num(dc, nan=-1) - np.nan_to_num(dg, nan=-1))
    rc = Vision(sc, np.random.default_rng(int(i))).rims(ic, dc)
    rg = Vision(sc, np.random.default_rng(int(i))).rims(ig, dg)
    same = len(rc) == len(rg) and (len(rc) == 0 or np.allclose(rc, rg, atol=1e-6))
    rows.append(dict(step=int(i), rims_cpu=int(len(rc)), rims_gpu=int(len(rg)), rims_same=bool(same),
                     px_mean=float(d.mean()), px_max=int(d.max()), px_n_gt1=int((d > 1).sum()), depth_maxdiff=float(dd.max()), n_off=tim[0][2]))
    if i == 468:
        import cv2; cv2.imwrite("gpu_468.png", np.hstack([ic[..., 0], ig[..., 0], np.clip(d * 50, 0, 255).astype(np.uint8)]))
# GPU timing alone, repeated on the same pose list (no CPU frame in between)
tg2 = []
for i in poses:
    sc.set_rover("cam", pose(i)); tim = []; G.first_person("cam", timings=tim); tg2.append(tim[0])
gl = np.array([t[0] for t in tg2]); sh = np.array([t[1] for t in tg2])
res = dict(poses=len(poses), cpu_first_person_ms_median=round(1e3 * np.median(tc), 1),
           gpu_gl_plus_zread_ms_median=round(1e3 * np.median(gl), 1), gpu_shade_ms_median=round(1e3 * np.median(sh), 1),
           gpu_shade_ms_p90=round(1e3 * np.percentile(sh, 90), 1), gpu_total_ms_median=round(1e3 * np.median(gl + sh), 1),
           frames_rims_identical=sum(r["rims_same"] for r in rows), rim_frames_cpu=sum(r["rims_cpu"] > 0 for r in rows),
           rim_pts_cpu=sum(r["rims_cpu"] for r in rows), rim_pts_gpu=sum(r["rims_gpu"] for r in rows),
           px_mean_max=max(r["px_mean"] for r in rows), px_max=max(r["px_max"] for r in rows),
           px_gt1_total=sum(r["px_n_gt1"] for r in rows), depth_maxdiff=max(r["depth_maxdiff"] for r in rows),
           off_ground_px_max=max(r["n_off"] for r in rows))
json.dump(dict(summary=res, rows=rows), open("gpu_test.json", "w"), indent=1); print(json.dumps(res, indent=1))
