"""Mission: two rovers run whole sorties on the real terrain, and store it all.

This is the rover's operations layer. It sits on top of the teammates'
parts and owns none of them:

  terrain      sim/terrain.py (Joseph): load_terrain()
  routes       Calvin's Planner + Drive, found by sim/planner_link.py
  resources    sim/resources.py: hidden deposits, target board
  science      sim/science.py: drill, verdict, record
  health       sim/health.py: battery, heaters, comms, faults
  storage      sim/mission_store.py: every output as JSON

What each rover does, leg after leg, until it docks:

  1. Health check. Enough battery for the drive home plus the 25% reserve?
     Inside the 22 h sortie? If not, go home.
  2. Choose a target. Score every open target by value and distance, keep
     those it can afford (there, the stop, home, reserve), take the best.
     Checking another agency's find is worth the most: it is what makes the
     shared map trustworthy.
  3. Plan the route with the teammate's planner and drive it, taking a
     neutron spectrometer reading every few metres. A strong reading away
     from every known target becomes a new target the rover found itself.
  4. Science stop: park, arm out, drill, read the core. A stalled drill is
     retried once. The record goes to the store, with the chain calls it
     needs in the outbox, queued until Earth is in view.
  5. An ice find becomes a target for the OTHER agency to check.

Stand-ins, labelled in mission.json and the run log: the navigation filter
(step 3 is not built; the estimate is the true pose plus a slowly wandering
error at a fixed sigma), and the planner if no teammate planner is found.

Usage (from the repo root):
    export LANTERN_DATA="$PWD/lantern_data"
    python sim/mission.py run                  # both rovers, full sorties
    python sim/mission.py run --planner standin
    python sim/mission.py list                 # stored missions
    python sim/mission.py push --dry-run       # the outbox, as chain calls
    python sim/mission.py push                 # post it (needs sim/chain.py and .env)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence as EV  # noqa: E402
import health as H  # noqa: E402
import mission_store as MS  # noqa: E402
import planner_link  # noqa: E402
import resources  # noqa: E402
import science  # noqa: E402
import terrain  # noqa: E402  (Joseph's step 2; read only)

# ---- ASSUMED parameters ----------------------------------------------------
SURVEY_EVERY_M = 5.0          # neutron reading spacing while driving
TELEMETRY_EVERY_M = 10.0      # telemetry sample spacing
SURVEY_FLAG_WT = 1.0          # a reading this strong flags a candidate (the [PB] p. 9 threshold)
SURVEY_FLAG_CLEAR_M = 25.0    # ...if no target is already this close
RESOURCE_CELL_M = 20.0        # resource map cell size
VALUE = {"water_ice": 1.0, "ilmenite": 0.6, "check": 1.3}
DIST_SCALE_M = 300.0          # score = value / (1 + distance / DIST_SCALE_M)
DOCK_FRAC = {"A": (0.12, 0.15), "B": (0.88, 0.85)}   # dock positions, fraction of the map
START_SOC = {"A": 1.0, "B": 0.42}  # B starts part charged, so the energy rule shows
MAX_LEGS = 14                 # per rover, a guard against loops
RUN_LOG_DT_S = 120.0          # run log step spacing, simulated seconds

# STAND-IN navigation filter (step 3 is someone else's and not built yet):
NAV_SIGMA_M = {"A": 0.8, "B": 0.9}   # reported sigma
NAV_CORR = 0.98                      # error is AR(1) per 0.5 m step, std NAV_SIGMA_M
# Cauligi et al. 2023 sec. 1: odometry drifts about 2% of distance driven
# (the same value as sim/standins.py ODOM_DRIFT). Applied as a scale bias.
ODOM_DRIFT = 0.02
ODOM_HEADING_NOISE_RAD = 0.0005   # ASSUMED, per 0.5 m step, as in sim/rover.py on step-4-spine
NAV_STANDIN = ("navigation: true pose plus a slowly wandering error with std = sigma "
               "(STAND-IN for the step 3 particle filter)")
# Step 3, the real one when present (sim/nav.py, Calvin's ShadowNav particle
# filter), fed the rim-point stand-in (sim/standins.py) every NAV_EVERY_M.
try:
    import nav as _nav
    import standins as _standins
    PARTICLE_NAV = _nav.ParticleNav
except ImportError:   # a branch without step 3: fall back to the labelled stand-in
    PARTICLE_NAV = None
NAV_EVERY_M = 2.0     # ASSUMED, as run_demo.py: one see-locate step every 2 m

SCIENCE_WH = H.Health.science_wh([{"name": n, "dur": d} for n, d in science.PHASES])
SCIENCE_S = sum(d for _, d in science.PHASES)


class Rover:
    def __init__(self, agency, dock, rng, craters=None):
        self.agency, self.dock = agency, dock
        self.pose = (dock[0], dock[1], 0.0)
        self.err = np.zeros(2)
        self.sigma = NAV_SIGMA_M[agency]
        self.pf = None
        if PARTICLE_NAV is not None and craters is not None:
            # The real filter: it never sees the true pose, only odometry and rims.
            self.pf = PARTICLE_NAV(True, rng, craters)
            self.pf.start(np.array(dock[:2]) + self.pf.err, 0.0)
            self.sigma = self.pf.sigma
            self.craters = craters
            self._est = np.array(dock[:2]) + self.pf.err
            self._pend = [0.0, 0.0]   # odometry (distance, dheading) since the last filter step
        self.t = 0.0
        self.start_soc = START_SOC[agency]
        self.health = H.Health(agency, battery_wh=START_SOC[agency] * H.BATTERY_WH)
        self.odom = list(self.pose)   # dead reckoning from the wheels alone
        # (t, x, y, h, est_x, est_y, odom_x, odom_y, odom_h, sigma, soc)
        self.track = [(0.0, *self.pose, *self.estimate, *self.odom, self.sigma, self.health.soc)]
        self.odo = 0.0
        self.next_survey = SURVEY_EVERY_M
        self.next_tel = 0.0
        self.mode = "docked"
        self.done = False
        self.legs = 0
        self.skipped = set()
        self.stats = dict(targets=0, finds=0, checks=0, dry=0, trace=0, faults=0,
                          discovered=0, unreachable=0)
        self.rng = rng

    @property
    def estimate(self):
        if self.pf is not None:
            return (float(self._est[0]), float(self._est[1]))
        return (self.pose[0] + self.err[0], self.pose[1] + self.err[1])

    def nav_step(self, d=0.0, dh=0.0):
        if self.pf is None:
            s = self.sigma * math.sqrt(1 - NAV_CORR ** 2)
            self.err = NAV_CORR * self.err + self.rng.normal(0, s, 2)
            return
        # Between filter steps, dead-reckon the estimate on the odometry.
        d_o, dh_o = d * (1 + ODOM_DRIFT), dh
        self._pend[0] += d_o
        self._pend[1] += dh_o
        h = self.pf.heading + self._pend[1]
        self._est = self._est + d_o * np.array([math.cos(h), math.sin(h)])
        if self._pend[0] >= NAV_EVERY_M:
            rims = _standins.rims_standin(self.pose, self.craters, self.rng)
            est, self.sigma = self.pf.step(tuple(self._pend), rims)
            self._est = np.asarray(est, float)
            self._pend = [0.0, 0.0]

    def odom_step(self, d, dh):
        """Wheel odometry: the true turn plus heading noise, the true
        distance scaled by the 2% drift. What the rover would think with no
        crater fixes; shown on the map next to the estimate."""
        self.odom[2] += dh + self.rng.normal(0, ODOM_HEADING_NOISE_RAD)
        self.odom[0] += d * (1 + ODOM_DRIFT) * math.cos(self.odom[2])
        self.odom[1] += d * (1 + ODOM_DRIFT) * math.sin(self.odom[2])

    def tracks(self):
        a = np.array(self.track)
        return a[:, 1:3], a[:, 4:6]   # true xy, estimated xy


class Mission:
    def __init__(self, seed=11, planner="auto", terrain_path=None, mission_id=None, site_params=None):
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.terrain_path = Path(terrain_path or terrain.default_path())
        self.height, self.craters, self.x0, self.y0, self.cell = terrain.load_terrain(self.terrain_path)
        self.size = self.height.shape[0] * self.cell
        self.link = planner_link.RouteLink(self.height, self.x0, self.y0, self.cell, prefer=planner)
        self.deposits, self.board = resources.build_site(
            self.height, self.craters, self.x0, self.y0, self.cell, site_params or resources.Params(seed=seed))
        self.store = MS.MissionStore(mission_id)
        self.outbox = []
        self.survey = {}   # resource map accumulator: (i, j) -> [sum, n]
        self.rovers = {}
        for ag, (fx, fy) in DOCK_FRAC.items():
            dock = self.link.free_near(self.x0 + fx * self.size, self.y0 + fy * self.size)
            self.rovers[ag] = Rover(ag, dock, self.rng, self.craters)
        self.nav_label = PARTICLE_NAV.NAME + "; " + _standins.RIMS_STANDIN if PARTICLE_NAV else NAV_STANDIN

    # ---- helpers -------------------------------------------------------
    def ev(self, r, etype, **kw):
        row = dict(t=round(r.t, 1), agency=r.agency, type=etype, **kw)
        self.store.event(row)
        return row

    def tel(self, r):
        x, y, h = r.pose
        ex, ey = r.estimate
        self.store.telemetry(dict(t=round(r.t, 1), agency=r.agency, x=round(x, 2), y=round(y, 2),
                                  heading=round(h, 4), est_x=round(ex, 2), est_y=round(ey, 2),
                                  sigma=r.sigma, odom_x=round(r.odom[0], 2), odom_y=round(r.odom[1], 2),
                                  odometer_m=round(r.odo, 1), **r.health.snapshot(r.t, r.mode)))

    def home_dist(self, r):
        return math.dist(r.estimate, r.dock)

    # ---- one leg ---------------------------------------------------------
    def choose_target(self, r):
        best, best_s = None, -1.0
        ex, ey = r.estimate
        for t in self.board.open():
            if t["id"] in r.skipped or t.get("finder_agency") == r.agency:
                continue
            v = VALUE["check"] if t["purpose"] == "check" else VALUE[t["kind"]]
            if "survey_reading" in t:
                v *= min(1.5, t["survey_reading"] / SURVEY_FLAG_WT)
            d = math.hypot(t["x"] - ex, t["y"] - ey)
            ok, _, _ = r.health.can_afford(d * H.DETOUR_FACTOR, SCIENCE_WH,
                                           math.dist((t["x"], t["y"]), r.dock))
            leg_s = d * H.DETOUR_FACTOR / H.DRIVE_SPEED_M_S + SCIENCE_S
            home_s = math.dist((t["x"], t["y"]), r.dock) * H.DETOUR_FACTOR / H.DRIVE_SPEED_M_S
            if not ok or r.t + leg_s + home_s > H.SORTIE_LIMIT_S:
                continue
            s = v / (1 + d / DIST_SCALE_M)
            if s > best_s:
                best, best_s = t, s
        return best, best_s

    def leg(self, r):
        r.legs += 1
        if r.legs > MAX_LEGS:
            return self.go_home(r, "leg limit")
        if r.health.must_return(self.home_dist(r)):
            return self.go_home(r, "battery: drive home plus reserve")
        tgt, score = self.choose_target(r)
        if tgt is None:
            left = [t for t in self.board.open() if t.get("finder_agency") != r.agency]
            return self.go_home(r, "every target it may take is done" if not left else
                                f"{len(left)} targets left, none fits the battery and the sortie time")
        self.board.claim(tgt["id"], r.agency)
        try:
            reuse = None
            if tgt.get("route_cid"):
                # A check: fetch the finder's completed route from evidence
                # (verified against its CID), and reuse what is still safe.
                ref = self.store.ev.get(tgt["route_cid"])
                route, reuse = self.link.reuse(r.pose[:2], ref["points"], (tgt["x"], tgt["y"]))
                self.ev(r, "route_reuse", target_id=tgt["id"], route_cid=tgt["route_cid"], **reuse)
            else:
                route = self.link.plan(r.pose[:2], (tgt["x"], tgt["y"]))
        except planner_link.NoRoute as e:
            tgt["status"] = "unreachable"
            r.stats["unreachable"] += 1
            self.ev(r, "unreachable", target_id=tgt["id"], reason=str(e))
            return
        length = planner_link.route_length(route)
        ok, need, have = r.health.can_afford(length, SCIENCE_WH, math.dist((tgt["x"], tgt["y"]), r.dock))
        if not ok:
            self.board.release(tgt["id"])
            r.skipped.add(tgt["id"])
            self.ev(r, "skip_energy", target_id=tgt["id"], need_wh=round(need), have_wh=round(have))
            return
        r.mode = "to target"
        self.ev(r, "depart", target_id=tgt["id"], kind=tgt["kind"], purpose=tgt["purpose"],
                route_m=round(length, 1), waypoints=len(route), score=round(score, 3),
                planner=self.link.name)
        if not self.drive(r, route, abort_on_low=True):
            self.board.release(tgt["id"])
            return self.go_home(r, "battery fell to the return threshold on the way")
        if math.dist(r.pose[:2], (tgt["x"], tgt["y"])) > tgt["radius"] + 1.0:
            self.board.release(tgt["id"])
            r.skipped.add(tgt["id"])
            self.ev(r, "missed", target_id=tgt["id"])
            return
        self.ev(r, "arrive", target_id=tgt["id"])
        self.science_stop(r, tgt)

    def science_stop(self, r, tgt):
        r.mode = "science"
        tries = 0
        while True:
            tries += 1
            if r.rng.random() >= H.DRILL_STALL_P:
                break
            r.stats["faults"] += 1
            stall_s = 120.0  # ASSUMED: time lost backing the bit out
            r.t += stall_s
            r.health.spend(r.health.idle_wh(stall_s) + H.DRILL_W * stall_s / 3600)
            self.ev(r, "fault", target_id=tgt["id"], what="drill stalled", attempt=tries)
            if tries >= H.DRILL_TRIES:
                tgt["status"] = "failed"
                self.ev(r, "drill_failed", target_id=tgt["id"],
                        action="target given up; rover moves on")
                return
        true_xy, est_xy = r.tracks()
        rec = science.run_science(r.agency, tgt, self.deposits, r.t, r.pose, r.estimate,
                                  r.sigma, est_xy, true_xy, seed=int(r.rng.integers(1 << 31)),
                                  attempts=tries)
        r.t += SCIENCE_S
        r.health.spend(SCIENCE_WH)
        if tgt["purpose"] == "check":
            rec["checks_record"] = str(tgt["find_id"]).removeprefix(MS.FIND_REF)
        # The route evidence is the last ROUTE_LENGTH_M actually driven, by the
        # rover's own estimate, before thinning.
        tail, _ = science.last_route(est_xy, spacing=0.0)
        rec = self.store.publish(rec, tail, parent_report_cid=tgt.get("report_cid"))
        self.board.complete(tgt["id"], rec["record_id"])
        new = MS.outbox_items_for(rec, r.health)
        self.store.queue(new)
        self.outbox.extend(new)
        v = rec["result"]["verdict"]
        r.stats["targets"] += 1
        if tgt["purpose"] == "check":
            r.stats["checks"] += 1
        elif v == "ice":
            r.stats["finds"] += 1
        else:
            r.stats[v] += 1
        self.ev(r, "science", record_id=rec["record_id"], target_id=tgt["id"], verdict=v,
                reading=rec["result"]["reading"], summary=rec["result"]["summary"],
                chain_call=rec["chain"]["call"], queued_until=new[-1]["earliest_post_t"] if new else None,
                phases=rec["phases"])
        if v == "ice" and tgt["purpose"] != "check":
            find = dict(id=MS.FIND_REF + rec["record_id"], agency=r.agency,
                        x=rec["position"]["x"], y=rec["position"]["y"], sigma=rec["position"]["sigma"],
                        report_cid=rec["evidence"]["report"]["cid"],
                        route_cid=rec["evidence"]["route"]["cid"])
            chk = self.board.add_check_target(find)
            self.ev(r, "check_requested", target_id=chk["id"], for_agency="other agency")
        self.tel(r)

    def drive(self, r, route, abort_on_low, on_step=None):
        """Drive a route. True when done, False on a battery abort, "stopped"
        when on_step(r) asked to stop (a new obstacle, say)."""
        prev = r.pose
        for pose in self.link.drive(route, r.pose):
            d = math.dist(pose[:2], prev[:2])
            dh = (pose[2] - prev[2] + math.pi) % (2 * math.pi) - math.pi
            prev = pose
            r.pose = pose
            r.odo += d
            r.t += d / H.DRIVE_SPEED_M_S
            r.health.spend(H.Health.drive_wh(d))
            r.nav_step(d, dh)
            r.odom_step(d, dh)
            r.track.append((r.t, *r.pose, *r.estimate, *r.odom, r.sigma, r.health.soc))
            if on_step is not None and on_step(r):
                return "stopped"
            if r.odo >= r.next_survey:
                r.next_survey += SURVEY_EVERY_M
                self.take_survey(r)
            if r.odo >= r.next_tel:
                r.next_tel += TELEMETRY_EVERY_M
                self.tel(r)
            if abort_on_low and r.health.must_return(self.home_dist(r)):
                self.ev(r, "abort", reason="battery at the return threshold",
                        soc=round(r.health.soc, 3))
                return False
        return True

    def take_survey(self, r):
        reading = resources.neutron_reading(self.deposits, r.pose[0], r.pose[1], r.rng)
        ex, ey = r.estimate
        self.store.survey(dict(t=round(r.t, 1), agency=r.agency, x=round(ex, 2), y=round(ey, 2),
                               sigma=r.sigma, wt_pct=round(reading, 3),
                               instrument="neutron spectrometer (simulated)"))
        k = (int((ey - self.y0) // RESOURCE_CELL_M), int((ex - self.x0) // RESOURCE_CELL_M))
        acc = self.survey.setdefault(k, [0.0, 0, 0.0])
        acc[0] += reading
        acc[1] += 1
        acc[2] = max(acc[2], reading)
        if reading >= SURVEY_FLAG_WT and not self.board.near(ex, ey, SURVEY_FLAG_CLEAR_M):
            t = self.board.add_survey_target(ex, ey, reading, r.agency, r.t)
            r.stats["discovered"] += 1
            self.ev(r, "new_target", target_id=t["id"], reading=round(reading, 3),
                    x=round(ex, 1), y=round(ey, 1), source="onboard neutron survey")

    def go_home(self, r, reason):
        r.mode = "returning"
        self.ev(r, "return", reason=reason, soc=round(r.health.soc, 3))
        try:
            route = self.link.plan(r.pose[:2], r.dock)
            self.drive(r, route, abort_on_low=False)
            r.mode = "docked"
            self.ev(r, "docked", soc=round(r.health.soc, 3),
                    reserve_ok=r.health.soc >= H.RESERVE_FRAC)
        except planner_link.NoRoute as e:
            r.mode = "stranded"
            self.ev(r, "stranded", reason=str(e))
        self.tel(r)
        r.done = True

    # ---- run -------------------------------------------------------------
    def run(self):
        t0 = time.perf_counter()
        for r in self.rovers.values():
            self.ev(r, "start", dock=[round(v, 1) for v in r.dock], soc=round(r.health.soc, 3))
            self.tel(r)
        # Round robin by mission clock, so the rovers interleave the way
        # they would in time, and B can check A's find after it is made.
        while True:
            live = [r for r in self.rovers.values() if not r.done]
            if not live:
                break
            with self.store.db.batch():   # one SQLite transaction per leg
                self.leg(min(live, key=lambda r: r.t))
        self.finish(time.perf_counter() - t0)
        return self.store

    # ---- outputs ---------------------------------------------------------
    def resource_map(self):
        n = int(math.ceil(self.size / RESOURCE_CELL_M))
        mean = [[None] * n for _ in range(n)]
        peak = [[None] * n for _ in range(n)]
        count = [[0] * n for _ in range(n)]
        for (i, j), (s, c, m) in self.survey.items():
            if 0 <= i < n and 0 <= j < n:
                mean[i][j], peak[i][j], count[i][j] = round(s / c, 3), round(m, 3), c
        return {"what": "neutron spectrometer survey, binned by the rover's estimated position",
                "unit": "wt% water-equivalent hydrogen, top 1 m", "cell_m": RESOURCE_CELL_M,
                "x0": self.x0 - self.cell / 2, "y0": self.y0 - self.cell / 2, "n": n,
                "rows": "row i covers y0 + i*cell_m (rows run north)",
                "mean": mean, "peak": peak, "count": count}

    def run_log(self):
        """The run in docs/handoffs.md "Run log" format, for the web viewer."""
        end = max(r.t for r in self.rovers.values())
        times = np.arange(0.0, end + RUN_LOG_DT_S, RUN_LOG_DT_S)
        per = {}
        for ag, r in self.rovers.items():
            a = np.array(r.track)
            idx = np.clip(np.searchsorted(a[:, 0], times, side="right") - 1, 0, len(a) - 1)
            per[ag] = a[idx]
        events = self.store.db.events(self.store.id, "science")
        # Finds are numbered 1, 2, ... in the order they were made, as the
        # contract would number them; find_refs maps each back to its record.
        find_no = {}
        steps = []
        for k, t in enumerate(times):
            rovers = {}
            for ag, a in per.items():
                row = a[k]
                rovers[ag] = {"true_pose": [round(v, 3) for v in row[1:4]],
                              "odom_pose": [round(v, 3) for v in row[6:9]],
                              "estimate": [round(row[4], 3), round(row[5], 3)],
                              "sigma": round(float(row[9]), 3), "nav_on": True, "rims": [], "fp_frame": None}
            evs, sci = [], []
            for e in events:
                if t <= e["t"] < t + RUN_LOG_DT_S and e["type"] == "science":
                    rec = self.store.load_report(e["record_id"])
                    p, c = rec["position"], rec["chain"]
                    refs = {"report_cid": rec["evidence"]["report"]["cid"],
                            "route_cid": rec["evidence"]["route"]["cid"]}
                    # Every stop, dry ones too, with phases for the animation.
                    # Its own key, so a viewer that knows only find and check
                    # events is not confused by it.
                    sci.append(science.run_log_event(rec))
                    if c["call"] == "post_find":
                        find_no[rec["record_id"]] = len(find_no) + 1
                        evs.append({"type": "find", "agency": rec["agency"],
                                    "find_id": find_no[rec["record_id"]], "x": p["x"],
                                    "y": p["y"], "sigma": p["sigma"],
                                    "reading": rec["result"]["reading"], "tx": None,
                                    "data_hash": rec["data_hash"],
                                    "record_id": rec["record_id"], **refs})
                    elif c["call"] == "check_find":
                        ref = str(c["args"]["find_id"]).removeprefix(MS.FIND_REF)
                        evs.append({"type": "check", "agency": rec["agency"],
                                    "find_id": find_no.get(ref, c["args"]["find_id"]),
                                    "x": p["x"], "y": p["y"],
                                    "sigma": p["sigma"], "reading": rec["result"]["reading"],
                                    "agrees": c["args"]["agrees"], "tx": None,
                                    "record_id": rec["record_id"], **refs})
            step = {"t": round(float(t), 1), "rovers": rovers, "events": evs}
            if sci:
                step["science"] = sci
            steps.append(step)
        return {
            "run_id": self.store.id,
            "label": "Science mission: survey, drill, find, check",
            "terrain": {"file": self.terrain_path.name, "x0": self.x0, "y0": self.y0, "cell": self.cell},
            "chain": {"chain_id": None, "contract": None, "explorer": None,
                      "note": "not posted yet: see outbox.json; tx fills in after push"},
            "truth": dict(resources.as_truth(self.deposits), ice_units="wt% water",
                          find_threshold_wt=science.ICE_THRESHOLD_WT_PCT),
            # Named parts, as run_demo.py writes them and web/js/data.js reads them.
            "standins": dict(
                navigation=self.nav_label,
                chain="chain: not posted yet; the calls wait in outbox.json, so there are no tx hashes",
                **({} if self.link.real else {"planner": self.link.name})),
            "steps": steps,
        }

    def storage_stats(self):
        """Where the bytes went, per layer: the numbers behind 'efficient'."""
        recs = self.store.reports()
        refs = [r["evidence"] for r in recs]
        driven = sum(e["route_points_driven"] for e in refs)
        stored = sum(e["route_points_stored"] for e in refs)
        ev_bytes = sum(e["report"]["bytes"] + e["route"]["bytes"] for e in refs)
        finds = sum(1 for r in recs if r["chain"]["call"] == "post_find")
        checks = sum(1 for r in recs if r["chain"]["call"] == "check_find")
        return {
            "chain": {
                "finds": finds, "checks": checks,
                # Lantern.sol Find: agency 20 + x 8 + y 8 + sigma 8 + reading 4
                # + dataHash 32 + time 8 = 88 bytes of fields per find.
                "bytes_of_fields_per_find": 88,
                "what": "position, sigma, reading, and the report's keccak256 (which is also its CID digest)",
            },
            "evidence": {
                "objects": 2 * len(refs), "bytes": ev_bytes, "backend": self.store.ev.storage,
                "route_points_driven": driven, "route_points_stored": stored,
                "route_thinning": f"{driven} -> {stored} points ({driven / max(stored, 1):.0f}x), "
                                  f"each within max({EV.ROUTE_TOLERANCE_M} m, its rover sigma)",
            },
            "cache": dict(path=str(self.store.db.path), **self.store.db.counts(self.store.id)),
        }

    def extra_manifest(self):
        return {}

    def finish(self, wall_s):
        self.store.write("targets.json", self.board.to_list())
        self.store.write("HIDDEN_TRUTH.json", {
            "note": "What no rover may know. For the viewer's labelled reveal only.",
            "deposits": [d.__dict__ for d in self.deposits]})
        self.store.write("resource_map.json", self.resource_map())
        self.store.commit()
        self.store.export_outbox()
        self.store.write("run_log.json", self.run_log())
        rovers = {}
        for ag, r in self.rovers.items():
            true_xy, _ = r.tracks()
            rovers[ag] = dict(
                dock=[round(v, 1) for v in r.dock], end_mode=r.mode,
                distance_m=round(r.odo, 1), duration_s=round(r.t, 1),
                speed_made_good_m_s=round(r.odo / r.t, 4) if r.t else 0.0,
                speed_target_m_s=H.TARGET_SPEED_MADE_GOOD_M_S,
                battery_start_soc=r.start_soc, battery_end_soc=round(r.health.soc, 3),
                reserve_kept=r.health.soc >= H.RESERVE_FRAC, **r.stats)
        manifest = {
            "schema": MS.SCHEMA, "mission_id": self.store.id,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": self.seed,
            "terrain": {"file": str(self.terrain_path.name), "x0": self.x0, "y0": self.y0,
                        "cell": self.cell, "size_m": self.size},
            "planner": {"name": self.link.name, "real": self.link.real},
            "navigation": self.nav_label,
            "rovers": rovers,
            "targets": {s: sum(1 for t in self.board.targets if t["status"] == s)
                        for s in ("open", "claimed", "sampled", "failed", "unreachable")},
            "outbox": {"items": len(self.outbox),
                       "calls": {c: sum(1 for i in self.outbox if i["call"] == c)
                                 for c in ("post_safety_zone", "post_find", "check_find")}},
            "storage": self.storage_stats(),
            "files": sorted(p.name for p in self.store.dir.iterdir()),
            "wall_seconds": round(wall_s, 1),
        }
        manifest.update(self.extra_manifest())
        self.store.write("mission.json", manifest)
        self.store.close()
        try:
            self.draw()
        except Exception as e:  # a picture must never lose the mission data
            print(f"map.png not drawn: {e}")

    def draw(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle

        s = 8
        h = self.height[::s, ::s]
        gy, gx = np.gradient(h, self.cell * s)
        shade = np.clip(0.55 - 0.35 * gx - 0.2 * gy, 0, 1)
        ext = (self.x0, self.x0 + self.size, self.y0, self.y0 + self.size)
        fig, ax = plt.subplots(figsize=(10, 10), dpi=100)
        ax.imshow(shade, origin="lower", extent=ext, cmap="gray", vmin=0, vmax=1)
        bg = self.link.blocked_grid()
        if bg is not None:
            m, bx, by, bc = bg
            over = np.zeros((*m.shape, 4))
            over[m] = (0.85, 0.15, 0.15, 0.45)
            ax.imshow(over, origin="lower", interpolation="nearest",
                      extent=(bx - bc / 2, bx + (m.shape[1] - .5) * bc, by - bc / 2, by + (m.shape[0] - .5) * bc))
        rm = self.resource_map()
        # Cell mean, not peak: the peak of many noisy readings shows noise as
        # hydrogen. Only cells whose mean clears 0.5 wt% are drawn.
        heat = np.array([[np.nan if v is None else v for v in row] for row in rm["mean"]], float)
        heat[heat < 0.5] = np.nan
        ax.imshow(np.ma.masked_invalid(heat), origin="lower", cmap="cool", vmin=0.5, vmax=4, alpha=0.85,
                  extent=(rm["x0"], rm["x0"] + rm["n"] * rm["cell_m"], rm["y0"], rm["y0"] + rm["n"] * rm["cell_m"]))
        for d in self.deposits:
            ax.add_patch(Circle((d.x, d.y), 2 * d.radius, fill=False, ls=":", lw=1,
                                color="#7cc4ff" if d.kind == "water_ice" else "#f0a35e"))
        colors = {"A": "#4f8dff", "B": "#ff8a3d"}
        for ag, r in self.rovers.items():
            tr, es = r.tracks()
            ax.plot(tr[:, 0], tr[:, 1], color=colors[ag], lw=1.6, label=f"rover {ag} true track")
            ax.plot(*r.dock, marker="s", ms=10, color=colors[ag], mec="white")
        mk = {"sampled": "o", "open": "+", "claimed": "+", "failed": "x", "unreachable": "x"}
        for t in self.board.targets:
            c = "#7cc4ff" if t["kind"] == "water_ice" else "#f0a35e"
            ax.plot(t["x"], t["y"], mk.get(t["status"], "+"), color=c, ms=9, mew=2,
                    mfc="none" if t["status"] == "sampled" else c)
            if t["purpose"] != "check":   # checks sit on their find; the P marker shows them
                ax.annotate(t["id"], (t["x"], t["y"]), xytext=(6, 6), textcoords="offset points",
                            color="white", fontsize=8, fontweight="bold")
        for rec in self.store.reports():
            if rec["result"]["verdict"] == "ice":
                p = rec["position"]
                star = "*" if rec["target"]["purpose"] != "check" else "P"
                ax.plot(p["x"], p["y"], star, color=colors[rec["agency"]], ms=14, mec="white")
        ax.set_xlim(ext[0], ext[1])
        ax.set_ylim(ext[2], ext[3])
        ax.set_title(f"{self.store.id}: routes by {self.link.name}\n"
                     "cyan-pink: survey hydrogen (cell mean >= 0.5 wt%)  dotted: hidden deposits  "
                     "red: blocked > 15 deg\n"
                     "o sampled  x failed/unreachable  + open  * ice find  P check", fontsize=9)
        ax.legend(loc="lower right", fontsize=8)
        fig.savefig(self.store.dir / "map.png", bbox_inches="tight")
        plt.close(fig)


# ---- CLI ---------------------------------------------------------------------
def summary(store):
    m = store.read("mission.json")
    print(f"\n{m['mission_id']}  ->  {store.dir}")
    print(f"terrain {m['terrain']['file']}   planner: {m['planner']['name']}")
    for ag, r in m["rovers"].items():
        print(f"  rover {ag}: {r['distance_m']:.0f} m in {r['duration_s'] / 3600:.1f} h, "
              f"speed made good {100 * r['speed_made_good_m_s']:.1f} cm/s "
              f"(target {100 * r['speed_target_m_s']:.1f}), battery {100 * r['battery_start_soc']:.0f}% -> "
              f"{100 * r['battery_end_soc']:.0f}% ({'reserve kept' if r['reserve_kept'] else 'RESERVE BROKEN'}), "
              f"{r['end_mode']}")
        print(f"           stops {r['targets']}: finds {r['finds']}, checks {r['checks']}, trace {r['trace']}, "
              f"dry {r['dry']}; faults {r['faults']}; self-found targets {r['discovered']}")
    print(f"  targets: {m['targets']}")
    print(f"  outbox: {m['outbox']['items']} chain calls {m['outbox']['calls']}")
    s = m["storage"]
    print(f"  storage: chain {s['chain']['finds']} finds x {s['chain']['bytes_of_fields_per_find']} B; "
          f"evidence {s['evidence']['objects']} objects, {s['evidence']['bytes'] / 1024:.1f} KiB "
          f"({s['evidence']['backend']}); routes {s['evidence']['route_thinning']}")
    c = s["cache"]
    print(f"           cache {c['path']}: telemetry {c['telemetry']}, events {c['events']}, "
          f"survey {c['survey']}, reports {c['reports']}, outbox {c['outbox']}")
    for e in store.db.events(store.id, "route_reuse"):
        extra = (f": reused {e['reused_m']} m, patched {e['patches']} segment(s), dropped {e['dropped_points']} "
                 f"blocked point(s)") if "reused_m" in e else ""
        clear = f", {e['clearance_m']} m clear of {', '.join(e['flagged_on_route'])}" if e.get("clearance_m") is not None else ""
        print(f"  route to {e['target_id']} for {e['agency']}: {e.get('chosen', e['mode'])} ({e['mode']}{extra}){clear}")
    print(f"  ran in {m['wall_seconds']} s")


def verify(store):
    """Check every report the way an outsider would, from the chain side:
    rebuild the CID from the find's dataHash, fetch the object by that CID,
    re-hash it, and follow its route reference the same way."""
    ok = True
    for it in store.db.outbox(store.id):
        if it["call"] != "post_find":
            continue
        dh = it["args"]["data_hash"]
        cid = EV.cid_from_data_hash(dh)
        try:
            rep = store.ev.get(cid)                 # verified against the CID
            route = store.ev.get(rep["refs"]["route"])
            good = rep["report_id"] == it["record_id"]
            print(f"  {'OK ' if good else 'BAD'} find {it['record_id']}: dataHash {dh[:12]}... -> "
                  f"{cid[:20]}... -> report ({rep['result']['verdict']}, {rep['result']['reading_wt_pct']} wt%,"
                  f" simulated={rep['simulated']}) -> route {len(route['points'])} pts")
            ok &= good
        except Exception as e:
            ok = False
            print(f"  BAD find {it['record_id']}: {type(e).__name__}: {e}")
    print("every find's evidence verifies from its dataHash" if ok else "VERIFY FAILED")
    return ok


def publish_run(store, runs_dir):
    """Add this mission as a run where app.py looks for runs: <runs_dir>/<id>/log.json
    and an entry in <runs_dir>/index.json. run_demo.py's entries are kept."""
    import shutil
    runs_dir = Path(runs_dir)
    out = runs_dir / store.id
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(store.dir / "run_log.json", out / "log.json")
    if (store.dir / "fp").exists():   # first-person frames, if rendered
        shutil.copytree(store.dir / "fp", out / "fp", dirs_exist_ok=True)
    if (store.dir / "lidar").exists():   # lidar frames (sim/mission_render.py)
        shutil.copytree(store.dir / "lidar", out / "lidar", dirs_exist_ok=True)
    # The mission's evidence objects, so a static host serves them and a
    # browser can re-hash them against the chain.
    (out / "evidence").mkdir(exist_ok=True)
    cids = [r["cid"] for r in store.db.c.execute("SELECT cid FROM evidence WHERE mission_id = ?", (store.id,))]
    for rec in store.reports():
        cids += [rec["evidence"]["report"]["cid"], rec["evidence"]["route"]["cid"]]
    for cid in set(cids):
        src = store.ev.objects / f"{cid}.json"
        if src.exists():
            shutil.copyfile(src, out / "evidence" / f"{cid}.json")
    label = json.loads((store.dir / "run_log.json").read_text()).get("label", "Science mission")
    idx_path = runs_dir / "index.json"
    index = json.loads(idx_path.read_text()) if idx_path.exists() else []
    index = [r for r in index if r.get("run_id") != store.id]
    index.append({"run_id": store.id, "label": label, "dir": store.id, "status": "mission"})
    idx_path.write_text(json.dumps(index, indent=1))
    print(f"published as a run: {runs_dir / store.id / 'log.json'} (python app.py serves it)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--seed", type=int, default=11)
    r.add_argument("--planner", choices=("auto", "standin"), default="auto")
    r.add_argument("--terrain")
    r.add_argument("--id")
    r.add_argument("--publish-run", metavar="RUNS_DIR",
                   help="also add the mission as a run for app.py (its runs folder, e.g. data/runs)")
    sub.add_parser("list")
    v = sub.add_parser("verify")
    v.add_argument("--mission")
    p = sub.add_parser("push")
    p.add_argument("--mission")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        store = Mission(a.seed, a.planner, a.terrain, a.id).run()
        summary(store)
        for rec in store.reports():
            print(f"    [{rec['record_id']}] {rec['result']['summary']}  "
                  f"(chain: {rec['chain']['call'] or '-'}, true error "
                  f"{rec['HIDDEN_TRUTH']['position_error_m']} m)")
        if a.publish_run:
            publish_run(store, a.publish_run)
    elif a.cmd == "list":
        for mid in MS.MissionStore.list():
            print(mid)
    elif a.cmd == "verify":
        store = MS.MissionStore.open(a.mission) if a.mission else MS.MissionStore.latest()
        sys.exit(0 if verify(store) else 1)
    else:
        store = MS.MissionStore.open(a.mission) if a.mission else MS.MissionStore.latest()
        print(f"outbox of {store.id}:")
        if a.dry_run:
            MS.push_outbox(store, dry_run=True)
            return
        try:
            from sim.chain import LanternClient   # step 1, branch step-1-chain
        except ImportError:
            sys.exit("sim/chain.py is not on this branch yet (it is on step-1-chain). "
                     "Use --dry-run, or merge step 1 first.")
        done = MS.push_outbox(store, LanternClient())
        print(f"posted {len(done)} calls")


if __name__ == "__main__":
    main()
