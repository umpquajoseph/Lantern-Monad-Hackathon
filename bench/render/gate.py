"""Fraction of check-2 steps a geometry gate must render, for crater-size floors and ranges (read-only)."""
import sys, math, numpy as np
sys.path.insert(0, r"C:\Monad-Hackathon-render")
from scipy.spatial import cKDTree
from sim.terrain import load_terrain
rid = "w00003-c44d133e"
z = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz")
a = z["S1-01.step"]; o = z["S1-01.rim_o"]; rims = z["S1-01.rims"].astype(float)
rng = np.hypot(rims[:, 0], rims[:, 1]); print(f"kept rim range m: min {rng.min():.1f} median {np.median(rng):.1f} max {rng.max():.1f}")
rim_steps = np.flatnonzero(np.diff(o) > 0)
_, cr, *_ = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
print("craters on ground", len(cr), "D>=3", int((cr[:, 2] >= 3).sum()), "D>=10", int((cr[:, 2] >= 10).sum()))
for dmin in (10, 5, 3):
    for rmax in (15, 20, 25):
        c = cr[cr[:, 2] >= dmin]; t = cKDTree(c[:, :2]); need = np.zeros(len(a), bool)
        for k, (x, y, hd) in enumerate(a[:, 1:4]):
            for j in t.query_ball_point((x, y), rmax + c[:, 2].max() / 2):
                cx, cy, d = c[j, :3]; dc = math.hypot(cx - x, cy - y)
                if dc - d / 2 > rmax: continue
                ang = abs((math.atan2(cy - y, cx - x) - hd + math.pi) % (2 * math.pi) - math.pi)
                if dc <= d / 2 or ang <= math.radians(30 + 5) + math.asin(min(1, d / 2 / dc)):
                    need[k] = True; break
        print(f"D>={dmin:2d} m, range {rmax} m: render {need.mean()*100:5.1f}% of steps; run's rim steps inside gate {need[rim_steps].sum()}/{len(rim_steps)}")
