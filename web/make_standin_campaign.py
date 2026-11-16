"""Stand-in campaign records for the viewer, until the campaign engine writes real ones.

Writes records in the exact format of docs/handoffs.md, "Campaign simulator"
(record.json, poses.npz, summary.csv, by_sortie.csv), to
<DATA>/campaigns/standin/. Every record carries a top-level `standin`
sentence and the page shows it. Nothing here is a result: the Scouts are
scripted (fixed survey blocks, straight legs, a scripted search and drill),
navigation error is a crude stand-in for the engine's sampled navigation, and
the constants below marked STAND-IN are this script's, not the simulator's.

    python web/make_standin_campaign.py
"""
from __future__ import annotations

import csv
import heapq
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.settings import Settings, WorldRanges  # noqa: E402
from sim.terrain import DATA, TERRAIN_DIR, load_terrain  # noqa: E402

BATCH = "standin"
OUT = DATA / "campaigns" / BATCH
STANDIN = ("Stand-in record from web/make_standin_campaign.py, not from the campaign engine. "
           "The Scouts are scripted and the navigation error is a crude stand-in. No number here is a result.")

# STAND-IN constants: this script's choices, used nowhere else.
DOCK_XY = (72150.0, 36480.0)       # on the 5 m LOLA backdrop, west of the 1 km drive area
BLOCK_W, BLOCK_H = 400.0, 200.0    # survey block, metres; 10 lines of 400 m at 20 m spacing
BLOCK_ORIGIN = (73300.0, 36050.0)  # south-west corner of block 0, inside the drive area
BLOCK_COLS, BLOCK_ROWS = 2, 4
DRIVE_SPEED = 0.115                # m/s while driving (spec 11.2 #2 is open: 7.4 or 8.8 cm/s average)
READ_S = 10.0                      # stop per neutron reading
SEARCH_S = 900.0                   # time spent searching around a find
SIGHT_RANGE_M = 10.0               # an unmapped rim is seen within this
SIGHT_MIN_D = 6.0                  # smallest crater a Scout logs as a sighting
DETECT_MIN_D = 7.5                 # A24 sec. V-B lower end, for p_wrong's missing share
FIX_EVERY_M = 300.0                # stand-in for the engine's check scheduling
STEP_M = 5.0                       # integration step; a pose row every log_spacing_m


def rnd(v, n=2):
    return round(float(v), n)


class World:
    def __init__(self, seed: int, s: Settings, r: WorldRanges):
        self.rng = np.random.default_rng(seed)
        g = self.rng
        _, craters, x0, y0, cell = load_terrain(TERRAIN_DIR / "shoemaker.npz")
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self.draws = {
            "crater_n_ge10_per_km2": 600.0,  # the Shoemaker tile's own density (terrain meta); not drawn here
            "orbital_map_min_d_m": r.orbital_map_min_d_m,
            "missing_share_large": rnd(g.uniform(*r.missing_share_large), 3),
            "n_deposits": int(g.integers(r.n_deposits[0], r.n_deposits[1] + 1)),
            "drift_frac": rnd(g.uniform(*r.drift_frac), 4),
            "dock_reg_sigma_m": rnd(g.uniform(*r.dock_reg_sigma_m), 2),
        }
        sig = self.draws["dock_reg_sigma_m"]
        self.dock_err = g.normal(0, sig, 2)
        self.draws["dock_reg_err"] = [rnd(v) for v in self.dock_err]
        big = craters[:, 2] >= r.orbital_map_min_d_m
        drop = big & (g.random(len(craters)) < self.draws["missing_share_large"])
        in_map = big & ~drop
        self.start = craters[in_map]
        self.missing = craters[~in_map]
        self.added_true = set()  # indices into self.missing now in the shared map
        # Hidden ice inside the surveyed blocks.
        bx0, by0 = BLOCK_ORIGIN
        self.ice = []
        for _ in range(self.draws["n_deposits"]):
            self.ice.append({"x": rnd(g.uniform(bx0 + 20, bx0 + BLOCK_COLS * BLOCK_W - 20)),
                             "y": rnd(g.uniform(by0 + 20, by0 + BLOCK_ROWS * BLOCK_H - 20)),
                             "radius": rnd(g.uniform(*r.deposit_radius_m), 1),
                             "peak": rnd(g.uniform(*r.deposit_peak_wt), 2)})
        det_m = self.missing[:, 2] >= DETECT_MIN_D
        self.det_missing_xy = self.missing[det_m, :2]
        self.det_missing_idx = np.nonzero(det_m)[0]
        self.det_start_xy = self.start[self.start[:, 2] >= DETECT_MIN_D, :2]
        sm = self.missing[:, 2] >= SIGHT_MIN_D
        self.sight_xy, self.sight_idx = self.missing[sm, :2], np.nonzero(sm)[0]

    def ice_wt(self, x, y):
        v = 0.0
        for d in self.ice:
            r2 = (x - d["x"]) ** 2 + (y - d["y"]) ** 2
            v = max(v, d["peak"] * math.exp(-r2 / (2 * (d["radius"] / 2) ** 2)))
        return v

    def has_deposit(self, x, y):
        return any(math.hypot(x - d["x"], y - d["y"]) <= d["radius"] for d in self.ice)


class Sortie:
    """One scripted sortie: drives in the Scout's estimated frame, logs poses and events."""

    def __init__(self, camp, scout, n, t0, wait_s):
        self.c, self.s, self.w = camp, camp.s, camp.world
        self.scout, self.n = scout, n
        self.id = f"{scout}-{n:02d}"
        self.t = t0
        self.rows, self.events = [], []
        self.time = {"drive": 0.0, "check": 0.0, "reading": 0.0, "drill": 0.0, "wait": wait_s}
        self.energy = self.s.battery_kwh
        # The dock registration error is shared by every sortie: the Scout starts believing
        # it is at the dock's mapped position, and it is really dock_err away.
        self.est = np.array(DOCK_XY, float)
        self.err = -self.w.dock_err.copy()   # est - true
        self.d_since, self.d_log, self.d_total = 0.0, 0.0, 0.0
        self.efix95 = self.s.fix_err_good[0][0] + 1.645 * self.s.fix_err_good[0][1]
        self.heading = 0.0
        self.checks = self.wrong = 0
        self.fault = None
        self.sighted = set()
        self.surveyed = set()
        self.drift_dir = camp.rng.normal(size=2)

    @property
    def true(self):
        return self.est - self.err

    def bound95(self):
        return self.efix95 + self.c.drift * self.d_since

    def row(self, act):
        x, y = self.true
        self.rows.append([self.t, x, y, self.heading, self.est[0], self.est[1], self.bound95(), self.energy, act])

    def ev(self, typ, **kw):
        x, y = self.true
        e = {"t": rnd(self.t, 1), "sortie_id": self.id, "scout": self.scout, "type": typ, "x": rnd(x), "y": rnd(y)}
        e.update(kw)
        self.events.append(e)
        return e

    def spend(self, kind, secs, watts):
        self.t += secs
        self.time[kind] += secs
        self.energy -= (watts + self.s.hotel_w) * secs / 3.6e6

    def stop(self, kind, secs, watts, act):
        self.row(act)
        self.spend(kind, secs, watts)
        self.row(act)

    def drive_to(self, target, act, survey=False):
        target = np.asarray(target, float)
        g = self.c.rng
        while True:
            d = target - self.est
            dist = float(np.hypot(*d))
            if dist < 1e-6:
                return
            step = min(STEP_M, dist)
            u = d / dist
            self.heading = math.atan2(u[1], u[0])
            self.drift_dir = self.drift_dir * 0.98 + g.normal(size=2) * 0.2
            dd = self.drift_dir / (np.hypot(*self.drift_dir) + 1e-9)
            self.est = self.est + u * step
            self.err = self.err + dd * self.c.drift * step
            self.d_since += step
            self.d_log += step
            self.d_total += step
            self.spend("drive", step / DRIVE_SPEED, self.s.drive_w)
            if self.d_log >= self.s.log_spacing_m:
                self.d_log = 0.0
                self.row(act)
            if survey:
                self.survey_step()
            if self.d_since >= FIX_EVERY_M and self.fault not in ("camera", "lamp"):
                self.check(act)

    def check(self, act):
        g, s, w = self.c.rng, self.s, self.w
        tx, ty = self.true
        cur = self.c.map_xy()
        in_view = int(np.sum(np.hypot(cur[:, 0] - tx, cur[:, 1] - ty) <= s.fix_range_m))
        near_start = np.sum(np.hypot(w.det_start_xy[:, 0] - tx, w.det_start_xy[:, 1] - ty) <= s.fix_range_m)
        dm = np.hypot(w.det_missing_xy[:, 0] - tx, w.det_missing_xy[:, 1] - ty) <= s.fix_range_m
        miss = int(sum(1 for i in w.det_missing_idx[dm] if i not in w.added_true))
        total = near_start + int(dm.sum())
        share = miss / total if total else 0.0
        p_wrong = min(1.0, s.p_wrong_base + s.p_wrong_per_missing * share)
        wrong = bool(g.random() < p_wrong)
        if wrong:
            m, sd = s.fix_err_wrong_match
        else:
            m, sd = s.fix_err_good[int(g.integers(0, len(s.fix_err_good)))]
        mag = abs(g.normal(m, sd))
        a = g.uniform(0, 2 * math.pi)
        self.row(3)
        self.spend("check", s.check_stop_s, 0.0)
        self.err = np.array([mag * math.cos(a), mag * math.sin(a)])
        self.d_since = 0.0
        self.checks += 1
        self.wrong += wrong
        self.ev("check", est=[rnd(v) for v in self.est], bound95=rnd(self.bound95()), wrong_match=wrong,
                craters_in_view=in_view)
        self.row(act)

    def survey_step(self):
        """A reading every reading_spacing_m (= STEP_M here) and sightings of unmapped rims."""
        s, w, g = self.s, self.w, self.c.rng
        tx, ty = self.true
        self.surveyed.add((int(tx // 10), int(ty // 10)))
        if self.fault != "neutron":
            self.spend("reading", READ_S, 0.0)
            wt = w.ice_wt(tx, ty) + g.normal(0, s.sensor_noise_wt)
            if wt > s.find_threshold_wt:
                est = [rnd(v) for v in self.est]
                b = rnd(self.bound95())
                self.row(4)
                self.ev("reading", reading_wt=rnd(wt, 2), est=est, bound95=b)
                near = [f for f in [*self.c.finds.values(), *self.c.pending_finds]
                        if math.hypot(f["est"][0] - est[0], f["est"][1] - est[1]) < 15.0]
                if not near:
                    fid = len(self.c.finds) + len(self.c.pending_finds) + 1
                    f = {"find_id": fid, "scout": self.scout, "sortie_id": self.id, "est": est, "bound95": b,
                         "reading_wt": rnd(wt, 2), "status": "find", "_true": [tx, ty], "_t": self.t}
                    self.c.pending_finds.append(f)
                    self.ev("find", find_id=fid, est=est, bound95=b, reading_wt=rnd(wt, 2))
        if self.fault not in ("camera", "lamp"):
            d = np.hypot(w.sight_xy[:, 0] - tx, w.sight_xy[:, 1] - ty)
            for j in np.nonzero(d <= SIGHT_RANGE_M)[0]:
                i = int(w.sight_idx[j])
                if i in self.sighted or i in w.added_true:
                    continue
                self.sighted.add(i)
                cx, cy, cd = w.missing[i]
                e = self.ev("sighting", est=[rnd(cx + self.err[0] + g.normal(0, 0.5)), rnd(cy + self.err[1] + g.normal(0, 0.5))],
                            d_est=rnd(cd * (1 + g.normal(0, 0.1)), 1), bound95=rnd(self.bound95()))
                e["_true"] = i
                self.c.sightings.append(e)

    def search(self, f):
        s, w, g = self.s, self.w, self.c.rng
        self.drive_to(f["est"], 6)
        radius = s.search_radius_k * math.hypot(f["bound95"], self.bound95()) + s.footprint_radius_m
        self.ev("search_start", find_id=f["find_id"], radius_m=rnd(radius))
        self.row(6)
        best = -1.0
        for k in range(8):
            a = 2 * math.pi * k / 8
            px, py = self.true + 0.6 * radius * np.array([math.cos(a), math.sin(a)])
            best = max(best, w.ice_wt(px, py) + g.normal(0, s.sensor_noise_wt))
        self.spend("reading", SEARCH_S, 0.0)
        res = "confirmed" if best > s.find_threshold_wt else "unconfirmed"
        f["status"] = res
        self.ev("search_end", find_id=f["find_id"], radius_m=rnd(radius), result=res,
                truth_has_deposit=w.has_deposit(*f["_true"]))
        self.row(6)

    def drill(self, f):
        self.drive_to(f["est"], 5)
        tx, ty = self.true
        self.stop("drill", self.s.drill_h * 3600, self.s.drill_w, 5)
        f["status"] = "drilled"
        self.ev("drill", find_id=f["find_id"], est=[rnd(v) for v in self.est],
                true_err_m=rnd(math.hypot(tx - f["_true"][0], ty - f["_true"][1])))


class Campaign:
    def __init__(self, seed: int, s: Settings, forced_fault=None):
        self.seed, self.s = seed, s
        self.rng = np.random.default_rng(seed + 1000)
        self.world = World(seed, s, WorldRanges())
        self.drift = self.world.draws["drift_frac"]
        self.finds = {}
        self.pending_finds = []
        self.sightings = []
        self.added = []
        self.forced_fault = forced_fault or {}
        self.scouts = [f"S{i + 1}" for i in range(s.n_scouts)]

    def map_xy(self):
        a = self.world.start[:, :2]
        if self.added:
            a = np.vstack([a, np.array([[c["x"], c["y"]] for c in self.added])])
        return a

    def merge(self, t, sortie_id):
        """Stand-in merge (part 2 writes the real one): a missing crater joins the map once
        sightings_to_add sightings from different sorties agree within merge_tol_m."""
        if not self.s.map_refinement:
            return []
        by = {}
        for e in self.sightings:
            by.setdefault(e["_true"], []).append(e)
        out = []
        for i, es in by.items():
            if i in self.world.added_true or len({e["sortie_id"] for e in es}) < self.s.sightings_to_add:
                continue
            xy = np.array([e["est"] for e in es])
            if np.max(np.hypot(*(xy - xy.mean(0)).T)) > self.s.merge_tol_m:
                continue
            cid = f"C{len(self.added) + 1}"
            c = {"crater_id": cid, "x": rnd(xy[:, 0].mean()), "y": rnd(xy[:, 1].mean()),
                 "d": rnd(np.mean([e["d_est"] for e in es]), 1), "after_sortie": sortie_id,
                 "sightings": len(es), "true_crater": int(i)}
            self.added.append(c)
            self.world.added_true.add(i)
            out.append({"t": rnd(t, 1), "sortie_id": sortie_id, "scout": sortie_id.split("-")[0],
                        "type": "crater_added", "x": c["x"], "y": c["y"], "crater_id": cid})
        return out

    def block_lines(self, g):
        b = g % (BLOCK_COLS * BLOCK_ROWS)
        second = (g // (BLOCK_COLS * BLOCK_ROWS)) % 2
        x0 = BLOCK_ORIGIN[0] + (b % BLOCK_COLS) * BLOCK_W
        y0 = BLOCK_ORIGIN[1] + (b // BLOCK_COLS) * BLOCK_H + (10.0 if second else 0.0)
        pts = []
        sp = self.s.survey_line_spacing_m
        for k in range(int(BLOCK_H // sp)):
            y = y0 + k * sp
            xs = (x0, x0 + BLOCK_W) if k % 2 == 0 else (x0 + BLOCK_W, x0)
            pts += [(xs[0], y), (xs[1], y)]
        return pts

    def run(self):
        s = self.s
        sorties, events = [], []
        q = [(i * s.dock_charge_h * 3600.0, sc, 1, 0.0) for i, sc in enumerate(self.scouts)]
        heapq.heapify(q)
        dock_free = 0.0
        g = 0
        done_finds_t = []
        map_by_n = {}
        # Contact gaps: one per contact_gaps_per_month, up to contact_gap_max_days.
        month = s.sorties_per_scout * (s.sortie_h + s.dock_charge_h) * 3600
        gap0 = self.rng.uniform(0.2, 0.6) * month
        gap1 = gap0 + self.rng.uniform(1, s.contact_gap_max_days) * 86400
        events += [{"t": rnd(gap0, 1), "sortie_id": None, "scout": None, "type": "contact_lost"},
                   {"t": rnd(gap1, 1), "sortie_id": None, "scout": None, "type": "contact_regained"}]
        while q:
            t0, sc, n, wait = heapq.heappop(q)
            so = Sortie(self, sc, n, t0, wait)
            map_n = len(self.map_xy())
            so.row(0)
            so.ev("launch", energy_kwh=rnd(so.energy, 3))
            fault = self.forced_fault.get(so.id)
            if fault is None:
                for mod, p in (("camera", s.p_fail_camera), ("lamp", s.p_fail_lamp), ("neutron", s.p_fail_neutron)):
                    if self.rng.random() < p:
                        fault = mod
                        break
            lines = self.block_lines(g)
            g += 1
            # Descent to the block, then searches and drilling, then the survey lines.
            so.drive_to(lines[0], 0)
            outcome = "ok"
            if fault:
                so.fault = fault
                so.ev("fault", module=fault)
                if fault in ("camera", "lamp"):
                    so.ev("turn_back", reason="fault")
                    outcome = "fault_return"
            if outcome == "ok":
                ready = [f for f in done_finds_t if f["status"] == "find" and f["scout"] != sc]
                for f in ready[:2]:
                    so.search(f)
                drill = [f for f in done_finds_t if f["status"] == "confirmed"][: s.drill_sites_max]
                for f in drill:
                    so.drill(f)
                so.drive_to(lines[0], 1)
                for p in lines[1:]:
                    so.drive_to(p, 1, survey=True)
            so.drive_to(DOCK_XY, 2)
            need = s.reserve_frac * s.battery_kwh
            if outcome == "ok" and so.energy < need:
                outcome = "below_reserve"
            so.row(7)
            so.ev("dock_arrive", energy_kwh=rnd(so.energy, 3))
            t_end = so.t
            for f in self.pending_finds:
                self.finds[f["find_id"]] = f
                done_finds_t.append(f)
            self.pending_finds = []
            added = self.merge(t_end, so.id)
            sorties.append({"sortie_id": so.id, "scout": sc, "n": n, "t_start": rnd(t0, 1), "t_end": rnd(t_end, 1),
                            "outcome": outcome, "faults": [fault] if fault else [],
                            "time_s": {k: rnd(v, 1) for k, v in so.time.items()},
                            "energy_end_kwh": rnd(so.energy, 3), "distance_m": rnd(so.d_total, 1),
                            "checks": so.checks, "wrong_matches": so.wrong,
                            "_rows": np.array(so.rows, np.float32), "_surveyed": so.surveyed, "_map_n": map_n})
            events += so.events + added
            start = max(t_end, dock_free)
            dock_free = start + s.dock_charge_h * 3600
            nxt = dock_free
            if fault:
                events.append({"t": rnd(t_end, 1), "sortie_id": so.id, "scout": sc, "type": "swap",
                               "x": DOCK_XY[0], "y": DOCK_XY[1], "module": fault, "downtime_h": s.swap_downtime_h})
                nxt += s.swap_downtime_h * 3600
            if n < s.sorties_per_scout:
                # The next launch waits for the charge; time in the queue is the next sortie's wait.
                heapq.heappush(q, (nxt, sc, n + 1, start - t_end))
        events.sort(key=lambda e: e["t"])
        return sorties, events

    def record(self):
        s = self.s
        sorties, events = self.run()
        for e in events:
            e.pop("_true", None)
        w = self.world
        run_id = f"w{self.seed:05d}-{s.key()}"
        made = datetime.now(timezone(timedelta(hours=-8))).isoformat(timespec="seconds")
        xs = [r[1] for so in sorties for r in so["_rows"]] + [DOCK_XY[0]]
        ys = [r[2] for so in sorties for r in so["_rows"]] + [DOCK_XY[1]]
        rec = {
            "format": "campaign-record/1", "run_id": run_id, "seed": self.seed, "made_at": made,
            "nav": "sampled", "standin": STANDIN,
            "settings": json.loads(json.dumps(asdict(s), default=list)),
            "world": w.draws,
            "terrain": {"site": "shoemaker", "file": "shoemaker.npz", "x0": w.x0, "y0": w.y0, "cell": w.cell,
                        "bounds": [rnd(min(xs) - 50, 1), rnd(min(ys) - 50, 1), rnd(max(xs) + 50, 1), rnd(max(ys) + 50, 1)]},
            "dock": {"x": DOCK_XY[0], "y": DOCK_XY[1]},
            "scouts": self.scouts,
            "crater_map": {"start": [[rnd(x, 1), rnd(y, 1), rnd(d, 1)] for x, y, d in w.start], "added": self.added},
            "sorties": [{k: v for k, v in so.items() if not k.startswith("_")} for so in sorties],
            "events": events,
            "truth": {"label": "hidden truth", "ice": w.ice,
                      "craters_missing": [[rnd(x, 1), rnd(y, 1), rnd(d, 1)] for x, y, d in w.missing]},
        }
        rec["summary"] = self.summary(rec, sorties)
        by = [{"run_id": run_id, "sortie_n": so["n"], "checks": so["checks"], "wrong_matches": so["wrong_matches"],
               "map_craters": so["_map_n"]} for so in sorties]
        return rec, {so["sortie_id"]: so["_rows"] for so in sorties}, by

    def summary(self, rec, sorties):
        s, w = self.s, self.world
        ev = rec["events"]
        drills = [e["true_err_m"] for e in ev if e["type"] == "drill"]
        checks = sum(so["checks"] for so in sorties)
        wrong = sum(so["wrong_matches"] for so in sorties)
        ends = {e["find_id"]: e for e in ev if e["type"] == "search_end"}
        conf = [i for i, e in ends.items() if e["result"] == "confirmed"]
        false_conf = [i for i in conf if not ends[i]["truth_has_deposit"]]
        conf_true = [self.finds[i]["_true"] for i in conf]
        missed = sum(1 for d in w.ice if not any(math.hypot(x - d["x"], y - d["y"]) <= d["radius"] for x, y in conf_true))
        cells = set().union(*[so["_surveyed"] for so in sorties]) if sorties else set()
        h = lambda k: rnd(sum(so["time_s"][k] for so in sorties) / 3600, 2)
        row = {"run_id": rec["run_id"], "seed": self.seed}
        for k in ("n_scouts", "check_policy", "map_refinement", "top_speed_mps", "check_stop_s", "drill_limit_m"):
            row[k] = getattr(s, k)
        for k in ("crater_n_ge10_per_km2", "drift_frac", "missing_share_large", "n_deposits", "dock_reg_sigma_m"):
            row[k] = w.draws[k]
        row.update({
            "sorties": len(sorties),
            "sorties_ok": sum(so["outcome"] == "ok" for so in sorties),
            "sorties_below_reserve": sum(so["outcome"] == "below_reserve" for so in sorties),
            "sorties_fault_return": sum(so["outcome"] == "fault_return" for so in sorties),
            "scouts_lost": sum(so["outcome"] == "lost" for so in sorties),
            "drill_err_p95_m": rnd(np.percentile(drills, 95)) if drills else "",
            "checks_per_sortie": rnd(checks / len(sorties), 2),
            "wrong_match_rate": rnd(wrong / checks, 4) if checks else "",
            "finds": len(self.finds), "confirmed": len(conf), "false_confirmed": len(false_conf),
            "deposits_missed": missed, "area_km2": rnd(len(cells) * 100 / 1e6, 4),
            "h_drive": h("drive"), "h_check": h("check"), "h_reading": h("reading"), "h_drill": h("drill"),
            "h_wait": h("wait"),
        })
        return row


def main():
    base = Settings().with_(sorties_per_scout=4)
    runs = [
        (11, base, {"S2-02": "lamp"}),
        (11, base.with_(map_refinement=False), {"S2-02": "lamp"}),
        (11, base.with_(n_scouts=2), {}),
        (12, base.with_(n_scouts=5, sorties_per_scout=3), {}),
        (12, base.with_(n_scouts=1, sorties_per_scout=6), {}),
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    rows, by_all = [], []
    for seed, s, ff in runs:
        rec, poses, by = Campaign(seed, s, ff).record()
        d = OUT / rec["run_id"]
        d.mkdir(exist_ok=True)
        (d / "record.json").write_text(json.dumps(rec, separators=(",", ":")), encoding="utf8")
        np.savez_compressed(d / "poses.npz", **poses)
        rows.append(rec["summary"])
        by_all += by
        sm = rec["summary"]
        print(f"{rec['run_id']}: {sm['sorties']} sorties, {sm['finds']} finds, {sm['confirmed']} confirmed, "
              f"{len(rec['crater_map']['added'])} craters added, {sum(len(p) for p in poses.values())} pose rows")
    for name, data in (("summary.csv", rows), ("by_sortie.csv", by_all)):
        with open(OUT / name, "w", newline="", encoding="utf8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(data[0]))
            wr.writeheader()
            wr.writerows(data)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
