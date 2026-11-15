"""Both demo runs, end to end: python run_demo.py

Writes one folder per run under data/runs/ in the main checkout, in the run
log format of docs/handoffs.md: log.json plus fp/<agency>_<step>.jpg.

Run 1, crater fixes on: rover A drives in the dark, crosses buried ice,
posts the find with its uncertainty. Rover B, from the other agency, reads
the find, drives there with its own navigation, measures ice, and posts a
check that agrees: confirmed.

Run 2, A's crater fixes off: A drives about a kilometre on odometry alone,
so its position is about 20 m off (2% drift). It finds the same kind of
deposit, but reports it about 20 m from where it is. B measures at the
reported spot, finds nothing, and posts a check that disagrees: disputed.

How the story is kept honest: each run is simulated twice with the same
random streams. The first pass drives A alone to learn its true path; the
main deposit is then placed on that path, about where A has driven the
run's distance. The second pass is the run. A's motion does not depend on
the ice until it finds it, so both passes drive identically. Whether B's
check agrees is then decided only by where A *reported* the find, and the
script asserts that in run 2 the reported spot is outside every deposit.

Stand-ins (labelled in the log under "standins"): navigation, rim points,
planner and chain. Real parts replace them behind the same interfaces.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from sim.ice import (DEPOSIT_RADIUS_M, FIND_THRESHOLD_WT, RICH_PEAK_WT, Deposit, IceField, IceSensor,
                     FOOTPRINT_RADIUS_M)
from sim.rover import PLANNER_STANDIN, SLOPE_LIMIT_DEG, SPEED_M_S, STEP_M, Rover, SlopeMap
from sim.nav import ParticleNav
from sim.lidar import Lidar
from sim.vision import Vision, draw_plan, rover_to_world
from sim.standins import RIMS_STANDIN, ChainStandin, rims_standin
try:
    from sim import chain as lantern   # step 1: the real contract on Monad testnet
except Exception:                    # no web3, or no deployment file: stay on the stand-in
    lantern = None
from sim.terrain import DATA, TERRAIN_DIR, default_path, load_terrain

RUNS_DIR = DATA / "runs"           # LANTERN_DATA/runs, or the default in sim/terrain.py
DT_S = STEP_M / SPEED_M_S          # simulated seconds per step (PROSPECT p.3 speed)
ARRIVE_M = 0.3                     # assumed: B stops when its estimate is this close to the find
BLOCKED_ARRIVE_M = 2.0             # assumed: or this close, when the find is on ground B may not enter
                                   # (a crater the lidar marked); inside the check gate 3 x (sigma + sigma)
CHECK_READS = 3                    # assumed: B averages this many readings at the find
TAIL_STEPS = 5                     # assumed: idle steps after the last check, so the end state shows
MAX_STEPS = 1500                   # assumed: safety stop
N_DECOYS = 5                       # assumed: other deposits elsewhere in the drive area
JPEG_QUALITY = 85                  # assumed
FRAMES_NOTE = {
    "fp_frame": "sensor frame: rendered live in the sim loop at the step's pose, after the drive and before localisation",
    "fp_sub": "display only: poses interpolated between steps, rendered after the sim from the log; not sensor frames",
}

# Scenario positions are metres from the terrain's (x0, y0) corner.
SCENARIOS = [
    dict(key="run1", label="Run 1: both rovers navigate by crater rims", seed=101, a_fixes=True,
         a_start=(300.0, 300.0, 0.6), a_goal=(620.0, 520.0), find_at_m=260.0,
         b_start=(700.0, 256.75, 2.4)),  # nearest spot with 3 m of ground under 15 deg around it (was 700, 250: 24.6 deg)
    dict(key="run2", label="Run 2: rover A on wheel odometry alone", seed=202, a_fixes=False,
         a_start=(100.0, 100.0, 0.66), a_goal=(950.0, 760.0), find_at_m=990.0,
         b_start=(880.0, 560.0, 2.4)),
]


def _particles_log(nav):
    """ParticleNav's particles after this step: [[x, y, w], ...], world metres
    to 1 cm, w normalised to sum 1 (4 decimals). Empty without a filter."""
    p = getattr(nav, "p", None)
    if p is None:
        return []
    w = nav._normalised()
    return [[round(float(x), 2), round(float(y), 2), round(float(v), 4)] for (x, y), v in zip(p, w)]


def simulate(scn, terrain, slope, field=None, cam=None):
    """One run. With field=None only rover A drives (pass 1, to learn its path).
    cam: a LiveCamera that takes each rover's step frame in the loop."""
    height, craters, x0, y0, cell = terrain
    seed = scn["seed"]

    def rng(k):
        return np.random.default_rng([seed, k])

    ax, ay, ah = scn["a_start"]
    bx, by, bh = scn["b_start"]
    for key, (sx, sy, _) in (("a_start", scn["a_start"]), ("b_start", scn["b_start"])):
        deg = float(slope.at(x0 + sx, y0 + sy))
        if deg > SLOPE_LIMIT_DEG:  # a rover must never start on no-go ground
            raise ValueError(f"{scn['key']} {key} ({sx}, {sy}) is on {deg:.1f} deg ground, over {SLOPE_LIMIT_DEG} deg")
    A = Rover("A", (x0 + ax, y0 + ay, ah), ParticleNav(scn["a_fixes"], rng(2), craters), slope, rng(1))
    B = Rover("B", (x0 + bx, y0 + by, bh), ParticleNav(True, rng(12), craters), slope, rng(11))
    rims_rng = {"A": rng(3), "B": rng(13)}
    if cam is not None:
        cam.start((A, B))
        cam.attach("A", rng(4), rng(5))
        cam.attach("B", rng(14), rng(15))
        A.sensing = B.sensing = cam.sensors
    goal_a = (x0 + scn["a_goal"][0], y0 + scn["a_goal"][1])
    A.set_goal(goal_a)
    # Real chain when the step 1 client can sign for both agencies (.env keys), else the stand-in.
    if lantern is not None and all(lantern.client().can_sign(a) for a in "AB"):
        chain = lantern.run_scope()
    else:
        chain = ChainStandin(salt=f"{scn['key']}-{seed}")
    sensor = IceSensor(field, rng(7)) if field is not None else None

    mode = {"A": "survey", "B": "wait" if field is not None else "off"}
    target_b = None
    in_ice, best, a_readings = False, None, []
    steps, tail = [], None
    find_truth = {}

    def snapshot(r, rims, moved, reading):
        return {"true_pose": [round(float(v), 3) for v in r.true],
                "odom_pose": [round(float(v), 3) for v in r.odom],
                "estimate": [round(float(v), 3) for v in r.estimate],
                "sigma": round(float(r.sigma), 3),
                "nav_on": bool(r.nav.fixes_on),
                "rims": np.round(rims, 2).tolist(),
                "mode": mode[r.agency],
                "ice_reading": None if reading is None else round(reading, 3),
                "est_error": round(float(np.hypot(*(r.estimate - r.true[:2]))), 3),
                "distance": round(r.distance, 2),
                "fp_frame": cam.last.get(r.agency) if cam is not None and cam.run_dir is not None else None,
                "lidar_frame": cam.last_lidar.get(r.agency) if cam is not None else None,
                "lidar_hazards": cam.haz_count.get(r.agency, {}) if cam is not None else {},
                "plan": ([[round(float(px), 2), round(float(py), 2)] for px, py in r.planned_route()]
                         if r.sensing and mode[r.agency] in ("survey", "goto") else None),
                "particles": _particles_log(r.nav)}

    for k in range(MAX_STEPS):
        events = []
        snap = {}
        for r in (A, B):
            ag = r.agency
            moved = False
            reading = None
            if k > 0 and mode[ag] in ("survey", "goto"):
                d, dh = r.follow()
                seen = None
                if cam is not None:  # the camera sees the new pose before navigation runs
                    seen = cam.shoot(k, r, d > 0)
                odo, err = r.odometry(d, dh)
                rims = seen if seen is not None else rims_standin(r.true, craters, rims_rng[ag])
                r.localize(err, d, len(rims), odom=odo, rims=rims)
                moved = d > 0
                if sensor is not None:
                    reading = sensor.read(r.true[0], r.true[1])
            else:
                rims = rims_standin(r.true, craters, rims_rng[ag]) if k == 0 else np.zeros((0, 2))
                if cam is not None and k == 0:
                    seen = cam.shoot(k, r, False)
                    if seen is not None:
                        rims = seen

            # Rover A: a find is the peak reading of one pass over ice.
            if ag == "A" and mode["A"] == "survey" and reading is not None:
                if reading >= FIND_THRESHOLD_WT:
                    in_ice = True
                    a_readings.append(reading)
                    if best is None or reading > best[0]:
                        best = (reading, r.estimate.copy(), r.sigma, r.true[:2].copy())
            goal_reached = (ag == "A" and mode["A"] == "survey" and
                            math.hypot(goal_a[0] - r.estimate[0], goal_a[1] - r.estimate[1]) < 1.0)
            if ag == "A" and mode["A"] == "survey" and in_ice and (goal_reached or (reading is not None and reading < FIND_THRESHOLD_WT)):
                rd, est, sig, true_xy = best
                data_hash = "0x" + hashlib.sha256(json.dumps([round(v, 4) for v in a_readings]).encode()).hexdigest()
                fid = chain.post_find("A", float(est[0]), float(est[1]), float(sig), float(rd), data_hash)
                events.append({"type": "find", "agency": "A", "find_id": fid, "x": round(float(est[0]), 3),
                               "y": round(float(est[1]), 3), "sigma": round(float(sig), 3),
                               "reading": round(float(rd), 3), "tx": chain.last_tx})
                find_truth = {"true_xy": true_xy, "reported_xy": est, "sigma": sig, "reading": rd}
                mode["A"] = "done"
            if goal_reached and mode["A"] == "survey":
                mode["A"] = "done"
            if field is None and ag == "A" and r.distance >= scn["find_at_m"] + 30:
                mode["A"] = "done"

            # Rover B: read the chain before planning; go to another agency's find.
            if ag == "B" and mode["B"] == "wait":
                open_finds = [f for f in chain.get_finds() if f["agency"] != "B" and f["status"] == "reported"]
                if open_finds:
                    f = open_finds[0]
                    target_b = (f["x"], f["y"])
                    r.set_goal(target_b)
                    mode["B"] = "goto"
                    target_b_id = f["id"]
            dist_b = math.hypot(target_b[0] - r.estimate[0], target_b[1] - r.estimate[1]) if target_b else None
            if ag == "B" and mode["B"] == "goto" and (dist_b < ARRIVE_M or (dist_b < BLOCKED_ARRIVE_M and getattr(r, "goal_blocked", lambda: False)())):
                reads = [sensor.read(r.true[0], r.true[1]) for _ in range(CHECK_READS)]
                reading = float(np.mean(reads))
                agrees = reading >= FIND_THRESHOLD_WT
                tx = chain.check_find("B", target_b_id, float(r.estimate[0]), float(r.estimate[1]),
                                      float(r.sigma), reading, agrees)
                events.append({"type": "check", "agency": "B", "find_id": target_b_id,
                               "x": round(float(r.estimate[0]), 3), "y": round(float(r.estimate[1]), 3),
                               "sigma": round(float(r.sigma), 3), "reading": round(reading, 3),
                               "agrees": bool(agrees), "tx": tx})
                find_truth["check_true_xy"] = r.true[:2].copy()
                find_truth["check_reading"] = reading
                mode["B"] = "done"
            snap[ag] = snapshot(r, rims, moved or k == 0, reading)

        steps.append({"t": round(k * DT_S, 1), "rovers": snap, "events": events})
        if mode["A"] == "done" and mode["B"] in ("done", "off"):
            tail = (tail or 0) + 1
            if field is None or tail > TAIL_STEPS:
                break
    return steps, chain, find_truth


def place_ice(scn, a_steps, x0, y0, rng):
    """Main deposit on A's true path at the run's distance, plus decoys well away from it."""
    track = np.array([s["rovers"]["A"]["true_pose"][:2] for s in a_steps])
    dist = np.array([s["rovers"]["A"]["distance"] for s in a_steps])
    i = int(np.searchsorted(dist, scn["find_at_m"]))
    if i >= len(track):
        raise RuntimeError(f"{scn['key']}: A drove only {dist[-1]:.0f} m, short of {scn['find_at_m']:.0f} m")
    cx, cy = track[i]
    deps = [Deposit(float(cx), float(cy), DEPOSIT_RADIUS_M, RICH_PEAK_WT)]
    tries = 0
    while len(deps) < 1 + N_DECOYS and tries < 1000:
        tries += 1
        r = float(rng.uniform(4.0, 8.0))
        x = float(x0 + rng.uniform(60, 940))
        y = float(y0 + rng.uniform(60, 940))
        if np.min(np.hypot(track[:, 0] - x, track[:, 1] - y)) < r + 25:   # never under A's path
            continue
        if math.hypot(x - cx, y - cy) < 80:                                # nowhere near the main find
            continue
        if any(math.hypot(x - d.x, y - d.y) < d.radius + r + 20 for d in deps):
            continue
        deps.append(Deposit(x, y, r, float(rng.uniform(2.0, RICH_PEAK_WT))))
    return IceField(deps)


def _lerp_pose(p0, p1, f):
    """Pose a fraction f of the way from p0 to p1, as the viewer interpolates (web/js/data.js poseAt)."""
    dh = math.atan2(math.sin(p1[2] - p0[2]), math.cos(p1[2] - p0[2]))
    return [p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f, p0[2] + dh * f]


def _pose_log(r):
    """A rover's true pose as the log rounds it, so frames sit exactly at logged poses."""
    return [round(float(v), 3) for v in r.true]


def _write_jpg(path, img):
    cv2.imwrite(str(path), img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])


def _thin_marks(hz, kinds, hz_world, cell_m=1.5, max_range_m=25.0, max_marks=30):
    """Crater cells only, one mark per cell_m square, nearest first: the lidar
    flags 0.5 m cells, and marking every one buries the camera view."""
    if len(hz) == 0:
        return hz_world
    keep, seen = [], set()
    order = np.argsort(np.hypot(hz[:, 0], hz[:, 1]))
    for i in order:
        if kinds[i] != "crater" or np.hypot(*hz[i]) > max_range_m:
            continue
        key = (int(hz[i, 0] // cell_m), int(hz[i, 1] // cell_m))
        if key in seen:
            continue
        seen.add(key)
        keep.append(i)
        if len(keep) >= max_marks:
            break
    return hz_world[keep] if keep else np.zeros((0, 2))


class LiveCamera:
    """Sensor frames, fp/<agency>_<step>.jpg: taken inside the sim loop right
    after a rover drives and before it localises, as its camera would (vision
    reads them there once it lands). The other rover is wherever it is at
    that moment in the loop."""

    def __init__(self, scene, run_dir, frame_every=1, sensors=True):
        """run_dir None: sense only, write no files (pass 1, so A's path matches pass 2).
        sensors: run vision (rims) and lidar (hazards) on each live frame."""
        self.scene, self.run_dir, self.frame_every = scene, run_dir, frame_every
        if run_dir is not None:
            (run_dir / "fp").mkdir(parents=True, exist_ok=True)
            if sensors:
                (run_dir / "lidar").mkdir(parents=True, exist_ok=True)
        self.last, self.times = {}, []
        self._moved, self._posed = {}, {}
        self.sensors = sensors
        self.vis, self.lid = {}, {}
        self.last_lidar, self.haz_count = {}, {}
        self.t_vision, self.t_lidar = [], []   # seconds per live frame, for the timing summary
        self.score_every = 0                   # score vision on every Nth live frame (sim/rim_truth.py)
        self.scores = []

    def attach(self, agency, rng_vision, rng_lidar):
        """Per-run sensor noise streams, so pass 1 and pass 2 see the same."""
        if self.sensors:
            self.vis[agency] = Vision(self.scene, rng_vision)
            self.lid[agency] = Lidar(self.scene, rng_lidar)

    def _place(self, r):
        pose = _pose_log(r)
        if self._posed.get(r.agency) != pose:
            self.scene.set_rover(r.agency, pose)
            self._posed[r.agency] = pose

    def start(self, rovers):
        for r in rovers:
            self._place(r)

    def shoot(self, k, r, moved):
        ag = r.agency
        self._place(r)
        self._moved[ag] = self._moved.get(ag, 0) + (1 if moved else 0)
        if k == 0 or ag not in self.last or (moved and self._moved[ag] % self.frame_every == 0):
            t0 = time.perf_counter()
            img, depth = self.scene.first_person(ag)
            self.times.append(time.perf_counter() - t0)
            name = f"fp/{ag}_{k:04d}.jpg"
            rims, shown = None, img
            if ag in self.vis:
                # Vision: rim points for the particle filter. Lidar: crater and
                # slope hazards for the planner, from the moment they are seen.
                t1 = time.perf_counter()
                rims = self.vis[ag].rims(img, depth)
                t2 = time.perf_counter()
                pts, rng_img = self.lid[ag].scan(r.true)
                hz, kinds = self.lid[ag].hazards(pts)
                self.t_vision.append(t2 - t1)
                self.t_lidar.append(time.perf_counter() - t2)
                if self.score_every and len(self.t_vision) % self.score_every == 0:
                    try:
                        from sim.rim_truth import rim_truth, score   # branch step-6-rim-truth
                        self.scores.append(score(rims, rim_truth(self.scene, ag, depth)))
                    except ImportError:
                        self.score_every = 0
                hz_world = rover_to_world(self.lid[ag].to_camera_frame(hz), r.true)
                r.add_hazards(hz_world)
                self.haz_count[ag] = {kk: kinds.count(kk) for kk in ("crater", "slope")}
                if self.run_dir is not None:
                    # Lidar hazards are marked on the saved frame; the planned
                    # route is drawn live by the viewer from the logged "plan".
                    shown = draw_plan(img, [], r.true, self.scene, _thin_marks(hz, kinds, hz_world))
                    lname = f"lidar/{ag}_{k:04d}.jpg"
                    _write_jpg(self.run_dir / lname, self.lid[ag].view(rng_img))
                    self.last_lidar[ag] = lname
            if self.run_dir is not None:
                _write_jpg(self.run_dir / name, shown)
            self.last[ag] = name
            return rims
        return None


def sub_frame_jobs(steps, run_dir, sub_m):
    """Display frames between steps, every ~sub_m metres: the camera's own
    rover at poses interpolated as the viewer does, every other rover at its
    step pose. Sets fp_sub on each rover's step and returns the render jobs
    (path, agency, pose, {other agency: pose})."""
    jobs = []
    for k in range(len(steps) - 1):
        rov, nxt = steps[k]["rovers"], steps[k + 1]["rovers"]
        for ag, r in rov.items():
            p0, p1 = r["true_pose"], nxt[ag]["true_pose"]
            n = int(round(math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / sub_m)) - 1
            others = {a: o["true_pose"] for a, o in rov.items() if a != ag}
            subs = []
            for j in range(1, n + 1):
                f = j / (n + 1)
                name = f"fp/{ag}_{k:04d}_{j}.jpg"
                jobs.append((str(run_dir / name), ag, _lerp_pose(p0, p1, f), others))
                subs.append([round(f, 4), name])
            if subs:
                r["fp_sub"] = subs
    return jobs


_WORKER = {}


def _worker_init(shared, x0, y0, cell, display):
    from sim.scene import Scene
    height = np.load(Path(shared) / "height.npy", mmap_mode="r")
    craters = np.load(Path(shared) / "craters.npy")
    _WORKER["scene"] = Scene(height, craters, x0, y0, cell, display=display, shared=shared)
    _WORKER["posed"] = {}


def render_jobs(scene, posed, jobs):
    """Render display-frame jobs in one Scene. A frame is a function of the
    poses alone (sim/scene.py FINE_SNAP_M), so job order and process do not
    change its pixels."""
    times = []
    for path, ag, pose, others in jobs:
        t0 = time.perf_counter()
        for a, p in list(others.items()) + [(ag, pose)]:
            if posed.get(a) != p:
                scene.set_rover(a, p)
                posed[a] = p
        img, _ = scene.first_person(ag)
        times.append(time.perf_counter() - t0)
        _write_jpg(path, img)
    return times


def _worker_run(chunk):
    import psutil
    times = render_jobs(_WORKER["scene"], _WORKER["posed"], chunk)
    mi = psutil.Process().memory_full_info()
    return times, os.getpid(), mi.uss, getattr(mi, "peak_wset", mi.rss)


WORKER_GB_ASSUMED = 0.7    # one render worker's private memory with the maps shared: 0.61 GB measured 12:19
HEADROOM_GB = 2.0          # leave this much RAM free
MAX_WORKERS = 8


def render_sub_frames(jobs, shared, terrain, display, workers=None):
    """Render display frames across a process pool sized to free memory."""
    import multiprocessing as mp
    import psutil
    if workers is None:
        free_gb = psutil.virtual_memory().available / 2 ** 30
        # Round before the floor: in floats (3.4 - 2.0) // 0.7 is 1.0, not 2.
        fit = math.floor(round((free_gb - HEADROOM_GB) / WORKER_GB_ASSUMED, 6))
        workers = int(max(1, min(MAX_WORKERS, os.cpu_count() or 1, fit)))
        print(f"  pool: {free_gb:.1f} GB free, {workers} workers")
    _, _, x0, y0, cell = terrain
    size = math.ceil(len(jobs) / (workers * 4))
    chunks = [jobs[i:i + size] for i in range(0, len(jobs), size)]  # contiguous: squares rebuild rarely
    times, mem = [], {}
    with mp.get_context("spawn").Pool(workers, _worker_init, (str(shared), x0, y0, cell, display)) as pool:
        for t, pid, uss, peak in pool.imap_unordered(_worker_run, chunks):
            times += t
            u0, p0 = mem.get(pid, (0, 0))
            mem[pid] = (max(uss, u0), max(peak, p0))
    return times, workers, mem


class _FrameNames:
    """Stands in for LiveCamera in --resume: names each step frame exactly as
    LiveCamera would and records how to render it (the camera's pose and the
    other rovers' poses at that moment in the loop), but renders nothing.
    A frame is a function of those poses alone, so a missing one can be
    rendered later, anywhere, with the same pixels."""

    def __init__(self, run_dir, frame_every=1):
        self.run_dir, self.frame_every = run_dir, frame_every
        self.sensors = False
        self.last, self.times, self.t_vision, self.scores = {}, [], [], []
        self.last_lidar, self.haz_count = {}, {}
        self.jobs = []                      # (path, agency, pose, {other agency: pose})
        self._moved, self._posed = {}, {}

    def start(self, rovers):
        for r in rovers:
            self._posed[r.agency] = _pose_log(r)

    def attach(self, *_):
        pass

    def shoot(self, k, r, moved):
        ag = r.agency
        self._posed[ag] = _pose_log(r)
        self._moved[ag] = self._moved.get(ag, 0) + (1 if moved else 0)
        if k == 0 or ag not in self.last or (moved and self._moved[ag] % self.frame_every == 0):
            name = f"fp/{ag}_{k:04d}.jpg"
            others = {a: p for a, p in self._posed.items() if a != ag}
            self.jobs.append((str(self.run_dir / name), ag, self._posed[ag], others))
            self.last[ag] = name
        return None


def _jpeg_complete(path):
    """True if the file ends with the JPEG end-of-image marker (a frame cut
    short by a stopped run does not)."""
    try:
        with open(path, "rb") as f:
            f.seek(-2, os.SEEK_END)
            return f.read(2) == b"\xff\xd9"
    except OSError:
        return False


def _merge_index(path, entries):
    """index.json with these runs replacing any earlier run of the same
    scenario; other scenarios' entries stay. Order follows SCENARIOS."""
    try:
        old = json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError, ValueError):
        old = []
    keys = {e["run_id"].split("-")[0] for e in entries}
    merged = [e for e in old if e["run_id"].split("-")[0] not in keys] + list(entries)
    order = {s["key"]: i for i, s in enumerate(SCENARIOS)}
    return sorted(merged, key=lambda e: order.get(e["run_id"].split("-")[0], len(order)))


def main():
    ap = argparse.ArgumentParser(description="Run both Lantern demo runs and write their logs.")
    ap.add_argument("--out", default=str(RUNS_DIR))
    ap.add_argument("--only", choices=[s["key"] for s in SCENARIOS], default=None)
    ap.add_argument("--frame-every", type=int, default=1, help="render a first-person frame every N steps a rover moves")
    ap.add_argument("--no-render", action="store_true", help="simulate and write logs without frames (stand-in rims, no lidar)")
    ap.add_argument("--score-rims", type=int, default=0, metavar="N",
                    help="score vision rims against sim/rim_truth.py on every Nth live frame (needs that file)")
    ap.add_argument("--vision", action="store_true",
                    help="navigate on the camera (sim/vision.py rims) and lidar (sim/lidar.py hazards); "
                         "off by default until both runs pass with it")
    ap.add_argument("--stamp", default=None, help="run_id suffix (default: now, YYYYMMDD-HHMM); reuse one to overwrite a run")
    ap.add_argument("--display", choices=("full", "detail", "off"), default="full",
                    help="first-person terrain display layer (sim/scene.py): full = rim lips and rocks, "
                         "detail = texture only (no made-up geometry), off = sim grid only")
    ap.add_argument("--sub-frame-m", type=float, default=0.5,
                    help="also render first-person frames every this many metres between steps (0: one per step)")
    ap.add_argument("--workers", type=int, default=None,
                    help="processes for the in-between display frames (default: from free memory, at most 8)")
    ap.add_argument("--resume", action="store_true",
                    help="finish an earlier run (needs --stamp): simulate again, render only frames that are "
                         "missing or cut short, then write the logs and index.json")
    args = ap.parse_args()
    if args.resume and not args.stamp:
        ap.error("--resume needs the --stamp of the run to finish")
    if args.resume and (args.vision or args.no_render):
        ap.error("--resume finishes a run made without --vision; it renders, so not with --no-render")

    t = time.perf_counter()
    tpath = default_path()
    terrain = load_terrain(tpath)
    height, craters, x0, y0, cell = terrain
    slope = SlopeMap(height, x0, y0, cell)
    print(f"terrain {Path(tpath).name} loaded in {time.perf_counter() - t:.1f}s")
    out = Path(args.out)
    stamp = args.stamp or datetime.now().strftime("%Y%m%d-%H%M")
    scene = display = shared = cam = None
    if args.resume:
        # Built later, and only if a frame is missing.
        display = args.display if (TERRAIN_DIR / "display" / "display.json").exists() else "off"
    elif not args.no_render:
        try:
            from sim.scene import Scene
        except ImportError as e:   # vtk: the camera, vision and lidar all need it
            raise SystemExit(f"run_demo.py: cannot render ({e}). Install it with `python3 -m pip install vtk`, "
                             "or run with --no-render (stand-in rims, no camera or lidar).")
        t = time.perf_counter()
        # Display assets from $LANTERN_DATA (scene.py's own lookup only checks
        # the repo's data/ and a fixed C:\ path, so off Windows it fell back to "off").
        scene = Scene(height, craters, x0, y0, cell, display=args.display, display_dir=TERRAIN_DIR / "display")
        display = scene.display  # what was drawn (falls back to "off" without assets)
        if args.sub_frame_m > 0 and args.frame_every == 1:
            shared = scene.share(out / "_shared")
            np.save(shared / "craters.npy", np.asarray(craters))
        print(f"scene built in {time.perf_counter() - t:.1f}s")
    t_all = time.perf_counter()
    done, jobs = [], []
    for scn in SCENARIOS:
        if args.only and scn["key"] != args.only:
            continue
        t0 = time.perf_counter()
        # Pass 1 senses like pass 2 (no files), so A drives the same path.
        cam1 = LiveCamera(scene, None, args.frame_every, sensors=args.vision) if scene is not None else None
        a_steps, _, _ = simulate(scn, terrain, slope, field=None, cam=cam1)
        field = place_ice(scn, a_steps, x0, y0, np.random.default_rng([scn["seed"], 99]))
        run_id = f"{scn['key']}-{stamp}"
        run_dir = out / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        cam = LiveCamera(scene, run_dir, args.frame_every, sensors=args.vision) if scene is not None else None
        if args.resume:
            cam = _FrameNames(run_dir, args.frame_every)
        if cam is not None:
            cam.score_every = args.score_rims
        steps, chain, ft = simulate(scn, terrain, slope, field=field, cam=cam)
        finds = chain.get_finds()
        if not finds:
            raise RuntimeError(f"{scn['key']}: A never posted a find")
        rep = np.asarray(ft["reported_xy"])
        conc_at_report = float(field.concentration(rep[0], rep[1]))
        a_err = float(np.hypot(*(rep - ft["true_xy"])))
        dep = field.deposits[0]
        print(f"{scn['key']}: {len(steps)} steps, {steps[-1]['t'] / 3600:.2f} h simulated, "
              f"A drove {steps[-1]['rovers']['A']['distance']:.0f} m, B drove {steps[-1]['rovers']['B']['distance']:.0f} m")
        print(f"  find: reported {a_err:.1f} m from where A really was (sigma {ft['sigma']:.1f} m); "
              f"reported spot is {math.hypot(rep[0] - dep.x, rep[1] - dep.y):.1f} m from the deposit centre "
              f"(1 wt% radius {dep.radius:.0f} m); true ice there {conc_at_report:.2f} wt%")
        print(f"  check: B read {ft.get('check_reading', float('nan')):.2f} wt% -> status {finds[0]['status']}")
        for ag in ("A", "B"):
            r = steps[-1]["rovers"][ag]
            print(f"  {ag} final: estimate {r['est_error']:.2f} m from true, sigma {r['sigma']:.2f} m")
        if scn["key"] == "run2":
            # The miss must come from A's drift, not from where B stood.
            assert conc_at_report < 0.5 * FIND_THRESHOLD_WT, "run 2 reported spot is on ice"
            assert a_err > dep.radius + FOOTPRINT_RADIUS_M, "run 2 drift is too small to miss the deposit"
            assert finds[0]["status"] == "disputed", finds[0]
        else:
            assert finds[0]["status"] == "confirmed", finds[0]
        msg = f"  simulated in {time.perf_counter() - t0:.0f}s"
        if cam is not None and cam.times:
            msg += f" with {len(cam.times)} live step frames, median {1e3 * np.median(cam.times):.0f} ms"
        print(msg)
        if cam is not None and cam.t_vision:
            print(f"  per live frame: render {1e3 * np.median(cam.times):.0f} ms, vision {1e3 * np.median(cam.t_vision):.0f} ms, "
                  f"lidar {1e3 * np.median(cam.t_lidar):.0f} ms (medians; pass 2 only)")
        if cam is not None and cam.scores:
            sc = {k: float(np.nanmean([d[k] for d in cam.scores])) for k in ("precision", "recall", "recall_shadow", "mean_dist_m", "gauss")}
            print(f"  vision vs rim_truth on {len(cam.scores)} frames: " + ", ".join(f"{k} {v:.2f}" for k, v in sc.items()))
        if args.resume:
            want = cam.jobs + (sub_frame_jobs(steps, run_dir, args.sub_frame_m)
                               if args.sub_frame_m > 0 and args.frame_every == 1 else [])
            missing = [j for j in want if not _jpeg_complete(j[0])]
            print(f"  resume: {len(want) - len(missing)} of {len(want)} frames already there, {len(missing)} to render")
            jobs += missing
        elif shared is not None:
            jobs += sub_frame_jobs(steps, run_dir, args.sub_frame_m)
        sensing = cam is not None and cam.sensors
        done.append((scn, run_id, run_dir, steps, field, finds, chain, sensing))
    t_sim = time.perf_counter() - t_all

    if jobs and args.resume:
        from sim.scene import Scene
        sc = Scene(height, craters, x0, y0, cell, display=args.display, display_dir=TERRAIN_DIR / "display")
        display = sc.display
        shared = sc.share(out / "_shared")
        np.save(shared / "craters.npy", np.asarray(craters))
        sc = None
    if jobs:
        cam = scene = None  # the loop's Scene is not needed by the pool
        gc.collect()
        t = time.perf_counter()
        rt, workers, mem = render_sub_frames(jobs, shared, terrain, display, args.workers)
        dt = time.perf_counter() - t
        peak = max(p for _, p in mem.values()) / 2 ** 30
        uss = max(u for u, _ in mem.values()) / 2 ** 30
        print(f"display frames: {len(rt)} in {dt:.0f}s on {workers} workers ({len(rt) / dt:.1f} frames/s, "
              f"median {1e3 * np.median(rt):.0f} ms each); per worker: private memory up to {uss:.2f} GB, "
              f"peak working set (shared maps included) {peak:.2f} GB")
    if shared is not None:
        shutil.rmtree(shared, ignore_errors=True)

    index = []
    for scn, run_id, run_dir, steps, field, finds, chain, sensing in done:
        log = {
            "run_id": run_id,
            "label": scn["label"],
            "terrain": {"file": Path(tpath).name, "x0": x0, "y0": y0, "cell": cell},
            "chain": (chain.run_log_chain() if hasattr(chain, "run_log_chain")
                      else {"chain_id": 0, "contract": "stand-in (no chain yet)", "explorer": None}),
            "truth": {"ice": field.to_log(),
                      "ice_units": "wt% water-equivalent hydrogen; radius is the 1 wt% contour",
                      "find_threshold_wt": FIND_THRESHOLD_WT},
            "nav": ParticleNav.NAME,
            "planner": PLANNER_STANDIN,  # a real part (sim/rover.py A*); the constant keeps its old name
            "vision": Vision.NAME if sensing else None,
            "lidar": Lidar.NAME if sensing else None,
            "standins": {k: v for k, v in {**({} if sensing else {"rims": RIMS_STANDIN}),
                         "chain": chain.STANDIN, "ice_sensor": IceSensor.STANDIN}.items() if v},
            "dt_s": round(DT_S, 2),
            "display": display,
            "frames": FRAMES_NOTE,
            "steps": steps,
            "final_finds": finds,
        }
        with open(run_dir / "log.json", "w", encoding="utf8") as f:
            json.dump(log, f, separators=(",", ":"))
        index.append({"run_id": run_id, "label": scn["label"], "dir": run_id, "status": finds[0]["status"]})
        print(f"wrote {run_dir}")
    print(f"total {time.perf_counter() - t_all:.0f}s (simulation with live step frames {t_sim:.0f}s)")

    merged = _merge_index(out / "index.json", index)
    with open(out / "index.json", "w", encoding="utf8") as f:
        json.dump(merged, f, indent=1)
    print("index.json: " + ", ".join(e["run_id"] for e in merged))


if __name__ == "__main__":
    main()
