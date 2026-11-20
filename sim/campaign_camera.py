"""Camera navigation inside a campaign run (Settings.nav == "camera", the default).

docs/campaign-spec.md sec. 1: every run navigates by camera vision. Each
Scout, at every step (sim/rover.py STEP_M: 2 m driven, or one turn in place):

1. renders its lamp-lit view at its true pose (sim/scene.py first_person),
   when a crater rim could be in view (GATE_* below; otherwise no rim exists),
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
import os
import time

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from sim.campaign import ACT, K95_2D, Sortie, rnd, rng_for


class _AheadRng:
    """Vision's generator, with the one standard-normal block a camera step
    draws (the frame's depth noise, or the stream-keeping draw of a skipped
    frame) drawn on a worker thread from the step's start, so it overlaps the
    render or the rest of the step. Calls reach the generator in the same
    order as before: a pending block is collected (or, for a skipped frame,
    left drawn and dropped) before any other call, so every value is the same."""

    _pool = None

    def __init__(self, g):
        self.g, self.fut, self.shape = g, None, None

    def draw_ahead(self, shape):
        self._settle()
        if _AheadRng._pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _AheadRng._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vis-noise")
        self.shape, self.fut = tuple(shape), _AheadRng._pool.submit(self.g.standard_normal, tuple(shape))

    def _settle(self):
        if self.fut is not None:
            self.fut.result()
            self.fut = None

    def standard_normal(self, size=None, *a, **k):
        if self.fut is not None:
            if a or k or tuple(np.atleast_1d(size)) != self.shape:
                raise RuntimeError("Vision drew a block other than the one drawn ahead")
            out, self.fut = self.fut.result(), None
            return out
        return self.g.standard_normal(size, *a, **k)

    def __getattr__(self, name):
        self._settle()
        return getattr(self.g, name)
from sim.crossing import R95_2D

CAM = "cam"                  # the scene's one agency: only the sortie being simulated is in it
LIDAR_KEEP = 2               # steps.npz keeps every 2nd lidar beam row and column (record size, not physics)
PROGRESS_M = 0.5             # assumed: "getting closer" to a waypoint means by at least this much (cam_stuck_steps)
DOCK_SIGHT_M = 25.0          # assumed: the dock is found within this of the Scout's believed area (GATE_RANGE_M's view range)
PROGRESS_MIN_M = 50.0       # assumed: progress measured before CameraSortie.overhead() replaces 1 (the corridor alone is ~900 m)
# A Scout whose battery runs flat within STUCK_RADIUS_M of every true
# position it held for the last STUCK_MIN_H was stuck, not short of energy.
# Both assumed: in an hour a moving Scout covers about 1 km (top speed), and
# the s89_134e Scouts of workflow-first3d sat within 2 m for 17-24 h.
STUCK_RADIUS_M = 10.0
STUCK_MIN_H = 1.0
BLOCKED_ARRIVE_M = 2.0       # run_demo.py BLOCKED_ARRIVE_M (assumed there): arrived, when the goal is on no-go ground
SIGHT_LINK_M = 2.0           # assumed: unmatched rim points this close are one arc
SIGHT_CELL_M = 0.25          # unmatched points are thinned to one per cell of this size before clustering (memory:
                             # a long pass held 42k points and 5 GB of point pairs); the fit sees the cell means
NEIGHBOURS = 4               # nearest craters checked per rim point (labels only)
PROGRESS_EVERY = int(os.environ.get("LANTERN_CAM_PROGRESS", "0"))   # print a line every N Scout-steps (0: never)
# Render gate: a step renders and runs vision only when some true crater's rim
# could be in the camera's view. Otherwise the frame holds no rim, and a frame
# without rims leaves the filter unchanged (nav.correct runs only on rims), so
# skipping it changes nothing but Vision's random stream. Measured on check 2
# (live-nav-check2 w00003-c44d133e, bench/render/gatecheck.py): 990 frames
# outside a narrower gate (craters >= 10 m within 15 m) rendered, 0 with rims;
# kept rims reached 19.7 m. Every crater counts here, out to 25 m.
GATE_RANGE_M = 25.0          # assumed: rims are kept to 19.7 m on check 2; 5 m spare
GATE_MARGIN_DEG = 5.0        # assumed: beyond the 30 deg half view (sim/scene.py HFOV_DEG)
GATE_ON = os.environ.get("LANTERN_CAM_GATE", "1") != "0"   # 0: render every step (to check the gate)
# A skipped frame still draws the frame's depth noise (Vision.noisy_depth: one
# standard normal per pixel, the frame's only draw when it holds no rim), so a
# gated run is step-for-step identical to an ungated one. Costs ~2.6 ms a step;
# False saves it and leaves runs statistically the same, not identical.
GATE_KEEP_STREAM = True


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


def _edge_pair(tree, craters, pts):
    """(index of the crater whose edge is nearest each point, that edge
    distance): the pairing the filter's Q-score makes, no tolerance."""
    if len(pts) == 0 or tree is None:
        return np.full(len(pts), -1, int), np.full(len(pts), np.inf)
    k = min(NEIGHBOURS, len(craters))
    _, idx = tree.query(pts, k=k)
    idx = np.asarray(idx).reshape(len(pts), k)
    c = craters[idx]
    de = np.abs(np.hypot(c[..., 0] - pts[:, 0:1], c[..., 1] - pts[:, 1:2]) - c[..., 2] / 2)
    j = de.argmin(axis=1)
    return idx[np.arange(len(pts)), j], de[np.arange(len(pts)), j]


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
        self.display_dir = None
        if s.cam_display != "off":
            # The camera texture for this ground (sim/terrain_display.py run_ground_params:
            # ShadowNav's render recipe, amplitude from the ground's own roughness).
            import os
            import sys
            here = os.path.dirname(os.path.abspath(__file__))
            if here not in sys.path:
                sys.path.insert(0, here)  # terrain_display does `import terrain`
            import terrain_display as TD
            self.display_dir = TD.build_for_ground(self.ground_path)
        self.scene = Scene(h, cr, x0, y0, cell, display=s.cam_display, display_dir=self.display_dir)
        # docs/terrain/position.md row 9: the Scout's footprint tilt, or a test case.
        self.slope = SlopeMap(h, x0, y0, cell, mode=s.slope_test)
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
        self.true_r_max = float(w.craters[:, 2].max()) / 2 if len(w.craters) else 0.0
        self.frames = {"rendered": 0, "skipped_no_crater": 0, "skipped_camera_off": 0}
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
                    carved = np.asarray(f["craters"])
                same_world = (json.dumps(meta.get("world_draws"), sort_keys=True)
                              == json.dumps(json.loads(json.dumps(w.draws)), sort_keys=True))
                # Every crater carved into the ground must still be in the world
                # (WorldRanges is not in the run id; e.g. dock_clear_m drops craters).
                if len(carved):
                    from scipy.spatial import cKDTree
                    dist, _ = cKDTree(w.craters).query(carved[:, :3])
                    same_world = same_world and bool(np.all(dist < 1e-6))
                want = float(s.ground_rough_rms_m) if s.ground_roughness else 0.0
                rough_ok = abs(float(meta.get("roughness_rms_m", -1.0)) - want) < 1e-9
                if meta.get("box") == box and meta.get("seed") == camp.seed and meta.get("site") == w.site \
                        and same_world and rough_ok:
                    return path, box
            except Exception:
                pass
        # build_ground adds its own GroundBuild.margin_m; the box already holds ours.
        out = build_ground(w.site, camp.seed, box[0], box[1], box[2], box[3], camp.ranges, name=name,
                           roughness=s.ground_roughness, rough_rms_m=s.ground_rough_rms_m)
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

    def crater_in_view(self, pose):
        """True when some true crater's rim lies within GATE_RANGE_M of the
        camera and inside its view widened by GATE_MARGIN_DEG plus the
        crater's angular radius, or the camera stands inside a crater."""
        if not GATE_ON:
            return True
        if self.true_tree is None:
            return False
        from sim.scene import HFOV_DEG
        x, y, hd = pose
        idx = self.true_tree.query_ball_point((x, y), GATE_RANGE_M + self.true_r_max)
        if not idx:
            return False
        c = self.camp.world.craters[idx]
        R = c[:, 2] / 2
        dc = np.hypot(c[:, 0] - x, c[:, 1] - y)
        near = dc - R <= GATE_RANGE_M
        if np.any(near & (dc <= R)):
            return True
        ang = np.abs((np.arctan2(c[:, 1] - y, c[:, 0] - x) - hd + math.pi) % (2 * math.pi) - math.pi)
        half = np.arcsin(np.minimum(1.0, R / np.maximum(dc, 1e-6)))
        return bool(np.any(near & (ang <= math.radians(HFOV_DEG / 2 + GATE_MARGIN_DEG) + half)))


class CameraSortie(Sortie):
    """A sortie whose Scout drives and localises on its own camera, lidar
    and particle filter. State shared with Sortie: ex, ey (the estimate,
    map frame), errx, erry (estimate minus truth, so tx, ty are the truth),
    heading (true), bound (95th-pct radial, from the filter)."""

    def __init__(self, camp, scout, n, t0):
        super().__init__(camp, scout, n, t0)
        from sim.lidar import Lidar
        from sim.nav import ParticleNav, PFNoise
        from sim.rover import Rover
        from sim.vision import Vision
        s, w, rig = self.s, self.w, camp.rig
        self.rig = rig
        si = camp.scouts.index(scout)
        self.rng_odo = rng_for(camp.seed, 8, si, n)
        # The filter's motion model from this run's Settings (pf_*), not
        # sim/nav.py's import-time defaults, so a --set of them takes effect.
        noise = PFNoise(drift_sigma=s.pf_drift_sigma, step_noise_frac=s.pf_step_noise_frac,
                        heading_walk_rad=s.pf_heading_walk_rad, scale_walk=s.pf_scale_walk, sigma0_m=s.pf_sigma0_m)
        nav = ParticleNav(True, rng_for(camp.seed, 5, si, n), camp.map_arr, noise=noise)
        reg = w.draws["dock_reg_err"]
        nav.err = np.array([float(reg[0]), float(reg[1])])      # the hidden registration error: est0 = dock + err
        wx, wy = camp.corridor[1] if len(camp.corridor) > 1 else camp.entry
        h0 = math.atan2(wy - camp.dock_reg[1], wx - camp.dock_reg[0])
        self.rover = r = Rover(scout, (w.dock[0], w.dock[1], h0), nav, rig.slope, rng_for(camp.seed, 9, si, n))
        r.sensing = True
        # The filter's start belief: the dock's registered position, spread to
        # the registration bound the Scouts believe (Settings.dock_reg_bound95_m),
        # not sim/nav.py's 1 m landing sigma. Particles are scaled about their mean.
        k = (s.dock_reg_bound95_m / K95_2D) / nav.noise.sigma0_m
        mu = nav.p.mean(axis=0)
        nav.p = mu + (nav.p - mu) * k
        _, r.sigma = nav.belief()
        self.vis = Vision(rig.scene, rng_for(camp.seed, 6, si, n))
        self.vis.rng = _AheadRng(self.vis.rng)
        self.lid = Lidar(rig.scene, rng_for(camp.seed, 7, si, n))
        self.lid.slope_mode = "footprint" if camp.s.slope_test == "footprint" else "2m"
        self.map_true = self._map_truth()
        self.drift_true = w.draws["drift_frac"]
        self.k = 0
        self.prog_m = self.prog_t = 0.0      # overhead(): progress toward goals (m) and the drive time it took (s)
        self.point_turns = 0
        self.skipped = 0
        self.goal_xy = (float(wx), float(wy))
        self.st = {k: [] for k in ("step", "particles", "rims", "rim_map", "rim_map_m", "rim_true", "rim_wrong", "lidar",
                                   "haz", "haz_kind", "plan")}
        self.st_o = {k: [0] for k in ("rim", "haz", "plan")}
        self.pass_ = None
        self.gap = 0
        self.n_rim_pts = self.n_rim_wrong = self.n_rim_matched = self.n_rim_unpaired = 0
        self._sync()
        self._sense_and_log(moved=False)    # the first frame, at the dock

    # ------------------------------------------------------------ truth
    def _map_truth(self):
        """Per map row, the world crater it stands for. Start rows: the crater
        the orbital cut took (World.map_index; its orbital error does not
        make a pairing wrong). Added rows: the nearest true crater whose
        centre is within map_match_tol_m and size within merge_size_tol_frac,
        else -1 (a misplaced or phantom entry)."""
        c, s = self.c, self.s
        if c.map_tree is None or self.rig.true_tree is None:
            return np.zeros(0, int)
        d, j = self.rig.true_tree.query(c.map_arr[:, :2])
        ok = d <= s.map_match_tol_m
        dd = np.abs(self.w.craters[j, 2] - c.map_arr[:, 2]) <= s.merge_size_tol_frac * np.maximum(
            self.w.craters[j, 2], c.map_arr[:, 2])
        out = np.where(ok & dd, j, -1)
        mi = self.w.map_index
        if mi is not None:
            out[:len(mi)] = mi
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

    def overhead(self):
        """Drive hours this sortie took per hour that straight driving at top
        speed would have taken, for the same progress toward its waypoints
        (its own estimate of distance to each goal, per step, gains only).
        Turns in place, detours, back-and-forth and blocked waypoints all
        count. The Scout measures it on itself; no truth. 1 until it has
        PROGRESS_MIN_M of progress."""
        if self.prog_m < PROGRESS_MIN_M:
            return 1.0
        return max(1.0, self.prog_t / (self.prog_m / self.v))

    def _return_energy(self):
        # The sampled engine drives the route straight, so route length over
        # top speed is its real cost. A camera Scout does not: on
        # live-nav-first3 / workflow-first3 returns took 17-20 h against
        # about 4 h of straight driving, and Scouts ran flat on the way home
        # (s89_134e S1-01, S2-01). Scale by the Scout's own measured overhead.
        d = self._return_dist()
        return d / self.v * self.overhead() * self.em["p_drive_w"] / 3.6e6

    def _must_turn_back(self):
        s = self.s
        if self.energy - s.reserve_frac * s.battery_kwh <= self._return_energy():
            return "energy"
        if self.t - self.t_launch + self._return_dist() / self.v * self.overhead() >= s.sortie_h * 3600:
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
            td = self.time["drive"]
            self._step()
            nd = math.hypot(gx - self.ex, gy - self.ey)
            self.prog_m += max(0.0, dist - nd)
            self.prog_t += self.time["drive"] - td
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
        # With a star tracker (Settings.star_tracker, draft 4.6) the Scout's
        # heading is measured, not dead-reckoned (A24 sec. III-A, the filter's
        # pf_heading_walk_rad = 0), as the sampled engine already does
        # (Sortie._advance adds heading drift only without one). The camera
        # odometer added heading noise regardless, and the filter, which
        # takes heading as known, drifted 0.7 deg (median) to 3.2 deg from
        # the truth unseen (workflow-first3b/3c survey steps). The noise is
        # still drawn, so the odometer's random stream is unchanged.
        nh = rg.normal(0.0, s.odom_heading_noise_rad) if d > 0 else 0.0
        dh_o = dh + (0.0 if s.star_tracker else nh)
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
        if PROGRESS_EVERY and rig.steps % PROGRESS_EVERY == 0:
            import psutil
            mem = psutil.Process().memory_info().rss / 1e9
            print(f"{self.sid} step {self.k} (run {rig.steps}): t {self.t / 3600:.2f} h, act {self.act}, "
                  f"err {math.hypot(self.errx, self.erry):.2f} m, bound {self.bound:.2f} m, "
                  f"{rig.s_per_step()}, rss {mem:.2f} GB", flush=True)
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
        cam_ok = self.nav_ok      # a camera or lamp fault leaves the camera blind
        look = cam_ok and rig.crater_in_view(pose)   # GATE_*: no crater in view, no rim in the frame
        if cam_ok and (look or GATE_KEEP_STREAM):
            from sim.scene import FP_SIZE
            self.vis.rng.draw_ahead((FP_SIZE[1], FP_SIZE[0]))   # rims' depth noise, or the kept-stream draw
        if look:
            rig.scene.set_rover(CAM, pose)
            self.vis.set_pose(pose)   # the camera tilts with the body; vision levels with the known tilt
            img, depth = rig.scene.first_person(CAM)
            rig.frames["rendered"] += 1
        else:
            rig.frames["skipped_camera_off" if not cam_ok else "skipped_no_crater"] += 1
            # cam_ok: the block drawn ahead above keeps Vision's stream where it was.
        t1 = time.perf_counter()
        rims = self.vis.rims(img, depth) if look else np.zeros((0, 2))
        t2 = time.perf_counter()
        pts, rng_img = self.lid.scan(pose)
        hz, kinds = self.lid.hazards(pts)
        if len(hz):
            r.add_hazards(rover_to_world(self.lid.to_camera_frame(hz), pose), kinds)
        t3 = time.perf_counter()
        if look:
            del img, depth
        # The filter step (sim/nav.py ParticleNav.step, spelled out): predict,
        # pair each rim with a map crater at the filter's mean before the
        # update (sim/calibrate.py's false-match rule, Alg. 1 line 4), correct.
        nav = r.nav
        if odo is not None:
            nav.predict(*odo)
        mu, _ = nav.belief()
        hd = float(nav.heading + nav._normalised() @ nav.th)
        used = odo is not None and nav.fixes_on and len(rims) > 0
        if len(rims):
            mrow, mdist = _edge_pair(self.c.map_tree, self.c.map_arr, _to_world(rims, mu[0], mu[1], hd))
        if used:
            used = nav.correct(rims)      # False: A24's 20 m gate skipped the update (sim/nav.py)
        if odo is not None:
            est, r.sigma = nav.belief()
            r.estimate = np.asarray(est, dtype=np.float64)
        t4 = time.perf_counter()
        self._sync()
        est_h = float(nav.heading)
        if len(rims):
            # Source: the true crater whose edge lies within cam_match_tol_m of
            # the point placed at the true pose (none: a spurious edge). A
            # point is a false match unless its source is mapped and it pairs
            # with that crater's map row (sim/calibrate.py false_match_rate).
            tw = _to_world(rims, pose[0], pose[1], pose[2])
            trow = _edge_match(rig.true_tree, self.w.craters, tw, s.cam_match_tol_m)
            mt = self.map_true[mrow] if len(self.map_true) else np.full(len(rims), -1)
            wrong = ~((trow >= 0) & (mrow >= 0) & (mt == trow))
            if not used:
                wrong[:] = False          # the filter did not use them (start frame, camera off)
            matched = (mrow >= 0) & (mdist <= s.cam_match_tol_m)
            pw = _to_world(rims, self.ex, self.ey, est_h)   # sightings: the estimated frame after the update
        else:
            pw = np.zeros((0, 2))
            mrow = trow = np.zeros(0, int)
            mdist = np.zeros(0)
            wrong = matched = np.zeros(0, bool)
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
        self.st["rim_map_m"].append(np.asarray(mdist, np.float16))
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
        if used:
            paired = mrow >= 0
            self.n_rim_pts += int(paired.sum())          # paired rims: the false-match rate's denominator
            self.n_rim_unpaired += int((~paired).sum())  # no map crater in reach (an empty map)
            self.n_rim_wrong += int((wrong & paired).sum())
        self.n_rim_matched += int(matched.sum())
        return dict(pw=pw, mrow=mrow, trow=trow, wrong=wrong, matched=matched)

    # ------------------------------------------------------------ passes
    def _pass_update(self, lab):
        n = len(lab["mrow"])
        if n:
            if self.pass_ is None:
                self.pass_ = dict(k0=self.k, err_before=self.err_prev, mrows=set(), trows=set(), pts=0,
                                  matched=0, wrong=[], free=[])
            p = self.pass_
            p["k1"] = self.k
            m = lab["matched"]
            p["mrows"].update(int(v) for v in lab["mrow"][m])
            p["trows"].update(int(v) for v in lab["trow"][lab["trow"] >= 0])
            p["pts"] += n
            p["matched"] += int(m.sum())
            for i in np.flatnonzero(lab["wrong"]):
                p["wrong"].append((int(lab["mrow"][i]), int(lab["trow"][i])))
            if (~m).any():
                p["free"].append(lab["pw"][~m])
            self.gap = 0
            if p["k1"] - p["k0"] + 1 >= self.s.cam_pass_max_steps:
                self._end_pass()
        elif self.pass_ is not None:
            self.gap += 1
            if self.gap >= self.s.cam_pass_gap_steps:
                self._end_pass()

    def _before_dock(self):
        if self.pass_ is not None:
            self._end_pass()

    def _can_home(self, d_home):
        """A camera Scout skips return waypoints it cannot reach, so the end
        of its route is not the dock: on live-nav-first3 and
        workflow-first3b the last hop, driven free, was up to 2,355 m. It
        finds the dock (lit) when the dock lies inside the area it believes
        it is in, widened by what its lamp-lit camera sees."""
        return d_home <= self.bound + DOCK_SIGHT_M

    def _flat_reason(self):
        """"stuck" when the battery ran flat with the Scout held within
        STUCK_RADIUS_M of where it died for at least STUCK_MIN_H, else
        "battery". stuck_h: hours since it was last farther than that. The
        Scout's true positions decide it; this labels the loss and changes
        nothing the Scout does."""
        st = np.asarray(self.st["step"], float)
        if len(st) == 0:
            return {"reason": "battery"}
        far = np.hypot(st[:, 1] - st[-1, 1], st[:, 2] - st[-1, 2]) > STUCK_RADIUS_M
        t_left = st[np.nonzero(far)[0][-1] + 1, 0] if far.any() else self.t_launch
        held_h = (self.t - t_left) / 3600.0
        if held_h >= STUCK_MIN_H:
            return {"reason": "stuck", "stuck_h": rnd(held_h, 2)}
        return {"reason": "battery"}

    def _end_pass(self):
        s, c = self.s, self.c
        p, self.pass_, self.gap = self.pass_, None, 0
        matched = len(p["mrows"])
        fixed = matched >= 1
        wrong = fixed and len(p["wrong"]) > 0.5 * p["pts"]
        cause = None
        if wrong:
            causes = []
            n0 = len(self.w.map_start)
            mapped_true = set(int(v) for v in self.map_true if v >= 0)
            for mrow, trow in p["wrong"]:
                if trow >= 0 and trow not in mapped_true:
                    causes.append("missing")
                elif mrow >= n0 and self.map_true[mrow] != trow:
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
        key = np.floor(pts / SIGHT_CELL_M).astype(np.int64)
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        inv = inv.ravel()
        pts = np.column_stack([np.bincount(inv, pts[:, 0]), np.bincount(inv, pts[:, 1])]) / cnt[:, None]
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
            f"{sid}.rim_map_m": cat("rim_map_m", np.float16, ()),
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
