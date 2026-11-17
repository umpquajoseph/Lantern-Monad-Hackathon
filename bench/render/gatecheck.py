"""Render check-2 frames OUTSIDE a narrow geometry gate (craters D>=10 m within 15 m, view +-35 deg) and count frames where
the run's vision (Vision.rims) still returns rims. Writes only to this scratch folder."""
import sys, math, time, json, numpy as np
sys.path.insert(0, r"C:\Monad-Hackathon-render")
from scipy.spatial import cKDTree
from sim.terrain import load_terrain
from sim.scene import Scene
from sim.vision import Vision
rid = "w00003-c44d133e"
a = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz")["S1-01.step"]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
def gate(dmin, rmax):
    c = cr[cr[:, 2] >= dmin]; t = cKDTree(c[:, :2]); need = np.zeros(len(a), bool)
    for k, (x, y, hd) in enumerate(a[:, 1:4]):
        for j in t.query_ball_point((x, y), rmax + c[:, 2].max() / 2):
            cx, cy, d = c[j, :3]; dc = math.hypot(cx - x, cy - y)
            if dc - d / 2 > rmax: continue
            ang = abs((math.atan2(cy - y, cx - x) - hd + math.pi) % (2 * math.pi) - math.pi)
            if dc <= d / 2 or ang <= math.radians(35) + math.asin(min(1, d / 2 / dc)):
                need[k] = True; break
    return need
narrow, wide = gate(10, 15), gate(0, 25)
sc = Scene(h, cr, x0, y0, cell, display="off")
out = []; t0 = time.perf_counter()
todo = np.flatnonzero(~narrow)[::2]
for n, k in enumerate(todo):
    sc.set_rover("cam", tuple(float(v) for v in a[k, 1:4])); img, depth = sc.first_person("cam")
    r = Vision(sc, np.random.default_rng(int(k))).rims(img, depth)
    out.append((int(k), int(len(r)), bool(wide[k])))
    if n % 200 == 0: print(n, len(todo), f"{time.perf_counter() - t0:.0f} s", flush=True)
hit = [o for o in out if o[1] > 0]
res = dict(narrow_render_frac=float(narrow.mean()), wide_render_frac=float(wide.mean()), rendered_outside_narrow=len(out),
           frames_with_rims=len(hit), of_which_outside_wide=sum(not o[2] for o in hit), hits=hit[:50])
json.dump(res, open("gatecheck.json", "w"), indent=1); print(res)
