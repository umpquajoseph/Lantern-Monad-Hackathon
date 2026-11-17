"""Fused-kernel first_person on consecutive check-2 steps: wall time by stage (cuda events around the kernels)."""
import sys, os, time, zlib, numpy as np, cupy as cp
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..")); sys.path.insert(0, os.path.dirname(__file__))
from sim.terrain import load_terrain
from sim.scene import Scene
import gpu_fused as GF
rid = "w00003-c44d133e"
a = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz")["S1-01.step"]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
sc = Scene(h, cr, x0, y0, cell, display="off"); G = GF.FusedShade(sc)
T, K = [], []
for i in range(300, 500):
    sc.set_rover("cam", tuple(float(v) for v in a[i, 1:4]))
    tim = []; G.first_person("cam", timings=tim); T.append(tim[0][:2])
T = np.array(T[5:]) * 1e3
print(f"consecutive: gl+zread median {np.median(T[:,0]):.1f} ms, shade+readback median {np.median(T[:,1]):.1f} ms, total median {np.median(T.sum(1)):.1f}, mean {T.sum(1).mean():.1f}")
# kernel-only time, and the CPU noise draw, on the last pose
st, en = cp.cuda.Event(), cp.cuda.Event()
st.record(); [G.first_person("cam") for _ in range(10)]; en.record(); en.synchronize()
seed = 1; t = time.perf_counter(); [np.random.default_rng(seed).standard_normal((480, 640), dtype=np.float32) for _ in range(20)]
print(f"noise draw (cpu) {(time.perf_counter()-t)/20*1e3:.1f} ms")
