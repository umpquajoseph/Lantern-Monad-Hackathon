"""Make stand-in runs for the web viewer, in the run-log format of docs/handoffs.md.

Until run_demo.py exists, this scripts both rovers, fakes the navigation
estimate and the chain, and renders the real first-person frames with
sim/scene.py. Everything made up is flagged in the log ("standin") so the
viewer can say so on screen.

    python web/make_standin_run.py              # both demo runs -> web/runs/
    python web/make_standin_run.py --every 4    # quicker: a frame every 4th step

Writes web/runs/<run_id>/log.json, web/runs/<run_id>/fp/<agency>_<step>.jpg
and web/runs/index.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.terrain import load_terrain  # noqa: E402

TERRAIN = Path(r"C:\Monad-Hackathon-Project\data\terrain\shoemaker.npz")
OUT = ROOT / "web" / "runs"

# PROSPECT Team Brief, RASC-AL 2027 Theme 3: Scout must average about 7.4 cm/s.
SPEED_MPS = 0.074
# ShadowNav (Cauligi et al. 2023), section 1: odometry drifts about 2% of distance.
ODOM_DRIFT_FRAC = 0.02
# docs/handoffs.md, stand-in rims: near rims of mapped craters within 15 m.
RIM_RANGE_M = 15.0

STEP_M = 2.0               # ASSUMED: one log step every 2 m of driving.
TURN_PER_STEP = 0.18       # ASSUMED: max heading change per step, radians.
NAV_SIGMA_MIN = 0.6        # ASSUMED: stand-in filter sigma with many rims in view, metres.
NAV_SIGMA_MAX = 2.4        # ASSUMED: stand-in filter sigma with no rims in view, metres.
ICE_RADIUS_M = 3.0         # ASSUMED: 1-sigma radius of the hidden ice patch.
ICE_PEAK = 0.85            # ASSUMED: sensor reading at the centre of the patch.
AGREE_READING = 0.3        # ASSUMED: a reading above this means "ice is here".
RIM_NOISE_M = 0.1          # ASSUMED: stand-in rim point noise.
RIM_MISS_FRAC = 0.3        # ASSUMED: fraction of stand-in rim points dropped.

# Routes in metres from the map's south-west corner. ASSUMED, chosen by eye on
# the terrain preview to stay on gentle ground.
ROUTE_A = [(150, 150), (300, 260), (420, 330), (560, 420), (620, 520)]
ROUTE_B = [(820, 230), (760, 330), (820, 420), (740, 470)]
FIND_AT_M = 480.0          # ASSUMED: A's sensor crosses the ice this far along its route.


def drive(start, waypoints, heading0, max_steps):
    """True poses stepping STEP_M toward each waypoint with a turn-rate limit."""
    x, y = start
    hd = heading0
    poses = [(x, y, hd)]
    wps = list(waypoints)
    while wps and len(poses) < max_steps:
        tx, ty = wps[0]
        if math.hypot(tx - x, ty - y) < STEP_M * 1.5:
            wps.pop(0)
            continue
        want = math.atan2(ty - y, tx - x)
        d = (want - hd + math.pi) % (2 * math.pi) - math.pi
        hd += max(-TURN_PER_STEP, min(TURN_PER_STEP, d))
        x += STEP_M * math.cos(hd)
        y += STEP_M * math.sin(hd)
        poses.append((x, y, hd))
    return poses


def rims_for(pose, craters):
    """Stand-in rim points, rover frame [x_fwd, y_left], per docs/handoffs.md."""
    x, y, hd = pose
    c, s = math.cos(hd), math.sin(hd)
    d = np.hypot(craters[:, 0] - x, craters[:, 1] - y)
    near = craters[(d < RIM_RANGE_M + craters[:, 2] / 2) & (d > craters[:, 2] / 2)]
    pts = []
    rng = np.random.default_rng(int(abs(x * 1000 + y)) % 2**32)
    for cx, cy, dia in near:
        R = dia / 2
        face = math.atan2(y - cy, x - cx)
        for a in np.linspace(face - 1.0, face + 1.0, 9):
            px, py = cx + R * math.cos(a), cy + R * math.sin(a)
            dx, dy = px - x, py - y
            fwd, left = c * dx + s * dy, -s * dx + c * dy
            if fwd <= 0 or math.hypot(dx, dy) > RIM_RANGE_M or rng.random() < RIM_MISS_FRAC:
                continue
            pts.append([round(fwd + rng.normal(0, RIM_NOISE_M), 2), round(left + rng.normal(0, RIM_NOISE_M), 2)])
    return pts


def fake_tx(*parts):
    return "0x" + hashlib.sha256(("standin:" + ":".join(map(str, parts))).encode()).hexdigest()


class Nav:
    """Stand-in navigation: odometry with a 2% scale error, and an estimate."""

    def __init__(self, pose, nav_on, rng):
        self.ox, self.oy = pose[0], pose[1]
        self.nav_on = nav_on
        self.rng = rng
        self.scale = 1.0 + ODOM_DRIFT_FRAC * rng.choice([-1, 1])
        self.dist = 0.0
        self.err = np.zeros(2)

    def step(self, prev, pose, n_rims):
        ds = math.hypot(pose[0] - prev[0], pose[1] - prev[1])
        self.dist += ds
        # Heading known (star tracker, as in the spec); distance has the scale error.
        self.ox += ds * self.scale * math.cos(pose[2])
        self.oy += ds * self.scale * math.sin(pose[2])
        if self.nav_on:
            sigma = NAV_SIGMA_MAX - (NAV_SIGMA_MAX - NAV_SIGMA_MIN) * min(1.0, n_rims / 30.0)
            self.err = 0.85 * self.err + self.rng.normal(0, 0.35 * sigma, 2)
            est = (pose[0] + self.err[0], pose[1] + self.err[1])
        else:
            sigma = NAV_SIGMA_MIN + ODOM_DRIFT_FRAC * self.dist
            est = (self.ox, self.oy)
        return (self.ox, self.oy), est, sigma


def ice_reading(ice, x, y):
    return ICE_PEAK * math.exp(-0.5 * ((x - ice[0]) ** 2 + (y - ice[1]) ** 2) / ICE_RADIUS_M ** 2)


def make_run(run_id, label, a_nav_on, height, craters, x0, y0, cell, scene, every, seed):
    rng = np.random.default_rng(seed)
    w = lambda p: (x0 + p[0], y0 + p[1])  # noqa: E731
    pa = drive(w(ROUTE_A[0]), [w(p) for p in ROUTE_A[1:]], 0.6, 400)
    k_find = int(FIND_AT_M / STEP_M)
    ice = pa[k_find][:2]

    nav = {"A": Nav(pa[0], a_nav_on, rng), "B": Nav(w(ROUTE_B[0]), True, rng)}
    poses = {"A": pa}
    pb = drive(w(ROUTE_B[0]), [w(p) for p in ROUTE_B[1:]], 2.2, k_find + 1)
    poses["B"] = pb

    out = OUT / run_id
    (out / "fp").mkdir(parents=True, exist_ok=True)
    steps, last = [], {}
    find = None
    b_target = None
    checked = False
    k = 0
    n_total = None
    while True:
        # Rover B: after A's find, head for the reported spot by its own estimate.
        if b_target is not None and not checked and k >= len(poses["B"]):
            bx, by, bh = poses["B"][-1]
            ex, ey = last["B"]["estimate"]
            # Steer by the estimate: the true pose moves by the commanded step.
            want = math.atan2(b_target[1] - ey, b_target[0] - ex)
            dh = (want - bh + math.pi) % (2 * math.pi) - math.pi
            bh += max(-TURN_PER_STEP, min(TURN_PER_STEP, dh))
            rem = math.hypot(b_target[0] - ex, b_target[1] - ey)
            st = min(STEP_M, rem)
            poses["B"].append((bx + st * math.cos(bh), by + st * math.sin(bh), bh))
        if k >= len(poses["B"]):
            poses["B"].append(poses["B"][-1])
        if k >= len(poses["A"]):
            poses["A"].append(poses["A"][-1])

        rec = {"t": round(k * STEP_M / SPEED_MPS, 1), "rovers": {}, "events": []}
        for ag in ("A", "B"):
            pose = poses[ag][k]
            prev = poses[ag][k - 1] if k else pose
            rims = rims_for(pose, craters)
            odom, est, sigma = nav[ag].step(prev, pose, len(rims))
            fp = None
            if k % every == 0:
                scene.set_rover(ag, pose)
                fp = f"fp/{ag}_{k:04d}.jpg"
            rover = {"true_pose": [round(pose[0], 3), round(pose[1], 3), round(pose[2], 4)],
                     "odom_pose": [round(odom[0], 3), round(odom[1], 3), round(pose[2], 4)],
                     "estimate": [round(est[0], 3), round(est[1], 3)], "sigma": round(sigma, 3),
                     "nav_on": nav[ag].nav_on, "rims": rims, "fp_frame": fp}
            rec["rovers"][ag] = rover
            last[ag] = rover
        for ag in ("A", "B"):
            scene.set_rover(ag, poses[ag][k])
        for ag in ("A", "B"):
            if rec["rovers"][ag]["fp_frame"]:
                img, _ = scene.first_person(ag)
                cv2.imwrite(str(out / rec["rovers"][ag]["fp_frame"]), img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 82])

        if k == k_find:
            a = last["A"]
            reading = ice_reading(ice, *poses["A"][k][:2])
            find = {"type": "find", "agency": "A", "find_id": 1, "x": a["estimate"][0], "y": a["estimate"][1],
                    "sigma": a["sigma"], "reading": round(reading, 3), "tx": fake_tx(run_id, "find", 1)}
            rec["events"].append(find)
            b_target = (find["x"], find["y"])
        if b_target is not None and not checked:
            b = last["B"]
            if math.hypot(b["estimate"][0] - b_target[0], b["estimate"][1] - b_target[1]) < 0.5:
                reading = ice_reading(ice, *poses["B"][k][:2])
                rec["events"].append({"type": "check", "agency": "B", "find_id": 1,
                                      "x": b["estimate"][0], "y": b["estimate"][1], "sigma": b["sigma"],
                                      "reading": round(reading, 3), "agrees": reading > AGREE_READING,
                                      "tx": fake_tx(run_id, "check", 1)})
                checked = True
                n_total = k + 8
        steps.append(rec)
        if k % 50 == 0:
            print(f"{run_id} step {k}", flush=True)
        k += 1
        if n_total is not None and k >= n_total:
            break
        if k > 900:
            raise RuntimeError("rover B never reached the find")


    log = {
        "run_id": run_id,
        "label": label,
        "terrain": {"file": TERRAIN.name, "x0": x0, "y0": y0, "cell": cell},
        "chain": {"chain_id": None, "contract": None, "explorer": None},
        # Not in the hand-off format yet: proposed to the lead.
        "standin": {"poses": "scripted", "navigation": "stand-in estimate, not the particle filter",
                    "rims": "stand-in rims from the crater map", "tx": "fake hashes, nothing was posted"},
        "truth": {"ice": [{"x": round(ice[0], 3), "y": round(ice[1], 3), "radius": ICE_RADIUS_M}]},
        "steps": steps,
    }
    (out / "log.json").write_text(json.dumps(log, separators=(",", ":")))
    return {"run_id": run_id, "label": label, "path": f"runs/{run_id}/log.json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=1, help="render a first-person frame every N steps")
    args = ap.parse_args()
    from sim.scene import Scene
    height, craters, x0, y0, cell = load_terrain(TERRAIN)
    t = time.perf_counter()
    scene = Scene(height, craters, x0, y0, cell)
    runs = [
        make_run("run1-standin", "Run 1: crater fixes on", True, height, craters, x0, y0, cell, scene, args.every, 1),
        make_run("run2-standin", "Run 2: rover A on odometry only", False, height, craters, x0, y0, cell, scene, args.every, 2),
    ]
    (OUT / "index.json").write_text(json.dumps({"runs": runs}, indent=1))
    print(f"done in {time.perf_counter() - t:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
