"""Camera navigation inside a campaign run (Settings.nav == "camera", the default).

docs/campaign-spec.md sec. 1: every run navigates by camera vision. Each
Scout, at every step (sim/rover.py STEP_M: 2 m driven, or one turn in place):

1. renders its lamp-lit view at its true pose (sim/scene.py first_person),
   when a crater rim could be in view (GATE_* below; otherwise no rim exists),
2. finds crater rims in it (sim/vision.py Vision.rims),
3. scans with the lidar and marks hazards (sim/lidar.py),
4. moves its particle filter by its odometry (sim/nav.py). It corrects the
   filter on rims only during a scheduled fix, a half survey of one
   landmark crater from the dock's map as it stood at launch
   (CameraSortie.check; Settings.cam_fix_mode "scheduled"). Under
   "opportunistic", the test case, every frame with rims corrects it
   against the whole map,
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
# steps.npz steep_why codes: why the lidar's map missed a step onto true tilt over the
# limit (sim/lidar.py HazardMemory.why_missed), then what flagged the ones it knew of
# (sim/rover.py _note_true_steep). docs/handoffs.md, "steps.npz".
STEEP_WHY = ("never_in_band", "shadow", "too_sparse", "under_read", "judged", "orbital", "lidar_steep", "lidar_crater")
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
# Settings.cam_fix_mode and Settings.fix_stop_model (sourced there).
FIX_MODES = ("preplanned", "straight", "scheduled", "opportunistic")
# 'preplanned': at most this many fixes are planned on one leg (a guard; a
# 200 m lane at the 79.3 m spacing holds two or three).
LEG_FIXES_MAX = 10
LEG_SAMPLE_M = 5.0           # the leg's route is searched for landmarks at points this far apart (search only)
FIX_STOP_MODELS = ("exposure", "batch", "draft")
# A landmark's survey must end where the planned route on to the goal runs
# about straight: no longer than FIX_ROUTE_RATIO times the straight line plus
# FIX_ROUTE_SLACK_M (2 m planning cells, short legs). Both assumed: a guard
# against surveys that leave the Scout behind the crater on its own route.
FIX_ROUTE_RATIO = 1.5
FIX_ROUTE_SLACK_M = 10.0


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


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
        self.slope = SlopeMap(h, x0, y0, cell, mode=s.slope_test,
                              pitch_deg=s.pitch_limit_deg, roll_deg=s.roll_limit_deg)
        if s.plan_slope == "orbital":
            # What the Scout knows of the ground before it sees it: the site's
            # LOLA 5 m slope (Settings.plan_slope; SlopeMap.use_orbital).
            from sim.psr_sites import load_site
            site = load_site(w.site)
            # The slope's direction (limits by heading) from the LOLA heights it was made from.
            self.slope.use_orbital(site["slope"], site["x0"], site["y0"], site["cell"], height=site["height"])
            del site
        elif s.plan_slope != "truth":
            raise ValueError(f"plan_slope {s.plan_slope!r}: 'orbital' or 'truth'")
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
        r.retrace_m = s.retrace_track_m
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
        # Sortie record fields from this sortie alone (sim/campaign.py sortie_extra; docs/handoffs.md).
        self.extra = {"plan_slope": camp.s.plan_slope, "steep_unflagged": 0, "steep_unflagged_by": {},
                      "steep_unflagged_true_deg_max": None, "steep_unflagged_lola_deg_mean": None,
                      "steep_flagged_by": {}}
        self._orb_sum = 0.0
        self.goal_xy = (float(wx), float(wy))
        self.st = {k: [] for k in ("step", "particles", "rims", "rim_map", "rim_map_m", "rim_true", "rim_wrong", "lidar",
                                   "haz", "haz_kind", "plan", "map_ij", "map_v", "steep", "steep_why")}
        self.st_o = {k: [0] for k in ("rim", "haz", "plan", "map")}
        self.pass_ = None
        self.gap = 0
        self.n_rim_pts = self.n_rim_wrong = self.n_rim_matched = self.n_rim_unpaired = 0
        if s.cam_fix_mode not in FIX_MODES:
            raise ValueError(f"cam_fix_mode {s.cam_fix_mode!r}: one of {FIX_MODES}")
        if s.fix_stop_model not in FIX_STOP_MODELS:
            raise ValueError(f"fix_stop_model {s.fix_stop_model!r}: one of {FIX_STOP_MODELS}")
        self.fix_mode = s.cam_fix_mode
        self.opportunistic = self.fix_mode == "opportunistic"
        self._fix = None              # the scheduled fix under way (check()), else None
        self._fix_frame = False       # True while _observe() takes a fix frame
        self._fix_shift = None        # change in distance to the outer drive_to's goal across a fix
        self._passed = None           # 'preplanned': (route direction, reach) after a survey (_passed_by_survey)
        self._phase = None            # 'preplanned': the transit's or lane's waypoints (_leg_ahead)
        self._return_leg = None       # 'preplanned': the return's waypoints, from the first return drive
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
        # Scheduled fixes continue on the way home (draft 4.2: the limit applies
        # "along the whole sortie"): their stops at stop power. Their detours
        # are drive time, already in overhead().
        d = self._return_dist()
        return (d / self.v * self.overhead() * self.em["p_drive_w"]
                + d * self._fix_stop_per_m() * self.em["p_stop_w"]) / 3.6e6

    def _must_turn_back(self):
        s = self.s
        if self.energy - s.reserve_frac * s.battery_kwh <= self._return_energy():
            return "energy"
        d = self._return_dist()
        if self.t - self.t_launch + d / self.v * self.overhead() + d * self._fix_stop_per_m() >= s.sortie_h * 3600:
            return "time"
        return None

    # ------------------------------------------------------------ fixes
    # 'opportunistic': the filter corrects at every frame with rims and a
    # crater pass is the Scout's "check" (_end_pass); nothing is scheduled.
    # 'scheduled' (the default): a fix is due by distance driven
    # (Sortie._check_due, check_policy "fixed", draft Table 1 spacing) and
    # check() below drives a half survey of one landmark crater.
    def _map_predicts(self):
        return not self.opportunistic       # check() looks for its own landmark

    def work_check(self, why, force=False):
        pass

    def _fix_stops_s(self, n):
        """Seconds stopped at each of a fix's n planned observation points, and
        once at its end (Settings.fix_stop_model)."""
        s = self.s
        if s.fix_stop_model == "draft":
            return s.check_stop_s / max(n, 1), 0.0
        return s.fix_exposure_stop_s, (s.fix_batch_stop_s if s.fix_stop_model == "batch" else 0.0)

    def _fix_stop_per_m(self):
        """Expected fix stop time per metre still to drive: one fix per
        spacing_nominal, on a landmark of the middle of fix_crater_d_m."""
        s = self.s
        if self.opportunistic or not self.nav_ok:
            return 0.0
        # 'straight' takes at most a half survey's number of exposures too (_straight_tick).
        d = 0.5 * (s.fix_crater_d_m[0] + s.fix_crater_d_m[1])
        n = self._n_survey_points(d)
        each, once = self._fix_stops_s(n)
        return (n * each + once) / self.spacing_nominal

    def _n_survey_points(self, d):
        s = self.s
        return max(2, int(round(math.pi * (d / 2 + s.fix_survey_offset_m) / s.fix_survey_spacing_m)) + 1)

    def _survey_points(self, cx, cy, d):
        """A24 sec. V-C1's half survey: observation points on a half circle
        fix_survey_offset_m outside the rim, about fix_survey_spacing_m apart,
        from the side facing the Scout round the side nearer its current
        goal. Map frame, as the Scout believes it."""
        s = self.s
        R = d / 2 + s.fix_survey_offset_m
        a0 = math.atan2(self.ey - cy, self.ex - cx)
        ag = math.atan2(self.goal_xy[1] - cy, self.goal_xy[0] - cx)
        sgn = 1.0 if abs(_wrap(a0 + math.pi / 2 - ag)) <= abs(_wrap(a0 - math.pi / 2 - ag)) else -1.0
        n = self._n_survey_points(d)
        return [(cx + R * math.cos(a0 + sgn * math.pi * k / (n - 1)), cy + R * math.sin(a0 + sgn * math.pi * k / (n - 1)))
                for k in range(n)]

    def _pick_landmark(self):
        """The mapped crater, fix_crater_d_m across and ahead of the Scout toward
        its current goal, whose near rim lies within fix_reach_m of the
        estimate, whose half survey ends where the planned route on to the goal
        runs about straight, and which adds the least driving on the way to
        that goal.
        (row, cx, cy, d, points) or None."""
        s, c = self.s, self.c
        if c.map_tree is None or not len(c.map_arr):
            return None
        lo, hi = s.fix_crater_d_m
        ex, ey = self.ex, self.ey
        gx, gy = self.goal_xy
        direct = None                    # planned route from here to the goal, m (computed once needed)
        best, best_cost = None, None
        for i in c.map_tree.query_ball_point((ex, ey), s.fix_reach_m + hi / 2):
            cx, cy, d = (float(v) for v in c.map_arr[i, :3])
            if not lo <= d <= hi or math.hypot(cx - ex, cy - ey) - d / 2 > s.fix_reach_m:
                continue
            # Only a crater ahead, toward the goal: A24 sec. III-E2's landmarks
            # are craters the "planned trajectory" drives toward, and a half
            # survey ends opposite where it starts. One already passed would
            # carry the Scout back past it (nav-fix-check2: one crater
            # surveyed 11 times, 6 h near it against 0.28 h without fixes).
            if (cx - ex) * (gx - ex) + (cy - ey) * (gy - ey) <= 0:
                continue
            pts = self._survey_points(cx, cy, d)
            # From the survey's end, the Scout's own planned route to the goal
            # (sim/rover.py SlopeMap.plan, its believed frame) must run about
            # straight. Ahead in a straight line is not enough: in
            # nav-fix-check5 the route from a crater's far side led back round
            # it, and one crater was surveyed 58 times in 31 h. Requiring the
            # end to be nearer the goal was too strict (nav-fix-check6: 195
            # fixes found no landmark): corridor waypoints are 25 m apart, so
            # a survey overshoots the goal it was due on.
            after = self._route_m(pts[-1], (gx, gy))
            if not after <= FIX_ROUTE_RATIO * math.dist(pts[-1], (gx, gy)) + FIX_ROUTE_SLACK_M:
                continue
            if direct is None:
                direct = self._route_m((ex, ey), (gx, gy))
                if not math.isfinite(direct):
                    direct = math.hypot(gx - ex, gy - ey)
            arc = sum(math.dist(pts[k], pts[k + 1]) for k in range(len(pts) - 1))
            cost = math.dist((ex, ey), pts[0]) + arc + after - direct
            if best_cost is None or cost < best_cost:
                best, best_cost = (int(i), cx, cy, d, pts), cost
        return best

    def _route_m(self, a, b):
        """Length (m) of the global planner's route from a to b, inf when it finds none."""
        if math.dist(a, b) < 1e-6:
            return 0.0
        p = self.rover.slope.plan(a, b)
        if not p or len(p) == 1 and math.dist(a, b) > 3.0:
            return math.inf
        q = [tuple(a)] + [tuple(v) for v in p]
        return sum(math.dist(q[k], q[k + 1]) for k in range(len(q) - 1))

    def _blocked(self, act):
        """drive_to stops: lost, or homeward with this drive not the return.
        A fix that began on the way home drives on (act 'check')."""
        if self.lost:
            return True
        if not self.returning or act == ACT["return"]:
            return False
        return not (act == ACT["check"] and self._fix is not None and self._fix["homeward"])

    def check(self, why):
        """'scheduled' (a test case): when a fix falls due, a half survey of the
        landmark _pick_landmark finds from where the Scout stands."""
        if self.fix_mode != "scheduled" or self._fix is not None:
            return
        s = self.s
        lm = self._pick_landmark()
        if lm is None:
            # Retried once check_min_spacing_m more is driven; d_fix keeps growing.
            self.d_chk = 0.0
            self.event("fix_unavailable", est=[rnd(self.ex, 2), rnd(self.ey, 2)], bound95=rnd(self.bound, 2),
                       reason="no_landmark", reach_m=s.fix_reach_m, d_since_fix_m=rnd(self.d_fix, 1),
                       true_err_m=rnd(math.hypot(self.errx, self.erry), 3))
            return
        self._survey(*lm, why=why, in_step=True)

    def _survey(self, row, cx, cy, d, pts, why, in_step, **extra):
        """A half survey of one landmark crater: drive to each observation
        point, face the crater, take a long exposure, on which the filter
        updates against that crater alone. in_step: called from inside a
        drive step ('scheduled'), whose goal and progress this restores."""
        r = self.rover
        goal0, act0 = self.goal_xy, self.act
        g0 = math.hypot(goal0[0] - self.ex, goal0[1] - self.ey)
        pm, pt, td = self.prog_m, self.prog_t, self.time["drive"]
        each_s, once_s = self._fix_stops_s(len(pts))
        f = self._open_fix(row, cx, cy, d)
        stops = 0
        try:
            for px, py in pts:
                if not self.nav_ok or not self.drive_to(px, py, ACT["check"]):
                    break
                self._observe(cx, cy)
                stops += 1
                self.stop(each_s, "check", self.em["p_stop_w"], ACT["check"])
                if self.lost:
                    break
            if stops and once_s and not self.lost:
                self.stop(once_s, "check", self.em["p_stop_w"], ACT["check"])
        finally:
            self._fix = None
        # Progress toward survey points is not progress toward the sortie's
        # goals, but the survey's drive time is drive time (overhead()). Inside
        # a step it reaches overhead() through that step's time["drive"]
        # difference; between drives ('preplanned') it is added here.
        self.prog_m = pm
        self.prog_t = pt if in_step else pt + (self.time["drive"] - td)
        self.act = act0
        self.goal_xy = goal0
        if in_step:
            if not self.lost:
                r.set_goal(goal0)
            self._fix_shift = math.hypot(goal0[0] - self.ex, goal0[1] - self.ey) - g0
        self._close_fix(f, why, stops, len(pts), each_s, once_s, **extra)

    def _open_fix(self, row, cx, cy, d):
        """Start a fix on one landmark (the state _observe and _sense_and_log read)."""
        self._fix = f = dict(row=row, arr=np.array([[cx, cy, d]]), homeward=self.returning, frames=0, used=0,
                             pts=0, wrong=[], trows=set(), matched=0, cx=cx, cy=cy, d=d,
                             dist0=self.rover.distance, t0=self.t, err_before=math.hypot(self.errx, self.erry),
                             d_since=self.d_fix)
        return f

    def _close_fix(self, f, why, stops, planned, each_s, once_s, **extra):
        """Book a finished fix and log its check event."""
        r = self.rover
        fixed = f["used"] > 0
        wrong = fixed and len(f["wrong"]) > 0.5 * f["pts"]
        cause = self._wrong_cause(f["wrong"]) if wrong else None
        self.n_checks += 1
        # A fix that updated nothing (no rim seen on any exposure: e.g. a
        # shallow landmark, d/D 0.035 in nav-pre-t2, or one out of view) is
        # known to the Scout as no fix: the distance since its last fix keeps
        # counting, so the next one is due at once (after check_min_spacing_m).
        # Whether an update was a false match it cannot know.
        self.d_chk = 0.0
        if fixed:
            self.d_fix = 0.0
            self.t_fix = self.t
            self.n_fixes += 1
            self.n_wrong += int(wrong)
            self.anchor = f"{self.sid}#{self.n_fixes}"
        self.event("check", est=[rnd(self.ex, 2), rnd(self.ey, 2)], bound95=rnd(self.bound, 2),
                   wrong_match=bool(wrong), wrong_match_cause=cause, craters_in_view=len(f["trows"]),
                   matched=int(f["matched"] > 0), fixed=bool(fixed), reason=why, mode=self.fix_mode,
                   landmark=[rnd(f["cx"], 2), rnd(f["cy"], 2), rnd(f["d"], 2)], landmark_row=f["row"], stops=stops,
                   stops_planned=planned, frames_used=f["used"], points=f["pts"], wrong_points=len(f["wrong"]),
                   stop_s=rnd(stops * each_s + (once_s if stops else 0.0), 1),
                   detour_m=rnd(r.distance - f["dist0"], 1), fix_h=rnd((self.t - f["t0"]) / 3600, 3),
                   d_since_fix_m=rnd(f["d_since"], 1), due_m=rnd(self.spacing_nominal, 1),
                   true_err_before_m=rnd(f["err_before"], 3), true_err_m=rnd(math.hypot(self.errx, self.erry), 3),
                   **extra)

    # ------------------------------------------------- 'preplanned' legs
    def _plan_leg_fix(self, goal, ahead=()):
        """'preplanned': the landmark for the next fix, chosen along the leg
        being driven. A leg is one phase of the sortie's plan (the descent
        corridor, a transit, a lane, the return), which Sortie.run drives as
        waypoints about 25 m apart (corridor, return) up to 200 m (lanes):
        its route is Rover.set_goal's route from the estimate to the next
        waypoint `goal`, then straight on through the phase's later waypoints
        `ahead` (believed frame).

        The fix falls due `due` metres along it (spacing_nominal less d_fix).
        Candidates: craters of the Scout's map (the dock's map as it stood at
        launch: orbital plus what earlier sorties mapped) fix_crater_d_m
        across, beside the route (projecting inside it, not past an end) with
        the near rim within fix_reach_m of it. Each one's half survey runs on
        a half circle fix_survey_offset_m outside its rim, from the point
        beside it on the way in to the point beside it on the way out (along
        the route's direction there), round the side that adds less driving
        and faces the route; every observation point must be open ground on
        the Scout's own planning map. The survey leaves the route R (the
        survey radius) before the crater.

        Chosen: among surveys that leave the route within fix_plan_early_m
        before the due point, the one adding the least driving; else the first
        one after it (a late fix: the honest result where the map holds no
        landmark near the due point). The plan's `now` is True when the survey
        leaves the route before `goal` (drive_to does it on this waypoint);
        otherwise a later waypoint of the leg plans it again. None when the
        early stretch lies past the leg or the leg holds no landmark."""
        s, c, r = self.s, self.c, self.rover
        if c.map_tree is None or not len(c.map_arr):
            return None
        route = [(self.ex, self.ey)] + [tuple(map(float, p)) for p in (r.path or [goal])]
        n_hop = len(route)
        route += [tuple(map(float, p)) for p in ahead]
        P = np.asarray(route, float)
        seg = np.hypot(*(P[1:] - P[:-1]).T)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        L = float(cum[-1])
        L_hop = float(cum[n_hop - 1])
        due = max(0.0, self.spacing_nominal - self.d_fix)
        lo_s = max(0.0, due - s.fix_plan_early_m)
        if lo_s > L or L < 1e-6:
            return None
        lo, hi = s.fix_crater_d_m
        n = max(2, int(math.ceil((L - lo_s) / LEG_SAMPLE_M)) + 1)
        samples = np.array([self._along(P, cum, v) for v in np.linspace(lo_s, L, n)])
        cand = set()
        for q in c.map_tree.query_ball_point(samples, s.fix_reach_m + hi / 2 + LEG_SAMPLE_M):
            cand.update(q)
        best_in, best_late = None, None
        for i in sorted(cand):
            cx, cy, d = (float(v) for v in c.map_arr[i, :3])
            if not lo <= d <= hi:
                continue
            sc, lat = self._project(P, cum, (cx, cy))
            # Beside the leg only: a crater beyond either end projects onto that
            # end (nav-pre-t2, planning one waypoint at a time: a crater 20 m
            # past a 16 m hop's goal was surveyed, and the Scout drove 42 m back
            # to the goal and past the crater again).
            if lat - d / 2 > s.fix_reach_m or not 0.0 < sc < L:
                continue
            R = d / 2 + s.fix_survey_offset_m
            sd = sc - R
            if sd < lo_s or sd > L:
                continue
            sr = min(L, sc + R)
            pd, pr = self._along(P, cum, max(sd, 0.0)), self._along(P, cum, sr)
            fwd, back = self._along(P, cum, min(L, sc + 1.0)), self._along(P, cum, max(0.0, sc - 1.0))
            a_in = math.atan2(back[1] - fwd[1], back[0] - fwd[0])     # pointing back along the route
            got = None
            for sgn in (1.0, -1.0):
                pts = self._half_circle(cx, cy, d, a_in, sgn)
                if not all(r.slope.inside(x, y) and r.slope.open_any(x, y) for x, y in pts):
                    continue
                arc = sum(math.dist(pts[k], pts[k + 1]) for k in range(len(pts) - 1))
                cost = math.dist(pd, pts[0]) + arc + math.dist(pts[-1], pr) - (sr - max(sd, 0.0))
                # The two sides cost the same in straight lines; the one facing
                # the route (its middle point nearer the route) is the shorter
                # drive. Before 24 Nov the tie went to the far side.
                key = (round(cost, 1), self._project(P, cum, pts[len(pts) // 2])[1])
                if got is None or key < got[0]:
                    got = (key, pts)
            if got is None:
                continue
            plan = dict(row=int(i), cx=cx, cy=cy, d=d, pts=got[1], depart=pd, s_depart=sd, due=due,
                        along=(-math.cos(a_in), -math.sin(a_in)), R=R,
                        cost=got[0][0], late=sd > due, now=sd <= L_hop)
            if sd <= due:
                if best_in is None or plan["cost"] < best_in["cost"]:
                    best_in = plan
            elif due <= L and (best_late is None or (sd, plan["cost"]) < (best_late["s_depart"], best_late["cost"])):
                best_late = plan
        return best_in or best_late

    @staticmethod
    def _along(P, cum, v):
        """The point v metres along polyline P (cumulative lengths cum)."""
        k = int(np.clip(np.searchsorted(cum, v, side="right") - 1, 0, len(P) - 2))
        L = cum[k + 1] - cum[k]
        f = 0.0 if L < 1e-9 else (v - cum[k]) / L
        return (float(P[k, 0] + f * (P[k + 1, 0] - P[k, 0])), float(P[k, 1] + f * (P[k + 1, 1] - P[k, 1])))

    @staticmethod
    def _project(P, cum, q):
        """(distance along polyline P of the point nearest q, its distance from q)."""
        a, b = P[:-1], P[1:]
        ab = b - a
        L2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
        f = np.clip(((np.asarray(q) - a) * ab).sum(axis=1) / L2, 0.0, 1.0)
        foot = a + f[:, None] * ab
        dist = np.hypot(*(foot - np.asarray(q)).T)
        k = int(dist.argmin())
        return float(cum[k] + f[k] * math.sqrt(L2[k])), float(dist[k])

    def _half_circle(self, cx, cy, d, a0, sgn):
        """A24 sec. V-C1's half survey: _n_survey_points(d) observation points on
        a half circle fix_survey_offset_m outside the rim, from angle a0 round
        through sgn (+1 counter-clockwise)."""
        R = d / 2 + self.s.fix_survey_offset_m
        n = self._n_survey_points(d)
        return [(cx + R * math.cos(a0 + sgn * math.pi * k / (n - 1)), cy + R * math.sin(a0 + sgn * math.pi * k / (n - 1)))
                for k in range(n)]

    # ------------------------------------------------- 'straight'
    def _landmark_in_view(self):
        """'straight': the nearest mapped crater fix_crater_d_m across whose near
        rim lies within fix_view_m of the estimate, inside the camera's field
        of view (sim/scene.py HFOV_DEG, widened by the crater's own angular
        radius) and ahead toward the current goal. (row, cx, cy, d) or None."""
        from sim.scene import HFOV_DEG
        s, c, r = self.s, self.c, self.rover
        if c.map_tree is None or not len(c.map_arr):
            return None
        lo, hi = s.fix_crater_d_m
        ex, ey = self.ex, self.ey
        gx, gy = self.goal_xy
        h = float(r.nav.heading)
        best = None
        for i in c.map_tree.query_ball_point((ex, ey), s.fix_view_m + hi / 2):
            cx, cy, d = (float(v) for v in c.map_arr[i, :3])
            dc = math.hypot(cx - ex, cy - ey)
            if not lo <= d <= hi or dc - d / 2 > s.fix_view_m or dc <= d / 2:
                continue
            if (cx - ex) * (gx - ex) + (cy - ey) * (gy - ey) <= 0:
                continue
            half = math.asin(min(1.0, (d / 2) / dc))
            if abs(_wrap(math.atan2(cy - ey, cx - ex) - h)) > math.radians(HFOV_DEG / 2) + half:
                continue
            if best is None or dc < best[0]:
                best = (dc, int(i), cx, cy, d)
        return best[1:] if best else None

    def _straight_tick(self):
        """'straight', after each drive step. Once a fix is due, the first
        landmark to come into view starts one. While it lasts the Scout keeps
        to its route and, every fix_survey_spacing_m driven, stops, faces the
        landmark and takes an exposure, while the landmark stays within
        fix_view_m and not behind it, for at most the exposures a half survey
        of it would take (the same exposure count, so the two modes differ in
        geometry only). The fix ends when the landmark falls behind or out of
        range, or the exposures are spent."""
        s, r = self.s, self.rover
        f = self._fix
        if f is None:
            if not self.nav_ok or self.d_chk < s.check_min_spacing_m or self.d_fix < self.spacing_nominal:
                return
            lm = self._landmark_in_view()
            if lm is None:
                return
            row, cx, cy, d = lm
            f = self._open_fix(row, cx, cy, d)
            f.update(stops=0, planned=self._n_survey_points(d), next_at=r.distance)
        cx, cy, d = f["cx"], f["cy"], f["d"]
        dc = math.hypot(cx - self.ex, cy - self.ey)
        gx, gy = self.goal_xy
        ahead = (cx - self.ex) * (gx - self.ex) + (cy - self.ey) * (gy - self.ey) > 0
        in_range = d / 2 < dc and dc - d / 2 <= s.fix_view_m
        each_s, once_s = self._fix_stops_s(f["planned"])
        # Behind: past the crater, and no longer beside it.
        done = not self.nav_ok or self.lost or f["stops"] >= f["planned"] or not in_range \
            or (not ahead and dc - d / 2 > s.fix_survey_offset_m)
        if not done and r.distance >= f["next_at"]:
            act0 = self.act
            self._observe(cx, cy)
            f["stops"] += 1
            f["next_at"] = r.distance + s.fix_survey_spacing_m
            self.stop(each_s, "check", self.em["p_stop_w"], ACT["check"])
            self.act = act0
            done = self.lost or f["stops"] >= f["planned"]
        if done:
            if f["stops"] and once_s and not self.lost:
                self.stop(once_s, "check", self.em["p_stop_w"], ACT["check"])
            self._fix = None
            self._close_fix(f, "straight", f["stops"], f["planned"], each_s, once_s)

    def _observe(self, cx, cy):
        """At an observation point: turn in place to face the landmark where the
        Scout believes it is (A24 sec. IV-B: at each stop 'the rig was directed
        toward large craters'), then take the fix frame."""
        s, r, f = self.s, self.rover, self._fix
        dh = _wrap(math.atan2(cy - self.ey, cx - self.ex) - float(r.nav.heading))
        r.true = np.array([r.true[0], r.true[1], r.true[2] + dh])
        if abs(dh) > 1e-9:
            self.point_turns += 1
        self.k += 1
        self.err_prev = math.hypot(self.errx, self.erry)
        self._fix_frame = True
        try:
            lab = self._sense_and_log(moved=True, odo=self._odometry(0.0, dh))
        finally:
            self._fix_frame = False
        self._after_move(0.0, abs(dh) / math.radians(s.cam_turn_rate_deg_s), checks=False)
        f["frames"] += 1
        f["trows"].update(int(v) for v in lab["trow"] if v >= 0)
        if lab["used"]:
            f["used"] += 1
            f["pts"] += len(lab["mrow"])
            f["matched"] += int(lab["matched"].sum())
            f["wrong"] += [(int(lab["mrow"][i]), int(lab["trow"][i])) for i in np.flatnonzero(lab["wrong"])]

    def _wrong_cause(self, pairs):
        """The most common cause among false matches (mrow, trow): 'missing'
        (source crater not in the map), 'misplaced:<crater_id>' (paired with an
        added crater not its source), 'base' (another mapped crater, or no source)."""
        n0 = len(self.w.map_start)
        mapped_true = set(int(v) for v in self.map_true if v >= 0)
        causes = []
        for mrow, trow in pairs:
            if trow >= 0 and trow not in mapped_true:
                causes.append("missing")
            elif mrow >= n0 and self.map_true[mrow] != trow:
                causes.append("misplaced:" + self.c.added[mrow - n0]["crater_id"])
            else:
                causes.append("base")
        return max(set(causes), key=causes.count) if causes else None

    # ------------------------------------------------------------ motion
    def drive_to(self, gx, gy, act) -> bool:
        """Drive the estimate to (gx, gy): one leg of the sortie's plan.
        'preplanned': the leg's route is planned first, the landmark for any
        fix due on it chosen along it (_plan_leg_fix), and the Scout drives to
        where the survey leaves the route, surveys, and plans the rest of the
        leg from there (which may hold the next fix)."""
        if self.fix_mode != "preplanned" or act == ACT["check"] or self._fix is not None:
            return self._drive(gx, gy, act)
        s, r = self.s, self.rover
        if self._passed_by_survey(gx, gy):
            return True
        for _ in range(LEG_FIXES_MAX):
            self.act = act
            if self._blocked(act) or not self.nav_ok:
                break
            if math.hypot(gx - self.ex, gy - self.ey) < s.cam_arrive_m:
                return True
            self.goal_xy = (float(gx), float(gy))
            r.set_goal((gx, gy))
            plan = self._plan_leg_fix((gx, gy), self._leg_ahead(gx, gy, act))
            if plan is not None and not plan["now"]:
                break                     # the survey leaves the route on a later waypoint of this leg
            if plan is None:
                if self.d_fix >= self.spacing_nominal and self.d_chk >= s.check_min_spacing_m:
                    # Due (or overdue) and this leg holds no landmark: the next leg tries.
                    self.d_chk = 0.0
                    self.event("fix_unavailable", est=[rnd(self.ex, 2), rnd(self.ey, 2)], bound95=rnd(self.bound, 2),
                               reason="no_landmark_on_leg", reach_m=s.fix_reach_m, d_since_fix_m=rnd(self.d_fix, 1),
                               goal=[rnd(gx, 2), rnd(gy, 2)], true_err_m=rnd(math.hypot(self.errx, self.erry), 3))
                break
            if not self._drive(*plan["depart"], act):
                return False
            if self.lost or not self.nav_ok:
                break
            self._survey(plan["row"], plan["cx"], plan["cy"], plan["d"], plan["pts"], why="preplanned",
                         in_step=False, late=bool(plan["late"]), detour_planned_m=rnd(plan["cost"], 1),
                         depart_at_m=rnd(plan["s_depart"], 1), due_at_m=rnd(plan["due"], 1))
            if self.lost:
                return False
            self._passed = (plan["along"], 2 * plan["R"] + s.cam_arrive_m)
            if self._passed_by_survey(gx, gy):
                return True
        return self._drive(gx, gy, act)

    def _leg_ahead(self, gx, gy, act):
        """The waypoints of the current leg after (gx, gy), as Sortie.run will
        drive them: the descent corridor, the return (route to the floor entry,
        then the corridor up, as Sortie.run builds it on turning home), or the
        transit or lane set by transit_to and lane. [] when (gx, gy) is not on
        the leg (a search or drill drive)."""
        c = self.c
        if act == ACT["descent"]:
            lst = [tuple(map(float, p)) for p in c.corridor[1:]]
        elif act == ACT["return"]:
            if self._return_leg is None:
                # Sortie.run's own list, from the same estimate (its first drive_to is this call).
                self._return_leg = [tuple(map(float, p)) for p in c.route((self.ex, self.ey), c.entry)] +                     [tuple(map(float, p)) for p in c.corridor[::-1][1:]]
            lst = self._return_leg
        else:
            lst = self._phase or []
        for k, (x, y) in enumerate(lst):
            if abs(x - gx) < 1e-6 and abs(y - gy) < 1e-6:
                return lst[k + 1:]
        return []

    def transit_to(self, pts) -> bool:
        self._phase = [tuple(map(float, p)) for p in pts]
        try:
            return super().transit_to(pts)
        finally:
            self._phase = None

    def lane(self, ln) -> bool:
        (ax, ay), (bx, by) = ln["a"], ln["b"]
        if math.hypot(bx - self.ex, by - self.ey) < math.hypot(ax - self.ex, ay - self.ey):
            (ax, ay), (bx, by) = (bx, by), (ax, ay)        # Sortie.lane's own order
        self._phase = [(float(ax), float(ay)), (float(bx), float(by))]
        try:
            return super().lane(ln)
        finally:
            self._phase = None

    def _passed_by_survey(self, gx, gy):
        """A half survey ends about one survey radius beyond its landmark along
        the route, which can be past the end of a short leg (corridor and
        return waypoints are about 25 m apart). A waypoint the survey carried
        the Scout past (behind it along the route's direction at the
        landmark, and within the survey's span) is passed, not driven back
        to: driving back put the same landmark ahead again on the next leg
        (nav-pre-t1: 6 of 27 fixes repeated the one before). Logged as
        waypoint_skipped, reason passed_by_fix. Holds until a waypoint ahead."""
        if self._passed is None:
            return False
        (ux, uy), reach = self._passed
        dx, dy = gx - self.ex, gy - self.ey
        behind = -(dx * ux + dy * uy)              # along the route's direction at the landmark
        if 0.0 < behind <= reach:
            self.event("waypoint_skipped", reason="passed_by_fix", goal=[rnd(gx, 2), rnd(gy, 2)])
            self.skipped += 1
            return True
        self._passed = None
        return False

    def _drive(self, gx, gy, act) -> bool:
        s, r = self.s, self.rover
        self.act = act
        if self._blocked(act):
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
        pp = r.pingpong_blocks
        while True:
            if self._blocked(act):
                return False
            dist = math.hypot(gx - self.ex, gy - self.ey)
            if dist < s.cam_arrive_m or (dist < BLOCKED_ARRIVE_M and r.goal_blocked()):
                return True
            td = self.time["drive"]
            self._step()
            nd = math.hypot(gx - self.ex, gy - self.ey)
            self.prog_m += max(0.0, dist - nd)
            self.prog_t += self.time["drive"] - td
            if self._fix_shift is not None:
                # A fix's survey moved the Scout during that step. Its steps
                # without progress keep counting (a fix falls due about every
                # cam_stuck_steps steps, so a reset would never let a blocked
                # waypoint be skipped); the best distance moves by what the
                # survey added to the distance to this goal.
                best += self._fix_shift
                self._fix_shift = None
            if nd < best - PROGRESS_M:
                best, since = nd, 0
            else:
                since += 1
            if r.pingpong_blocks > pp and r.goal_blocked():
                # The Scout drove back to where it was two moves ago (the
                # back-and-forth guard, Rover._note_move) and its own map puts
                # the goal on ground it may not enter: it is as close as it
                # can get. A route planned on the orbital map can put a
                # waypoint inside a crater too small for LOLA; the Scout's
                # lidar sees it on arrival. Before 23 Nov 2026 it drove back
                # and forth at the crater's edge until cam_stuck_steps ran out
                # (all 4 skipped waypoints of spin-check-1, about 45 steps each).
                self.event("waypoint_skipped", reason="hazard", goal=[rnd(gx, 2), rnd(gy, 2)])
                self.skipped += 1
                return True
            pp = r.pingpong_blocks
            if since >= s.cam_stuck_steps:
                self.event("waypoint_skipped", reason="blocked", goal=[rnd(gx, 2), rnd(gy, 2)])
                self.skipped += 1
                return True

    def _odometry(self, d, dh):
        """sim/rover.py's odometer model with the world's drift_frac (a scale
        error) and Settings' per-step noise; the filter never sees these."""
        s, rg = self.s, self.rng_odo
        d_o = d * (1.0 + self.drift_true) + (rg.normal(0.0, s.odom_noise_frac * d) if d > 0 else 0.0)
        if self.rover.last_reverse:
            d_o = -d_o            # driven backwards: the wheels turned the other way (nav.predict)
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
        n_steep = len(r.steep_steps)
        d, dh = r.follow()
        rig.t["planner"] += time.perf_counter() - tp
        if len(r.steep_steps) > n_steep:
            self._tally_steep(r.steep_steps[n_steep:])
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
        # 'scheduled' fixes (check()) fall due here, never inside one. 'preplanned'
        # fixes are planned with each leg (drive_to); 'straight' ones start here.
        self._after_move(d, dt, checks=self.fix_mode == "scheduled" and self._fix is None)
        if self.fix_mode == "straight" and not self.lost:
            self._straight_tick()
        self._pass_update(rims)

    def _tally_steep(self, steps):
        """Count steps onto ground truly over the limit (Rover._note_true_steep)
        by what the Scout knew there; for the unflagged ones, why the lidar's
        map missed it (HazardMemory.why_missed, the map as it stood when the
        Scout chose the step). Every unflagged step is also LOLA averaging:
        the orbital slope there was at or under the limit."""
        x = self.extra
        k = len(self.st["step"])   # the step this move is logged as (_sense_and_log follows)
        for px, py, tdeg, orb, flag in steps:
            why = flag
            if flag == "none":
                why = self.lid.memory.why_missed(px, py, getattr(self.lid, "slope_mode", "footprint"))
            self.st["steep"].append([k, px, py, tdeg, orb])
            self.st["steep_why"].append(STEEP_WHY.index(why))
            if flag == "none":
                x["steep_unflagged"] += 1
                x["steep_unflagged_by"][why] = x["steep_unflagged_by"].get(why, 0) + 1
                x["steep_unflagged_true_deg_max"] = rnd(max(x["steep_unflagged_true_deg_max"] or 0.0, tdeg), 2)
                self._orb_sum += orb
                x["steep_unflagged_lola_deg_mean"] = rnd(self._orb_sum / x["steep_unflagged"], 2)
            else:
                x["steep_flagged_by"][flag] = x["steep_flagged_by"].get(flag, 0) + 1

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
        hz, kinds = self.lid.hazards(pts)     # this scan's own verdicts, for the step record
        # The rover drives on the sortie's lidar map, every cell judged afresh as
        # data merges in (sim/lidar.py HazardMemory; GESTALT sec. 4 steps 2-3).
        mhz, mkinds, clear, mgrads = self.lid.judge(pts, pose, widen_m=r.slope.widen_m)
        r.add_hazards(mhz, mkinds, clear=clear, grads=mgrads)
        map_ij, map_v = self.lid.memory.take_changes()   # for the replay (steps.npz map_*)
        t3 = time.perf_counter()
        if look:
            del img, depth
        # The filter step (sim/nav.py ParticleNav.step, spelled out): predict,
        # pair each rim with a map crater at the filter's mean before the
        # update (sim/calibrate.py's false-match rule, Alg. 1 line 4), correct.
        # 'scheduled' (Settings.cam_fix_mode): only a fix frame (_observe)
        # updates, scored against that fix's landmark alone (A24 sec. III-E2:
        # only landmark craters are labelled); every rim point pairs with it.
        nav = r.nav
        if odo is not None:
            nav.predict(*odo)
        mu, _ = nav.belief()
        hd = float(nav.heading + nav._normalised() @ nav.th)
        fix = self._fix if self._fix_frame else None
        used = odo is not None and nav.fixes_on and len(rims) > 0 and (self.opportunistic or fix is not None)
        if len(rims):
            if fix is not None:
                lw = _to_world(rims, mu[0], mu[1], hd)
                cx, cy, d = fix["arr"][0]
                mrow = np.full(len(rims), fix["row"])
                mdist = np.abs(np.hypot(lw[:, 0] - cx, lw[:, 1] - cy) - d / 2)
            else:
                mrow, mdist = _edge_pair(self.c.map_tree, self.c.map_arr, _to_world(rims, mu[0], mu[1], hd))
        if used:
            if fix is not None:
                full = nav.craters
                nav.set_map(fix["arr"])
                try:
                    used = nav.correct(rims)
                finally:
                    nav.set_map(full)
            else:
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
        self.st["map_ij"].append(map_ij)
        self.st["map_v"].append(map_v)
        self.st_o["map"].append(self.st_o["map"][-1] + len(map_v))
        plan = np.asarray(r.planned_route() if getattr(r, "local_plan", None) else [pose[:2]], np.float32)
        self.st["plan"].append(plan.reshape(-1, 2))
        self.st_o["plan"].append(self.st_o["plan"][-1] + len(plan))
        if used:
            paired = mrow >= 0
            self.n_rim_pts += int(paired.sum())          # paired rims: the false-match rate's denominator
            self.n_rim_unpaired += int((~paired).sum())  # no map crater in reach (an empty map)
            self.n_rim_wrong += int((wrong & paired).sum())
        self.n_rim_matched += int(matched.sum())
        return dict(pw=pw, mrow=mrow, trow=trow, wrong=wrong, matched=matched, used=bool(used))

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
        p, self.pass_, self.gap = self.pass_, None, 0
        if not self.opportunistic:
            # Scheduled fixes: a pass corrects nothing and is no check; its
            # unmatched rims still feed map refinement (draft 4.3).
            if p["free"]:
                self._sightings(np.concatenate(p["free"]))
            return
        matched = len(p["mrows"])
        fixed = matched >= 1
        wrong = fixed and len(p["wrong"]) > 0.5 * p["pts"]
        cause = self._wrong_cause(p["wrong"]) if wrong else None
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
            f"{sid}.map_o": np.asarray(o["map"], np.int32),
            f"{sid}.map_ij": cat("map_ij", np.int32, (2,)),
            f"{sid}.map_v": cat("map_v", np.uint8, ()),
            f"{sid}.steep": np.asarray(st["steep"], np.float32).reshape(-1, 5),
            f"{sid}.steep_why": np.asarray(st["steep_why"], np.uint8),
            "map_cell_m": np.float32(self.lid.memory.c),
        }

    def release(self):
        """Keep the arrays, drop the per-sortie hazard grids and sensors."""
        self.arrays = self.steps_arrays()
        self.st = None
        r = self.rover
        r.haz_fine = r.haz_steep = r.haz_crater = None
        self.vis = self.lid = None
