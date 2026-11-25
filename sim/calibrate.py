"""Calibration: Lantern's camera navigation on ShadowNav-style traverses, beside C23 Table 2.

    python -m sim.calibrate                       # find routes, render, run every config, write the table
    python -m sim.calibrate --seeds 25            # C23 sec. 6B ran 25 seeds per trajectory
    python -m sim.calibrate --batch NAME --reuse  # filter runs again on the detections already rendered
    python -m sim.calibrate --combine "600/km2 no filter=realism-filter:no-filter" ...   # combined.md

What it measures (Cauligi et al. 2023, docs/sources/ShadowNav-Cauligi-2023.*):
- final error: distance from the filter's weighted mean to the truth at the
  traverse's last step (C23 sec. 6B "Ground truth error");
- reported uncertainty: sqrt of the largest eigenvalue of the particles'
  weighted covariance (C23 sec. 6B "Particle filter uncertainty"), kept
  apart from the error;
- Mahalanobis distance of the final error under that covariance (C23 Table 2);
- false-match rate: the share of rim points the filter pairs with the wrong
  crater. Each rim point's source is the truth crater whose edge lies within
  SOURCE_TOL_M of the point placed with the renderer's depth at the true
  pose (none: a spurious edge). Its pairing is the mapped crater whose edge
  is nearest the noisy point placed at the filter's mean, as Alg. 1 line 4
  does per particle. A point from a crater missing from the map is always
  a false match.

The ground (--ground run): a run ground of the approved
recipe (docs/terrain/position.md) over the same 1 km window of s89_134e,
built as every camera run builds it (sim/site_ground.py build_ground: 1 m,
roughness Settings.ground_rough_rms_m, the world's craters 7.5-20 m at
Settings.crater_n_ge10_per_km2), rendered with Settings.cam_display and the
ground's own camera texture, the body tilted on its footprint plane.
--ground grid keeps the 0.25 m grid used before (sim/terrain.py build
--site s89_134e; LOLA 5 m, roughness 0.070 m, craters 2.3-20 m).

The three traverses (Route below), in the style of C23 sec. 4C and A24
sec. IV-A1:
- T1 "gap": a long drive whose middle sees no mapped crater for 150 m or
  more, past craters missing from the map (C23 traj. 1: "craters were
  out-of-sight", "spurious measurements from unlabeled crater").
- T2 "straight": a straight-line traverse past mapped craters (A24 "straight
  line trajectory").
- T3 "half survey": approach a mapped crater, drive half way round it,
  leave (A24 "half survey where 180 degrees of the ground truth crater is
  observed").
Routes go round craters of 5 m and more and over ground under the slope
limit (sim/rover.py SlopeMap.plan), at Lantern's 2 m step (sim/rover.py
STEP_M; C23 imaged every 1 m).

How each run is made. The true path is fixed per traverse, as in C23. Each
frame is rendered live from the true pose by sim/scene.py and read by
sim/vision.py's rim detector, once per traverse: detection draws no noise,
so it is the same for every seed. Each seed then draws, from its own
streams: the truth's odometer drift (WorldRanges.drift_frac, 1-4%, either
sign) and noise (Settings odom_*, as sim/rover.py), the start error (C23
sec. 6B sigma_0 = 3 m), the map (sim/orbital_map.py), the stereo noise on
every rim point and the filter's own draws. Frames are not saved; the
detected rim pixels are cached (detections_<T>.npz) so the filter can run again.

Configs, cumulative, the same seeds in each (common random numbers):
  before    map: every crater of 10 m and up; filter noise copied from the
            truth's odometer (nav.PF_NOISE_COPIED_TRUTH); depth noise 0.5%
  +map      map: sim/orbital_map.py (resolved craters less a random share)
  +filter   filter: its own model (nav.PFNoise defaults, A24 eqs. 3-6)
  after     depth: stereo (vision.py, A24 rig and Table 1); all three fixes
  odometry  after, with fixes off (dead reckoning), for scale
  all-mapped  after, with every true crater in the map (a control, not a map)
The table the paper uses:
  no-filter after, plus an orbital pixel's map error (sim/orbital_map.py)
  filter    no-filter, plus A24 sec. III-D's contour filter (sim/vision.py) with C23's
            80% arc share (width >= 8 m)
  filter-measured  the same filter with Lantern's measured arc share (Settings.rim_arc_share_headon)
The noisy-depth configs (noisy_detect) also run C23 sec. 3A's
far-side test after the contour filter, as sim/vision.py detect() does (far_side=False: off).
Depth follows A24 Table 1 in every 10 m band, with stereo holes
(sim/vision.py noisy_range), and detection steps over the holes (Vision.column_tests).
  filter-route-nd-block    holes in squares of pixels (Settings.stereo_hole_block_px)
  filter-route-nd-noholes  no holes (the band noise alone)
Another grid (--terrain, --display-dir) gives the same traverse styles on
other ground, e.g. one built at the equilibrium crater density.
--combine writes combined.md from batches already run.
  all-mapped  after, but the map holds every true crater (a control, not
            a realistic map): what the same frames give when every rim
            vision sees has its crater in the map

Nothing here is tuned to C23's numbers.

Output: <data>/campaigns/calibration/<batch>/ calibration.json (every run),
table.md, routes.png, error.png, frame_<traj>.png (one frame with its rims).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import replace
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from sim.nav import PF_NOISE_COPIED_TRUTH, CRATER_NEIGHBOURS, ParticleNav, PFNoise
from sim.orbital_map import orbital_map
from sim.rover import STEP_M, SlopeMap
from sim.settings import C23_TRAJ1_FALSE_MATCH, C23_TRAJ2, C23_TRAJ3, Settings, WorldRanges
from sim.terrain import DATA, load_terrain
from sim.vision import FAR_SIDE_PX, HOLE_SEARCH_PX, SHADOW_PROBE_PX, Vision, contour_ok, contour_stats, depth_jump_ok, rover_to_world

SITE = "s89_134e"
TERRAIN = DATA / "terrain" / f"{SITE}.npz"
DISPLAY_DIR = DATA / "terrain" / f"display_{SITE}"
OUT = DATA / "campaigns" / "calibration"

SIGMA0_C23 = 3.0              # C23 sec. 6B: "initial belief distribution with sigma_0 = 3 m"
MATCH_TOL_M = 1.0             # Settings.cam_match_tol_m, assumed: a rim point is paired with a
                              # map crater whose edge lies this close; for the second false-match column
SOURCE_TOL_M = 1.0            # assumed: a rim point comes from a crater if it lies this close to its edge
                              # (one 640 px row spans ~0.45 m of ground at 20 m)
AVOID_D_M = 5.0               # assumed: routes go round craters this wide and wider
AVOID_MARGIN_M = 1.5          # assumed: and keep this far outside their rims
VIEW_RANGE_M = 20.0           # vision.RIM_RANGE_M upper end
VIEW_HALF_DEG = 30.0          # sim/scene.py HFOV_DEG / 2
MARGIN_M = 60.0               # assumed: routes keep this far inside the 1 km grid (view range + margin)

GROUND_SEED = 7               # assumed: the run ground's world seed (sim/terrain.py Params.seed, the grid's own)


def ground_box():
    """The 1 km window the 0.25 m grid covers (sim/terrain.py site_base), so both grounds lie on the same LOLA."""
    from sim.terrain import Params, site_base
    *_, x_left, y_bottom = site_base(Params(site=SITE))
    size = Params().size_m
    return x_left, x_left + size, y_bottom, y_bottom + size


def run_ground(seed=GROUND_SEED):
    """A run ground of the approved recipe (docs/terrain/position.md) over ground_box(): the
    builder, roughness, crater sizes, density and depths every camera run uses (sim/site_ground.py
    build_ground, the single-run world ranges of sim/campaign.py). Reused when already built with
    the current roughness."""
    from sim.campaign import single_run_ranges
    from sim.site_ground import OUT as GROUND_DIR, build_ground
    s = Settings()
    path = GROUND_DIR / f"calibrate-{SITE}-w{seed:05d}.npz"
    if path.exists():
        m = _ground_meta(path)
        if m.get("roughness_rms_m") == (s.ground_rough_rms_m if s.ground_roughness else 0.0):
            return path
    return build_ground(SITE, seed, *ground_box(), ranges=single_run_ranges(s), name=path.stem,
                        roughness=s.ground_roughness, rough_rms_m=s.ground_rough_rms_m)


def run_ground_display(path):
    """The camera texture for that ground, as live runs build it (sim/campaign_camera.py CameraRig)."""
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)   # terrain_display does `import terrain`
    import terrain_display as TD
    return TD.build_for_ground(path)


def _ground_meta(path):
    with np.load(path) as f:
        return json.loads(str(f["meta"])) if "meta" in f.files else {}


# C23 Table 2 (checked against the PDF: rows in the order traj. 1, 2, 3; sim/settings.py).
C23 = {"T1": dict(err=C23_TRAJ1_FALSE_MATCH, unc=(1.84, 1.12), mahal=(8.74, 10.03)),
       "T2": dict(err=C23_TRAJ2, unc=(1.32, 0.76), mahal=(2.75, 1.88)),
       "T3": dict(err=C23_TRAJ3, unc=(1.39, 0.61), mahal=(2.92, 1.91))}
TRAJ_NAMES = {"T1": "gap, unmapped craters", "T2": "straight past craters", "T3": "half survey"}


# --------------------------------------------------------------------------- routes
def _blocked(slope, craters):
    """2 m planner cells inside a crater of AVOID_D_M or more (plus margin)."""
    ny, nx = slope.deg.shape
    b = slope.b
    jj, ii = np.meshgrid(np.arange(nx), np.arange(ny))
    cx, cy = slope.x0 + (jj + 0.5) * b, slope.y0 + (ii + 0.5) * b
    big = craters[craters[:, 2] >= AVOID_D_M]
    tree = cKDTree(np.column_stack([cx.ravel(), cy.ravel()]))
    blk = np.zeros(nx * ny, bool)
    for x, y, d in big:
        blk[tree.query_ball_point((x, y), d / 2 + AVOID_MARGIN_M)] = True
    return blk.reshape(ny, nx)


def _resample(pts, step):
    """Poses (K, 3) every `step` m along a polyline, heading along it."""
    pts = np.asarray(pts, float)
    seg = np.hypot(*np.diff(pts, axis=0).T)
    keep = np.concatenate([[True], seg > 1e-6])
    pts = pts[keep]
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    t = np.arange(0.0, s[-1] + 1e-9, step)
    xy = np.column_stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])
    d = np.diff(xy, axis=0)
    h = np.arctan2(d[:, 1], d[:, 0])
    h = np.concatenate([h, h[-1:]])
    return np.column_stack([xy, h])


def _smooth(pts, k=2):
    """Moving average over 2k+1 waypoints, ends kept: takes the 45 deg steps out of a grid path."""
    p = np.asarray(pts, float)
    if len(p) < 2 * k + 3:
        return p
    out = p.copy()
    for i in range(k, len(p) - k):
        out[i] = p[i - k:i + k + 1].mean(axis=0)
    return out


def plan_route(slope, blocked, waypoints):
    """Planned path through the waypoints, smoothed, as poses every STEP_M."""
    path = [tuple(waypoints[0])]
    for a, b in zip(waypoints[:-1], waypoints[1:]):
        leg = slope.plan(a, b, blocked=blocked, max_expand=400000)
        if leg is None or len(leg) == 1 and math.hypot(b[0] - a[0], b[1] - a[1]) > 8:
            return None
        path += [tuple(p) for p in leg]
    return _resample(_smooth(path), STEP_M)


def in_view(poses, craters):
    """(K, N) bool: crater's nearest rim point is 3-20 m ahead within the half view."""
    out = np.zeros((len(poses), len(craters)), bool)
    if len(craters) == 0:
        return out
    tree = cKDTree(craters[:, :2])
    for k, (x, y, h) in enumerate(poses):
        idx = tree.query_ball_point((x, y), VIEW_RANGE_M + 10.0)
        if not idx:
            continue
        c = craters[idx]
        dx, dy = c[:, 0] - x, c[:, 1] - y
        dist = np.hypot(dx, dy) - c[:, 2] / 2
        ang = np.abs((np.arctan2(dy, dx) - h + np.pi) % (2 * np.pi) - np.pi)
        out[k, idx] = (dist >= 3.0) & (dist <= VIEW_RANGE_M) & (np.degrees(ang) <= VIEW_HALF_DEG + 10)
    return out


def route_stats(poses, craters, d_min):
    mapped = craters[:, 2] >= d_min
    v = in_view(poses, craters)
    see_m = v[:, mapped].any(axis=1)
    see_u = v[:, ~mapped & (craters[:, 2] >= 7.5)].any(axis=1)
    run = best = 0
    gap_end = 0
    for k, s in enumerate(see_m):
        run = 0 if s else run + 1
        if run > best:
            best, gap_end = run, k
    return dict(length_m=float(len(poses) - 1) * STEP_M, steps=len(poses),
                share_seeing_mapped=float(see_m.mean()), longest_gap_m=best * STEP_M,
                gap_end_step=int(gap_end), share_seeing_unmapped_ge7_5=float(see_u.mean()),
                mapped_seen=int(v[:, mapped].any(axis=0).sum()),
                unmapped_ge7_5_seen=int(v[:, ~mapped & (craters[:, 2] >= 7.5)].any(axis=0).sum()),
                end_sees_mapped=float(see_m[-25:].mean()))


def find_routes(terrain, slope, seed=11, tries=400):
    """Pick the three traverses by random search on the grid (deterministic for a seed)."""
    height, craters, x0, y0, cell = terrain
    size = height.shape[0] * cell
    d_min = WorldRanges().orbital_map_min_d_m
    blocked = _blocked(slope, craters)
    rng = np.random.default_rng(seed)
    lo, hi = MARGIN_M, size - MARGIN_M
    mapped = craters[craters[:, 2] >= d_min]
    best = {}

    def rand_pt():
        return np.array([x0 + rng.uniform(lo, hi), y0 + rng.uniform(lo, hi)])

    def inside(p):
        return (x0 + lo <= p[0] <= x0 + hi) and (y0 + lo <= p[1] <= y0 + hi)

    for _ in range(tries):
        # T2, straight: a 450-550 m line; score = share of steps seeing a mapped crater,
        # with a straight-line penalty (path length over chord).
        a = rand_pt()
        th = rng.uniform(0, 2 * np.pi)
        L = rng.uniform(450, 550)
        b = a + L * np.array([math.cos(th), math.sin(th)])
        if not inside(b):
            continue
        p = plan_route(slope, blocked, [a, b])
        if p is None:
            continue
        st = route_stats(p, craters, d_min)
        straight = L / max(st["length_m"], 1)
        sc2 = st["share_seeing_mapped"] * straight ** 4 * (0.25 + st["end_sees_mapped"])   # the end sees craters
        if straight > 0.9 and sc2 > best.get("T2", (-1,))[0]:
            best["T2"] = (sc2, [a.tolist(), b.tolist()], st)
        # T1, gap: the same line scored by its longest blind stretch (>= 150 m) in the
        # middle, unmapped craters seen there, and mapped craters seen at the end.
        if straight > 0.85 and st["longest_gap_m"] >= 150 and st["gap_end_step"] < st["steps"] - 40:
            sc1 = st["longest_gap_m"] * st["share_seeing_unmapped_ge7_5"] * st["end_sees_mapped"]
            if sc1 > best.get("T1", (-1,))[0]:
                best["T1"] = (sc1, [a.tolist(), b.tolist()], st)
    # T3, half survey: a mapped crater 12-20 m across seen from three sides spanning 180 deg.
    # The camera looks ahead only, so the Scout drives at the crater from each side in turn
    # (from 25 m to 6 m outside the rim), after a 150 m approach, then leaves 30 m.
    for c in mapped[np.argsort(-mapped[:, 2])][:80]:
        if c[2] < 12:
            break
        for _ in range(4):
            th0 = rng.uniform(0, 2 * np.pi)
            r = c[2] / 2
            arc = []
            for th in (th0, th0 + np.pi / 2, th0 + np.pi):
                u = np.array([math.cos(th), math.sin(th)])
                arc += [c[:2] + (r + 25.0) * u, c[:2] + (r + 6.0) * u]
            u0 = np.array([math.cos(th0), math.sin(th0)])
            u1 = np.array([math.cos(th0 + np.pi), math.sin(th0 + np.pi)])
            a = c[:2] + (r + 25.0 + 150.0) * u0
            b = arc[-1] + 30.0 * np.array([-u1[1], u1[0]])      # leave sideways, 30 m after the last look
            if not (inside(a) and inside(b)):
                continue
            ok_arc = all(not _cell_blocked(slope, blocked, q) for q in arc)
            if not ok_arc:
                continue
            p = plan_route(slope, blocked, [a] + arc + [b])
            if p is None:
                continue
            st = route_stats(p, craters, d_min)
            if st["length_m"] > 700:
                continue
            sc3 = st["share_seeing_mapped"]
            if sc3 > best.get("T3", (-1,))[0]:
                best["T3"] = (sc3, [a.tolist()] + [q.tolist() for q in arc] + [b.tolist()], st)
    return {k: v[1] for k, v in best.items()}, {k: v[2] for k, v in best.items()}


def _cell_blocked(slope, blocked, p):
    ny, nx = slope.deg.shape
    i = int(np.clip(math.floor((p[1] - slope.y0) / slope.b), 0, ny - 1))
    j = int(np.clip(math.floor((p[0] - slope.x0) / slope.b), 0, nx - 1))
    return bool(blocked[i, j] or slope.deg[i, j] > 15.0)


# --------------------------------------------------------------------------- render
class _Cam:
    """camera_model() without a scene, for Vision in the worker processes."""
    def __init__(self, cm):
        self.cm = cm

    def camera_model(self):
        return self.cm


def source_ids(true_xy_rover, pose, tree, craters):
    """Truth crater each rim point came from (index, or -1: spurious edge)."""
    if len(true_xy_rover) == 0:
        return np.zeros(0, int)
    w = rover_to_world(true_xy_rover, pose)
    k = min(8, len(craters))
    _, idx = tree.query(w, k=k)
    c = craters[idx]
    de = np.abs(np.hypot(c[..., 0] - w[:, 0:1], c[..., 1] - w[:, 1:2]) - c[..., 2] / 2)
    j = de.argmin(axis=1)
    best = idx[np.arange(len(w)), j]
    return np.where(de[np.arange(len(w)), j] <= SOURCE_TOL_M, best, -1)


AXES_KEYS = (("fwd", 3), ("right", 3), ("up", 3), ("offset", 2))   # Vision.axes, as saved per frame


def axes_row(ax):
    return np.concatenate([np.asarray(ax[k], np.float64)[:n] for k, n in AXES_KEYS])


def axes_from_row(row):
    out, i = {}, 0
    for k, n in AXES_KEYS:
        out[k] = np.asarray(row[i:i + n], np.float64)
        i += n
    return out


def render_routes(routes, terrain, out_dir, display, display_dir=DISPLAY_DIR, max_new=None, tilt=True):
    """Render every pose of every route once; keep the rim pixels and their range."""
    from sim.scene import Scene
    import cv2
    height, craters, x0, y0, cell = terrain
    t = time.perf_counter()
    scene = Scene(height, craters, x0, y0, cell, display=display, display_dir=display_dir)
    # The body rests on the footprint plane and the camera tilts with it, as in
    # live runs (sim/campaign_camera.py). Neither ShadowNav paper says whether
    # its rendered camera tilted; both give the rover its full 3D orientation
    # (C23 sec. 3: a star tracker "can compute orientation in three dimensions";
    # A24 sec. V-C: "For simulation, the ground truth orientation is used"), so
    # each frame's attitude is saved and the workers place rims with it.
    scene.body_tilt = tilt
    print(f"scene ({scene.display}, body tilt {tilt}) built in {time.perf_counter() - t:.0f} s", flush=True)
    vis = Vision(scene, np.random.default_rng(0))
    tree = cKDTree(craters[:, :2])
    (out_dir / "camera.json").write_text(json.dumps({"camera_model": scene.camera_model(), "display": scene.display}))
    for name, poses in routes.items():
        if (out_dir / f"detections_{name}.npz").exists():
            print(f"{name}: detections already rendered", flush=True)
            continue
        if max_new is not None:
            if max_new <= 0:
                print(f"{name}: not rendered yet (--max-render reached); run again to continue", flush=True)
                continue
            max_new -= 1
        V, U, R, S, W, NP, off = [], [], [], [], [], [], [0]
        # Edge candidates before the depth-jump test (image only), with the renderer's range
        # at the three pixels the depth steps read: 3 px below and above the edge, and the
        # placed pixel. run_one draws one stereo noise field per frame over these.
        # FC: the renderer's range up each candidate's column above the placed pixel, for
        # C23 sec. 3A's far-side test (sim/vision.py far_side_keep; NaN off the image).
        # BC: the same down the column from the placed pixel, for the depth-jump test and the
        # placed range with stereo holes stepped over (sim/vision.py column_tests).
        EV, EU, RB, RA, RP, ES, FC, BC, eoff = [], [], [], [], [], [], [], [], [0]
        AX = []
        kp = SHADOW_PROBE_PX
        t = time.perf_counter()
        for k, pose in enumerate(poses):
            scene.set_rover("A", pose)
            vis.set_pose(pose)            # the frame's known attitude (level if body_tilt is off)
            AX.append(axes_row(vis.axes))
            img, depth = scene.first_person("A")
            ve, ue = vis.edge_candidates(img)
            vp = Vision.placed_row(ve, depth.shape[0])
            rp = depth[vp, ue].astype(np.float32)
            tp = vis._rover_points(rp.astype(np.float64), vp, ue) if len(ve) else np.zeros((0, 2))
            EV.append(ve.astype(np.int16)); EU.append(ue.astype(np.int16)); RP.append(rp)
            fr, fin = Vision.far_side_rows(vp)
            FC.append(np.where(fin, depth[fr, ue[:, None]], np.nan).astype(np.float32))
            br, bin_ = Vision.below_rows(ve, depth.shape[0])
            BC.append(np.where(bin_, depth[br, ue[:, None]], np.nan).astype(np.float32))
            RB.append(depth[ve + kp, ue].astype(np.float32)); RA.append(depth[ve - kp, ue].astype(np.float32))
            ES.append(source_ids(tp, pose, tree, craters).astype(np.int32))
            eoff.append(eoff[-1] + len(ve))
            # The noiseless detections, as before (configs without noisy_detect).
            v, u, wid, npx = vis.detect_contours(img, depth, nd=depth)   # before A24's contour filter
            r0 = depth[v, u].astype(np.float32)
            true = vis._rover_points(r0.astype(np.float64), v, u) if len(v) else np.zeros((0, 2))
            src = source_ids(true, pose, tree, craters)
            V.append(v.astype(np.int16)); U.append(u.astype(np.int16)); R.append(r0); S.append(src.astype(np.int32))
            W.append(wid.astype(np.float32)); NP.append(npx.astype(np.int32))
            off.append(off[-1] + len(v))
            if k == len(poses) // 2:   # one frame to look at, rims placed without noise
                show = np.ascontiguousarray(img).copy()
                kept = contour_ok(wid, npx)
                for uu, vv, s_, kk in zip(u, v, src, kept):
                    col = (60, 220, 60) if s_ >= 0 and craters[s_, 2] >= WorldRanges().orbital_map_min_d_m else \
                          (255, 170, 0) if s_ >= 0 else (230, 40, 40)
                    if kk:
                        cv2.circle(show, (int(uu), int(vv)), 2, col, -1)
                    else:      # dropped by the contour filter: hollow
                        cv2.circle(show, (int(uu), int(vv)), 2, col, 1)
                cv2.imwrite(str(out_dir / f"frame_{name}.png"), cv2.cvtColor(show, cv2.COLOR_RGB2BGR))
            if k % 50 == 0:
                print(f"  {name} frame {k}/{len(poses)}: {len(v)} rim px, {time.perf_counter() - t:.0f} s", flush=True)
        np.savez_compressed(out_dir / f"detections_{name}.npz", poses=np.asarray(poses), v=np.concatenate(V),
                            u=np.concatenate(U), r0=np.concatenate(R), src=np.concatenate(S), off=np.asarray(off),
                            width_m=np.concatenate(W), npx=np.concatenate(NP),
                            ev=np.concatenate(EV), eu=np.concatenate(EU), rb=np.concatenate(RB),
                            ra=np.concatenate(RA), rp=np.concatenate(RP), esrc=np.concatenate(ES),
                            fc=np.concatenate(FC) if FC else np.zeros((0, FAR_SIDE_PX), np.float32),
                            bc=np.concatenate(BC) if BC else np.zeros((0, SHADOW_PROBE_PX + HOLE_SEARCH_PX - 1), np.float32),
                            eoff=np.asarray(eoff), axes=np.asarray(AX), body_tilt=np.bool_(tilt))
        print(f"{name}: {len(poses)} frames in {time.perf_counter() - t:.0f} s", flush=True)


def load_detections(out_dir, names):
    out_dir = Path(out_dir)
    det = json.loads((out_dir / "camera.json").read_text())
    for name in names:
        z = np.load(out_dir / f"detections_{name}.npz")
        det[name] = {k: z[k] for k in z.files}
    return det


# --------------------------------------------------------------------------- runs
CONFIGS = {
    "before":   dict(map="old", noise="copied", depth="old", fixes=True),
    "+map":     dict(map="new", noise="copied", depth="old", fixes=True),
    "+filter":  dict(map="new", noise="own", depth="old", fixes=True),
    "after":    dict(map="new", noise="own", depth="stereo", fixes=True),
    "odometry": dict(map="new", noise="own", depth="stereo", fixes=False),
    "all-mapped": dict(map="all", noise="own", depth="stereo", fixes=True),
    # The map also carries an orbital pixel's error (sim/orbital_map.py).
    "no-filter": dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True, contour=False),
    "filter":    dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True, contour="c23"),
    # The same filter with Lantern's own measured arc share (Settings.rim_arc_share_headon).
    "filter-measured": dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True, contour="headon"),
    # The same, with every depth step reading one noisy stereo depth per frame: the
    # depth-jump test and the contour width as well as placement.
    "filter-measured-nd": dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True,
                               contour="headon", noisy_detect=True),
    # The route arc share (Settings.rim_arc_share_route), with noisy-depth detection as the runs use it.
    # The same with full-circle matching: the whole rim of the
    # nearest mapped craters, every frame (Settings.pf_match, pf_gate_m test cases).
    "filter-route-nd-full": dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True,
                                 contour="route", noisy_detect=True, match="full_circle", gate_m=0.0),
    "filter-route-nd": dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True,
                            contour="route", noisy_detect=True),
    # Test cases of the stereo holes (A24 Table 1; Settings.stereo_hole_model): holes in squares
    # of Settings.stereo_hole_block_px pixels, and no holes at all.
    "filter-route-nd-block": dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True,
                                  contour="route", noisy_detect=True, holes="block"),
    "filter-route-nd-noholes": dict(map="new", noise="own", depth="stereo", fixes=True, map_err=True,
                                    contour="route", noisy_detect=True, holes="none"),
}
# The headline row: the config that detects and filters as live runs do (sim/vision.py
# rims_detail on one noisy depth per frame, Settings.rim_arc_share_measured = the route share).
FINAL_CONFIG = "filter-route-nd"
_W = {}
# Bumped when run_one's counting changes, so saved runs from older code are run again.
RUN_CODE = "20nov-stereo-table1"


TRUTHS = ("a24", "lantern")
# A24 sec. III-E1 eqs. (4)-(6): the true odometer's error per step is a Gaussian vector of mean
# d * p|s| (d a unit direction drawn once per traverse, uniform) and covariance diag(|mean|).
# p: C23 sec. 6B "utilizing 2% odometry noise"; A24 Table 2 "2% added error".
A24_P = 0.02
# Eq. (6) writes the covariance as diag(|mu_x|, |mu_y|), metres set equal to square metres.
# Read here as the standard deviation, sigma = |mu| per axis (assumed; dimensionally
# consistent). Heading is known: "For simulation, the ground truth orientation is used" (A24 sec. V-C).


def _init(out_dir, names, terrain_path, truth="a24"):
    global TERRAIN
    TERRAIN = Path(terrain_path)
    _W["truth"] = truth
    _W["det"] = load_detections(out_dir, names)
    _W["craters"] = np.asarray(load_terrain(terrain_path)[1], float)


def run_one(task):
    """One seed on one traverse under one config. Returns a dict of final metrics."""
    name, cfg_name, seed, ti = task
    cfg = CONFIGS[cfg_name]
    det, craters = _W["det"], _W["craters"]
    D = det[name]
    poses = D["poses"]
    s, wr = Settings(), WorldRanges()
    g_truth = np.random.default_rng([seed, ti, 0])
    g_nav = np.random.default_rng([seed, ti, 1])
    g_vis = np.random.default_rng([seed, ti, 2])
    g_map = np.random.default_rng([seed, ti, 3])
    # The truth: this seed's odometer and start error.
    drift = float(g_truth.uniform(*wr.drift_frac)) * (1 if g_truth.random() < 0.5 else -1)
    start_err = g_truth.normal(0.0, SIGMA0_C23, 2)
    n = len(poses)
    w_d = g_truth.standard_normal(n)
    w_h = g_truth.standard_normal(n)
    truth = _W.get("truth", "a24")
    a24_dir = float(g_truth.uniform(0.0, 2 * np.pi))     # A24 eq. (5): d-hat, once per traverse
    a24_n = g_truth.standard_normal((n, 2))
    if truth == "a24":
        drift = A24_P
    # The map.
    m_new, mask_new, share = orbital_map(craters, g_map, errors=cfg.get("map_err", False))
    if cfg["map"] == "old":
        mask = craters[:, 2] >= wr.orbital_map_min_d_m
        share = 0.0
    elif cfg["map"] == "all":     # control, not a realistic map: every true crater, down to 2.3 m
        mask = np.ones(len(craters), bool)
        share = 0.0
    else:
        mask = mask_new
    map_idx = np.flatnonzero(mask)
    mp = m_new if cfg["map"] == "new" else craters[map_idx]     # row i is truth crater map_idx[i]
    mtree = cKDTree(mp[:, :2])
    noise = PF_NOISE_COPIED_TRUTH if cfg["noise"] == "copied" else PFNoise()
    noise = replace(noise, sigma0_m=SIGMA0_C23)
    nav = ParticleNav(cfg["fixes"], g_nav, mp, noise=noise, match=cfg.get("match"), gate_m=cfg.get("gate_m"))
    vis = Vision(_Cam(det["camera_model"]), g_vis, depth_model=cfg["depth"], holes=cfg.get("holes"))
    noisy_detect = cfg.get("noisy_detect", False)
    if noisy_detect and "ev" not in D:
        raise ValueError("noisy_detect needs detections rendered with edge candidates (render again)")
    far_side = noisy_detect and cfg.get("far_side", True)
    if noisy_detect and not ("fc" in D and "bc" in D):
        raise ValueError("noisy_detect reads each candidate's column (stereo holes, C23's far-side test): "
                         "render again")
    g_sd = np.random.default_rng([seed, ti, 4])     # one stereo noise field per frame, common to configs
    Hc, Wc = det["camera_model"]["height"], det["camera_model"]["width"]
    nav.start(poses[0, :2] + start_err, poses[0, 2])
    errs = np.zeros(n)
    errs[0] = float(np.hypot(*start_err))
    counts = dict(right=0, wrong_mapped=0, unmapped=0, spurious=0)
    tolc = dict(tol_right=0, tol_wrong=0, tol_unpaired=0)
    frames_used = 0
    AX = D.get("axes")                # per-frame attitude (older detections: level)
    for k in range(1, n):
        if AX is not None:
            vis.axes = axes_from_row(AX[k])
        d = float(np.hypot(*(poses[k, :2] - poses[k - 1, :2])))
        dh = float((poses[k, 2] - poses[k - 1, 2] + np.pi) % (2 * np.pi) - np.pi)
        d_o = d * (1.0 + drift) + s.odom_noise_frac * d * w_d[k]
        dh_o = dh + s.odom_heading_noise_rad * w_h[k]
        if noisy_detect:
            # sim/vision.py detect_contours() and rims_detail() on one noisy depth per frame.
            # One noise field and one hole field per frame, read at each candidate's columns.
            nf = g_sd.standard_normal((Hc, Wc))
            hf = vis.hole_field((Hc, Wc), g_sd) if vis.holes != "none" else None
            a, b = D["eoff"][k], D["eoff"][k + 1]
            ve, ue = D["ev"][a:b].astype(int), D["eu"][a:b].astype(int)
            vp = Vision.placed_row(ve, Hc)
            cs = vis._cos

            def noisy_col(r_col, rows, inside):
                cols = np.broadcast_to(ue[:, None], rows.shape)
                return np.where(inside, vis.noisy_range(r_col, cs[rows, cols], nf[rows, cols],
                                                        None if hf is None else hf[rows, cols]), np.nan)
            nbc = noisy_col(D["bc"][a:b], *Vision.below_rows(ve, Hc))
            nfc = noisy_col(D["fc"][a:b], *Vision.far_side_rows(vp))
            ok, npl = Vision.column_tests(nbc, nfc)
            sel = np.flatnonzero(ok)
            if cfg.get("contour", False) and len(sel):
                wid, npx = contour_stats(vp[sel], ue[sel], npl[sel], (Hc, Wc), det["camera_model"]["fx"])
                sel = sel[contour_ok(wid, npx, cfg["contour"])]
            if far_side and len(sel):                       # C23 sec. 3A, after the contours
                sel = sel[vis.far_side_keep(vp[sel], ue[sel], npl[sel], nfc[sel])]
            pl = vis.place(vp[sel], ue[sel], D["rp"][a:b][sel], r_noisy=npl[sel])
            src_pl = D["esrc"][a:b][sel][pl["idx"]]
        else:
            a, b = D["off"][k], D["off"][k + 1]
            sel = np.arange(a, b)
            if cfg.get("contour", False):                   # A24 sec. III-D contour filter (sim/vision.py)
                sel = sel[contour_ok(D["width_m"][a:b], D["npx"][a:b], cfg["contour"])]
            pl = vis.place(D["v"][sel].astype(int), D["u"][sel].astype(int), D["r0"][sel])
            src_pl = D["src"][sel][pl["idx"]]
        rims = pl["rims"]
        if truth == "a24":
            # A24 eqs. (3)-(6): the estimated step is the true step plus r_k ~ N(mu, diag(|mu|)^2).
            sx, sy = poses[k, :2] - poses[k - 1, :2]
            mu_r = A24_P * d * np.array([math.cos(a24_dir), math.sin(a24_dir)])
            r = mu_r + np.abs(mu_r) * a24_n[k]
            nav.predict_xy(sx + r[0], sy + r[1], dh)
        else:
            nav.predict(d_o, dh_o)
        # Frames the filter uses: A24 sec. III-E2's gate (sim/nav.py near_landmark) skips the rest.
        if cfg["fixes"] and len(rims) and nav.near_landmark():
            # Pairing at the filter's mean pose, before the update (Alg. 1 line 4).
            mu, _ = nav.belief()
            wgt = nav._normalised()
            hd = nav.heading + float(wgt @ nav.th)
            w = rover_to_world(rims, (mu[0], mu[1], hd))
            kk = min(CRATER_NEIGHBOURS, len(mp))
            _, idx = mtree.query(w, k=kk)
            idx = np.asarray(idx).reshape(len(w), kk)
            c = mp[idx]
            de = np.abs(np.hypot(c[..., 0] - w[:, 0:1], c[..., 1] - w[:, 1:2]) - c[..., 2] / 2)
            jmin = de.argmin(axis=1)
            paired = map_idx[idx[np.arange(len(w)), jmin]]
            near = de[np.arange(len(w)), jmin] <= MATCH_TOL_M
            src = src_pl
            in_map = np.isin(src, map_idx)
            counts["right"] += int(np.sum((src >= 0) & in_map & (paired == src)))
            counts["wrong_mapped"] += int(np.sum((src >= 0) & in_map & (paired != src)))
            counts["unmapped"] += int(np.sum((src >= 0) & ~in_map))
            counts["spurious"] += int(np.sum(src < 0))
            ok_pair = (src >= 0) & in_map & (paired == src)
            tolc["tol_right"] += int(np.sum(near & ok_pair))
            tolc["tol_wrong"] += int(np.sum(near & ~ok_pair))
            tolc["tol_unpaired"] += int(np.sum(~near))
            nav.correct(rims)
            frames_used += 1
        est, _ = nav.belief()
        errs[k] = float(np.hypot(*(est - poses[k, :2])))
    mu, cov = nav.covariance()
    e = mu - poses[-1, :2]
    try:
        mahal = float(np.sqrt(e @ np.linalg.solve(cov, e)))
    except np.linalg.LinAlgError:
        mahal = float("nan")
    tot = sum(counts.values())
    return dict(traj=name, config=cfg_name, seed=seed, drift=drift, missing_share_large=share,
                map_n=int(len(mp)), final_err_m=float(np.hypot(*e)), sigma_max_m=nav.sigma_max(),
                sigma_trace_m=float(nav.belief()[1]), mahal=mahal, start_err_m=float(np.hypot(*start_err)),
                rim_points=tot, false_match_rate=(1 - counts["right"] / tot) if tot else float("nan"),
                **{f"n_{k}": v for k, v in counts.items()}, **tolc, frames_with_rims=frames_used,
                err_series=np.round(errs, 3).tolist())


# --------------------------------------------------------------------------- output
def _ms(x):
    x = np.asarray([v for v in x if np.isfinite(v)], float)
    return (float(x.mean()), float(x.std(ddof=1))) if len(x) > 1 else (float("nan"), float("nan"))


def summarise(rows, routes_stats):
    out = {}
    for (name, cfg) in sorted({(r["traj"], r["config"]) for r in rows}):
        rr = [r for r in rows if r["traj"] == name and r["config"] == cfg]
        tot = sum(r["rim_points"] for r in rr)
        cnt = {k: sum(r[f"n_{k}"] for r in rr) for k in ("right", "wrong_mapped", "unmapped", "spurious")}
        out[f"{name}|{cfg}"] = dict(
            traj=name, config=cfg, n=len(rr), err=_ms([r["final_err_m"] for r in rr]),
            unc=_ms([r["sigma_max_m"] for r in rr]), mahal=_ms([r["mahal"] for r in rr]),
            err_median=float(np.median([r["final_err_m"] for r in rr])),
            err_p95=float(np.percentile([r["final_err_m"] for r in rr], 95)),
            share_over_5m=float(np.mean([r["final_err_m"] > 5 for r in rr])),
            false_match=(1 - cnt["right"] / tot) if tot else float("nan"),
            shares={k: (v / tot if tot else float("nan")) for k, v in cnt.items()},
            rim_points_per_run=tot / max(len(rr), 1),
            # Within MATCH_TOL_M (the campaign labeller's rule): wrong share of paired points, unpaired share.
            false_match_tol=(lambda p, w_: w_ / p if p else float("nan"))(
                sum(r.get("tol_right", 0) + r.get("tol_wrong", 0) for r in rr), sum(r.get("tol_wrong", 0) for r in rr)),
            unpaired_tol=(sum(r.get("tol_unpaired", 0) for r in rr) / tot) if tot else float("nan"))
    return out


def write_table(summary, routes_stats, out_dir, seeds, cmd):
    f = lambda m: f"{m[0]:.2f} ± {m[1]:.2f}"
    L = ["# Lantern camera navigation beside ShadowNav Table 2", "",
         f"Made by `{cmd}` on {datetime.now().astimezone().isoformat(timespec='minutes')}. "
         f"{seeds} seeds per traverse per config (C23 sec. 6B used 25). "
         "Final error and reported uncertainty (sqrt of the largest covariance eigenvalue) in metres, "
         "mean ± s.d. False match: share of rim points paired with the wrong crater (sim/calibrate.py).", "",
         f"## Final configuration ({FINAL_CONFIG}: detection and filter as live runs) beside C23 Table 2", "",
         "| Traverse | Lantern error | C23 error | Lantern uncertainty | C23 uncertainty | Lantern Mahalanobis | C23 Mahalanobis | Lantern false match | > 5 m |",
         "|---|---|---|---|---|---|---|---|---|"]
    for name in ("T1", "T2", "T3"):
        s_ = summary.get(f"{name}|{FINAL_CONFIG}")
        if not s_:
            continue
        c = C23[name]
        L.append(f"| {name} {TRAJ_NAMES[name]} | {f(s_['err'])} | {c['err'][0]:.2f} ± {c['err'][1]:.2f} | "
                 f"{f(s_['unc'])} | {c['unc'][0]:.2f} ± {c['unc'][1]:.2f} | {f(s_['mahal'])} | "
                 f"{c['mahal'][0]:.2f} ± {c['mahal'][1]:.2f} | {100 * s_['false_match']:.1f}% | "
                 f"{100 * s_['share_over_5m']:.0f}% |")
    L += ["", "## Each fix, cumulative (final error, uncertainty, false match)", "",
          "| Traverse | Config | Error (m) | Median | 95th pct | Uncertainty (m) | Mahalanobis | False match | right / wrong mapped / unmapped / spurious |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name in ("T1", "T2", "T3"):
        for cfg in CONFIGS:
            s_ = summary.get(f"{name}|{cfg}")
            if not s_:
                continue
            sh = s_["shares"]
            L.append(f"| {name} | {cfg} | {f(s_['err'])} | {s_['err_median']:.2f} | {s_['err_p95']:.2f} | {f(s_['unc'])} | "
                     f"{f(s_['mahal'])} | {100 * s_['false_match']:.1f}% | "
                     f"{100 * sh['right']:.0f} / {100 * sh['wrong_mapped']:.0f} / {100 * sh['unmapped']:.0f} / "
                     f"{100 * sh['spurious']:.0f}% |")
    L += ["", "## Traverses", "", "| Traverse | Length | Steps | Steps seeing a mapped crater | Longest blind stretch | "
          "Mapped craters seen | Unmapped craters >= 7.5 m seen |", "|---|---|---|---|---|---|---|"]
    for name, st in routes_stats.items():
        L.append(f"| {name} {TRAJ_NAMES[name]} | {st['length_m']:.0f} m | {st['steps']} | {100 * st['share_seeing_mapped']:.0f}% | "
                 f"{st['longest_gap_m']:.0f} m | {st['mapped_seen']} | {st['unmapped_ge7_5_seen']} |")
    (out_dir / "table.md").write_text("\n".join(L) + "\n", encoding="utf8")
    return "\n".join(L)


def plot(routes, terrain, rows, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    height, craters, x0, y0, cell = terrain
    d_min = WorldRanges().orbital_map_min_d_m
    fig, ax = plt.subplots(figsize=(8, 8))
    ext = [x0 - cell / 2, x0 + (height.shape[1] - 0.5) * cell, y0 - cell / 2, y0 + (height.shape[0] - 0.5) * cell]
    ax.imshow(height[::8, ::8], origin="lower", extent=ext, cmap="gray")
    for c in craters[craters[:, 2] >= 5.0]:
        mapped = c[2] >= d_min
        ax.add_patch(plt.Circle(c[:2], c[2] / 2, fill=False, lw=0.6 if mapped else 0.4,
                                color="#2a9d8f" if mapped else "#e76f51"))
    cols = {"T1": "#e9c46a", "T2": "#4cc9f0", "T3": "#f72585"}
    for name, p in routes.items():
        ax.plot(p[:, 0], p[:, 1], color=cols[name], lw=1.6, label=f"{name} {TRAJ_NAMES[name]}")
        ax.plot(*p[0, :2], "s", color=cols[name])
        ax.plot(*p[-1, :2], "o", color=cols[name])
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"Calibration traverses on {SITE} (teal: mapped >= {d_min:g} m; orange: 5-{d_min:g} m, not mapped)")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    fig.savefig(out_dir / "routes.png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    fig, axs = plt.subplots(1, 3, figsize=(14, 4), sharey=True)
    for ax, name in zip(axs, ("T1", "T2", "T3")):
        pal = ["#888888", "#e63946", "#457b9d", "#2a9d8f", "#e9c46a", "#6d597a", "#f4a261", "#264653", "#9d0208"]
        run_cfgs = [c for c in CONFIGS if any(r["config"] == c for r in rows)]
        for cfg, col in zip(run_cfgs, pal):
            E = np.array([r["err_series"] for r in rows if r["traj"] == name and r["config"] == cfg])
            if len(E) == 0:
                continue
            xs = np.arange(E.shape[1]) * STEP_M
            ax.plot(xs, E.mean(0), color=col, label=f"{cfg} mean")
            ax.fill_between(xs, np.percentile(E, 10, 0), np.percentile(E, 90, 0), color=col, alpha=0.15)
        c = C23[name]["err"]
        ax.axhline(c[0], color="k", ls="--", lw=0.8, label=f"C23 final {c[0]} m")
        ax.set_title(f"{name} {TRAJ_NAMES[name]}")
        ax.set_xlabel("distance (m)")
    axs[0].set_ylabel("position error (m), mean and 10-90%")
    axs[0].legend(fontsize=8)
    fig.savefig(out_dir / "error.png", dpi=100, bbox_inches="tight")
    plt.close(fig)


def combine(specs):
    """combined.md: Lantern columns from several batches beside C23 Table 2."""
    cols = []
    for spec in specs:
        label, rest = spec.split("=", 1)
        batch, cfg = rest.split(":", 1)
        cal = json.loads((OUT / batch / "calibration.json").read_text(encoding="utf8"))
        cols.append((label, batch, cfg, cal))
    f = lambda m: f"{m[0]:.2f} ± {m[1]:.2f}"
    L = ["# Lantern camera navigation beside ShadowNav Table 2 (C23)", "",
         "Final error and reported uncertainty (sqrt of the largest covariance eigenvalue, C23 sec. 6B) in metres, "
         "mean ± s.d. over seeds. > 5 m: share of runs ending more than 5 m off. "
         "False match (draft sec. 4.2: rims 'paired with the wrong mapped crater'): of rim points paired with a "
         "mapped crater, the share whose crater is not the rim's true one (a spurious rim that is paired counts "
         "as wrong); unpaired points are left out of both and reported apart. Two pairings: **filter**, the "
         "particle filter's own (Alg. 1 line 4, every point to its nearest mapped edge at the filter's mean pose "
         "before the update, so none is unpaired); **1 m**, the campaign labeller's rule (paired only when that "
         f"edge is within {MATCH_TOL_M:g} m).", ""]
    for label, batch, cfg, cal in cols:
        n = cal["summary"].get(f"T1|{cfg}", {}).get("n", "?")
        L.append(f"- **{label}**: batch `{batch}`, config `{cfg}`, terrain `{cal.get('terrain', 's89_134e.npz')}` "
                 f"({cal.get('n_craters', '?')} craters), {n} seeds per traverse. Made by `{cal['command']}`.")
    L += ["", "| Traverse | C23 error | C23 uncertainty | " + " | ".join(
        f"{lb} error | {lb} uncertainty | {lb} false match, filter | {lb} false match, 1 m (unpaired) | {lb} > 5 m"
        for lb, *_ in cols) + " |",
          "|---|---|---|" + "---|" * (5 * len(cols))]
    for name in ("T1", "T2", "T3"):
        c = C23[name]
        row = f"| {name} {TRAJ_NAMES[name]} | {c['err'][0]:.2f} ± {c['err'][1]:.2f} | {c['unc'][0]:.2f} ± {c['unc'][1]:.2f} |"
        for label, batch, cfg, cal in cols:
            s_ = cal["summary"].get(f"{name}|{cfg}")
            row += (f" {f(s_['err'])} | {f(s_['unc'])} | {100 * s_['false_match']:.0f}% | "
                    f"{100 * s_.get('false_match_tol', float('nan')):.0f}% ({100 * s_.get('unpaired_tol', float('nan')):.0f}%) | "
                    f"{100 * s_['share_over_5m']:.0f}% |" if s_ else " | | | | |")
        L.append(row)
    L += ["", "False-match breakdown (share of rim points: right / wrong mapped crater / crater not in the map / "
          "spurious edge), and rim points per run:", ""]
    for label, batch, cfg, cal in cols:
        for name in ("T1", "T2", "T3"):
            s_ = cal["summary"].get(f"{name}|{cfg}")
            if s_:
                sh = s_["shares"]
                L.append(f"- {label}, {name}: {100 * sh['right']:.0f} / {100 * sh['wrong_mapped']:.0f} / "
                         f"{100 * sh['unmapped']:.0f} / {100 * sh['spurious']:.0f}%, {s_['rim_points_per_run']:.0f} points")
    text = "\n".join(L) + "\n"
    (OUT / "combined.md").write_text(text, encoding="utf8")
    print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--batch", default=None, help="folder under campaigns/calibration (default: a new stamp)")
    ap.add_argument("--reuse", action="store_true", help="reuse the batch's routes.json (traverses already "
                    "rendered, detections_<T>.npz, are always reused)")
    ap.add_argument("--display", choices=("full", "detail", "off"), default=None,
                    help="first-person display layer the camera sees (sim/scene.py). Default: 'detail' "
                         "(Settings.cam_display, what live runs use) on --ground run, 'full' on --ground grid")
    ap.add_argument("--ground", choices=("run", "grid"), default="run",
                    help="run (default): a run ground of the approved recipe (sim/site_ground.py build_ground, "
                         "docs/terrain/position.md) over GROUND_BOX, with its own camera texture "
                         "(sim/terrain_display.py build_for_ground). grid: the 0.25 m --terrain grid")
    ap.add_argument("--ground-seed", type=int, default=GROUND_SEED, help="world seed of the run ground's craters")
    ap.add_argument("--truth", choices=TRUTHS, default="a24",
                    help="the true odometer: a24 (default): A24 eqs. 4-6, 2%% bias in one random "
                         "direction, heading known, as ShadowNav's tests; lantern: WorldRanges.drift_frac (1-4%%) "
                         "along the track plus Settings odom_* noise, as campaign runs")
    ap.add_argument("--level", action="store_true", help="keep the body level (test case)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--max-render", type=int, default=None,
                    help="render at most this many traverses not yet rendered, then stop (run again to continue)")
    ap.add_argument("--max-configs", type=int, default=None,
                    help="run at most this many configs not yet saved, then stop (run again to continue)")
    ap.add_argument("--terrain", default=str(TERRAIN), help="the 0.25 m grid (sim/terrain.py build)")
    ap.add_argument("--display-dir", default=str(DISPLAY_DIR), help="its display layer (sim/terrain_display.py build)")
    ap.add_argument("--combine", nargs="+", metavar="LABEL=BATCH:CONFIG",
                    help="only write combined.md from batches already run, one column set per LABEL")
    a = ap.parse_args()
    if a.combine:
        combine(a.combine)
        return
    batch = a.batch or ("cal-" + datetime.now().strftime("%Y%m%d-%H%M"))
    out_dir = OUT / batch
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.ground == "run":
        a.terrain = str(run_ground(a.ground_seed))
        a.display_dir = str(run_ground_display(a.terrain))
    if a.display is None:
        a.display = Settings().cam_display if a.ground == "run" else "full"
    terrain = load_terrain(a.terrain)
    height, craters, x0, y0, cell = terrain
    rj = out_dir / "routes.json"
    if a.reuse and rj.exists():
        saved = json.loads(rj.read_text())
        wps, rstats = saved["waypoints"], saved["stats"]
    else:
        slope = SlopeMap(height, x0, y0, cell)
        t = time.perf_counter()
        wps, rstats = find_routes(terrain, slope)
        print(f"routes found in {time.perf_counter() - t:.0f} s: {sorted(wps)}", flush=True)
        rj.write_text(json.dumps({"waypoints": wps, "stats": rstats}, indent=1))
    slope = None
    names = sorted(wps)
    if not all((out_dir / f"detections_{k}.npz").exists() for k in names):
        slope = SlopeMap(height, x0, y0, cell)
        blocked = _blocked(slope, craters)
        routes = {k: plan_route(slope, blocked, wps[k]) for k in names}
        del slope, blocked
        render_routes(routes, terrain, out_dir, a.display, a.display_dir, a.max_render, tilt=not a.level)
        if not all((out_dir / f"detections_{k}.npz").exists() for k in names):
            return
    det = load_detections(out_dir, names)
    routes = {k: det[k]["poses"] for k in names}
    for k, st in rstats.items():
        print(k, json.dumps(st))
    # One file of runs per config (runs_<config>.json), so a long table runs in chunks:
    # a config already run with the same seeds is read back, not run again.
    rows = []
    for cfg in a.configs.split(","):
        rp = out_dir / f"runs_{cfg}.json"
        if rp.exists():
            saved = json.loads(rp.read_text(encoding="utf8"))
            if saved["seeds"] == a.seeds and saved.get("code") == RUN_CODE and saved.get("truth", "lantern") == a.truth:
                rows += saved["rows"]
                print(f"{cfg}: {len(saved['rows'])} runs read from {rp.name}", flush=True)
                continue
        if a.max_configs is not None and a.max_configs <= 0:
            print(f"{cfg}: not run yet (--max-configs reached); run again to continue", flush=True)
            continue
        tasks = [(name, cfg, seed, ti) for ti, name in enumerate(sorted(routes)) for seed in range(a.seeds)]
        t = time.perf_counter()
        with Pool(a.workers, initializer=_init, initargs=(str(out_dir), names, a.terrain, a.truth)) as pool:
            got = pool.map(run_one, tasks, chunksize=4)
        rp.write_text(json.dumps({"seeds": a.seeds, "code": RUN_CODE, "truth": a.truth, "rows": got}), encoding="utf8")
        print(f"{cfg}: {len(got)} runs in {time.perf_counter() - t:.0f} s", flush=True)
        rows += got
        if a.max_configs is not None:
            a.max_configs -= 1
    done = {r["config"] for r in rows}
    if not all(c in done for c in a.configs.split(",")):
        return
    summary = summarise(rows, rstats)
    cmd = "python -m sim.calibrate " + " ".join(os.sys.argv[1:])
    (out_dir / "calibration.json").write_text(json.dumps(
        {"format": "lantern-calibration/1", "made_at": datetime.now().astimezone().isoformat(timespec="seconds"),
         "command": cmd, "site": SITE, "terrain": Path(a.terrain).name, "n_craters": int(len(craters)),
         "ground": a.ground, "truth": a.truth, "ground_meta": _ground_meta(a.terrain),
         "body_tilt": bool(det[names[0]].get("body_tilt", False)),
         "display": det["display"], "step_m": STEP_M, "sigma0_m": SIGMA0_C23,
         "settings": {k: getattr(Settings(), k) for k in ("pf_drift_sigma", "pf_step_noise_frac", "pf_heading_walk_rad",
                                                          "pf_scale_walk", "stereo_baseline_m", "stereo_width_px",
                                                          "stereo_hfov_deg", "stereo_disparity_sigma_px",
                                                          "stereo_law_max_m", "stereo_band_rms_m", "stereo_hole_pct",
                                                          "stereo_hole_model", "stereo_hole_block_px",
                                                          "odom_noise_frac", "odom_heading_noise_rad")},
         "orbital_map": {"min_d_m": WorldRanges().orbital_map_min_d_m,
                         "missing_share_large": list(WorldRanges().missing_share_large)},
         "routes": rstats, "summary": summary,
         "runs": [{k: v for k, v in r.items() if k != "err_series"} for r in rows]}, indent=1), encoding="utf8")
    print(write_table(summary, rstats, out_dir, a.seeds, cmd))
    plot(routes, terrain, rows, out_dir)
    print(f"wrote {out_dir}")


if __name__ == "__main__":
    main()
