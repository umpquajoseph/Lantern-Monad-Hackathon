"""The demo mission, precomputed by the real autonomy: python run_mission.py

One run, two rovers, in the run-log format of docs/handoffs.md plus the
"Mission log" additions there. The hosted site replays it and pauses at the
checkpoints the log names.

  1. Overview: the orbital candidate sites, S1 then S2, and both rovers parked.
  2. The Surveyor (A) drives to S1. Its a-priori route comes from the orbital
     map (the height grid averaged to 5 m, LOLA's resolution), which does not
     resolve small craters; the lidar finds them on the way and the planned
     path bends around them. The first such bend is the "divert" checkpoint.
  3. At S1 the Surveyor holds still and surveys (neutron readings over several
     steps). S1 is dry: a `site` event with verdict "dry". Nothing goes on
     chain for a dry site; the log says so.
  4. On to S2, no stops. S2 holds ice: the Surveyor posts the find to the
     Lantern contract on Monad testnet (real transaction, real hash).
  5. The Prospector (B) reads the contract, finds a reported find from
     another agency, and drives to it: a straight orbital route, bent only by
     what its own lidar sees.
  6. At the find it runs its instrument sequence, measures, and posts a check
     that agrees: the find is confirmed on chain.

Every part is the real one from the merged tree: ParticleNav (sim/nav.py),
the A* planner with lidar hazards (sim/rover.py, sim/lidar.py), camera rims
for the filter when --vision is on (sim/vision.py), the ice sensor
(sim/ice.py) and the Lantern client (sim/chain.py). Stand-ins remaining are
listed in the log under `standins`.

    python run_mission.py                      # render, lidar on, chain if .env has the keys
    python run_mission.py --no-render          # logic only: no frames, no lidar (no divert)
    python run_mission.py --stamp demo         # fixed run id "mission-demo"
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import shutil
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter

import run_demo as spine
from run_demo import DT_S, FRAMES_NOTE, LiveCamera, _particles_log, render_sub_frames, sub_frame_jobs
from sim.ice import DEPOSIT_RADIUS_M, FIND_THRESHOLD_WT, RICH_PEAK_WT, Deposit, IceField, IceSensor
from sim.nav import ParticleNav
from sim.rover import PLANNER_STANDIN, SLOPE_LIMIT_DEG, SPEED_M_S, STEP_M, Rover, SlopeMap
from sim.standins import RIMS_STANDIN, ChainStandin, rims_standin
from sim.terrain import DATA, TERRAIN_DIR, default_path, load_terrain
from sim.vision import Vision

try:
    from sim import chain as lantern
except Exception:
    lantern = None

RUNS_DIR = DATA / "runs"
ROLE = {"A": "Surveyor", "B": "Prospector"}

# ---- the mission (metres from the terrain's (x0, y0) corner) ----------------
# Positions are chosen on ground under the slope limit; run_mission.py checks.
MISSION = dict(
    key="mission", seed=303,
    label="Mission: survey two sites, prospect the one with ice",
    a_start=(389.7, 569.9, -1.22),   # flat 3 m around; the cleanest corridor to S1 with one crater to stage
    sites=[dict(id="S1", xy=(425.6, 471.2)),      # dry; the straight route from A crosses one crater, left unmapped
           dict(id="S2", xy=(322.2, 433.6))],     # ice
    b_start=(559.9, 467.0, -3.0),          # flat 3 m around; a crater on the straight route to S2 that the orbital map misses
)
ORBITAL_RES_M = 5.0        # LOLA south-pole DEM resolution (Barker et al. 2021, PGDA product 78): why a crater can be unmapped
UNMAP_MARGIN_M = 3.0       # assumed: the unmapped crater is cleared from the planning map out to this far past its rim
ARRIVE_M = 1.0             # assumed: a rover is "at" a site when its estimate is this close
BLOCKED_ARRIVE_M = 2.0     # assumed: or this close when the spot is on ground it may not enter
SURVEY_STEPS = 6           # assumed: neutron survey dwell, one reading per step (DT_S each)
PROSPECT_STEPS = 10        # assumed: prospector instrument sequence length in steps
CHECK_READS = 3            # assumed: readings averaged for the check (spine)
TAIL_STEPS = 4             # assumed: idle steps after the check so the end state shows
MAX_STEPS = 1500           # assumed: safety stop
N_DECOYS = 4               # assumed: other deposits elsewhere, never under the routes
# Instrument sequences the UI animates (names are the UI's; progress 0..1 per phase).
SURVEY_PHASES = ["park", "nir_scan", "neutron_survey", "analyze"]
PROSPECT_PHASES = ["park", "arm_deploy", "drill", "sample_lift", "analyze", "arm_stow"]


class UnmappedCraterSlopeMap(SlopeMap):
    """The planner's a-priori map: the true 0.25 m slope map with a few craters
    left out, as an orbital DEM at 5 m would miss them (LOLA, Barker et al. 2021).
    The global route goes straight through an unmapped crater until the lidar
    sees it; then the local planner bends around it. The fine map (at(),
    _feasible) stays the true ground, so no step is ever driven into a wall.
    """

    def __init__(self, height, x0, y0, cell, unmapped=()):
        super().__init__(height, x0, y0, cell)
        self.deg_true = self.deg.copy()
        self.unmapped = [tuple(map(float, c)) for c in unmapped]
        b = self.b
        for cx, cy, D in self.unmapped:
            reach = D / 2 + UNMAP_MARGIN_M
            j0, j1 = int((cx - reach - x0) // b), int((cx + reach - x0) // b) + 1
            i0, i1 = int((cy - reach - y0) // b), int((cy + reach - y0) // b) + 1
            for i in range(max(i0, 0), min(i1, self.deg.shape[0])):
                for j in range(max(j0, 0), min(j1, self.deg.shape[1])):
                    if math.hypot(x0 + (j + 0.5) * b - cx, y0 + (i + 0.5) * b - cy) <= reach:
                        self.deg[i, j] = 0.0


def staged_crater(craters, a_xy, b_xy, dmin=6.0, dmax=18.0, along_min=40.0):
    """The first crater of diameter dmin..dmax that the straight line a->b crosses
    at least along_min metres from a: the one left unmapped for that leg."""
    ax, ay = a_xy
    bx, by = b_xy
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    best = None
    for cx, cy, D in craters:
        if not (dmin <= D <= dmax):
            continue
        t = max(0.0, min(1.0, ((cx - ax) * vx + (cy - ay) * vy) / L2))
        if math.hypot(ax + t * vx - cx, ay + t * vy - cy) < D / 2 - 1.0:
            along = t * math.sqrt(L2)
            if along >= along_min and (best is None or along < best[0]):
                best = (along, (float(cx), float(cy), float(D)))
    return best


def sees_crater(r, crater):
    """True once the rover's lidar has marked hazard cells inside this crater."""
    cx, cy, D = crater
    sm = r.slope
    c = sm.cell
    reach = D / 2 + 1.0
    i0 = int(max((cy - reach - sm.y0) / c, 0)); i1 = int(min((cy + reach - sm.y0) / c, r.haz_fine.shape[0] - 1))
    j0 = int(max((cx - reach - sm.x0) / c, 0)); j1 = int(min((cx + reach - sm.x0) / c, r.haz_fine.shape[1] - 1))
    return bool(r.haz_fine[i0:i1 + 1, j0:j1 + 1].any())


def instrument(phases, i, n):
    """Which phase of an instrument sequence step i of n is in, and its progress."""
    per = max(1, n // len(phases))
    j = min(i // per, len(phases) - 1)
    return {"name": phases[j], "progress": round(min(1.0, ((i % per) + 1) / per), 2), "step": i + 1, "of": n}


def place_ice(x0, y0, sites, rng, routes):
    """S2 holds a rich deposit at its centre; S1 has none within sensing; decoys
    stay away from every route and both sites."""
    s2 = sites[1]["xy"]
    deps = [Deposit(x0 + s2[0], y0 + s2[1], DEPOSIT_RADIUS_M, RICH_PEAK_WT)]
    s1 = (x0 + sites[0]["xy"][0], y0 + sites[0]["xy"][1])
    pts = np.array([p for r in routes for p in r]) if routes else np.zeros((0, 2))
    tries = 0
    while len(deps) < 1 + N_DECOYS and tries < 2000:
        tries += 1
        r = float(rng.uniform(4.0, 8.0))
        x = float(x0 + rng.uniform(60, 940))
        y = float(y0 + rng.uniform(60, 940))
        if len(pts) and np.min(np.hypot(pts[:, 0] - x, pts[:, 1] - y)) < r + 25:
            continue
        if math.hypot(x - s1[0], y - s1[1]) < 60 or math.hypot(x - deps[0].x, y - deps[0].y) < 80:
            continue
        if any(math.hypot(x - d.x, y - d.y) < d.radius + r + 20 for d in deps):
            continue
        deps.append(Deposit(x, y, r, float(rng.uniform(2.0, RICH_PEAK_WT))))
    return IceField(deps)


def simulate(scn, terrain, slope, field=None, cam=None):
    """The mission. field=None: pass 1, Surveyor only, to learn its route."""
    height, craters, x0, y0, cell = terrain
    seed = scn["seed"]

    def rng(k):
        return np.random.default_rng([seed, k])

    for key, (sx, sy, *_) in [("a_start", scn["a_start"]), ("b_start", scn["b_start"])] + \
            [(s["id"], s["xy"]) for s in scn["sites"]]:
        deg = float(slope.at(x0 + sx, y0 + sy))
        if deg > SLOPE_LIMIT_DEG:
            raise ValueError(f"{key} ({sx}, {sy}) is on {deg:.1f} deg ground, over {SLOPE_LIMIT_DEG} deg")
    sites = [dict(id=s["id"], x=x0 + s["xy"][0], y=y0 + s["xy"][1]) for s in scn["sites"]]
    ax, ay, ah = scn["a_start"]
    bx, by, bh = scn["b_start"]
    staged = {"A": getattr(slope, "unmapped", [None])[0] if getattr(slope, "unmapped", None) else None,
              "B": getattr(slope, "unmapped", [None, None])[1] if len(getattr(slope, "unmapped", [])) > 1 else None}
    A = Rover("A", (x0 + ax, y0 + ay, ah), ParticleNav(True, rng(2), craters), slope, rng(1))
    B = Rover("B", (x0 + bx, y0 + by, bh), ParticleNav(True, rng(12), craters), slope, rng(11))
    rims_rng = {"A": rng(3), "B": rng(13)}
    if cam is not None:
        cam.start((A, B))
        cam.attach("A", rng(4), rng(5))
        cam.attach("B", rng(14), rng(15))
        A.sensing = B.sensing = cam.sensors
    if field is not None and lantern is not None and all(lantern.client().can_sign(a) for a in "AB"):
        chain = lantern.run_scope()
    else:
        chain = ChainStandin(salt=f"{scn['key']}-{seed}")
    sensor = IceSensor(field, rng(7)) if field is not None else None

    # Per-rover state machines.
    st = {"A": dict(phase="to", site=0, dwell=0, reads=[]),
          "B": dict(phase="wait" if field is not None else "off", target=None, dwell=0, reads=[])}
    A.set_goal((sites[0]["x"], sites[0]["y"]))
    states = {"A": [], "B": []}
    site_truth = {}
    steps = []
    tail = 0

    def note_state(ag, k, state, objective):
        if not states[ag] or states[ag][-1]["state"] != state or states[ag][-1]["objective"] != objective:
            states[ag].append({"t": round(k * DT_S, 1), "state": state, "objective": objective})

    def phase_label(ag):
        s = st[ag]
        if ag == "A":
            if s["phase"] == "to": return f"to {sites[s['site']]['id']}"
            if s["phase"] == "survey": return f"survey {sites[s['site']]['id']}"
            return "done"
        if s["phase"] == "wait": return "wait for a find"
        if s["phase"] == "goto": return f"to find {s['target']['id']}"
        if s["phase"] == "prospect": return f"prospect find {s['target']['id']}"
        return s["phase"]

    def snapshot(r, rims, moved, reading, instr):
        ag = r.agency
        s = st[ag]
        hz = cam.haz_count.get(ag, {}) if cam is not None else {}
        plan = getattr(r, "local_plan", None)
        return {"true_pose": [round(float(v), 3) for v in r.true],
                "odom_pose": [round(float(v), 3) for v in r.odom],
                "estimate": [round(float(v), 3) for v in r.estimate],
                "sigma": round(float(r.sigma), 3),
                "nav_on": bool(r.nav.fixes_on),
                "rims": np.round(rims, 2).tolist(),
                "mode": s["phase"],
                "role": ROLE[ag],
                "phase": phase_label(ag),
                "site_id": (sites[s["site"]]["id"] if ag == "A" and s["phase"] != "done" and s["site"] < len(sites)
                            else (s["target"]["site_id"] if ag == "B" and s.get("target") else None)),
                "plan": [[round(float(px), 2), round(float(py), 2)] for px, py in plan] if plan and moved else [],
                "instrument": instr,
                "ice_reading": None if reading is None else round(reading, 3),
                "est_error": round(float(np.hypot(*(r.estimate - r.true[:2]))), 3),
                "distance": round(r.distance, 2),
                "fp_frame": cam.last.get(ag) if cam is not None and cam.run_dir is not None else None,
                "lidar_frame": cam.last_lidar.get(ag) if cam is not None else None,
                "lidar_hazards": hz,
                "lidar_hazard_total": int(sum(v for v in hz.values() if isinstance(v, (int, float)))),
                "sees_unmapped_crater": bool(staged[ag] and sees_crater(r, staged[ag])),
                "particles": _particles_log(r.nav)}

    for k in range(MAX_STEPS):
        events, mevents = [], []
        snap = {}
        for r in (A, B):
            ag = r.agency
            s = st[ag]
            moved, reading, instr, rims = False, None, None, np.zeros((0, 2))
            driving = s["phase"] in ("to", "goto")
            if k > 0 and driving:
                d, dh = r.follow()
                seen = cam.shoot(k, r, d > 0) if cam is not None else None
                odo, err = r.odometry(d, dh)
                rims = seen if seen is not None else rims_standin(r.true, craters, rims_rng[ag])
                r.localize(err, d, len(rims), odom=odo, rims=rims)
                moved = d > 0
                if sensor is not None:
                    reading = sensor.read(r.true[0], r.true[1])
            elif k == 0:
                rims = rims_standin(r.true, craters, rims_rng[ag])
                if cam is not None:
                    seen = cam.shoot(k, r, False)
                    if seen is not None:
                        rims = seen

            # ---- Surveyor
            if ag == "A":
                site = sites[s["site"]] if s["site"] < len(sites) else None
                if s["phase"] == "to" and site is not None:
                    dist = math.hypot(site["x"] - r.estimate[0], site["y"] - r.estimate[1])
                    if dist < ARRIVE_M or (dist < BLOCKED_ARRIVE_M and r.goal_blocked()):
                        s["phase"], s["dwell"], s["reads"] = "survey", 0, []
                        note_state("A", k, "INSPECTING", f"survey {site['id']}")
                    else:
                        note_state("A", k, "NAVIGATING_TO_SITE", f"to {site['id']}")
                elif s["phase"] == "survey":
                    instr = instrument(SURVEY_PHASES, s["dwell"], SURVEY_STEPS)
                    if sensor is not None:
                        reading = sensor.read(r.true[0], r.true[1])
                        s["reads"].append(reading)
                    s["dwell"] += 1
                    if s["dwell"] >= SURVEY_STEPS:
                        mean = float(np.mean(s["reads"])) if s["reads"] else 0.0
                        ice = mean >= FIND_THRESHOLD_WT
                        ev = {"type": "site", "agency": "A", "site_id": site["id"], "verdict": "ice" if ice else "dry",
                              "reading": round(mean, 3), "x": round(float(r.estimate[0]), 3),
                              "y": round(float(r.estimate[1]), 3), "sigma": round(float(r.sigma), 3),
                              "on_chain": bool(ice)}
                        site_truth[site["id"]] = {"true_xy": r.true[:2].copy(), "reported_xy": r.estimate.copy(),
                                                  "sigma": float(r.sigma), "reading": mean, "verdict": ev["verdict"]}
                        if ice:
                            data_hash = "0x" + hashlib.sha256(json.dumps([round(v, 4) for v in s["reads"]]).encode()).hexdigest()
                            fid = chain.post_find("A", float(r.estimate[0]), float(r.estimate[1]), float(r.sigma), mean, data_hash)
                            ev["find_id"] = fid
                            ev["tx"] = chain.last_tx
                            ev["data_hash"] = data_hash
                            events.append({"type": "find", "agency": "A", "find_id": fid, "site_id": site["id"],
                                           "x": ev["x"], "y": ev["y"], "sigma": ev["sigma"], "reading": round(mean, 3),
                                           "data_hash": data_hash, "tx": chain.last_tx})
                            note_state("A", k, "PUBLISHING", f"find at {site['id']} on chain")
                        else:
                            note_state("A", k, "PUBLISHING", f"{site['id']} dry: nothing to record")
                        mevents.append(ev)
                        s["site"] += 1
                        if s["site"] < len(sites):
                            nxt = sites[s["site"]]
                            r.set_goal((nxt["x"], nxt["y"]))
                            s["phase"] = "to"
                        else:
                            s["phase"] = "done"
                            note_state("A", k, "IDLE", "survey complete")
                if k == 0:
                    note_state("A", 0, "NAVIGATING_TO_SITE", f"to {sites[0]['id']}")

            # ---- Prospector
            if ag == "B":
                if s["phase"] == "wait":
                    if k == 0:
                        note_state("B", 0, "IDLE", "waiting for a find on chain")
                    open_finds = [f for f in chain.get_finds() if f["agency"] != "B" and f["status"] == "reported"]
                    if open_finds:
                        f = open_finds[0]
                        f = dict(f, site_id=next((sid for sid, tr in site_truth.items()
                                                  if math.hypot(tr["reported_xy"][0] - f["x"], tr["reported_xy"][1] - f["y"]) < 0.5), None))
                        s["target"] = f
                        r.set_goal((f["x"], f["y"]))
                        s["phase"] = "goto"
                        note_state("B", k, "NAVIGATING", f"to find {f['id']} ({f['site_id'] or '?'})")
                        mevents.append({"type": "dispatch", "agency": "B", "find_id": f["id"], "site_id": f["site_id"],
                                        "x": f["x"], "y": f["y"], "read_from": "chain"})
                elif s["phase"] == "goto":
                    f = s["target"]
                    dist = math.hypot(f["x"] - r.estimate[0], f["y"] - r.estimate[1])
                    if dist < ARRIVE_M or (dist < BLOCKED_ARRIVE_M and r.goal_blocked()):
                        s["phase"], s["dwell"], s["reads"] = "prospect", 0, []
                        note_state("B", k, "ANALYZING", f"prospect find {f['id']}")
                elif s["phase"] == "prospect":
                    f = s["target"]
                    instr = instrument(PROSPECT_PHASES, s["dwell"], PROSPECT_STEPS)
                    if instr["name"] in ("drill", "sample_lift", "analyze") and sensor is not None:
                        reading = sensor.read(r.true[0], r.true[1])
                        s["reads"].append(reading)
                    s["dwell"] += 1
                    if s["dwell"] >= PROSPECT_STEPS:
                        reads = s["reads"] or [sensor.read(r.true[0], r.true[1]) for _ in range(CHECK_READS)]
                        mean = float(np.mean(reads))
                        agrees = mean >= FIND_THRESHOLD_WT
                        tx = chain.check_find("B", f["id"], float(r.estimate[0]), float(r.estimate[1]),
                                              float(r.sigma), mean, agrees)
                        events.append({"type": "check", "agency": "B", "find_id": f["id"], "site_id": f["site_id"],
                                       "x": round(float(r.estimate[0]), 3), "y": round(float(r.estimate[1]), 3),
                                       "sigma": round(float(r.sigma), 3), "reading": round(mean, 3),
                                       "agrees": bool(agrees), "tx": tx})
                        site_truth.setdefault(f["site_id"] or "find", {})["check_reading"] = mean
                        site_truth[f["site_id"] or "find"]["check_true_xy"] = r.true[:2].copy()
                        s["phase"] = "done"
                        note_state("B", k, "COMPLETED", f"find {f['id']} {'confirmed' if agrees else 'disputed'}")
            snap[ag] = snapshot(r, rims, moved or k == 0, reading, instr)

        steps.append({"t": round(k * DT_S, 1), "rovers": snap, "events": events, "mission_events": mevents})
        if st["A"]["phase"] == "done" and st["B"]["phase"] in ("done", "off"):
            tail += 1
            if field is None or tail > TAIL_STEPS:
                break
    return steps, chain, site_truth, states, sites


def checkpoints(steps, sites):
    """Where the replay pauses, from the log itself. Each one names the step,
    the time, a label for the UI and one line for whoever is talking."""
    cps = [dict(id="overview", step=0, t=0.0, label="Mission overview", pause=True,
                say=f"Two candidate sites from orbit, {sites[0]['id']} and {sites[1]['id']}. The Surveyor "
                    f"goes to them in order; the Prospector waits for the record.")]

    def first(pred, start=0):
        for k in range(start, len(steps)):
            if pred(k):
                return k
        return None

    def hz(k, ag):
        return steps[k]["rovers"][ag].get("lidar_hazard_total", 0)

    for ag, who in (("A", "Surveyor"), ("B", "Prospector")):
        k0 = first(lambda k: steps[k]["rovers"][ag]["mode"] in ("to", "goto"))
        if k0 is None:
            continue
        # The divert: the step before the lidar first marks the crater its map does not show.
        kd = first(lambda k: steps[k]["rovers"][ag].get("sees_unmapped_crater"), k0)
        if kd is not None and kd > 0:
            cps.append(dict(id=f"divert-{ag}", step=kd - 1, t=steps[kd - 1]["t"], label=f"{who}: crater ahead", pause=True,
                            say=f"The {who} is about to see a crater its map does not show. Play: the lidar "
                                f"marks it and the planned path bends around it."))
    # Arrivals and verdicts.
    for i, s in enumerate(sites):
        ka = first(lambda k: steps[k]["rovers"]["A"]["mode"] == "survey" and steps[k]["rovers"]["A"]["site_id"] == s["id"])
        if ka is not None:
            cps.append(dict(id=f"arrive-{s['id']}", step=ka, t=steps[ka]["t"], label=f"Surveyor at {s['id']}", pause=i == 0,
                            say=f"At {s['id']}. Play: the survey runs, and the verdict goes in the record."))
    kb = first(lambda k: any(e["type"] == "dispatch" for e in steps[k].get("mission_events", [])))
    if kb is not None:
        cps.append(dict(id="dispatch-B", step=kb, t=steps[kb]["t"], label="Prospector dispatched", pause=True,
                        say="The Prospector read the find from the chain, not from the Surveyor, and heads straight for it."))
    kp = first(lambda k: steps[k]["rovers"]["B"]["mode"] == "prospect")
    if kp is not None:
        cps.append(dict(id="arrive-B", step=kp, t=steps[kp]["t"], label="Prospector at the find", pause=True,
                        say="Play: drill, core, analyse, and the check goes on chain. Then open Monadscan."))
    cps.append(dict(id="end", step=len(steps) - 1, t=steps[-1]["t"], label="End: the record on Monad", pause=True,
                    say="Every hash in the feed is a real transaction on Monad testnet. Open one."))
    cps.sort(key=lambda c: c["step"])
    return cps


def main():
    ap = argparse.ArgumentParser(description="Run the demo mission and write its log.")
    ap.add_argument("--out", default=str(RUNS_DIR))
    ap.add_argument("--stamp", default=None, help="run_id suffix (default: now); reuse one to overwrite")
    ap.add_argument("--no-render", action="store_true", help="logic only: no frames, no lidar, stand-in rims")
    ap.add_argument("--no-vision", action="store_true", help="render, but navigate on stand-in rims (lidar still on)")
    ap.add_argument("--frame-every", type=int, default=1)
    ap.add_argument("--display", choices=("full", "detail", "off"), default="full")
    ap.add_argument("--sub-frame-m", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--no-chain", action="store_true", help="use the chain stand-in even if keys exist")
    args = ap.parse_args()
    global lantern
    if args.no_chain:
        lantern = None

    scn = MISSION
    t = time.perf_counter()
    tpath = default_path()
    terrain = load_terrain(tpath)
    height, craters, x0, y0, cell = terrain
    # With the lidar on, the a-priori map is the orbital one and the sensors find
    # the rest. Without it (--no-render, --no-vision) the rover has no way to
    # see a crater the map hides, so it plans on the true ground instead.
    sensors_on = not args.no_render and not args.no_vision
    ax, ay, _ = scn["a_start"]
    bx, by, _ = scn["b_start"]
    s1 = scn["sites"][0]["xy"]
    s2 = scn["sites"][1]["xy"]
    stA = staged_crater(craters, (x0 + ax, y0 + ay), (x0 + s1[0], y0 + s1[1]))
    stB = staged_crater(craters, (x0 + bx, y0 + by), (x0 + s2[0], y0 + s2[1]))
    unmapped = [c for _, c in (stA, stB) if c] if sensors_on else []
    if sensors_on and (stA is None or stB is None):
        raise SystemExit(f"no crater to stage on a straight leg (A: {stA}, B: {stB}); move a site or a start")
    slope = UnmappedCraterSlopeMap(height, x0, y0, cell, unmapped=unmapped)
    print(f"terrain {Path(tpath).name} in {time.perf_counter() - t:.1f}s; "
          + (f"unmapped craters: A's at {stA[0]:.0f} m along its first leg ({stA[1][2]:.1f} m wide), "
             f"B's at {stB[0]:.0f} m along ({stB[1][2]:.1f} m wide)" if sensors_on else "no sensors, so every crater is mapped"))
    out = Path(args.out)
    stamp = args.stamp or datetime.now().strftime("%Y%m%d-%H%M")
    scene = display = shared = None
    if not args.no_render:
        from sim.scene import Scene
        t = time.perf_counter()
        scene = Scene(height, craters, x0, y0, cell, display=args.display, display_dir=TERRAIN_DIR / "display")
        display = scene.display
        if args.sub_frame_m > 0 and args.frame_every == 1:
            shared = scene.share(out / "_shared")
            np.save(shared / "craters.npy", np.asarray(craters))
        print(f"scene built in {time.perf_counter() - t:.1f}s (display {display})")
    sensors = scene is not None and not args.no_vision

    t0 = time.perf_counter()
    cam1 = LiveCamera(scene, None, args.frame_every, sensors=sensors) if scene is not None else None
    a_steps, _, _, _, _ = simulate(scn, terrain, slope, field=None, cam=cam1)
    routes = [[s["rovers"]["A"]["true_pose"][:2] for s in a_steps]]
    field = place_ice(x0, y0, scn["sites"], np.random.default_rng([scn["seed"], 99]), routes)
    print(f"pass 1: Surveyor route of {len(a_steps)} steps; ice placed ({len(field.deposits)} deposits)")

    run_id = f"{scn['key']}-{stamp}"
    run_dir = out / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    cam = LiveCamera(scene, run_dir, args.frame_every, sensors=sensors) if scene is not None else None
    steps, chain, truth, states, sites = simulate(scn, terrain, slope, field=field, cam=cam)
    finds = chain.get_finds()
    verdicts = {sid: tr.get("verdict") for sid, tr in truth.items() if "verdict" in tr}
    print(f"mission: {len(steps)} steps, {steps[-1]['t'] / 3600:.2f} h simulated; verdicts {verdicts}; "
          f"finds {[(f['id'], f['status']) for f in finds]}")
    assert verdicts.get("S1") == "dry", f"S1 should be dry, got {verdicts.get('S1')}"
    assert verdicts.get("S2") == "ice", f"S2 should hold ice, got {verdicts.get('S2')}"
    assert finds and finds[0]["status"] == "confirmed", f"the find should end confirmed: {finds}"
    if cam is not None and cam.times:
        print(f"  {len(cam.times)} live frames, median {1e3 * np.median(cam.times):.0f} ms"
              + (f"; vision {1e3 * np.median(cam.t_vision):.0f} ms, lidar {1e3 * np.median(cam.t_lidar):.0f} ms" if cam.t_vision else ""))
    cps = checkpoints(steps, sites)
    for c in cps:
        print(f"  checkpoint {c['id']:>10} step {c['step']:4d} t {c['t']:7.0f}s  {c['label']}")
    if not any(c["id"] == "divert-A" for c in cps):
        print("  WARNING: no divert for the Surveyor (no lidar hazard rise while driving); move S1 or A's start")

    jobs = sub_frame_jobs(steps, run_dir, args.sub_frame_m) if shared is not None else []
    if jobs:
        t = time.perf_counter()
        try:
            import psutil  # noqa: F401  (the spine's render pool sizes itself with it)
            posed = dict(cam._posed) if cam is not None else {}
            cam = scene = None
            gc.collect()
            rt, workers, mem = render_sub_frames(jobs, shared, terrain, display, args.workers)
            print(f"display frames: {len(rt)} in {time.perf_counter() - t:.0f}s on {workers} workers")
        except ImportError:
            print(f"  psutil missing: rendering {len(jobs)} display frames in this process")
            rt = spine.render_jobs(scene, dict(cam._posed) if cam is not None else {}, jobs)
            print(f"display frames: {len(rt)} in {time.perf_counter() - t:.0f}s, median {1e3 * np.median(rt):.0f} ms")
    if shared is not None:
        shutil.rmtree(shared, ignore_errors=True)

    standins = {"ice_sensor": IceSensor.STANDIN, "survey": "survey and prospecting instruments: the ice sensor read over a dwell; "
                "instrument phase names are for the display (assumed sequence)"}
    if not sensors:
        standins["rims"] = RIMS_STANDIN
        standins["lidar"] = "lidar: off (--no-render), so no sensor hazards and no divert"
    if not (lantern is not None and hasattr(chain, "run_log_chain")):
        standins["chain"] = ChainStandin.STANDIN
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
        "planner": PLANNER_STANDIN + (f"; a-priori map: the slope map with {len(unmapped)} craters left unmapped, as a "
                                      f"{ORBITAL_RES_M:.0f} m orbital DEM would miss them; the lidar (sim/lidar.py) finds them "
                                      f"and the local planner bends around them" if sensors_on
                                      else "; a-priori map: the true 0.25 m ground (no sensors in this run)"),
        "vision": Vision.NAME if sensors else None,
        "lidar": "sim/lidar.py: forward lidar simulated from the terrain (no source specifies one); hazards: craters, rocks, slopes" if sensors else None,
        "standins": standins,
        "dt_s": round(DT_S, 2),
        "display": None if args.no_render else display,
        "frames": FRAMES_NOTE,
        "roles": ROLE,
        "mission": {
            "sites": [{"id": s["id"], "x": round(s["x"], 2), "y": round(s["y"], 2), "radius": DEPOSIT_RADIUS_M,
                       "order": i + 1, "verdict": verdicts.get(s["id"]), "kind": "orbital candidate"}
                      for i, s in enumerate(sites)],
            "checkpoints": cps,
            "unmapped_craters": [{"x": round(c[0], 2), "y": round(c[1], 2), "diameter": round(c[2], 1), "for": ag}
                                 for ag, c in zip("AB", unmapped)],
            "unmapped_note": f"these craters are left out of the planning map on purpose, as a {ORBITAL_RES_M:.0f} m orbital DEM "
                             f"would miss them; the rover finds each one with its lidar and diverts (sim/rover.py, sim/lidar.py)",
            "survey_phases": SURVEY_PHASES, "prospect_phases": PROSPECT_PHASES,
            "dry_sites": "a dry site is logged as a `site` event with verdict dry; nothing is posted on chain for it",
        },
        "satellite_sites": [{"id": s["id"], "x": round(s["x"], 2), "y": round(s["y"], 2), "kind": "orbital candidate"} for s in sites],
        "states": states,
        "steps": steps,
        "final_finds": finds,
    }
    with open(run_dir / "log.json", "w", encoding="utf8") as f:
        json.dump(log, f, separators=(",", ":"))
    index = [{"run_id": run_id, "label": scn["label"], "dir": run_id, "status": finds[0]["status"]}]
    with open(out / "index.json", "w", encoding="utf8") as f:
        json.dump(index, f, indent=1)
    print(f"wrote {run_dir} in {time.perf_counter() - t0:.0f}s; index lists {run_id}")


if __name__ == "__main__":
    main()
