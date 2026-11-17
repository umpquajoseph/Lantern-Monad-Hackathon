"""GPU first_person wall time, no profiling syncs: consecutive steps (as a run) and the 50 spread poses."""
import sys, time, numpy as np, cupy as cp
sys.path.insert(0, r"C:\Monad-Hackathon-render"); sys.path.insert(0, __import__("os").path.dirname(__file__))
from sim.terrain import load_terrain
from sim.scene import Scene
from gpu_fp import GpuShade
rid = "w00003-c44d133e"
a = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz")["S1-01.step"]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
sc = Scene(h, cr, x0, y0, cell, display="off"); G = GpuShade(sc)
def run(idx):
    T, S = [], []
    for i in idx:
        t = time.perf_counter(); sc.set_rover("cam", tuple(float(v) for v in a[i, 1:4])); S.append(time.perf_counter() - t)
        tim = []; G.first_person("cam", timings=tim); T.append(tim[0][0] + tim[0][1])
    return np.array(T[5:]) * 1e3, np.array(S[5:]) * 1e3
for name, idx in (("consecutive 300-500", range(300, 500)), ("spread 50", np.linspace(0, len(a) - 1, 50).astype(int))):
    T, S = run(idx)
    print(f"{name}: first_person median {np.median(T):.1f} ms, mean {T.mean():.1f}, p90 {np.percentile(T, 90):.1f}; set_rover mean {S.mean():.1f} ms")
