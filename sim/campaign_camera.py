"""Camera navigation inside a campaign run (Settings.nav == "camera", the default).

docs/campaign-spec.md sec. 1: every run navigates by camera vision. Each
Scout, at every step (sim/rover.py STEP_M: 2 m driven, or one turn in place):

1. renders its lamp-lit view at its true pose (sim/scene.py first_person),
2. finds crater rims in it (sim/vision.py Vision.rims),
3. scans with the lidar and marks hazards (sim/lidar.py),
4. moves its particle filter by its odometry and corrects it on the rims
   against the dock's crater map as it stood at launch (sim/nav.py),
5. plans its next move from its estimate around the hazards (sim/rover.py).

Frames are rendered, used and dropped; nothing is written but what the
pipeline produced (docs/handoffs.md, "Camera navigation": steps.npz and the
camera events). The sortie's plan, readings, finds, searches, drills,
energy and turn-back rules are sim/campaign.py's Sortie, unchanged: only how
the Scout moves and knows where it is changes.

The ground is the run ground (sim/site_ground.py, 1 m, the run's own
craters stamped in), built before the run over everything the run could
drive. Sorties are simulated one at a time, so a Scout's camera never sees
another Scout.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from sim.campaign import ACT, K95_2D, Sortie, rnd, rng_for
from sim.crossing import R95_2D

CAM = "cam"                  # the scene's one agency: only the sortie being simulated is in it
LIDAR_KEEP = 2               # steps.npz keeps every 2nd lidar beam row and column (record size, not physics)
PROGRESS_M = 0.5             # assumed: "getting closer" to a waypoint means by at least this much (cam_stuck_steps)
BLOCKED_ARRIVE_M = 2.0       # run_demo.py BLOCKED_ARRIVE_M (assumed there): arrived, when the goal is on no-go ground
SIGHT_LINK_M = 2.0           # assumed: unmatched rim points this close are one arc
NEIGHBOURS = 4               # nearest craters checked per rim point (labels only)


def _edge_match(tree, craters, pts, tol):
    """Index of the crater whose edge is nearest each point, within tol, else -1."""
    if len(pts) == 0 or tree is None:
        return np.full(len(pts), -1, int)
    k = min(NEIGHBOURS, len(craters))
    _, idx = tree.query(pts, k=k)
    idx = np.asarray(idx).reshape(len(pts), k)
    c = craters[idx]
    de = np.abs(np.hypot(c[..., 0] - pts[:, 0:1], c[..., 1] - pts[:, 1:2]) - c[..., 2] / 2)
    j = de.argmin(axis=1)
    out = idx[np.arange(len(pts)), j]
    out[de[np.arange(len(pts)), j] > tol] = -1
    return out


def _to_world(pts, x, y, h):
    c, s = math.cos(h), math.sin(h)
    return np.column_stack([x + c * pts[:, 0] - s * pts[:, 1], y + s * pts[:, 0] + c * pts[:, 1]])


def fit_circle(p):
    """Kasa least-squares circle: (cx, cy, r, rms of radial residuals)."""
    A = np.column_stack([p[:, 0], p[:, 1], np.ones(len(p))])
    b = p[:, 0] ** 2 + p[:, 1] ** 2
    (a, bb, c), *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = a / 2, bb / 2
    r2 = c + cx * cx + cy * cy
    if r2 <= 0:
        return cx, cy, 0.0, np.inf
    r = math.sqrt(r2)
    res = np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r
    return float(cx), float(cy), r, float(np.sqrt(np.mean(res ** 2)))


class CameraRig:
    """Per run: the fine ground, one Scene and slope map on it, the crater
    tables steps.npz indexes, and the pipeline's timings."""

    def __init__(self, camp):
        from sim.lidar import Lidar
        from sim.nav import ParticleNav
        from sim.rover import PLANNER_STANDIN, SlopeMap
        from sim.scene import Scene
        from sim.vision import Vision
        s, w = camp.s, camp.world
        self.camp = camp
        t0 = time.perf_counter()
        self.ground_path, self.box = self._ground(camp)
        from sim.terrain import load_terrain
        h, cr, x0, y0, cell = load_terrain(self.ground_path)
        self.scene = Scene(h, cr, x0, y0, cell, display=s.cam_display)
        self.slope = SlopeMap(h, x0, y0, cell)
        n = h.shape[0]
        self.ground = {"file": f"run_ground/{self.ground_path.name}", "x0": float(x0), "y0": float(y0),
                       "cell": float(cell), "bounds": [rnd(x0 - cell / 2, 2), rnd(y0 - cell / 2, 2),
                                                       rnd(x0 + (h.shape[1] - 0.5) * cell, 2),
                                                       rnd(y0 + (n - 0.5) * cell, 2)],
                       "display": self.scene.display,
                       "note": "the ground the cameras and lidar saw: sim/site_ground.py build_ground over the "
                               "box below, LOLA upsampled (interpolated) with made-up roughness and the world's "
                               "own craters stamped in",
                       "box": [rnd(v, 1) for v in self.box]}
        self.pipeline = {"nav": ParticleNav.NAME, "vision": Vision.NAME, "lidar": Lidar.NAME,
                         "planner": PLANNER_STANDIN + "; waypoints from sim/campaign.py's sortie plan",
                         "scene": f"sim/scene.py first_person, display {self.scene.display!r}, on the run ground"}
        self.true_tree = cKDTree(w.craters[:, :2]) if len(w.craters) else None
        self.map_rows, self.true_rows = {}, {}      # map row / world crater index -> table row
        self.map_tab, self.true_tab = [], []
        self.t = {"render": 0.0, "vision": 0.0, "lidar": 0.0, "filter": 0.0, "planner": 0.0, "labels": 0.0}
        self.steps = 0
        self.t_setup = time.perf_counter() - t0
        lid = Lidar(self.scene, np.random.default_rng(0))
        self.lidar_el = np.degrees(lid.el[::LIDAR_KEEP]).astype(np.float32)
        self.lidar_az = np.degrees(lid.az[::LIDAR_KEEP]).astype(np.float32)

    # -------------------------------------------------------------- ground
    @staticmethod
    def _ground(camp):
        """Build (or reuse) the run ground over the dock, the corridor and
        every lane the run's sorties could reserve, plus cam_ground_margin_m."""
        from sim.site_ground import OUT, build_ground
        s, w = camp.s, camp.world
        state = dict(camp.lane_state)
        lanes = []
        for _ in range(s.n_scouts * s.sorties_per_scout):
            got = camp._reserve_lanes(camp.entry, s.survey_m, state=state)
            if not got:
                break
            lanes += got
        pts = [w.dock, camp.dock_reg, *camp.corridor] + [p for ln in lanes for p in (ln["a"], ln["b"])]
        xy = np.array(pts, float)
        m = s.cam_ground_margin_m
        box = [float(xy[:, 0].min() - m), float(xy[:, 0].max() + m), float(xy[:, 1].min() - m),
               float(xy[:, 1].max() + m)]
        name = camp.run_id
        path = OUT / f"{name}.npz"
        if path.exists():
            try:
                with np.load(path) as f:
                    meta = json.loads(str(f["meta"]))
                if meta.get("box") == box and meta.get("seed") == camp.seed and meta.get("site") == w.site:
                    return path, box
            except Exception:
                pass
        # build_ground adds its own GroundBuild.margin_m; the box already holds ours.
        out = build_ground(w.site, camp.seed, box[0], box[1], box[2], box[3], camp.ranges, name=name)
        return out, box

    def inside(self, x, y):
        return self.slope.inside(x, y)

    # -------------------------------------------------------------- tables
    def map_index(self, rows):
        out = np.empty(len(rows), np.int32)
        for i, r in enumerate(rows):
            if r < 0:
                out[i] = -1
                continue
            k = self.map_rows.get(int(r))
            if k is None:
                k = self.map_rows[int(r)] = len(self.map_tab)
                self.map_tab.append(list(self.camp.map_rows[int(r)]))
            out[i] = k
        return out

    def true_index(self, rows):
        out = np.empty(len(rows), np.int32)
        for i, r in enumerate(rows):
            if r < 0:
                out[i] = -1
                continue
            k = self.true_rows.get(int(r))
            if k is None:
                k = self.true_rows[int(r)] = len(self.true_tab)
                self.true_tab.append(self.camp.world.craters[int(r)].tolist())
            out[i] = k
        return out

    def s_per_step(self):
        return {k: rnd(v / max(self.steps, 1), 4) for k, v in self.t.items()}


class CameraSortie(Sortie):
    """A sortie whose Scout drives and localises on its own camera, lidar
    and particle filter. State shared with Sortie: ex, ey (the estimate,
    map frame), errx, erry (estimate minus truth, so tx, ty are the truth),
    heading (true), bound (95th-pct radial, from the filter)."""

    def __init__(self, camp, scout, n, t0):
        super().__init__(camp, scout, n, t0)
        from sim.lidar import Lidar
        from sim.nav import SIGMA0_M, ParticleNav
        from sim.rover import Rover
        from sim.vision import Vision
        s, w, rig = self.s, self.w, camp.rig
        self.rig = rig
        si = camp.scouts.index(scout)
        self.rng_odo = rng_for(camp.seed, 8, si, n)
        nav = ParticleNav(True, rng_for(camp.seed, 5, si, n), camp.map_arr)
        reg = w.draws["dock_reg_err"]
        nav.err = np.array([float(reg[0]), float(reg[1])])      # the hidden registration error: est0 = dock + err
        wx, wy = camp.corridor[1] if len(camp.corridor) > 1 else camp.entry
        h0 = math.atan2(wy - camp.dock_reg[1], wx - camp.dock_reg[0])
        self.rover = r = Rover(scout, (w.dock[0], w.dock[1], h0), nav, rig.slope, rng_for(camp.seed, 9, si, n))
        r.sensing = True
        # The filter's start belief: the dock's registered position, spread to
        # the registration bound the Scouts believe (Settings.dock_reg_bound95_m),
        # not sim/nav.py's 1 m landing sigma. Particles are scaled about their mean.
        k = (s.dock_reg_bound95_m / K95_2D) / SIGMA0_M
        mu = nav.p.mean(axis=0)
        nav.p = mu + (nav.p - mu) * k
        _, r.sigma = nav.belief()
        self.vis = Vision(rig.scene, rng_for(camp.seed, 6, si, n))
        self.lid = Lidar(rig.scene, rng_for(camp.seed, 7, si, n))
        self.map_true = self._map_truth()
        self.drift_true = w.draws["drift_frac"]
        self.k = 0
        self.point_turns = 0
        self.skipped = 0
        self.goal_xy = (float(wx), float(wy))
        self.st = {k: [] for k in ("step", "particles", "rims", "rim_map", "rim_true", "rim_wrong", "lidar",
                                   "haz", "haz_kind", "plan")}
        self.st_o = {k: [0] for k in ("rim", "haz", "plan")}
        self.pass_ = None
        self.gap = 0
        self.n_rim_pts = self.n_rim_wrong = self.n_rim_matched = 0
        self._sync()
        self._sense_and_log(moved=False)    # the first frame, at the dock

    # ------------------------------------------------------------ truth
    def _map_truth(self):
        """Per map row, the world crater it stands for: the nearest true
        crater whose centre is within map_match_tol_m and size within
        merge_size_tol_frac, else -1 (a misplaced or phantom entry)."""
        c, s = self.c, self.s
        if c.map_tree is None or self.rig.true_tree is None:
            return np.zeros(0, int)
        d, j = self.rig.true_tree.query(c.map_arr[:, :2])
        ok = d <= s.map_match_tol_m
        dd = np.abs(self.w.craters[j, 2] - c.map_arr[:, 2]) <= s.merge_size_tol_frac * np.maximum(
            self.w.craters[j, 2], c.map_arr[:, 2])
        out = np.where(ok & dd, j, -1)
        return out

    # ------------------------------------------------------------ state
    @property
    def bound(self):
        return K95_2D * float(self.rover.sigma)

    def _sync(self):
        r = self.rover
        self.ex, self.ey = float(r.estimate[0]), float(r.estimate[1])
        self.errx, self.erry = self.ex - float(r.true[0]), self.ey - float(r.true[1])
        self.heading = float(r.true[2])

    def _return_energy(self):
        d = self._return_dist()
        return d / self.v * self.em["p_drive_w"] / 3.6e6

    def _must_turn_back(self):
        s = self.s
        if self.energy - s.reserve_frac * s.battery_kwh <= self._return_energy():
            return "energy"
        if self.t - self.t_launch + self._return_dist() / self.v >= s.sortie_h * 3600:
            return "time"
        return None

    # Camera Scouts correct at every step; a crater pass is their "check".
    def check(self, why):
        pass

    def work_check(self, why, force=False):
        pass

    # ------------------------------------------------------------ motion
    def drive_to(self, gx, gy, act) -> bool:
        s, r = self.s, self.rover
        self.act = act
        if self.lost or (self.returning and act != ACT["return"]):
            return False
        if math.hypot(gx - self.ex, gy - self.ey) < s.cam_arrive_m:
            return True
        self.goal_xy = (float(gx), float(gy))
        # Where the goal really is, as far as the Scout's own frame goes: off the fine ground?
        if not self.rig.inside(gx - self.errx, gy - self.erry):
            self.event("waypoint_skipped", reason="off_ground", goal=[rnd(gx, 2), rnd(gy, 2)])
            self.skipped += 1
            return True
        r.set_goal((gx, gy))
        best, since = math.hypot(gx - self.ex, gy - self.ey), 0
        while True:
            if self.lost or (self.returning and act != ACT["return"]):
                return False
            dist = math.hypot(gx - self.ex, gy - self.ey)
            if dist < s.cam_arrive_m or (dist < BLOCKED_ARRIVE_M and r.goal_blocked()):
                return True
            self._step()
            nd = math.hypot(gx - self.ex, gy - self.ey)
            if nd < best - PROGRESS_M:
                best, since = nd, 0
            else:
                since += 1
            if since >= s.cam_stuck_steps:
                self.event("waypoint_skipped", reason="blocked", goal=[rnd(gx, 2), rnd(gy, 2)])
                self.skipped += 1
                return True

    def _odometry(self, d, dh):
        """sim/rover.py's odometer model with the world's drift_frac (a scale
        error) and Settings' per-step noise; the filter never sees these."""
        s, rg = self.s, self.rng_odo
        d_o = d * (1.0 + self.drift_true) + (rg.normal(0.0, s.odom_noise_frac * d) if d > 0 else 0.0)
        dh_o = dh + (rg.normal(0.0, s.odom_heading_noise_rad) if d > 0 else 0.0)
        self.rover.distance += d
        return d_o, dh_o

    def _step(self):
        s, r, rig = self.s, self.rover, self.rig
        tp = time.perf_counter()
        d, dh = r.follow()
        rig.t["planner"] += time.perf_counter() - tp
        if d == 0.0:
            self.point_turns += 1
        self.k += 1
        self.err_prev = math.hypot(self.errx, self.erry)
        rims = self._sense_and_log(moved=True, odo=self._odometry(d, dh))
        dt = d / self.v if d > 0 else abs(dh) / math.radians(s.cam_turn_rate_deg_s)
        self._after_move(d, dt, checks=False)
        self._pass_update(rims)

    def _sense_and_log(self, moved, odo=None):
        """Render at the true pose, find rims, scan, update the filter, label
        and log the step. Returns the step's rim labels for the pass logic."""
        s, r, rig = self.s, self.rover, self.rig
        from sim.vision import rover_to_world
        pose = tuple(float(v) for v in r.true)
        t0 = time.perf_counter()
        rig.scene.set_rover(CAM, pose)
        img, depth = rig.scene.first_person(CAM)
        t1 = time.perf_counter()
        cam_ok = self.nav_ok      # a camera or lamp fault leaves the camera blind
        rims = self.vis.rims(img, depth) if cam_ok else np.zeros((0, 2))
        t2 = time.perf_counter()
        pts, rng_img = self.lid.scan(pose)
        hz, kinds = self.lid.hazards(pts)
        if len(hz):
            r.add_hazards(rover_to_world(self.lid.to_camera_frame(hz), pose), kinds)
        t3 = time.perf_counter()
        del img, depth
        if odo is not None:
            r.localize(None, odo[0], len(rims), odom=odo, rims=rims)
        t4 = time.perf_counter()
        self._sync()
        # Labels: which map crater each rim pairs with under the estimate, and
        # which true crater it really lies on.
        nav = r.nav
        est_h = float(nav.heading)
        if len(rims):
            pw = _to_world(rims, self.ex, self.ey, est_h)
            mrow = _edge_match(self.c.map_tree, self.c.map_arr, pw, s.cam_match_tol_m)
            tw = _to_world(rims, pose[0], pose[1], pose[2])
            trow = _edge_match(rig.true_tree, self.w.craters, tw, s.cam_match_tol_m)
            mt = np.where(mrow >= 0, self.map_true[np.maximum(mrow, 0)] if len(self.map_true) else -1, -1)
            wrong = (mrow >= 0) & ((trow < 0) | (mt != trow))
        else:
            pw = np.zeros((0, 2))
            mrow = trow = np.zeros(0, int)
            wrong = np.zeros(0, bool)
        t5 = time.perf_counter()
        rig.t["render"] += t1 - t0
        rig.t["vision"] += t2 - t1
        rig.t["lidar"] += t3 - t2
        rig.t["filter"] += t4 - t3
        rig.t["labels"] += t5 - t4
        rig.steps += 1
        # ---- steps.npz rows
        w = nav._normalised()
        part = np.column_stack([nav.p[:, 0] - self.ex, nav.p[:, 1] - self.ey, w]).astype(np.float16)
        self.st["step"].append([self.t, pose[0], pose[1], pose[2], self.ex, self.ey, est_h, self.bound,
                                self.energy, self.act, self.goal_xy[0], self.goal_xy[1]])
        self.st["particles"].append(part)
        self.st["rims"].append(np.asarray(rims, np.float16).reshape(-1, 2))
        self.st["rim_map"].append(rig.map_index(mrow))
        self.st["rim_true"].append(rig.true_index(trow))
        self.st["rim_wrong"].append(wrong.astype(np.uint8))
        self.st_o["rim"].append(self.st_o["rim"][-1] + len(rims))
        ri = rng_img[::LIDAR_KEEP, ::LIDAR_KEEP]
        self.st["lidar"].append(np.where(np.isfinite(ri), np.clip(np.round(ri * 100), 1, 65535), 0).astype(np.uint16))
        self.st["haz"].append(np.asarray(hz, np.float16).reshape(-1, 2))
        self.st["haz_kind"].append(np.array([{"crater": 0, "slope": 1, "rock": 2}[k] for k in kinds], np.uint8))
        self.st_o["haz"].append(self.st_o["haz"][-1] + len(hz))
        plan = np.asarray(r.planned_route() if getattr(r, "local_plan", None) else [pose[:2]], np.float32)
        self.st["plan"].append(plan.reshape(-1, 2))
        self.st_o["plan"].append(self.st_o["plan"][-1] + len(plan))
        self.n_rim_pts += len(rims)
        self.n_rim_matched += int((mrow >= 0).sum())
        self.n_rim_wrong += int(wrong.sum())
        return dict(pw=pw, mrow=mrow, trow=trow, wrong=wrong)

    # ------------------------------------------------------------ passes
    def _pass_update(self, lab):
        n = len(lab["mrow"])
        if n:
            if self.pass_ is None:
                self.pass_ = dict(k0=self.k, err_before=self.err_prev, mrows=set(), trows=set(), pts=0,
                                  matched=0, wrong=[], free=[])
            p = self.pass_
            p["k1"] = self.k
            m = lab["mrow"] >= 0
            p["mrows"].update(int(v) for v in lab["mrow"][m])
            p["trows"].update(int(v) for v in lab["trow"][lab["trow"] >= 0])
            p["pts"] += n
            p["matched"] += int(m.sum())
            for i in np.flatnonzero(lab["wrong"]):
                p["wrong"].append((int(lab["mrow"][i]), int(lab["trow"][i])))
            if (~m).any():
                p["free"].append(lab["pw"][~m])
            self.gap = 0
        elif self.pass_ is not None:
            self.gap += 1
            if self.gap >= self.s.cam_pass_gap_steps:
                self._end_pass()

    def _before_dock(self):
        if self.pass_ is not None:
            self._end_pass()

    def _end_pass(self):
        s, c = self.s, self.c
        p, self.pass_, self.gap = self.pass_, None, 0
        matched = len(p["mrows"])
        fixed = matched >= 1
        wrong = fixed and len(p["wrong"]) > 0.5 * p["matched"]
        cause = None
        if wrong:
            causes = []
            n0 = len(self.w.map_start)
            mapped_true = set(int(v) for v in self.map_true if v >= 0)
            for mrow, trow in p["wrong"]:
                if trow >= 0 and trow not in mapped_true:
                    causes.append("missing")
                elif mrow >= n0:
                    causes.append("misplaced:" + c.added[mrow - n0]["crater_id"])
                else:
                    causes.append("base")
            cause = max(set(causes), key=causes.count)
        self.n_checks += 1
        if fixed:
            self.n_fixes += 1
            self.n_wrong += int(wrong)
        self.event("check", est=[rnd(self.ex, 2), rnd(self.ey, 2)], bound95=rnd(self.bound, 2),
                   wrong_match=bool(wrong), wrong_match_cause=cause, craters_in_view=len(p["trows"]),
                   matched=matched, fixed=bool(fixed), reason="camera", points=p["pts"],
                   wrong_points=len(p["wrong"]), steps=[p["k0"], p["k1"]],
                   true_err_before_m=rnd(p["err_before"], 3), true_err_m=rnd(math.hypot(self.errx, self.erry), 3))
        if fixed:
            self.anchor = f"{self.sid}#{self.n_fixes}"
        if p["free"]:
            self._sightings(np.concatenate(p["free"]))

    def _sightings(self, pts):
        """Unmatched rim arcs -> circle fits -> sighting events (estimated frame)."""
        s = self.s
        if len(pts) < s.cam_sighting_min_points:
            return
        tree = cKDTree(pts)
        pairs = tree.query_pairs(SIGHT_LINK_M, output_type="ndarray")
        n = len(pts)
        g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)) if len(pairs) else \
            coo_matrix((n, n))
        ncomp, lab = connected_components(g, directed=False)
        for k in range(ncomp):
            q = pts[lab == k]
            if len(q) < s.cam_sighting_min_points:
                continue
            cx, cy, rr, rms = fit_circle(q)
            d = 2 * rr
            if not (rms <= s.cam_sighting_fit_rms_m and 0.5 * s.rim_detect_d_min_m <= d <= 1.5 * s.rim_detect_d_max_m):
                continue
            meas95 = R95_2D * math.hypot(s.rim_pos_noise_m, 0.5 * s.rim_size_err_frac * d)   # crossing.make_sighting's model
            ev = self.event("sighting", est=[rnd(cx, 2), rnd(cy, 2)], d_est=rnd(d, 2),
                            bound95=rnd(math.hypot(self.bound, meas95), 2), anchor=self.anchor,
                            points=int(len(q)), fit_rms_m=rnd(rms, 3))
            self.sightings.append(ev)

    # ------------------------------------------------------------ output
    def steps_arrays(self):
        sid = self.sid
        st, o = self.st, self.st_o

        def cat(key, dtype, cols):
            return np.concatenate(st[key]).astype(dtype) if st[key] else np.zeros((0,) + cols, dtype)
        return {
            f"{sid}.step": np.asarray(st["step"], np.float32).reshape(-1, 12),
            f"{sid}.particles": np.stack(st["particles"]).astype(np.float16),
            f"{sid}.rim_o": np.asarray(o["rim"], np.int32),
            f"{sid}.rims": cat("rims", np.float16, (2,)),
            f"{sid}.rim_map": cat("rim_map", np.int32, ()),
            f"{sid}.rim_true": cat("rim_true", np.int32, ()),
            f"{sid}.rim_wrong": cat("rim_wrong", np.uint8, ()),
            f"{sid}.lidar": np.stack(st["lidar"]),
            f"{sid}.haz_o": np.asarray(o["haz"], np.int32),
            f"{sid}.haz": cat("haz", np.float16, (2,)),
            f"{sid}.haz_kind": cat("haz_kind", np.uint8, ()),
            f"{sid}.plan_o": np.asarray(o["plan"], np.int32),
            f"{sid}.plan": cat("plan", np.float32, (2,)),
        }

    def release(self):
        """Keep the arrays, drop the per-sortie hazard grids and sensors."""
        self.arrays = self.steps_arrays()
        self.st = None
        r = self.rover
        r.haz_fine = r.haz_steep = r.haz_crater = None
        self.vis = self.lid = None
