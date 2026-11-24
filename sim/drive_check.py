"""Drive check: replay a stretch of a camera run's drive in seconds, without the camera.

    python -m sim.drive_check data/campaigns/spin-check-2/w00003-77e2cab8 --step 2650 --steps 300

A full camera sortie takes 20-60 min, most of it rendering the camera
(sim/scene.py first_person, 0.13-0.45 s a step). Steering does not use the
camera: it runs on the lidar's map (sim/lidar.py HazardMemory), the slope
map (sim/rover.py SlopeMap) and the Scout's estimate. This replays that part
alone, from any step of a finished run:

- the run's own ground (record "ground"), slope test, slope limits and
  orbital slope (record "settings"), as sim/campaign_camera.py CameraRig;
- the Scout starts at the run's true pose at --step. Its lidar map is empty,
  or with --warm W holds the scans taken from the run's own true poses at
  the W steps before --step (the ground the Scout had seen on its way in;
  the lidar's range noise is drawn afresh, so cells near a verdict's edge
  can differ);
- its estimate is the truth plus the run's error at --step, held fixed: no
  camera, so no rim fixes and no drift;
- it steers for the waypoints the run steered for from --step on (steps.npz
  goal columns), by the rules of sim/campaign_camera.py CameraSortie.drive_to:
  arrived within cam_arrive_m, or within BLOCKED_ARRIVE_M of a goal on no-go
  ground; skipped as "hazard" when the back-and-forth guard fires with the
  goal on no-go ground; skipped as "blocked" after cam_stuck_steps without
  getting PROGRESS_M closer.

For steering and planning only. It says nothing about navigation error,
energy or time, and its estimate does not move as the run's did, so a
stretch far from --step drifts from the run's path. It is a check, not a
run: nothing is written to data/campaigns.

Prints the counts a sortie record keeps for steering (turns in place,
reversals, back-and-forth blocks, skips, steps onto ground over the limits)
and writes a picture of the path over the ground's slope and the lidar's
hazards (--png, default <run>/drive_check_<step>.png).
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np


class _FixedOffsetNav:
    """What Rover reads from a filter: the start error. No updates."""
    sigma = 1.0

    def __init__(self, err):
        self.err = np.asarray(err, float)


def _goals_from(steps, k0):
    """The run's goals from step k0 on, in order, each once (map frame)."""
    out = []
    for gx, gy in steps[k0:, 10:12]:
        g = (float(gx), float(gy))
        if not out or math.hypot(g[0] - out[-1][0], g[1] - out[-1][1]) > 1e-3:
            out.append(g)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run", help="a camera run's folder: data/campaigns/<batch>/<run>")
    ap.add_argument("--sortie", default=None, help="sortie id (default: the first in steps.npz)")
    ap.add_argument("--step", type=int, required=True, help="start at this step of the sortie")
    ap.add_argument("--steps", type=int, default=300, help="drive at most this many steps")
    ap.add_argument("--warm", type=int, default=0, help="scan from the run's own poses at the W steps before --step first")
    ap.add_argument("--trace", action="store_true", help="print every step: true pose, distance, turn, goal")
    ap.add_argument("--png", default=None)
    a = ap.parse_args(argv)

    from sim import campaign_camera as CC
    from sim.lidar import Lidar
    from sim.rover import Rover, SlopeMap
    from sim.scene import Scene
    from sim.settings import Settings
    from sim.terrain import DATA, load_terrain

    t0 = time.perf_counter()
    run = Path(a.run)
    rec = json.loads((run / "record.json").read_text(encoding="utf-8"))
    st = np.load(run / "steps.npz")
    sid = a.sortie or sorted({k.split(".")[0] for k in st.files if k.endswith(".step")})[0]
    steps = st[f"{sid}.step"].astype(np.float64)
    k0 = a.step
    if not 0 <= k0 < len(steps):
        raise SystemExit(f"--step {k0}: sortie {sid} has {len(steps)} steps")

    known = {f.name for f in __import__("dataclasses").fields(Settings)}
    s = Settings(**{k: v for k, v in rec["settings"].items() if k in known and not isinstance(v, (dict, list))})
    ground = DATA / "terrain" / rec["ground"]["file"]
    h, cr, x0, y0, cell = load_terrain(ground)
    scene = Scene(h, cr, x0, y0, cell, display="off")   # the lidar ray-casts the height; no camera texture
    slope = SlopeMap(h, x0, y0, cell, mode=s.slope_test, pitch_deg=s.pitch_limit_deg, roll_deg=s.roll_limit_deg)
    if s.plan_slope == "orbital":
        from sim.psr_sites import load_site
        site = load_site(rec["summary"]["site"])
        slope.use_orbital(site["slope"], site["x0"], site["y0"], site["cell"], height=site["height"])
        del site

    tx, ty, th, ex, ey = steps[k0, 1:6]
    err = (ex - tx, ey - ty)
    r = Rover("S", (tx, ty, th), _FixedOffsetNav(err), slope, np.random.default_rng(0))
    r.sensing = True
    lid = Lidar(scene, np.random.default_rng(1))
    lid.slope_mode = "footprint" if s.slope_test == "footprint" else "2m"
    t_setup = time.perf_counter() - t0

    def sense():
        pose = tuple(float(v) for v in r.true)
        pts, _ = lid.scan(pose)
        hz, kinds, clear, grads = lid.judge(pts, pose, widen_m=r.slope.widen_m)
        r.add_hazards(hz, kinds, clear=clear, grads=grads)

    def est():
        r.estimate = r.true[:2] + np.asarray(err)
        return float(r.estimate[0]), float(r.estimate[1])

    for kw in range(max(k0 - a.warm, 0), k0):   # the map the Scout had built on its way in
        pose = tuple(float(v) for v in steps[kw, 1:4])
        pts, _ = lid.scan(pose)
        hz, kinds, clear, grads = lid.judge(pts, pose, widen_m=r.slope.widen_m)
        r.add_hazards(hz, kinds, clear=clear, grads=grads)
    goals = _goals_from(steps, k0)
    path = [tuple(r.true[:2])]
    log = {"steps": 0, "turns_in_place": 0, "reversals": 0, "arrived": 0, "skipped": {}, "deg_turned_in_place": 0.0}
    sense()
    t1 = time.perf_counter()
    for gx, gy in goals:
        if log["steps"] >= a.steps:
            break
        ex_, ey_ = est()
        if math.hypot(gx - ex_, gy - ey_) < s.cam_arrive_m:
            log["arrived"] += 1
            continue
        r.set_goal((gx, gy))
        best, since, pp = math.hypot(gx - ex_, gy - ey_), 0, r.pingpong_blocks
        why = None
        while log["steps"] < a.steps:
            ex_, ey_ = est()
            dist = math.hypot(gx - ex_, gy - ey_)
            if dist < s.cam_arrive_m or (dist < CC.BLOCKED_ARRIVE_M and r.goal_blocked()):
                why = "arrived"
                break
            rev0 = r.reversals
            d, dh = r.follow()
            log["steps"] += 1
            if d == 0.0:
                log["turns_in_place"] += 1
                log["deg_turned_in_place"] += abs(math.degrees(dh))
            log["reversals"] += r.reversals - rev0
            path.append(tuple(r.true[:2]))
            if a.trace:
                print(f"{k0 + log['steps']:6d} true ({r.true[0]:.2f}, {r.true[1]:.2f}) h {math.degrees(r.true[2]) % 360:5.1f}"
                      f" d {d:.2f} dh {math.degrees(dh):6.1f}{' REV' if r.reversals > rev0 else ''}"
                      f" goal ({gx:.1f}, {gy:.1f}) steep {len(r.steep_steps)}")
            sense()
            ex_, ey_ = est()
            nd = math.hypot(gx - ex_, gy - ey_)
            if nd < best - CC.PROGRESS_M:
                best, since = nd, 0
            else:
                since += 1
            if r.pingpong_blocks > pp and r.goal_blocked():
                why = "hazard"
                break
            pp = r.pingpong_blocks
            if since >= s.cam_stuck_steps:
                why = "blocked"
                break
        if why == "arrived":
            log["arrived"] += 1
        elif why:
            log["skipped"][why] = log["skipped"].get(why, 0) + 1
    t_drive = time.perf_counter() - t1
    log["pingpong_blocks"] = r.pingpong_blocks
    log["steps_over_limits"] = len(r.steep_steps)
    log["goals_tried"] = log["arrived"] + sum(log["skipped"].values())
    log["seconds"] = {"setup": round(t_setup, 1), "drive": round(t_drive, 1),
                      "per_step": round(t_drive / max(log["steps"], 1), 3)}
    print(json.dumps({"run": str(run), "sortie": sid, "from_step": k0, **log}, indent=1))

    # The picture: true slope (grey), lidar no-go (red), crater floor (orange),
    # lidar slope cells judged by heading (purple), path (blue), goals (green).
    t2 = time.perf_counter()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    P = np.array(path)
    G = np.array(goals[:max(log["goals_tried"] + 1, 1)])
    pad = 15.0   # framed on the path; goals outside it are not drawn
    xa, xb = P[:, 0].min() - pad, P[:, 0].max() + pad
    ya, yb = P[:, 1].min() - pad, P[:, 1].max() + pad
    if xb - xa > 400 or yb - ya > 400:   # keep the picture on the start
        xa, xb, ya, yb = P[0, 0] - 200, P[0, 0] + 200, P[0, 1] - 200, P[0, 1] + 200
    c = slope.cell
    j0, j1 = int((xa - slope.x0) / c), int((xb - slope.x0) / c) + 1
    i0, i1 = int((ya - slope.y0) / c), int((yb - slope.y0) / c) + 1
    win = (slice(max(i0, 0), i1), slice(max(j0, 0), j1))
    ext = [slope.x0 + max(j0, 0) * c, slope.x0 + j1 * c, slope.y0 + max(i0, 0) * c, slope.y0 + i1 * c]
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(slope.fine_deg[win], origin="lower", extent=ext, cmap="Greys", vmin=0, vmax=30)
    for grid, col in ((r.haz_crater, (1.0, 0.6, 0.1, 0.55)), (r.haz_steep, (0.9, 0.1, 0.1, 0.7)),
                      (np.isfinite(r.haz_gx), (0.6, 0.2, 0.9, 0.7))):
        m = np.zeros(grid[win].shape + (4,))
        m[grid[win]] = col
        ax.imshow(m, origin="lower", extent=ext, interpolation="nearest")
    ax.plot(P[:, 0], P[:, 1], "-", color="#1f6feb", lw=1.2)
    ax.plot(P[0, 0], P[0, 1], "o", color="#1f6feb")
    ax.plot(G[:, 0] - err[0], G[:, 1] - err[1], "*", color="#2da44e", ms=12)
    ax.set_xlim(xa, xb)
    ax.set_ylim(ya, yb)
    ax.set_title(f"{run.name} {sid} from step {k0}: {log['steps']} steps, {log['turns_in_place']} turns in place, "
                 f"{log['reversals']} reversals", fontsize=8)
    out = Path(a.png) if a.png else run / f"drive_check_{k0}.png"
    fig.savefig(out, dpi=90, bbox_inches="tight")
    print(f"picture: {out} ({time.perf_counter() - t2:.1f} s)")


if __name__ == "__main__":
    main()
