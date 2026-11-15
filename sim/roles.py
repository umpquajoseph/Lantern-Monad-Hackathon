"""Surveyor and Prospector: a two-rover mission coordinated through the registry.

Rover A is the Surveyor, rover B the Prospector. They never talk to each
other. Everything B knows about A's work it reads from the
LunarDiscoveryRegistry contract (sim/registry_client.py), and everything
either of them measured lives in the evidence store (sim/evidence.py), named
by the hash the contract holds.

Surveyor states: IDLE, EXPLORING, CANDIDATE_FOUND, NAVIGATING_TO_SITE,
INSPECTING, PUBLISHING, RESUMING, ERROR.
  Explores toward orbital candidate sites, neutron-surveying as it drives.
  It has no arm and no drill. It stops short of a site, faces it, scans it,
  scores its confidence and registers a candidate on chain; then it maps the
  site with its mast camera, sits for a neutron dwell, photographs it, and
  publishes a Positive, Negative or Inconclusive result with its report and
  the route it drove. On the way, a crater the orbital map missed makes it
  stop (MAPPING_HAZARD), face it, map and photograph it, and flag it in the
  route it publishes; then it drives around it.

Prospector states: IDLE, FETCHING_TARGET, PLANNING, NAVIGATING, REPLANNING,
ANALYZING, REPORTING, COMPLETED, ERROR.
  Reads the registry for sites the Surveyor found positive (the eligibility
  policy is configurable), picks the nearest by planned path cost, fetches
  the Surveyor's routes by the CIDs on chain and checks their hashes, adds
  every hazard the Surveyor flagged to its own map, and plans its own route
  around them; it takes the Surveyor's route only where that is shorter. At
  the site it drills: NIR scan, 1 m core, mass spectrometer, and reports a
  new, linked analysis.

Usage:
    python sim/roles.py run                 # chain auto: local anvil if Foundry is installed
    python sim/roles.py run --chain offline # no chain: labelled OFFLINE MOCK
    python sim/roles.py run --publish-run data/runs
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence as EV  # noqa: E402
import health as H  # noqa: E402
import mission as M  # noqa: E402
import mission_store as MS  # noqa: E402
import planner_link  # noqa: E402
import registry_client as RC  # noqa: E402
import resources  # noqa: E402
import science  # noqa: E402

ROLE = {"A": "Surveyor", "B": "Prospector"}
SURVEYOR_STATES = ("IDLE", "EXPLORING", "CANDIDATE_FOUND", "NAVIGATING_TO_SITE", "MAPPING_HAZARD", "INSPECTING",
                   "PUBLISHING", "RESUMING", "ERROR")
PROSPECTOR_STATES = ("IDLE", "FETCHING_TARGET", "PLANNING", "NAVIGATING", "REPLANNING", "ANALYZING",
                     "REPORTING", "COMPLETED", "ERROR")
RESULT_OF = {"ice": "Positive", "dry": "Negative", "trace": "Inconclusive"}

# ---- ASSUMED parameters ------------------------------------------------------
# Site mix: 4 ice deposits among 8 candidate sites, so about half the
# Surveyor's inspections come back positive. A demo choice, not lunar data.
SITE_MIX = dict(n_ice=4, n_ilmenite=1, n_decoys=3)
START_SOC = {"A": 1.0, "B": 0.9}
SCAN_RADIUS_M = 4.0          # the Surveyor's candidate scan: centre plus 6 points this far out
POLL_S = 600.0               # the Prospector re-reads the registry this often while it waits
HAZARD_SENSE_M = 10.0        # a new obstacle is seen this far ahead (lamp-lit stereo range, about)
HAZARD_RADIUS_M = 5.0        # fallback obstacle when no crater lies by the route: a boulder field
HAZARD_CRATER_D_M = (6.0, 20.0)   # the crater the orbital map missed: this wide (stamped craters, sim/terrain.py)
HAZARD_MARGIN_M = 1.5        # rim boulders reach this far past the rim
HAZARD_ALONG = (0.3, 0.75)   # and it lies this far along that route
# ASSUMED, a demo choice: the crater lies on the Surveyor's n-th drive from one
# ice site to another. Those are the hops the Prospector drives later too, so
# it meets the flagged crater's corridor (with seed 11, the T04 -> T06 hop).
HAZARD_LEG = 2
FLAGGED_MARGIN_M = 4.0       # ASSUMED: the Prospector keeps this much extra room around a flagged hazard
STANDOFF_M = 6.0             # the Surveyor stops this far short of a site, facing it, to map it
TURN_RATE_RAD_S = 0.1        # turning in place to face something
SLOPE_BASE_M = 2.0           # slopes measured across this baseline, as sim/rover.py SlopeMap
ELIGIBILITY = {"positive": ("Positive",), "positive+inconclusive": ("Positive", "Inconclusive")}


class RolesMission(M.Mission):
    def __init__(self, seed=11, planner="auto", terrain_path=None, mission_id=None, chain="auto",
                 eligibility="positive"):
        super().__init__(seed, planner, terrain_path, mission_id,
                         site_params=resources.Params(seed=seed, **SITE_MIX))
        self.reg = RC.connect(chain)
        self.eligible = ELIGIBILITY[eligibility]
        self.eligibility = eligibility
        self.mkey = RC.mission_key(self.store.id)
        self.published_t = {}        # site_id -> sim time its inspection went on chain
        self.site_target = {}        # site_id -> target dict
        self.site_records = {}       # site_id -> {"candidate": ref, "inspection": record}
        self.reg_events = []         # every registry write, with sim time, for the run log
        self.hidden_hazards = []     # on no map until a rover sees them
        self.planned = []            # every planned route, for the dashboard (planned is not driven)
        self.hazard_log = []
        self.hazard_stops = []       # the Surveyor's stops at a flagged hazard, for the run log
        self.crater_placed = False
        self.ice_legs = 0            # Surveyor drives from one ice site to another, so far
        self.prev_ice = False        # did the Surveyor's last site hold ice (hidden truth, for placing the crater)
        for ag, r in self.rovers.items():
            r.start_soc = START_SOC[ag]
            r.health.battery_wh = START_SOC[ag] * H.BATTERY_WH
            r.track[0] = (*r.track[0][:10], r.health.soc)
            r.role = ROLE[ag]
            r.state, r.objective, r.states = None, "", []
            r.analysed = set()
            self.set_state(r, "IDLE", "at the dock")

    # ---- small helpers -----------------------------------------------------
    def set_state(self, r, state, objective):
        allowed = SURVEYOR_STATES if r.role == "Surveyor" else PROSPECTOR_STATES
        assert state in allowed, (r.role, state)
        r.state, r.objective = state, objective
        r.states.append((r.t, state, objective))
        self.ev(r, "state", state=state, objective=objective, role=r.role)

    def state_at(self, r, t):
        i = bisect.bisect_right([s[0] for s in r.states], t) - 1
        return r.states[max(i, 0)]

    def chain_write(self, r, call, fn, *args, **info):
        """One registry write: the contract call, its receipt, logged with sim time."""
        try:
            res = fn(*args)
        except RC.RegistryError as e:
            self.set_state(r, "ERROR", f"{call} refused by the contract: {e}")
            self.ev(r, "registry_error", call=call, error=str(e))
            return None
        row = dict(t=round(r.t, 1), agency=r.agency, role=r.role, call=call, mode=res["mode"],
                   tx=res["tx"], block=res["block"], report_id=res["report_id"], gas_used=res["gas_used"], **info)
        self.reg_events.append(row)
        self.ev(r, "registry", **{k: v for k, v in row.items() if k not in ("t", "agency")})
        return res

    def plan_log(self, r, route, kind, target_id):
        pts = EV.thin_route([list(map(float, p)) for p in np.asarray(route).reshape(-1, 2)], 0.5)
        self.planned.append({"agency": r.agency, "t": round(r.t, 1), "kind": kind, "target_id": target_id,
                             "points": pts})

    def scan(self, r, site_true, site_est):
        """The Surveyor's candidate scan: neutron readings at the site and
        around it, positions as the rover estimates them."""
        out = []
        ex, ey = site_est
        for k in range(7):
            a = 2 * math.pi * k / 6
            d = 0.0 if k == 0 else SCAN_RADIUS_M
            tx, ty = site_true[0] + d * math.cos(a), site_true[1] + d * math.sin(a)
            w = resources.neutron_reading(self.deposits, tx, ty, r.rng)
            out.append((ex + d * math.cos(a), ey + d * math.sin(a), w))
        return out

    def drill(self, r, tgt, phases, observation, parent_cid=None):
        """Drill and analyse at a target; returns the published record, or None
        when the drill stalls twice."""
        tries = 0
        while True:
            tries += 1
            if r.rng.random() >= H.DRILL_STALL_P:
                break
            r.stats["faults"] += 1
            r.t += 120.0
            r.health.spend(r.health.idle_wh(120.0) + H.DRILL_W * 120.0 / 3600)
            self.ev(r, "fault", target_id=tgt["id"], what="drill stalled", attempt=tries)
            if tries >= H.DRILL_TRIES:
                return None
        true_xy, est_xy = r.tracks()
        rec = science.run_science(r.agency, tgt, self.deposits, r.t, r.pose, r.estimate, r.sigma,
                                  est_xy, true_xy, seed=int(r.rng.integers(1 << 31)), attempts=tries,
                                  phases_spec=phases)
        rec["observation"] = observation
        return self.publish_record(r, rec, tgt, phases, est_xy, parent_cid)

    def publish_record(self, r, rec, tgt, phases, est_xy, parent_cid=None):
        """Spend the stop's time and energy, store its evidence, queue its
        Lantern-contract calls, and log it."""
        observation = rec["observation"]
        dur = sum(d for _, d in phases)
        r.t += dur
        r.health.spend(H.Health.science_wh([{"name": n, "dur": d} for n, d in phases]))
        r.track.append((r.t, *r.pose, *r.estimate, *r.odom, r.sigma, r.health.soc))
        tail, _ = science.last_route(est_xy, spacing=0.0)
        rec = self.store.publish(rec, tail, parent_report_cid=parent_cid)
        new = MS.outbox_items_for(rec, r.health)   # the Lantern contract's calls, for testnet
        self.store.queue(new)
        self.outbox.extend(new)
        v = rec["result"]["verdict"]
        r.stats["targets"] += 1
        self.ev(r, "science", record_id=rec["record_id"], target_id=tgt["id"], verdict=v,
                reading=rec["result"]["reading"], summary=rec["result"]["summary"],
                chain_call=rec["chain"]["call"], phases=rec["phases"], observation=observation)
        return rec

    # ---- dispatch ------------------------------------------------------------
    def leg(self, r):
        r.legs += 1
        if r.legs > 3 * M.MAX_LEGS:
            return self.finish_rover(r, "leg limit")
        if r.role == "Surveyor":
            self.surveyor_leg(r)
        else:
            self.prospector_leg(r)

    def finish_rover(self, r, reason):
        self.go_home(r, reason)
        self.set_state(r, "IDLE" if r.role == "Surveyor" else "COMPLETED",
                       f"docked with {100 * r.health.soc:.0f}% battery: {reason}")

    # ---- Surveyor --------------------------------------------------------------
    def surveyor_leg(self, r):
        if r.health.must_return(self.home_dist(r)):
            return self.finish_rover(r, "battery: drive home plus reserve")
        self.set_state(r, "EXPLORING", "surveying toward the next candidate site")
        tgt, score = self.choose_target(r)
        if tgt is None:
            return self.finish_rover(r, "no candidate site left that fits the battery and the sortie")
        self.board.claim(tgt["id"], r.agency)
        goal = (tgt["x"], tgt["y"])
        try:
            route = self.link.plan(r.pose[:2], goal)
        except planner_link.NoRoute as e:
            tgt["status"] = "unreachable"
            self.set_state(r, "ERROR", f"no safe route to {tgt['id']}: {e}")
            self.set_state(r, "RESUMING", "skipping an unreachable site")
            return
        self.plan_log(r, route, "planned", tgt["id"])
        self.ev(r, "depart", target_id=tgt["id"], kind=tgt["kind"], route_m=round(planner_link.route_length(route), 1),
                waypoints=len(route), planner=self.link.name)
        if self.prev_ice and self._has_ice(tgt):
            self.ice_legs += 1
            if self.ice_legs == HAZARD_LEG and not self.crater_placed:
                self._place_crater(route)
        self.prev_ice = self._has_ice(tgt)
        r.mode = "to target"
        flagged = []
        for attempt in range(5):
            self._why = None
            res = self.drive(r, route, abort_on_low=True, on_step=lambda rr: self._surveyor_watch(rr, goal))
            if res is False:
                self.board.release(tgt["id"])
                return self.finish_rover(r, "battery fell to the return threshold on the way")
            if res is True or self._why == "standoff":
                break
            flagged.append(self.hazard_stop(r, self.hazard_log[-1]))
            try:
                route = self.link.plan(r.pose[:2], goal)
            except planner_link.NoRoute as e:
                self.board.release(tgt["id"])
                return self.set_state(r, "ERROR", f"no way around the hazard to {tgt['id']}: {e}")
            self.plan_log(r, route, "replanned", tgt["id"])
            self.ev(r, "replanned", target_id=tgt["id"], route_m=round(planner_link.route_length(route), 1))
            self.set_state(r, "NAVIGATING_TO_SITE",
                           f"around the flagged crater to {tgt['id']} ({planner_link.route_length(route):.0f} m)")
        self.face(r, goal)

        # Where the site is, by the rover's own estimate plus the camera's range to it.
        site_est = (r.estimate[0] + goal[0] - r.pose[0], r.estimate[1] + goal[1] - r.pose[1])

        # CANDIDATE_FOUND: scan around the site, score it, register it.
        sid = RC.site_id(self.store.id, tgt["id"])
        scan = self.scan(r, goal, site_est)
        best = max(w for _, _, w in scan)
        conf = float(np.clip(0.1 + 0.85 * best / 2.0, 0.05, 0.95))
        self.set_state(r, "CANDIDATE_FOUND",
                       f"site {tgt['id']}: neutron scan peak {best:.2f} wt%, confidence {conf:.0%}")
        lat, lon = science.south_polar_to_latlon(*site_est)
        cand = EV.make_candidate(self.store.id, sid, tgt, r.agency, r.t, site_est, r.sigma, (lat, lon), scan, conf)
        cref = self.store.ev.put(cand, "candidate")
        self.store.db.evidence(self.store.id, cref)
        self.site_target[sid] = tgt
        self.site_records[sid] = {"candidate": cref}
        self.set_state(r, "PUBLISHING", f"registering candidate {tgt['id']} on chain")
        ex, ey = site_est
        if self.chain_write(r, "registerCandidate", self.reg.register_candidate, sid, self.mkey, ex, ey, conf,
                            cref["data_hash"], cref["cid"], site_id=sid, target_id=tgt["id"],
                            confidence=round(conf, 3), report_cid=cref["cid"], evidence_hash=cref["data_hash"],
                            x=round(ex, 2), y=round(ey, 2),
                            explain=f"Surveyor registers candidate site {tgt['id']}: position ±{r.sigma:.1f} m, "
                                    f"confidence {conf:.0%}, and the hash of its scan report") is None:
            return self.set_state(r, "RESUMING", "moving on after a refused registration")

        # INSPECTING: map it, dwell on it, photograph it. No drill on this rover.
        self.set_state(r, "INSPECTING", f"mapping {tgt['id']} with the mast camera and a neutron dwell")
        r.mode = "science"
        true_xy, est_xy = r.tracks()
        rec = science.run_site_survey(r.agency, tgt, self.deposits, r.t, goal, site_est, r.sigma, r.pose,
                                      est_xy, true_xy, self.ground_stats(*goal, 8.0),
                                      seed=int(r.rng.integers(1 << 31)))
        rec["observation"] = "surveyor site survey"
        if flagged:
            rec["route_hazards"] = flagged
        rec = self.publish_record(r, rec, tgt, science.SURVEY_PHASES, est_xy)
        self.board.complete(tgt["id"], rec["record_id"])
        result = RESULT_OF[rec["result"]["verdict"]]
        r.stats[{"Positive": "finds", "Negative": "dry", "Inconclusive": "trace"}[result]] += 1
        self.site_records[sid]["inspection"] = rec
        ev = rec["evidence"]
        self.set_state(r, "PUBLISHING", f"publishing a {result.lower()} result for {tgt['id']} with its route")
        self.chain_write(r, "submitSurveyorInspection", self.reg.submit_surveyor_inspection, sid, result,
                         ev["report"]["data_hash"], ev["report"]["cid"], ev["route"]["data_hash"], ev["route"]["cid"],
                         site_id=sid, target_id=tgt["id"], result=result, record_id=rec["record_id"],
                         report_cid=ev["report"]["cid"], route_cid=ev["route"]["cid"],
                         evidence_hash=ev["report"]["data_hash"], reading=rec["result"]["reading"],
                         flagged_hazards=len(flagged),
                         explain=f"Surveyor reports {tgt['id']} {result.upper()} "
                                 f"(neutron dwell {rec['result']['reading']:.2f} wt%) with the hash of its report "
                                 f"and of the {rec['route']['length_m']:.0f} m route it drove"
                                 + (f", which flags {len(flagged)} hazard(s)" if flagged else ""))
        self.published_t[sid] = r.t
        self.set_state(r, "RESUMING", "back to exploring")

    def _surveyor_watch(self, r, goal):
        """Stop for a new hazard, or STANDOFF_M short of the site."""
        if self._sense_hazards(r):
            self._why = "hazard"
            return True
        if math.dist(r.pose[:2], goal) <= STANDOFF_M:
            self._why = "standoff"
            return True
        return False

    def face(self, r, xy):
        """Turn in place toward xy (the site, or a hazard), so the camera looks at it."""
        if math.dist(r.pose[:2], xy) < 0.5:
            return
        hd = math.atan2(xy[1] - r.pose[1], xy[0] - r.pose[0])
        dh = (hd - r.pose[2] + math.pi) % (2 * math.pi) - math.pi
        r.t += abs(dh) / TURN_RATE_RAD_S
        r.health.spend(r.health.idle_wh(abs(dh) / TURN_RATE_RAD_S))
        r.pose = (r.pose[0], r.pose[1], hd)
        r.nav_step(0.0, dh)
        r.odom_step(0.0, dh)
        r.track.append((r.t, *r.pose, *r.estimate, *r.odom, r.sigma, r.health.soc))

    def ground_stats(self, x, y, radius):
        """What the mast camera's stereo map says about the ground within
        `radius` of (x, y): slopes across SLOPE_BASE_M, and how much of it the
        planner calls no-go."""
        c = self.cell
        n = max(1, int(round(SLOPE_BASE_M / c)))
        i0, i1 = int((y - radius - self.y0) / c) - n, int((y + radius - self.y0) / c) + n + 1
        j0, j1 = int((x - radius - self.x0) / c) - n, int((x + radius - self.x0) / c) + n + 1
        H_, W_ = self.height.shape
        i0, j0, i1, j1 = max(i0, 0), max(j0, 0), min(i1, H_), min(j1, W_)
        z = self.height[i0:i1, j0:j1]
        gy = (z[2 * n:, n:-n] - z[:-2 * n, n:-n]) / (2 * n * c)
        gx = (z[n:-n, 2 * n:] - z[n:-n, :-2 * n]) / (2 * n * c)
        deg = np.degrees(np.arctan(np.hypot(gx, gy)))
        ii, jj = np.mgrid[i0 + n:i1 - n, j0 + n:j1 - n]
        inside = np.hypot(self.x0 + jj * c - x, self.y0 + ii * c - y) <= radius
        d = deg[inside] if inside.any() else deg.ravel()
        pts = [(x + rr * math.cos(a), y + rr * math.sin(a)) for rr in np.linspace(0, radius, 6)
               for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
        blocked = np.mean([self.link.blocked(px, py) for px, py in pts])
        return {"radius_m": round(radius, 1), "max_slope_deg": round(float(d.max()), 1),
                "mean_slope_deg": round(float(d.mean()), 1), "no_go_fraction": round(float(blocked), 2),
                "slope_baseline_m": SLOPE_BASE_M, "source": "simulated stereo map (the sim height grid)"}

    def hazard_stop(self, r, h):
        """Stop, face the hazard, map and photograph it, and write it up. The
        write-up goes in this rover's route object, which is on chain by its
        hash, so the next rover can read it."""
        n = len(self.hazard_stops) + 1
        hid = f"H{n}"
        what = f"a crater {h['crater_d']:.0f} m across, boulders on its rim," if h.get("crater_d") else "a boulder field"
        self.set_state(r, "MAPPING_HAZARD", f"{what} not on the orbital map, {h['seen_at_m']:.0f} m ahead: "
                                            "stopping to map and photograph it")
        self.face(r, (h["x"], h["y"]))
        t0 = r.t
        phases, tt = [], t0
        for name, dur in science.HAZARD_PHASES:
            phases.append({"name": name, "t0": round(tt, 2), "dur": dur})
            tt += dur
        r.t = tt
        r.health.spend(r.health.idle_wh(tt - t0))
        r.track.append((r.t, *r.pose, *r.estimate, *r.odom, r.sigma, r.health.soc))
        stats = self.ground_stats(h["x"], h["y"], h["radius"])
        # The hazard where this rover thinks it is: its estimate plus the camera's range.
        hx, hy = r.estimate[0] + h["x"] - r.pose[0], r.estimate[1] + h["y"] - r.pose[1]
        obj = {"schema": "lantern.hazard/1", "mission_id": self.store.id, "hazard_id": hid, "rover": r.agency,
               "observation": "hazard survey (mast camera stereo map and photo)", "simulated": True,
               "t": round(t0, 1),
               "position": {"x": round(hx, 2), "y": round(hy, 2), "sigma": round(r.sigma, 2), "source": "rover estimate"},
               "hazard": {"kind": h["kind"], "radius_m": round(h["radius"], 1),
                          "crater_diameter_m": h.get("crater_d"), **stats},
               "decision": "unsafe to cross: flagged in this rover's route; it drives around, "
                           "and later rovers plan around it"}
        ref = self.store.ev.put(obj, "hazard")
        self.store.db.evidence(self.store.id, ref)
        h.update(hazard_id=hid, report_cid=ref["cid"], max_slope_deg=stats["max_slope_deg"])
        self.hazard_stops.append({
            "type": "science", "agency": r.agency, "record_id": f"{r.agency}-{hid}-t{int(round(t0)):06d}",
            "target_id": hid, "x": round(hx, 2), "y": round(hy, 2), "sigma": round(r.sigma, 2),
            "verdict": "unsafe", "reading": stats["max_slope_deg"],
            "summary": f"{h['kind']}: slopes to {stats['max_slope_deg']:.0f} deg; unsafe, flagged for the Prospector",
            "phases": phases, "chain_call": None, "observation": "hazard survey", "kind": "hazard",
            "report_cid": ref["cid"], "route_cid": None, "data_hash": ref["data_hash"],
            "hazard": {"x": h["x"], "y": h["y"], "radius": h["radius"], "kind": h["kind"], "crater_d": h.get("crater_d")}})
        self.ev(r, "hazard_flagged", hazard_id=hid, report_cid=ref["cid"], **stats)
        return {"hazard_id": hid, "x": round(hx, 2), "y": round(hy, 2), "radius_m": round(h["radius"], 1),
                "kind": h["kind"], "max_slope_deg": stats["max_slope_deg"], "report_cid": ref["cid"]}

    def _has_ice(self, tgt):
        c, _ = resources.concentration(self.deposits, "water_ice", tgt["x"], tgt["y"])
        return tgt.get("kind") == "water_ice" and c >= science.ICE_THRESHOLD_WT_PCT

    def _place_crater(self, route):
        """The crater the orbital map missed: a real crater of the terrain
        beside the Surveyor's route, whose rim boulders block the way. The
        planner does not know about the boulders until a rover sees them."""
        pts = np.asarray(route, float)
        seg = np.hypot(*np.diff(pts, axis=0).T)
        cum = np.concatenate([[0], np.cumsum(seg)])
        L = float(cum[-1])
        if L < 30:
            return
        lo, hi = HAZARD_CRATER_D_M
        cr = self.craters[(self.craters[:, 2] >= lo) & (self.craters[:, 2] <= hi)]
        box = (pts.min(0) - hi, pts.max(0) + hi)
        cr = cr[(cr[:, 0] > box[0][0]) & (cr[:, 0] < box[1][0]) & (cr[:, 1] > box[0][1]) & (cr[:, 1] < box[1][1])]
        best = None
        for cx, cy, D in cr:
            # Nearest point of the route to the crater centre, and how far along it is.
            a, b = pts[:-1], pts[1:]
            ab = b - a
            tt = np.clip(np.einsum("ij,ij->i", np.array([cx, cy]) - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-9), 0, 1)
            near = a + tt[:, None] * ab
            d = np.hypot(near[:, 0] - cx, near[:, 1] - cy)
            k = int(np.argmin(d))
            along = (cum[k] + tt[k] * seg[k]) / L
            if HAZARD_ALONG[0] <= along <= HAZARD_ALONG[1] and d[k] <= D / 2 + 3.0:
                score = abs(along - 0.5)
                if best is None or score < best[0]:
                    best = (score, cx, cy, D, d[k])
        self.crater_placed = True
        if best:
            _, x, y, D, dist = best
            radius = max(D / 2 + HAZARD_MARGIN_M, dist + 2.0)   # the boulders reach the route
            self.hidden_hazards.append({"x": float(x), "y": float(y), "radius": float(radius), "seen": False,
                                        "kind": "crater rim with boulders", "crater_d": round(float(D), 1)})
        else:
            s = 0.5 * L
            k = max(min(int(np.searchsorted(cum, s)) - 1, len(seg) - 1), 0)
            x, y = pts[k] + (s - cum[k]) / max(seg[k], 1e-9) * (pts[k + 1] - pts[k])
            self.hidden_hazards.append({"x": float(x), "y": float(y), "radius": HAZARD_RADIUS_M, "seen": False,
                                        "kind": "boulder field", "crater_d": None})

    # ---- Prospector --------------------------------------------------------------
    def prospector_leg(self, r):
        if r.health.must_return(self.home_dist(r)):
            return self.finish_rover(r, "battery: drive home plus reserve")
        self.set_state(r, "FETCHING_TARGET", "reading the registry for sites the Surveyor found positive")
        sites = self.reg.sites()   # a real read from the contract (or the labelled mock)
        eligible = [s for s in sites if s["stage"] == "SurveyorInspected" and s["surveyor_result"] in self.eligible
                    and self.published_t.get(s["site_id"], math.inf) <= r.t and s["site_id"] not in r.analysed]
        self.ev(r, "registry_read", sites=len(sites), eligible=len(eligible), policy=self.eligibility)
        if not eligible:
            if not self.rovers["A"].done:
                known = sum(1 for e in self.reg_events if e["call"] == "registerCandidate" and e["t"] <= r.t)
                self.set_state(r, "IDLE", f"no eligible site on chain yet ({known} registered); waiting")
                r.t += POLL_S
                r.health.spend(r.health.idle_wh(POLL_S))
                r.track.append((r.t, *r.pose, *r.estimate, *r.odom, r.sigma, r.health.soc))
                return
            return self.finish_rover(r, "every eligible site on chain is analysed")

        # PLANNING: nearest eligible site by planned path cost.
        self.set_state(r, "PLANNING", f"{len(eligible)} eligible site(s); costing a path to each")
        best = None
        for s in eligible:
            try:
                route = self.link.plan(r.pose[:2], (s["x"], s["y"]))
            except planner_link.NoRoute:
                continue
            cost = planner_link.route_length(route)
            if best is None or cost < best[0]:
                best = (cost, s)
        if best is None:
            for s in eligible:
                r.analysed.add(s["site_id"])
            return self.set_state(r, "ERROR", "no safe path to any eligible site")
        cost, site = best
        sid = site["site_id"]
        r.analysed.add(sid)
        tgt_src = self.site_target[sid]
        rep = self.reg.get_report(site["latest_report_id"])        # from the chain
        ref = self.store.ev.get(rep["route_cid"])                   # verified against its CID
        route_ok = EV.keccak256(EV.canonical_bytes(ref)).hex() == rep["route_hash"].removeprefix("0x")
        goal = (site["x"], site["y"])
        # Every hazard the Surveyor flagged, from its routes on chain, goes on this rover's map first.
        flagged = self.read_flagged(sites)
        for hz in flagged:
            self.link.add_hazard(hz["x"], hz["y"], hz["radius_m"] + FLAGGED_MARGIN_M)
        # A route on which the Surveyor had to stop for a hazard is not one to
        # follow: plan a new one with the hazard (and a margin) on the map.
        on_way = ref.get("hazards", [])
        try:
            own = self.link.plan(r.pose[:2], goal)
            if on_way:
                route, info = own, {"mode": "own plan: the Surveyor's route ran into a flagged hazard"}
            else:
                reuse_route, info = self.link.reuse(r.pose[:2], ref["points"], goal)
                route = own if planner_link.route_length(own) <= planner_link.route_length(reuse_route) else reuse_route
        except planner_link.NoRoute as e:
            return self.set_state(r, "ERROR", f"cannot plan to {tgt_src['id']}: {e}")
        route_m = planner_link.route_length(route)
        choice = "its own plan" if route is own else "the Surveyor's route, checked"
        clear = None
        if on_way:
            # Closest this route comes to each flagged rim, the room it leaves.
            pts = np.asarray(route, float)
            clear = min(float(np.min(np.hypot(pts[:, 0] - hz["x"], pts[:, 1] - hz["y"]))) - hz["radius_m"]
                        for hz in on_way)
        self.ev(r, "route_reuse", target_id=tgt_src["id"], route_cid=rep["route_cid"], route_hash_ok=route_ok,
                flagged=len(flagged), flagged_on_route=[hz["hazard_id"] for hz in on_way], chosen=choice,
                route_m=round(route_m, 1), clearance_m=None if clear is None else round(clear, 1),
                **{k: v for k, v in info.items() if k not in ("route_m",)})
        hash_txt = "hash matches the chain" if route_ok else "hash DOES NOT match"
        if on_way:
            ids = ", ".join(hz["hazard_id"] for hz in on_way)
            self.set_state(r, "PLANNING", f"site {tgt_src['id']}: the Surveyor's route ({hash_txt}) flags crater {ids}; "
                                          f"planning its own way, {clear:.0f} m clear of it ({route_m:.0f} m)")
        else:
            self.set_state(r, "PLANNING", f"site {tgt_src['id']}: Surveyor's route fetched by CID, {hash_txt}; "
                                          f"taking the {choice} ({route_m:.0f} m)")

        # NAVIGATING, with REPLANNING when something new blocks the way.
        r.mode = "to target"
        for attempt in range(4):
            self.plan_log(r, route, "planned" if attempt == 0 else "replanned", tgt_src["id"])
            self.set_state(r, "NAVIGATING", f"driving to {tgt_src['id']} ({planner_link.route_length(route):.0f} m)")
            res = self.drive(r, route, abort_on_low=True, on_step=self._sense_hazards)
            if res is True:
                break
            if res is False:
                return self.finish_rover(r, "battery fell to the return threshold on the way")
            h = self.hazard_log[-1]
            self.set_state(r, "REPLANNING", f"new obstacle {h['radius'] * 2:.0f} m wide across the route, "
                                            f"{h['seen_at_m']:.0f} m ahead; planning around it")
            try:
                route = self.link.plan(r.pose[:2], goal)
            except planner_link.NoRoute as e:
                return self.set_state(r, "ERROR", f"no way around the obstacle: {e}")
            self.ev(r, "replanned", target_id=tgt_src["id"], route_m=round(planner_link.route_length(route), 1))

        # ANALYZING: the fuller instrument sequence, a new linked report.
        insp = self.site_records[sid].get("inspection")
        tgt = dict(id=f"P-{tgt_src['id']}", kind="water_ice", purpose="check", x=goal[0], y=goal[1], radius=3.0,
                   find_id=MS.FIND_REF + insp["record_id"] if insp else None, finder_agency="A",
                   source="registry site (Surveyor positive)", report_cid=rep["report_cid"])
        self.set_state(r, "ANALYZING", f"NIR scan, 1 m drill and mass spectrometer at {tgt_src['id']}")
        r.mode = "science"
        rec = self.drill(r, tgt, science.PROSPECTOR_PHASES, "prospector analysis", parent_cid=rep["report_cid"])
        if rec is None:
            return self.set_state(r, "ERROR", f"drill stalled twice at {tgt_src['id']}; site left for later")
        result = RESULT_OF[rec["result"]["verdict"]]
        r.stats["checks"] += 1
        ev = rec["evidence"]
        self.set_state(r, "REPORTING", f"publishing a {result.lower()} analysis of {tgt_src['id']}")
        self.chain_write(r, "submitProspectorAnalysis", self.reg.submit_prospector_analysis, sid, result,
                         ev["report"]["data_hash"], ev["report"]["cid"], ev["route"]["data_hash"], ev["route"]["cid"],
                         site["latest_report_id"], site_id=sid, target_id=tgt_src["id"], result=result,
                         record_id=rec["record_id"], report_cid=ev["report"]["cid"], route_cid=ev["route"]["cid"],
                         evidence_hash=ev["report"]["data_hash"], parent_report_id=site["latest_report_id"],
                         reading=rec["result"]["reading"],
                         explain=f"Prospector reports {tgt_src['id']} {result.upper()} "
                                 f"({rec['result']['reading']:.2f} wt% water) as a new report linked to the "
                                 f"Surveyor's (#{site['latest_report_id']}); the Surveyor's record is untouched")

    def read_flagged(self, sites):
        """Hazards flagged in the Surveyor's routes: each route read by the
        CID its report holds on chain, and checked against that hash."""
        out, seen = [], set()
        for s in sites:
            rid = s["latest_report_id"]
            while rid:
                rep = self.reg.get_report(rid)
                cid = rep.get("route_cid")
                if cid and cid not in seen:
                    seen.add(cid)
                    obj = self.store.ev.get(cid)
                    if obj.get("rover") == "A" and \
                            EV.keccak256(EV.canonical_bytes(obj)).hex() == rep["route_hash"].removeprefix("0x"):
                        out += obj.get("hazards", [])
                rid = rep["parent_report_id"]
        return out

    def _sense_hazards(self, r):
        for h in self.hidden_hazards:
            if h["seen"]:
                continue
            d = math.hypot(r.pose[0] - h["x"], r.pose[1] - h["y"])
            if d <= HAZARD_SENSE_M + h["radius"]:
                h["seen"] = True
                self.link.add_hazard(h["x"], h["y"], h["radius"])
                row = dict(x=round(h["x"], 2), y=round(h["y"], 2), radius=round(h["radius"], 2), seen_t=round(r.t, 1),
                           seen_by=r.agency, seen_at_m=round(d - h["radius"], 1), kind=h.get("kind"),
                           crater_d=h.get("crater_d"))
                self.hazard_log.append(row)
                self.ev(r, "hazard", **row)
                return True
        return False

    # ---- run, outputs ------------------------------------------------------------
    def run(self):
        try:
            return super().run()
        finally:
            self.reg.close()

    def extra_manifest(self):
        return {"roles": ROLE, "registry": self.reg.info(), "eligibility": self.eligibility,
                "registry_writes": len(self.reg_events), "hazards": self.hazard_log,
                "sites": self.registry_dump()}

    def registry_dump(self):
        out = []
        for s in self.reg.sites():
            tgt = self.site_target.get(s["site_id"], {})
            reps, rid = [], s["latest_report_id"]
            while rid:
                rep = self.reg.get_report(rid)
                reps.append(rep)
                rid = rep["parent_report_id"]
            out.append(dict(s, target_id=tgt.get("id"), kind=tgt.get("kind"), reports=list(reversed(reps))))
        return out

    def run_log(self):
        log = super().run_log()
        # Steps at every science stop, so the rover holds still while it works.
        extra = []
        for e in list(self.store.db.events(self.store.id, "science")) + self.hazard_stops:
            ph = e["phases"]
            extra += [ph[0]["t0"], ph[-1]["t0"] + ph[-1]["dur"]]
        times = sorted({s["t"] for s in log["steps"]} | {round(t, 1) for t in extra})
        steps_by_t = {s["t"]: s for s in log["steps"]}
        steps = []
        for t in times:
            s = steps_by_t.get(t) or {"t": t, "rovers": {}, "events": []}
            for ag, r in self.rovers.items():
                a = np.array(r.track)
                i = max(int(np.searchsorted(a[:, 0], t, side="right")) - 1, 0)
                row = a[i]
                st = self.state_at(r, t)
                s["rovers"][ag] = dict(s["rovers"].get(ag) or {}, **{
                    "true_pose": [round(float(v), 3) for v in row[1:4]],
                    "odom_pose": [round(float(v), 3) for v in row[6:9]],
                    "estimate": [round(float(row[4]), 3), round(float(row[5]), 3)],
                    "sigma": round(float(row[9]), 3), "nav_on": True, "rims": [],
                    "fp_frame": (s["rovers"].get(ag) or {}).get("fp_frame"),
                    "role": r.role, "state": st[1], "objective": st[2], "soc": round(float(row[10]), 4)})
            steps.append(s)
        # Registry writes onto the step at or after their time.
        ts = [s["t"] for s in steps]
        for e in self.hazard_stops:
            k = min(bisect.bisect_left(ts, round(e["phases"][0]["t0"], 1)), len(steps) - 1)
            steps[k].setdefault("science", []).append(e)
        for e in self.reg_events:
            k = min(bisect.bisect_left(ts, e["t"]), len(steps) - 1)
            steps[k].setdefault("registry", []).append(e)
        log["steps"] = steps
        log["label"] = "Surveyor and Prospector: registry mission"
        log["roles"] = ROLE
        log["registry"] = self.reg.info()
        log["registry_sites"] = self.registry_dump()
        log["hazards"] = self.hazard_log
        log["planned_routes"] = self.planned
        # What the satellite relayed before the mission: where to look, nothing else.
        log["satellite_sites"] = [{"id": t["id"], "x": round(t["x"], 2), "y": round(t["y"], 2), "kind": t["kind"]}
                                  for t in self.board.targets if t.get("source", "").startswith("orbital")]
        log["states"] = {ag: [{"t": round(t, 1), "state": s, "objective": o} for t, s, o in r.states]
                         for ag, r in self.rovers.items()}
        log["dt_s"] = M.RUN_LOG_DT_S
        log["standins"] = {"rims": "rims: near-rim arcs of mapped craters (sim/standins.py rims_standin), not vision",
                           "science": "deposits, readings and drill cores are simulated (sim/resources.py, sim/science.py)"}
        if self.reg.mode == "offline":
            log["standins"]["chain"] = "chain: OFFLINE MOCK registry; tx hashes are fake"
        elif self.reg.mode == "local":
            log["standins"]["chain"] = ("chain: a local anvil chain, real contract and transactions, not Monad; "
                                        "tx hashes have no public explorer")
        if not self.link.real:
            log["standins"]["planner"] = self.link.name
        log["chain"] = {"chain_id": self.reg.chain_id, "contract": self.reg.address,
                        "explorer": self.reg.explorer, "label": self.reg.label}
        return log


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--seed", type=int, default=11)
    r.add_argument("--planner", choices=("auto", "standin"), default="auto")
    r.add_argument("--chain", choices=("auto", "local", "monad", "offline"), default="auto")
    r.add_argument("--eligibility", choices=tuple(ELIGIBILITY), default="positive")
    r.add_argument("--terrain")
    r.add_argument("--id")
    r.add_argument("--publish-run", metavar="RUNS_DIR")
    r.add_argument("--render", action="store_true", help="render first-person frames (needs VTK)")
    a = ap.parse_args()
    t0 = time.perf_counter()
    m = RolesMission(a.seed, a.planner, a.terrain, a.id, a.chain, a.eligibility)
    store = m.run()
    M.summary(store)
    man = store.read("mission.json")
    print(f"  registry: {man['registry']['label']}  contract {man['registry']['contract']}")
    for s in man["sites"]:
        chain = " -> ".join(f"#{x['report_id']} {x['stage']}:{x['result']}" for x in s["reports"])
        print(f"    site {s['target_id']:4s} ({s['kind']:9s}) conf {s['confidence']:.0%}  {chain}")
    for h in man["hazards"]:
        print(f"  {h.get('kind') or 'obstacle'} at ({h['x']:.0f}, {h['y']:.0f}) seen by {h['seen_by']} "
              f"{h['seen_at_m']} m ahead -> mapped, flagged, driven around")
    if a.render:
        import mission_render
        mission_render.render(store)
    if a.publish_run:
        M.publish_run(store, a.publish_run)
    print(f"  total {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
