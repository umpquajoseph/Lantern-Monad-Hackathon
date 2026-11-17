"""Lantern's own front-arc share for a 10 m crater, measured on held-out frames.

    python -m sim.arc_share --batch arcshare-1 --frames 120 --chunk 40   # run three times; it resumes

The rule (fixed and committed before any measurement, 16 Nov 2026):

- What: for one crater in one frame, the share of its front half arc (the
  half of the rim circle nearest the camera, C23 sec. 5A, sim/rim_truth.py
  `near`) that has a detected rim pixel within rim_truth.TOL_M (0.5 m).
  Detected pixels are sim/vision.py detect_contours() before the contour
  filter, placed with the renderer's depth at the true pose.
- Which craters and poses: true craters 10-12 m across on the s89_134e
  grid. The camera stands 8-12 m (uniform) from the crater's nearest rim
  point, on a random bearing, facing the crater centre within +-10 deg,
  on ground under the slope limit and outside every crater of 5 m and up.
- Held out: every crater and every camera pose is more than HOLD_OUT_M from
  every pose of the three calibration traverses (routes.json of batch
  realism-cal), so no frame shows ground the traverses see. Seed 2026,
  unused by sim/calibrate.py.
- Statistic: the median over frames. The contour filter's width threshold
  is then that median x the map cut (WorldRanges.orbital_map_min_d_m),
  the same rule as before with Lantern's measured share in place of C23
  sec. 1's 80%.
- The number goes into Settings.rim_arc_share_measured with this batch and
  command as its source, and is not changed after the traverse table is
  read.

Second and last measurement, rule "route" (fixed and committed before it was
measured, 16 Nov 2026 20:20). The head-on rule above (rule "head-on", batch
arcshare-1, 0.553) faced each crater within +-10 deg; on camera runs most
detected mapped rims are seen about 31 deg off-axis (sim/rim_census.py on
live-nav-check2), so it overstated the arc a route frame shows. Changes,
and only these:

- Heading: the bearing to the crater centre plus a uniform offset over the
  camera's whole field of view, +-30 deg (A24 IV-B 60 deg; sim/scene.py).
- Detection reads the frame's noisy stereo depth (Vision.noisy_depth, as
  the runs do since ab141f1); a detected pixel is scored at its
  renderer-depth position, as before (the share asks which arc pixels vision
  found, not how well stereo placed them).
- Held out: a grid of its own, never used by a traverse, a run or
  arcshare-1: site s89_014e, terrain seed 2027, at the runs' equilibrium
  density (336/km2), built by
  `python -m sim.terrain build --site s89_014e --seed 2027 --out
  data/terrain/s89_014e_s2027.npz` and its display layer
  (sim/terrain_display.py build, display_s89_014e_s2027). Pose seed 2027.
  (Amended before any frame was measured: the first version kept the
  s89_134e grid and held out arcshare-1's craters by 60 m, which left 19
  of its 168 craters of 10-12 m, 12 of them usable.)
- Frames: every eligible crater of 10-12 m on that grid, at most 120.
- Same crater sizes, ranges, slope and crater-floor rules, statistic (the
  median) and threshold rule (median x the map cut). Whatever it gives is
  the threshold the runs use (Settings.rim_arc_share_measured); the head-on
  result stays as Settings.rim_arc_share_headon.

    python -m sim.arc_share --rule route --batch arcshare-route --frames 120 --chunk 120

Output: <data>/campaigns/calibration/<batch>/arc_share.json (every frame)
and arc_share.md.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from sim.calibrate import DISPLAY_DIR, OUT, TERRAIN, _blocked
from sim.rim_truth import TOL_M, rim_truth
from sim.rover import SlopeMap
from sim.settings import WorldRanges
from sim.terrain import DATA, load_terrain
from sim.vision import Vision, rover_to_world

HOLD_OUT_M = 60.0          # assumed: view range (20 m) + crater + margin, both from crater and camera
D_RANGE_M = (10.0, 12.0)   # the rule: craters just over the map cut
R_RANGE_M = (8.0, 12.0)    # the rule: range from the camera to the nearest rim point
AIM_JITTER_DEG = 10.0      # the rule
SEED = 2026
# The two rules (see the docstring). Each: heading offset (deg), seed, depth read by
# detection, and batches whose frames are held out.
RULES = {
    "head-on": dict(aim_deg=AIM_JITTER_DEG, seed=SEED, noisy=False, hold_out_batches=()),
    "route": dict(aim_deg=30.0, seed=2027, noisy=True, hold_out_batches=(),
                  terrain=DATA / "terrain" / "s89_014e_s2027.npz",
                  display_dir=DATA / "terrain" / "display_s89_014e_s2027"),
}


def measure(n_frames, routes_path, out_dir, chunk=None, rule="head-on"):
    """Append frames to frames.jsonl until n_frames exist (at most `chunk` new ones per call).
    Each crater draws its pose from its own stream (SEED, crater), so a resumed run measures
    the same frames as an unbroken one."""
    from sim.scene import Scene
    R = RULES[rule]
    seed = R["seed"]
    own_grid = "terrain" in R              # a grid of its own: nothing on it to hold out from
    terrain = load_terrain(R.get("terrain", TERRAIN))
    height, craters, x0, y0, cell = terrain
    slope = SlopeMap(height, x0, y0, cell)
    if own_grid:
        tp = np.array([[np.inf, np.inf]])
    else:
        blocked = _blocked(slope, craters)
        routes = json.loads(Path(routes_path).read_text())
        # Traverse poses: the planned routes through the saved waypoints, as sim/calibrate.py drives them.
        from sim.calibrate import plan_route
        tp = np.concatenate([plan_route(slope, blocked, w)[:, :2] for w in routes["waypoints"].values()])
    for b in R["hold_out_batches"]:            # earlier measurements' craters and poses
        prev = [json.loads(x) for x in (OUT / b / "frames.jsonl").read_text().splitlines()]
        tp = np.concatenate([tp, np.array([r["pose"][:2] for r in prev]),
                             craters[[r["crater"] for r in prev], :2]])
    ttree = cKDTree(tp)
    rng = np.random.default_rng(seed)
    size = height.shape[0] * cell
    jl = Path(out_dir) / "frames.jsonl"
    rows = [json.loads(x) for x in jl.read_text().splitlines()] if jl.exists() else []
    tried = {r["crater"] for r in rows}
    skipped_p = Path(out_dir) / "skipped.json"
    skipped = set(json.loads(skipped_p.read_text())) if skipped_p.exists() else set()
    new = 0
    cand = np.flatnonzero((craters[:, 2] >= D_RANGE_M[0]) & (craters[:, 2] <= D_RANGE_M[1]))
    cand = cand[ttree.query(craters[cand, :2])[0] > HOLD_OUT_M]
    rng.shuffle(cand)
    scene = Scene(height, craters, x0, y0, cell, display="full", display_dir=R.get("display_dir", DISPLAY_DIR))
    vis = Vision(scene, np.random.default_rng(seed + 1))
    big = craters[craters[:, 2] >= 5.0]
    btree = cKDTree(big[:, :2])
    t = time.perf_counter()
    for ci in cand:
        if len(rows) >= n_frames or (chunk is not None and new >= chunk):
            break
        if int(ci) in tried or int(ci) in skipped:
            continue
        rng = np.random.default_rng([seed, int(ci)])
        cx, cy, d = craters[ci]
        for _ in range(10):                       # a few bearings until one is drivable and held out
            b = rng.uniform(0, 2 * math.pi)
            rr = rng.uniform(*R_RANGE_M)
            px, py = cx + (d / 2 + rr) * math.cos(b), cy + (d / 2 + rr) * math.sin(b)
            if not (x0 + 30 < px < x0 + size - 30 and y0 + 30 < py < y0 + size - 30):
                continue
            if ttree.query((px, py))[0] <= HOLD_OUT_M or float(slope.at(px, py)) > 15.0:
                continue
            near = btree.query_ball_point((px, py), 12.0)
            if any(math.hypot(px - big[j, 0], py - big[j, 1]) < big[j, 2] / 2 + 1.0 for j in near):
                continue
            hd = math.atan2(cy - py, cx - px) + math.radians(rng.uniform(-R["aim_deg"], R["aim_deg"]))
            break
        else:
            skipped.add(int(ci))
            skipped_p.write_text(json.dumps(sorted(skipped)))
            continue
        pose = (px, py, hd)
        scene.set_rover("A", pose)
        img, depth = scene.first_person("A")
        # The renderer's depth, as measured in arcshare-1 (nd=depth keeps that measurement
        # reproducible; Vision now reads noisy depth by default).
        v, u, wid, npx = vis.detect_contours(img, depth, nd=vis.noisy_depth(depth) if R["noisy"] else depth)
        det = vis._rover_points(depth[v, u].astype(np.float64), v, u) if len(v) else np.zeros((0, 2))
        tr = rim_truth(scene, "A", depth)
        sel = (tr["crater_id"] == ci) & tr["near"]
        arc = tr["xy"][sel]
        if len(arc) == 0:
            skipped.add(int(ci))
            skipped_p.write_text(json.dumps(sorted(skipped)))
            continue
        if len(det):
            dist = cKDTree(det).query(arc)[0]
            share = float(np.mean(dist <= TOL_M))
        else:
            share = 0.0
        row = dict(crater=int(ci), d_m=float(d), range_m=float(rr), pose=[float(px), float(py), float(hd)],
                   share=share, arc_points=int(len(arc)), in_view_share=float(np.mean(tr["in_view"][sel])),
                   visible_share=float(np.mean(tr["visible"][sel])))
        with open(jl, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        rows.append(row)
        new += 1
        if len(rows) % 20 == 0:
            print(f"  {len(rows)} frames, {time.perf_counter() - t:.0f} s, median so far "
                  f"{np.median([r['share'] for r in rows]):.3f}", flush=True)
    exhausted = all(int(c) in {r["crater"] for r in rows} or int(c) in skipped for c in cand)
    return rows, exhausted


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--batch", default="arcshare-1")
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--routes", default=str(OUT / "realism-cal" / "routes.json"))
    ap.add_argument("--chunk", type=int, default=None, help="at most this many new frames per call (resumes)")
    ap.add_argument("--rule", default="head-on", choices=sorted(RULES))
    a = ap.parse_args()
    out = OUT / a.batch
    out.mkdir(parents=True, exist_ok=True)
    rows, exhausted = measure(a.frames, a.routes, out, a.chunk, a.rule)
    if len(rows) < a.frames and not exhausted:
        print(f"{len(rows)} of {a.frames} frames so far; run again to continue")
        return
    s = np.array([r["share"] for r in rows])
    med = float(np.median(s))
    cut = WorldRanges().orbital_map_min_d_m
    cmd = f"python -m sim.arc_share --batch {a.batch} --frames {a.frames}" + (
        f" --rule {a.rule}" if a.rule != "head-on" else "")
    summary = dict(frames=len(rows), median=med, mean=float(s.mean()), p25=float(np.percentile(s, 25)),
                   p75=float(np.percentile(s, 75)), zero_share=float(np.mean(s == 0)),
                   width_threshold_m=med * cut, command=cmd,
                   made_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    (out / "arc_share.json").write_text(json.dumps({"summary": summary, "frames": rows}, indent=1))
    txt = (f"# Lantern front-arc share, held-out frames\n\n`{cmd}`\n\n"
           f"{len(rows)} frames of craters {D_RANGE_M[0]:g}-{D_RANGE_M[1]:g} m at {R_RANGE_M[0]:g}-{R_RANGE_M[1]:g} m. "
           f"Median share {med:.3f} (mean {s.mean():.3f}, quartiles {summary['p25']:.3f}-{summary['p75']:.3f}, "
           f"{100 * summary['zero_share']:.0f}% of frames with none). Width threshold: {med:.3f} x {cut:g} m "
           f"= {med * cut:.2f} m.\n")
    (out / "arc_share.md").write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
