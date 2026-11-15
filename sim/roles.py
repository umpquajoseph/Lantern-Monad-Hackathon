"""Surveyor and Prospector: a two-rover mission coordinated through the registry.

Rover A is the Surveyor, rover B the Prospector. They never talk to each
other. Everything B knows about A's work it reads from the
LunarDiscoveryRegistry contract (sim/registry_client.py), and everything
either of them measured lives in the evidence store (sim/evidence.py), named
by the hash the contract holds.

Surveyor states: IDLE, EXPLORING, CANDIDATE_FOUND, NAVIGATING_TO_SITE,
INSPECTING, PUBLISHING, RESUMING, ERROR.
  Explores toward orbital candidate sites, neutron-surveying as it drives.
  At a site it scans around it, scores its confidence, and registers a
  candidate on chain. Then it drills (inspects), publishes a Positive,
  Negative or Inconclusive result with its report and the route it drove,
  and resumes.

Prospector states: IDLE, FETCHING_TARGET, PLANNING, NAVIGATING, REPLANNING,
ANALYZING, REPORTING, COMPLETED, ERROR.
  Reads the registry for sites the Surveyor found positive (the eligibility
  policy is configurable), picks the nearest by planned path cost, fetches
  the Surveyor's route by the CID on chain and checks its hash, validates it
  against its own map and reuses what is safe. While driving it can find a
  new obstacle the Surveyor never saw; it marks it and replans. At the site
  it runs a fuller instrument sequence and reports a new, linked analysis.

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
SURVEYOR_STATES = ("IDLE", "EXPLORING", "CANDIDATE_FOUND", "NAVIGATING_TO_SITE", "INSPECTING",
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
HAZARD_RADIUS_M = 4.0        # the new obstacle: a boulder field this wide
HAZARD_AT = 0.55             # placed this far along the Prospector's first route
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

    def scan(self, r, cx, cy):
        """The Surveyor's candidate scan: neutron readings at the site and
        around it, positions as the rover estimates them."""
        out = []
        ex, ey = r.estimate
        for k in range(7):
            a = 2 * math.pi * k / 6
            d = 0.0 if k == 0 else SCAN_RADIUS_M
            tx, ty = r.pose[0] + d * math.cos(a), r.pose[1] + d * math.sin(a)
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
        dur = sum(d for _, d in phases)
        r.t += dur
        r.health.spend(H.Health.science_wh([{"name": n, "dur": d} for n, d in phases]))
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
        try:
            route = self.link.plan(r.pose[:2], (tgt["x"], tgt["y"]))
        except planner_link.NoRoute as e:
            tgt["status"] = "unreachable"
            self.set_state(r, "ERROR", f"no safe route to {tgt['id']}: {e}")
            self.set_state(r, "RESUMING", "skipping an unreachable site")
            return
        self.plan_log(r, route, "planned", tgt["id"])
        self.ev(r, "depart", target_id=tgt["id"], kind=tgt["kind"], route_m=round(planner_link.route_length(route), 1),
                waypoints=len(route), planner=self.link.name)
        r.mode = "to target"
        if self.drive(r, route, abort_on_low=True) is False:
            self.board.release(tgt["id"])
            return self.finish_rover(r, "battery fell to the return threshold on the way")

        # CANDIDATE_FOUND: scan around the site, score it, register it.
        sid = RC.site_id(self.store.id, tgt["id"])
        scan = self.scan(r, tgt["x"], tgt["y"])
        best = max(w for _, _, w in scan)
        conf = float(np.clip(0.1 + 0.85 * best / 2.0, 0.05, 0.95))
        self.set_state(r, "CANDIDATE_FOUND",
                       f"site {tgt['id']}: neutron scan peak {best:.2f} wt%, confidence {conf:.0%}")
        lat, lon = science.south_polar_to_latlon(*r.estimate)
        cand = EV.make_candidate(self.store.id, sid, tgt, r.agency, r.t, r.estimate, r.sigma, (lat, lon), scan, conf)
        cref = self.store.ev.put(cand, "candidate")
        self.store.db.evidence(self.store.id, cref)
        self.site_target[sid] = tgt
        self.site_records[sid] = {"candidate": cref}
        self.set_state(r, "PUBLISHING", f"registering candidate {tgt['id']} on chain")
        ex, ey = r.estimate
        if self.chain_write(r, "registerCandidate", self.reg.register_candidate, sid, self.mkey, ex, ey, conf,
                            cref["data_hash"], cref["cid"], site_id=sid, target_id=tgt["id"],
                            confidence=round(conf, 3), report_cid=cref["cid"], evidence_hash=cref["data_hash"],
                            x=round(ex, 2), y=round(ey, 2),
                            explain=f"Surveyor registers candidate site {tgt['id']}: position ±{r.sigma:.1f} m, "
                                    f"confidence {conf:.0%}, and the hash of its scan report") is None:
            return self.set_state(r, "RESUMING", "moving on after a refused registration")

        # INSPECTING: drill it.
        self.set_state(r, "NAVIGATING_TO_SITE", f"positioning the drill over {tgt['id']}")
        self.set_state(r, "INSPECTING", f"drilling a 1 m core at {tgt['id']}")
        r.mode = "science"
        rec = self.drill(r, tgt, science.PHASES, "surveyor inspection")
        if rec is None:
            # Two stalls: an honest Inconclusive, with what was attempted.
            self.set_state(r, "ERROR", f"drill stalled twice at {tgt['id']}")
            tgt["status"] = "failed"
            failed = {"schema": EV.SCHEMA_REPORT, "mission_id": self.store.id, "site_id": sid, "rover": r.agency,
                      "observation": "surveyor inspection (drill failed)", "simulated": True, "t": round(r.t, 1),
                      "result": {"verdict": "inconclusive", "summary": "drill stalled twice; no core"}}
            rref = self.store.ev.put(failed, "report")
            true_xy, est_xy = r.tracks()
            route_obj = EV.make_route(self.store.id, r.agency, tgt["id"], science.last_route(est_xy, spacing=0.0)[0],
                                      r.t, sigma=r.sigma)
            route_ref = self.store.ev.put(route_obj, "route")
            for ref in (rref, route_ref):
                self.store.db.evidence(self.store.id, ref)
            self.set_state(r, "PUBLISHING", f"publishing an inconclusive result for {tgt['id']}")
            self.chain_write(r, "submitSurveyorInspection", self.reg.submit_surveyor_inspection, sid,
                             "Inconclusive", rref["data_hash"], rref["cid"], route_ref["data_hash"], route_ref["cid"],
                             site_id=sid, target_id=tgt["id"], result="Inconclusive", report_cid=rref["cid"],
                             route_cid=route_ref["cid"], evidence_hash=rref["data_hash"],
                             explain=f"Surveyor reports {tgt['id']} inconclusive: the drill stalled twice")
            self.published_t[sid] = r.t
            return self.set_state(r, "RESUMING", "moving on")
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
                         explain=f"Surveyor reports {tgt['id']} {result.upper()} "
                                 f"({rec['result']['reading']:.2f} wt% water) with the hash of its report "
                                 f"and of the {rec['route']['length_m']:.0f} m route it drove")
        self.published_t[sid] = r.t
        self.set_state(r, "RESUMING", "back to exploring")

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
        try:
            route, info = self.link.reuse(r.pose[:2], ref["points"], goal)
        except planner_link.NoRoute as e:
            return self.set_state(r, "ERROR", f"cannot plan to {tgt_src['id']}: {e}")
        self.ev(r, "route_reuse", target_id=tgt_src["id"], route_cid=rep["route_cid"], route_hash_ok=route_ok, **info)
        self.set_state(r, "PLANNING",
                       f"site {tgt_src['id']}: {cost:.0f} m away; Surveyor's route fetched by CID, hash "
                       f"{'matches the chain' if route_ok else 'DOES NOT match'}; {info['mode']}")
        if not self.hazard_log and len(route) >= 2:   # one new obstacle, on the first trip
            self._place_hazard(route)

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

    def _place_hazard(self, route):
        """A boulder field the Surveyor never met, on the Prospector's way."""
        pts = np.asarray(route, float)
        seg = np.hypot(*np.diff(pts, axis=0).T)
        cum = np.concatenate([[0], np.cumsum(seg)])
        s = HAZARD_AT * cum[-1]
        k = min(int(np.searchsorted(cum, s)) - 1, len(seg) - 1)
        k = max(k, 0)
        f = (s - cum[k]) / max(seg[k], 1e-9)
        x, y = pts[k] + f * (pts[k + 1] - pts[k])
        self.hidden_hazards.append({"x": float(x), "y": float(y), "radius": HAZARD_RADIUS_M, "seen": False})

    def _sense_hazards(self, r):
        for h in self.hidden_hazards:
            if h["seen"]:
                continue
            d = math.hypot(r.pose[0] - h["x"], r.pose[1] - h["y"])
            if d <= HAZARD_SENSE_M + h["radius"]:
                h["seen"] = True
                self.link.add_hazard(h["x"], h["y"], h["radius"])
                row = dict(x=round(h["x"], 2), y=round(h["y"], 2), radius=h["radius"], seen_t=round(r.t, 1),
                           seen_by=r.agency, seen_at_m=round(d - h["radius"], 1))
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
        for e in self.store.db.events(self.store.id, "science"):
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
        print(f"  new obstacle at ({h['x']:.0f}, {h['y']:.0f}) seen by {h['seen_by']} {h['seen_at_m']} m ahead -> replanned")
    if a.render:
        import mission_render
        mission_render.render(store)
    if a.publish_run:
        M.publish_run(store, a.publish_run)
    print(f"  total {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
