"""Per-part timing of a camera Scout-step on check-2 true poses (read-only)."""
import sys, time, numpy as np, cProfile, pstats, io
sys.path.insert(0, r"C:\Monad-Hackathon-render")
from sim.terrain import load_terrain
from sim.scene import Scene
from sim.vision import Vision
from sim.lidar import Lidar
rid = "w00003-c44d133e"
st = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz"); a = st["S1-01.step"]
poses = list(range(400, 520))   # 120 consecutive steps incl. a rim episode at 468
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
t=time.perf_counter(); sc = Scene(h, cr, x0, y0, cell, display="off"); print("scene setup s", round(time.perf_counter()-t,1))
vis = Vision(sc, np.random.default_rng(1)); lid = Lidar(sc, np.random.default_rng(2))
T = {k: [] for k in ("set_rover", "gl+zread", "shade", "vision", "lidar_scan", "lidar_haz")}
def step(i):
    pose = tuple(float(v) for v in a[i, 1:4])
    t0 = time.perf_counter(); sc.set_rover("cam", pose); t1 = time.perf_counter()
    img, depth = sc.first_person("cam"); t2 = time.perf_counter()
    r = vis.rims(img, depth); t3 = time.perf_counter()
    pts, rng = lid.scan(pose); t4 = time.perf_counter(); lid.hazards(pts); t5 = time.perf_counter()
    T["set_rover"].append(t1-t0); T["gl+zread"].append(sc.timings["fp_render_ms"]/1e3); T["shade"].append(sc.timings["fp_shade_ms"]/1e3)
    T["vision"].append(t3-t2); T["lidar_scan"].append(t4-t3); T["lidar_haz"].append(t5-t4)
    return len(r)
for i in poses[:3]: step(i)
for k in T: T[k].clear()
pr = cProfile.Profile(); pr.enable()
n = [step(i) for i in poses]
pr.disable()
print("frames with rims", sum(x > 0 for x in n), "of", len(n))
for k, v in T.items(): print(f"{k:11s} mean {np.mean(v)*1e3:7.1f} ms  median {np.median(v)*1e3:7.1f}  max {np.max(v)*1e3:7.1f}")
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(22); print(s.getvalue()[-4500:])
