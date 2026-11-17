import sys, time, numpy as np, cupy as cp
sys.path.insert(0, r"C:\Monad-Hackathon-render"); sys.path.insert(0, r"C:\Users\umpqu\AppData\Local\Temp\claude\646ef6c61f8dd376\d2b0ef5b-4874-4ba2-92cf-a139a8382f89\scratchpad")
from sim.terrain import load_terrain
from sim.scene import Scene
from gpu_fp import GpuShade
rid = "w00003-c44d133e"
a = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz")["S1-01.step"]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
sc = Scene(h, cr, x0, y0, cell, display="off"); G = GpuShade(sc)
names = ["gl+zread", "zbuf->P", "reach+Pr", "slopes/alb/normals", "off-ground", "horizons", "irradiance", "tone", "noise(cpu)", "noise up+gamma", "readback"]
acc = np.zeros(len(names)); n = 0
for i in range(400, 460):
    sc.set_rover("cam", tuple(float(v) for v in a[i, 1:4])); t0 = time.perf_counter(); prof = []
    G.first_person("cam", prof=prof)
    if i < 405: continue
    ts = [t0] + [t for _, t in prof]; acc += np.diff(ts); n += 1
for k, v in zip(names, acc / n): print(f"{k:20s} {v*1e3:6.1f} ms")
print("total", round(acc.sum() / n * 1e3, 1))
