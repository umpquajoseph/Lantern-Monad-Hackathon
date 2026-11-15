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
import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from sim.ice import (DEPOSIT_RADIUS_M, FIND_THRESHOLD_WT, RICH_PEAK_WT, Deposit, IceField, IceSensor,
                     FOOTPRINT_RADIUS_M)
from sim.rover import PLANNER_STANDIN, SPEED_M_S, STEP_M, Rover, SlopeMap
from sim.standins import RIMS_STANDIN, ChainStandin, NavStandin, rims_standin
from sim.terrain import default_path, load_terrain

RUNS_DIR = Path(r"C:\Monad-Hackathon-Project\data\runs")
DT_S = STEP_M / SPEED_M_S          # simulated seconds per step (PROSPECT p.3 speed)
ARRIVE_M = 0.3                     # assumed: B stops when its estimate is this close to the find
CHECK_READS = 3                    # assumed: B averages this many readings at the find
TAIL_STEPS = 5                     # assumed: idle steps after the last check, so the end state shows
MAX_STEPS = 1500                   # assumed: safety stop
N_DECOYS = 5                       # assumed: other deposits elsewhere in the drive area
JPEG_QUALITY = 85                  # assumed

# Scenario positions are metres from the terrain's (x0, y0) corner.
SCENARIOS = [
    dict(key="run1", label="Run 1: crater fixes on", seed=101, a_fixes=True,
         a_start=(300.0, 300.0, 0.6), a_goal=(620.0, 520.0), find_at_m=260.0,
         b_start=(700.0, 250.0, 2.4)),
    dict(key="run2", label="Run 2: A's crater fixes off", seed=202, a_fixes=False,
         a_start=(100.0, 100.0, 0.66), a_goal=(950.0, 760.0), find_at_m=990.0,
         b_start=(880.0, 560.0, 2.4)),
]


def simulate(scn, terrain, slope, field=None):
    """One run. With field=None only rover A drives (pass 1, to learn its path)."""
    height, craters, x0, y0, cell = terrain
    seed = scn["seed"]

    def rng(k):
        return np.random.default_rng([seed, k])

    ax, ay, ah = scn["a_start"]
    bx, by, bh = scn["b_start"]
    A = Rover("A", (x0 + ax, y0 + ay, ah), NavStandin(scn["a_fixes"], rng(2)), slope, rng(1))
    B = Rover("B", (x0 + bx, y0 + by, bh), NavStandin(True, rng(12)), slope, rng(11))
    rims_rng = {"A": rng(3), "B": rng(13)}
    goal_a = (x0 + scn["a_goal"][0], y0 + scn["a_goal"][1])
    A.set_goal(goal_a)
    chain = ChainStandin()
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
                "_moved": moved}

    for k in range(MAX_STEPS):
        events = []
        snap = {}
        for r in (A, B):
            ag = r.agency
            moved = False
            reading = None
            if k > 0 and mode[ag] in ("survey", "goto"):
                d, dh = r.follow()
                _, err = r.odometry(d, dh)
                rims = rims_standin(r.true, craters, rims_rng[ag])
                r.localize(err, d, len(rims))
                moved = d > 0
                if sensor is not None:
                    reading = sensor.read(r.true[0], r.true[1])
            else:
                rims = rims_standin(r.true, craters, rims_rng[ag]) if k == 0 else np.zeros((0, 2))

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
            if ag == "B" and mode["B"] == "goto" and math.hypot(target_b[0] - r.estimate[0], target_b[1] - r.estimate[1]) < ARRIVE_M:
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


def render(scn, steps, terrain, run_dir, frame_every):
    from sim.scene import Scene
    height, craters, x0, y0, cell = terrain
    scene = Scene(height, craters, x0, y0, cell)
    fp_dir = run_dir / "fp"
    fp_dir.mkdir(parents=True, exist_ok=True)
    last = {}
    moved_count = {"A": 0, "B": 0}
    times = []
    posed = {}
    for k, s in enumerate(steps):
        for ag, r in s["rovers"].items():
            if posed.get(ag) != r["true_pose"]:  # set_rover rebuilds that rover's lamp shadow map
                scene.set_rover(ag, r["true_pose"])
                posed[ag] = r["true_pose"]
        for ag, r in s["rovers"].items():
            moved = r.pop("_moved")
            if moved:
                moved_count[ag] += 1
            want = k == 0 or (moved and moved_count[ag] % frame_every == 0) or (s["events"] and moved)
            if want or ag not in last:
                t0 = time.perf_counter()
                img, _ = scene.first_person(ag)
                times.append(time.perf_counter() - t0)
                name = f"fp/{ag}_{k:04d}.jpg"
                cv2.imwrite(str(run_dir / name), img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                last[ag] = name
            r["fp_frame"] = last[ag]
    return times


def main():
    ap = argparse.ArgumentParser(description="Run both Lantern demo runs and write their logs.")
    ap.add_argument("--out", default=str(RUNS_DIR))
    ap.add_argument("--only", choices=[s["key"] for s in SCENARIOS], default=None)
    ap.add_argument("--frame-every", type=int, default=1, help="render a first-person frame every N steps a rover moves")
    ap.add_argument("--no-render", action="store_true", help="simulate and write logs without frames")
    args = ap.parse_args()

    t = time.perf_counter()
    tpath = default_path()
    terrain = load_terrain(tpath)
    height, craters, x0, y0, cell = terrain
    slope = SlopeMap(height, x0, y0, cell)
    print(f"terrain {Path(tpath).name} loaded in {time.perf_counter() - t:.1f}s")
    out = Path(args.out)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    index = []
    for scn in SCENARIOS:
        if args.only and scn["key"] != args.only:
            continue
        t0 = time.perf_counter()
        a_steps, _, _ = simulate(scn, terrain, slope, field=None)
        field = place_ice(scn, a_steps, x0, y0, np.random.default_rng([scn["seed"], 99]))
        steps, chain, ft = simulate(scn, terrain, slope, field=field)
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
        if scn["key"] == "run2":
            # The miss must come from A's drift, not from where B stood.
            assert conc_at_report < 0.5 * FIND_THRESHOLD_WT, "run 2 reported spot is on ice"
            assert a_err > dep.radius + FOOTPRINT_RADIUS_M, "run 2 drift is too small to miss the deposit"
            assert finds[0]["status"] == "disputed", finds[0]
        else:
            assert finds[0]["status"] == "confirmed", finds[0]

        run_id = f"{scn['key']}-{stamp}"
        run_dir = out / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if args.no_render:
            for s in steps:
                for r in s["rovers"].values():
                    r.pop("_moved", None)
                    r["fp_frame"] = None
            rt = []
        else:
            rt = render(scn, steps, terrain, run_dir, args.frame_every)
        log = {
            "run_id": run_id,
            "label": scn["label"],
            "terrain": {"file": Path(tpath).name, "x0": x0, "y0": y0, "cell": cell},
            "chain": {"chain_id": 0, "contract": "stand-in (no chain yet)", "explorer": None},
            "truth": {"ice": field.to_log(),
                      "ice_units": "wt% water-equivalent hydrogen; radius is the 1 wt% contour",
                      "find_threshold_wt": FIND_THRESHOLD_WT},
            "standins": {"nav": NavStandin.STANDIN, "rims": RIMS_STANDIN, "planner": PLANNER_STANDIN,
                         "chain": ChainStandin.STANDIN, "ice_sensor": IceSensor.STANDIN},
            "dt_s": round(DT_S, 2),
            "steps": steps,
            "final_finds": finds,
        }
        with open(run_dir / "log.json", "w", encoding="utf8") as f:
            json.dump(log, f, separators=(",", ":"))
        index.append({"run_id": run_id, "label": scn["label"], "dir": run_id, "status": finds[0]["status"]})
        msg = f"  wrote {run_dir} in {time.perf_counter() - t0:.0f}s"
        if rt:
            msg += f"; {len(rt)} frames, first person median {1e3 * np.median(rt):.0f} ms"
        print(msg)

    if not args.only:
        with open(out / "index.json", "w", encoding="utf8") as f:
            json.dump(index, f, indent=1)


if __name__ == "__main__":
    main()
