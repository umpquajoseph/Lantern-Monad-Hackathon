"""Gate share per SEEDS site: the sampled engine's true paths (3 Scouts x 3 sorties, the live-run settings and seed),
resampled every 2 m, against the world's craters. Narrow: D>=10 m within 15 m; wide: every crater within 25 m;
view +-35 deg. Read-only: runs Campaign in memory, writes only to this folder."""
import sys, math, json, time, numpy as np
sys.path.insert(0, r"C:\Monad-Hackathon-render")
from dataclasses import replace
from scipy.spatial import cKDTree
from sim.campaign import Campaign, POSE_COLS
from sim.live_runs import SEEDS, settings_for, ranges_for
ix, iy, ih = POSE_COLS.index("true_x"), POSE_COLS.index("true_y"), POSE_COLS.index("true_heading")
def share(cr, P, dmin, rmax):
    c = cr[cr[:, 2] >= dmin]; t = cKDTree(c[:, :2]); dmax = c[:, 2].max() / 2; need = np.zeros(len(P), bool)
    for k, (x, y, hd) in enumerate(P):
        for j in t.query_ball_point((x, y), rmax + dmax):
            cx, cy, d = c[j, :3]; dc = math.hypot(cx - x, cy - y)
            if dc - d / 2 > rmax: continue
            ang = abs((math.atan2(cy - y, cx - x) - hd + math.pi) % (2 * math.pi) - math.pi)
            if dc <= d / 2 or ang <= math.radians(35) + math.asin(min(1, d / 2 / dc)):
                need[k] = True; break
    return need.mean()
out = {}
for site, seed in SEEDS.items():
    t0 = time.perf_counter()
    s = replace(settings_for(site, 3, 3), nav="sampled")
    c = Campaign(seed, s, ranges_for(s)).run()
    cr = np.asarray(c.world.craters, float)
    P = []
    for sid, rows in c.poses.items():
        a = np.asarray(rows, float)
        xy = a[:, [ix, iy]]; seg = np.hypot(*np.diff(xy, axis=0).T); cum = np.concatenate([[0], np.cumsum(seg)])
        if cum[-1] < 2: continue
        q = np.arange(0, cum[-1], 2.0)
        x = np.interp(q, cum, xy[:, 0]); y = np.interp(q, cum, xy[:, 1])
        k = np.clip(np.searchsorted(cum, q, side="right") - 1, 0, len(seg) - 1)
        hd = np.arctan2(xy[k + 1, 1] - xy[k, 1], xy[k + 1, 0] - xy[k, 0])   # direction of travel
        P.append(np.column_stack([x, y, hd]))
    P = np.vstack(P)
    d = cr[:, 2]
    out[site] = dict(seed=seed, path_steps_2m=len(P), km=round(len(P) * 2 / 1000, 1), craters=len(cr),
                     ge10_per_km2_world=None, narrow=round(share(cr, P, 10, 15), 4), wide=round(share(cr, P, 0, 25), 4),
                     mid=round(share(cr, P, 0, 20), 4), secs=round(time.perf_counter() - t0))
    print(site, out[site], flush=True)
json.dump(out, open("gate_sites.json", "w"), indent=1)
