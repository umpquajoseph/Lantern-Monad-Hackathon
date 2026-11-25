"""Step 4: a rover's body, odometry and a simple planner.

The rover moves its true pose, reports odometry with drift, and steers
toward a goal from where it *believes* it is (its estimate), avoiding
ground steeper than the slope limit where it really is. So with fixes off,
odometry drift moves its real path away from the intended one, as it would.

The planner: A* on a 2 m grid through ground under the slope limit, then a
local check each step that the next 2 m really is under the limit.
"""

from __future__ import annotations

import math
import os

import numpy as np


# The Scout's slope limits by heading (sim/settings.py pitch_limit_deg,
# roll_limit_deg, VIPER's; sim/footprint.py severity). Every drive step,
# every planning move and the descent corridor judge pitch along the heading
# and roll across it. SLOPE_LIMIT_DEG is the heading-free limit, the lower
# of the two: ground under it is open to every heading. Displays read it.
from sim.footprint import severity as _severity, open_any_heading as _open_any, grad_from_slope as _grad_from_slope
PITCH_LIMIT_DEG = 15.0   # sim/settings.py Settings.pitch_limit_deg (VIPER requirement)
ROLL_LIMIT_DEG = 15.0    # sim/settings.py Settings.roll_limit_deg (VIPER suspension roll range)
SLOPE_LIMIT_DEG = min(PITCH_LIMIT_DEG, ROLL_LIMIT_DEG)
# The 8 planning moves (di, dj) = (rows y, columns x), in A*'s order
# (sim/astar_fast.py DI, DJ), and each move's heading.
MOVES = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
MOVE_HEADINGS = tuple(math.atan2(di, dj) for di, dj in MOVES)
# docs/handoffs.md, step 2: slope for planning is measured across 2 m (assumed there).
SLOPE_BASELINE_M = 2.0
# PROSPECT brief p.3: the Scout must average at least ~7.4 cm/s in the dark.
SPEED_M_S = 0.074

ASTAR_COMPILED = os.environ.get("LANTERN_ASTAR_COMPILED", "1") != "0"   # sim/astar_fast.py; 0 = Python loop
STEP_M = 2.0                    # assumed: distance per sim step (one see-locate-plan-drive cycle)
# The truth's odometer (sim/settings.py drift_frac, odom_*); the filter in
# sim/nav.py has its own model (pf_*) and never reads these.
from sim import astar_fast as _astar
from sim.settings import Settings as _Settings
ODOM_BIAS = _Settings().drift_frac            # C23 sec. 1: ~2% drift, applied as a scale bias (sign assumed)
ODOM_NOISE = _Settings().odom_noise_frac      # assumed: per-step distance noise, fraction of the step
ODOM_HEADING_NOISE_RAD = _Settings().odom_heading_noise_rad  # assumed: per-step heading noise
TURN_OFFSETS_DEG = (0, 10, -10, 20, -20, 30, -30, 45, -45, 60, -60, 90, -90, 135, -135, 180)  # assumed
MAP_MARGIN_M = 15.0             # assumed: stay this far inside the map edge
LOOKAHEAD_WAYPOINTS = 4         # assumed: steer for the global waypoint ~16 m ahead
LOCAL_PLAN_MAX_CELLS = 3000     # assumed: local A* search budget (2 m cells)

# Sensor hazards come in two kinds (sim/lidar.py hazards()):
# - "slope": ground over the slope limit. No-go: never driven onto (checked
#   on the 0.25 m grid by _feasible), and a 2 m planning cell is closed when
#   at least STEEP_CELL_FRAC of it is steep (the fine check handles the rest).
# - "crater": a crater floor under gentle slopes. Drivable, but avoided: the
#   planner pays CRATER_COST extra per crater-covered cell, so it detours
#   round small craters and still reaches its goal when no detour exists.
# The earlier rule closed a whole 2 m cell for any one hazard point of either
# kind (fraction 0.01), which fenced rover A in among the small craters near
# its start on run 1 (it turned back at step 42).
STEEP_CELL_FRAC = 0.01     # assumed: any steep point closes its cell, as the fine check would refuse it
CRATER_COST = 4.0          # assumed: a crater cell costs 1 + 4 x its crater share to cross

# Assumed: how a sensing rover steers. A real rover
# drives arcs and turns at a bounded rate; here it turns by at most
# MAX_TURN_DEG per 2 m step (a turn radius of about 5.7 m) and aims at the
# local plan AIM_M ahead, so the grid's 45 deg corners are cut, not driven.
# When nothing inside that turn is clear it turns in place, as a skid-steer
# rover can, to the clear heading nearest its aim (searched every
# POINT_TURN_SCAN_DEG), or by POINT_TURN_DEG when no heading is clear.
MAX_TURN_DEG = 20.0
AIM_M = 6.0
POINT_TURN_DEG = 30.0
POINT_TURN_SCAN_DEG = 5.0
TURN_STEP_DEG = 2.5        # heading candidates inside the turn limit

# Assumed: how much of the planned route is logged for display (the camera
# view's dotted line), metres ahead of the rover: about as far as the camera
# resolves rims and the lidar judges craters (20-25 m).
PLAN_DISPLAY_M = 20.0

# Assumed: how far the rover turns in place when no way forward is clear.
STUCK_TURN_DEG = 60.0

# Retrace (assumed, no source): when the Scout's own map
# closes every way out of where it stands, it drives back along its own recent
# track, ignoring the map, until its map leaves a way out. The track's length
# is Settings.retrace_track_m (Rover.retrace_m). The check that every way out
# is closed (Rover._enclosed) floods the map from the Scout over a box
# ENCLOSE_HALF_M each side at ENCLOSE_CELL_M; it is closed when the flood
# never reaches the box's edge. It runs only after a turn in place or a
# back-and-forth block, at most once every ENCLOSE_EVERY steps.
ENCLOSE_HALF_M = 30.0    # assumed: about twice a 14 m trap crater
ENCLOSE_CELL_M = 0.5     # the lidar map's cell (sim/lidar.py HAZ_CELL_M)
ENCLOSE_EVERY = 5        # assumed: steps between checks (speed only)
LOOP_ERASE_M = 1.0       # assumed: track points this close are one place (the route back skips loops)

# Assumed: half the rover's width, from the rover model in sim/scene.py
# (wheels at y = +-0.72 m, 0.22 m wide).
ROVER_HALF_WIDTH_M = 0.83

PLANNER_STANDIN = ("planner: A* on a 2 m grid; a cell is blocked if any point within the rover's half "
                   "width is over 15 deg across 2 m, measured on the 0.25 m grid. Followed by the "
                   "rover's estimate, with a local slope check each step (sim/rover.py)")


SLOPE_TESTS = ("footprint", "widened", "point")


class SlopeMap:
    """Slope in degrees per grid point and per 2 m planning cell, by one of
    SLOPE_TESTS (Settings.slope_test, docs/terrain/position.md row 9):

    "footprint" (main case): the tilt of the Scout's footprint plane at each
    point (sim/footprint.py: least-squares plane over a 1.55 m square, brief
    p.6), not widened. A rover rests on its wheels, so the tilt of the plane
    under it is what the 15 deg limit bounds.
    "widened" (a test case): slope across 2 m as a
    central difference (docs/handoffs.md, step 2), widened by the rover's half
    width, so a point is blocked if any spot under the rover is over the limit.
    "point": the 2 m slope at the point, no widening, for the planner and the
    lidar hazards alike.

    INTERPOLATED: the grid is 5 m LOLA upsampled, plus stamped craters and
    roughness (sim/site_ground.py), so its slope is not a measurement. The
    measured slope is LOLA's own at 5 m (ldsm), which the campaign batch uses
    (sim/psr_sites.py). Every grid point is judged, not one sample per 2 m,
    which would miss crater walls between samples.
    """

    def __init__(self, height, x0, y0, cell, widen_m=None, mode="footprint",
                 pitch_deg=PITCH_LIMIT_DEG, roll_deg=ROLL_LIMIT_DEG):
        """widen_m: the margin lidar hazards (craters, rocks; slopes too except
        in "footprint") are widened by in add_hazards, so the rover's body
        stays off them: the rover's half width by default, 0 in "point".
        pitch_deg, roll_deg: the limits by heading (Settings.pitch_limit_deg,
        roll_limit_deg). "footprint" keeps the plane's gradient, so its
        slopes are judged by heading; the test cases "widened" and "point"
        keep only a magnitude and judge it against the lower limit."""
        from scipy.ndimage import maximum_filter
        if mode not in SLOPE_TESTS:
            raise ValueError(f"slope test {mode!r}: one of {SLOPE_TESTS}")
        self.mode = mode
        self.pitch_deg, self.roll_deg = float(pitch_deg), float(roll_deg)
        self.tp, self.tr = math.tan(math.radians(self.pitch_deg)), math.tan(math.radians(self.roll_deg))
        self.limit_deg = min(self.pitch_deg, self.roll_deg)   # open to every heading under this
        if widen_m is None:
            widen_m = 0.0 if mode == "point" else ROVER_HALF_WIDTH_M
        self.widen_m = float(widen_m)
        # Margin for the slope itself: the footprint plane already spans the rover.
        self.slope_widen_m = 0.0 if mode == "footprint" else self.widen_m
        k = max(1, int(round(SLOPE_BASELINE_M / cell)))
        b = k * cell
        h = np.asarray(height, dtype=np.float32)
        if mode == "footprint":
            from sim.footprint import tilt_grad
            fgx, fgy = tilt_grad(h, cell)
            fine = np.degrees(np.arctan(np.hypot(fgx, fgy)))
            self.fine_gx, self.fine_gy = fgx, fgy
        else:
            self.fine_gx = self.fine_gy = None
            half = k // 2                                   # +-1 m: a 2 m central difference
            hp = np.pad(h, half, mode="edge")
            gx = (hp[half:-half, 2 * half:] - hp[half:-half, :-2 * half]) / np.float32(2 * half * cell)
            gy = (hp[2 * half:, half:-half] - hp[:-2 * half, half:-half]) / np.float32(2 * half * cell)
            fine = np.degrees(np.arctan(np.hypot(gx, gy)))
            del hp, gx, gy
            # Widen by the rover's half width, so a cell is blocked if any part of
            # the rover standing in it would be on a steep spot.
            r = int(math.ceil(self.slope_widen_m / cell))
            if r > 0:
                fine = maximum_filter(fine, size=2 * r + 1, mode="nearest")
        # The A* grid: 2 m cell [i, j] spans x0 + j*b .. x0 + (j+1)*b (the
        # floor convention of plan()); it takes the widened slope at its
        # centre, x0 + (j + 0.5)*b, where plan() puts its waypoints. Every
        # drive step is then checked against the 0.25 m map by at().
        c0 = k // 2
        self.deg = fine[c0::k, c0::k].astype(np.float64)
        self.fine_deg = fine
        self.gx2, self.gy2 = self._grad_of(self.fine_gx, self.fine_gy, fine, (slice(c0, None, k), slice(c0, None, k)))
        self.cell = cell
        self.x0, self.y0, self.b = x0, y0, b
        self.x1 = x0 + (height.shape[1] - 1) * cell
        self.y1 = y0 + (height.shape[0] - 1) * cell

    @staticmethod
    def _grad_of(gx, gy, deg, idx):
        """(gx, gy) float64 at grid index idx: the gradient where it is kept,
        else (tan deg, NaN), a magnitude judged against the lower limit."""
        if gx is not None:
            return gx[idx].astype(np.float64), gy[idx].astype(np.float64)
        d = np.asarray(deg[idx], np.float64)
        return np.tan(np.radians(np.clip(d, 0.0, 89.9))), np.full(d.shape, np.nan)

    # ------------------------------------------------ slope by heading
    def _fine_index(self, x, y):
        j = np.clip(np.rint((np.asarray(x) - self.x0) / self.cell).astype(int), 0, self.fine_deg.shape[1] - 1)
        i = np.clip(np.rint((np.asarray(y) - self.y0) / self.cell).astype(int), 0, self.fine_deg.shape[0] - 1)
        return i, j

    def grad_true(self, x, y):
        """The ground's own gradient (gx, gy) at world points (see _grad_of)."""
        return self._grad_of(self.fine_gx, self.fine_gy, self.fine_deg, self._fine_index(x, y))

    def grad_at(self, x, y):
        """The gradient the planner reads at world points: the orbital map's
        after use_orbital(), else the ground's own (grad_true)."""
        if self.orbital:
            S, ox, oy, c = self._orbital
            j = np.clip(np.rint((np.asarray(x) - ox) / c).astype(int), 0, S.shape[1] - 1)
            i = np.clip(np.rint((np.asarray(y) - oy) / c).astype(int), 0, S.shape[0] - 1)
            gx, gy = self._orbital_g
            return gx[i, j].astype(np.float64), gy[i, j].astype(np.float64)
        return self.grad_true(x, y)

    def sev(self, x, y, heading):
        """sim/footprint.py severity of the planner's slope at world points for
        a rover facing heading: at or under 1 is within both limits."""
        gx, gy = self.grad_at(x, y)
        return _severity(gx, gy, heading, self.tp, self.tr)

    def true_sev(self, x, y, heading):
        """severity of the ground's own slope (what really happens)."""
        gx, gy = self.grad_true(x, y)
        return _severity(gx, gy, heading, self.tp, self.tr)

    def open_any(self, x, y):
        """True where the planner's slope lets some heading through."""
        gx, gy = self.grad_at(x, y)
        return _open_any(gx, gy, self.tp, self.tr)

    def _move_ok(self):
        """(8, ny, nx) bool: 2 m cell [i, j] may be entered by planning move t
        (MOVES[t]): the cell's slope within both limits along that move."""
        return np.stack([_severity(self.gx2, self.gy2, h, self.tp, self.tr) <= 1.0 for h in MOVE_HEADINGS])

    def use_orbital(self, slope, x0, y0, cell, height=None):
        """Plan and steer on an orbital slope map instead of the ground itself
        (Settings.plan_slope 'orbital'). slope: (H, W) deg, sample [i, j] at
        (x0 + j * cell, y0 + i * cell), the site file's LOLA 5 m product
        (sim/psr_sites.py 'ldsm') as it holds it. After this, deg (the A*
        grid), at() and so plan(), _feasible and goal_blocked read the orbital
        slope; true_at() and fine_deg keep the ground's own, for what happens.

        Resampling: every point, a 2 m planning cell's centre or a 0.125 m
        step sample, takes the LOLA sample whose 5 m cell holds it (nearest
        sample). No max, mean or interpolation: the product is read as it
        is, so a steep wall narrower than LOLA's own 5 m slope baseline is
        averaged away exactly as far as LOLA averages it. A NaN sample
        (no LOLA slope) is read as over the limit.

        height: the site's LOLA heights on the same samples (psr_sites
        'height', from ldem). The slope's direction, for the limits by
        heading, is its gradient's; the magnitude stays the product's. Without
        it every orbital slope is judged against the lower limit."""
        S = np.asarray(slope, np.float32)
        self._orbital = (np.where(np.isfinite(S), S, np.float32(90.0)), float(x0), float(y0), float(cell))
        self._orbital_g = _grad_from_slope(S, height, float(cell))
        self.true_deg = self.deg
        ny, nx = self.deg.shape
        xc = self.x0 + (np.arange(nx) + 0.5) * self.b
        yc = self.y0 + (np.arange(ny) + 0.5) * self.b
        self.deg = self._orbital_at(xc[None, :], yc[:, None]).astype(np.float64)
        self.gx2, self.gy2 = self.grad_at(xc[None, :], yc[:, None])
        self._ok_base = self._labels = self._astar_buf = None   # caches built on deg

    @property
    def orbital(self):
        return getattr(self, "_orbital", None) is not None

    def _orbital_at(self, x, y):
        S, ox, oy, c = self._orbital
        j = np.clip(np.rint((np.asarray(x) - ox) / c).astype(int), 0, S.shape[1] - 1)
        i = np.clip(np.rint((np.asarray(y) - oy) / c).astype(int), 0, S.shape[0] - 1)
        return S[i, j]

    def at(self, x, y):
        """Slope (deg) the planner reads at world points: the orbital map after
        use_orbital(), else the ground's own 0.25 m map (true_at)."""
        if self.orbital:
            return self._orbital_at(x, y)
        return self.true_at(x, y)

    def true_at(self, x, y):
        """The ground's own widened slope (deg) at world points, from the 0.25 m map."""
        j = np.clip(np.rint((np.asarray(x) - self.x0) / self.cell).astype(int), 0, self.fine_deg.shape[1] - 1)
        i = np.clip(np.rint((np.asarray(y) - self.y0) / self.cell).astype(int), 0, self.fine_deg.shape[0] - 1)
        return self.fine_deg[i, j]

    def inside(self, x, y):
        return (self.x0 + MAP_MARGIN_M <= x <= self.x1 - MAP_MARGIN_M) and (self.y0 + MAP_MARGIN_M <= y <= self.y1 - MAP_MARGIN_M)

    def _may_connect(self, s, g):
        """False only when no path joins planning cells s and g through the
        open cells plan() uses with no hazards (open to some move, off the
        margin), s and g themselves open. Hazards only close cells, and a
        cell open to some move is a superset of what plan() enters, so False
        holds for any plan() call. 8-connected components, cached."""
        if getattr(self, "_labels", None) is None:
            from scipy.ndimage import label
            ok = self._base_ok().any(axis=0)
            self._labels, _ = label(ok, structure=np.ones((3, 3), int))
        if max(abs(s[0] - g[0]), abs(s[1] - g[1])) <= 1:
            return True
        L = self._labels

        def around(c):
            blk = L[max(c[0] - 1, 0):c[0] + 2, max(c[1] - 1, 0):c[1] + 2]
            return set(blk[blk > 0].ravel().tolist())
        return bool(around(s) & around(g))

    def _base_ok(self):
        """(8, ny, nx) bool, cached: planning move t may enter 2 m cell [i, j]
        (_move_ok), off the MAP_MARGIN_M margin. The slope map never changes
        after use_orbital(), so it is built once."""
        if getattr(self, "_ok_base", None) is None:
            m = int(math.ceil(MAP_MARGIN_M / self.b))
            base = self._move_ok()
            base[:, :m, :] = base[:, -m:, :] = False
            base[:, :, :m] = base[:, :, -m:] = False
            self._ok_base = base
        return self._ok_base

    def plan(self, start_xy, goal_xy, max_expand=None, blocked=None, cost=None):
        """A* over the 2 m grid, 8-connected, through cells under the slope limit.

        Returns world (x, y) waypoints from start to goal, [goal] if no path
        exists, or None if max_expand cells were searched without reaching it. The rover follows them by its estimate, so drift still
        moves its real path off the planned one.
        cost: optional extra cost per 2 m cell (0 = plain ground), paid on
        entering it; the straight-line heuristic stays admissible.
        """
        import heapq
        ny, nx = self.deg.shape
        base8 = self._base_ok()
        def cell(p):  # same floor convention as at(): cell j spans [x0 + j*b, x0 + (j+1)*b)
            return (int(np.clip(math.floor((p[1] - self.y0) / self.b), 0, ny - 1)),
                    int(np.clip(math.floor((p[0] - self.x0) / self.b), 0, nx - 1)))

        s, g = cell(start_xy), cell(goal_xy)
        if max_expand is None and not self._may_connect(s, g):
            # A* would search every cell it can reach and then give up: the
            # same answer, without the search (seconds on a 2 km run ground).
            return [tuple(goal_xy)]
        if (ASTAR_COMPILED and max_expand is not None and blocked is not None and cost is not None
                and cost.dtype == np.float32 and _astar.available()):
            # The sensing rover's local search, compiled (sim/astar_fast.py):
            # the same float32 arithmetic and pop order as the loop below.
            if getattr(self, "_astar_buf", None) is None:
                N = ny * nx
                self._astar_buf = dict(g=np.zeros(N, np.float32), came=np.zeros(N, np.int64),
                                       stamp=np.zeros(N, np.int32), tag=0, hyp=_astar.hypot_table(ny, nx),
                                       okb=np.ascontiguousarray(base8).reshape(-1))   # move t, cell n: [t * N + n]
            B = self._astar_buf
            B["tag"] += 1
            st, fl = _astar.search(B["okb"], np.ascontiguousarray(blocked).reshape(-1),
                                   np.ascontiguousarray(cost).reshape(-1), B["hyp"], ny, nx,
                                   s[0] * nx + s[1], g[0] * nx + g[1], int(max_expand),
                                   B["g"], B["came"], B["stamp"], B["tag"])
            if st == 1:
                return None
            if len(fl) == 0:
                return [tuple(goal_xy)]
            path = [divmod(int(v), nx) for v in fl]
            pts = [(self.x0 + (j + 0.5) * self.b, self.y0 + (i + 0.5) * self.b) for i, j in path[1:-1]]
            return pts[::2] + [tuple(goal_xy)]
        # A cell is entered by a move only when its slope is within both
        # limits along that move (_base_ok), and it is not a hazard cell.
        ok = base8 & ~blocked[None] if blocked is not None else base8.copy()
        ok[:, s[0], s[1]] = ok[:, g[0], g[1]] = True
        # Cells are flat indices i * nx + j: heap ties then break in the same
        # (i, j) order as tuples, and lookups are on bytes and dicts, so the
        # search, its float arithmetic and its result are as before, faster.
        N = ny * nx
        okb = memoryview(np.ascontiguousarray(ok).reshape(-1))   # move t, cell n: [t * N + n]
        costf = cost.ravel() if cost is not None else None
        nbrs = [(-1, -1, 1.4142), (-1, 0, 1.0), (-1, 1, 1.4142), (0, -1, 1.0),
                (0, 1, 1.0), (1, -1, 1.4142), (1, 0, 1.0), (1, 1, 1.4142)]   # MOVES' order
        nbrs = [(di, dj, di * nx + dj, w, t * N) for t, (di, dj, w) in enumerate(nbrs)]
        gi, gj = g
        sf, gf = s[0] * nx + s[1], gi * nx + gj
        gcost = {sf: 0.0}
        came = {}
        heap = [(math.hypot(gi - s[0], gj - s[1]), sf)]
        expanded = 0
        hypot, push, pop = math.hypot, heapq.heappush, heapq.heappop
        while heap:
            _, c = pop(heap)
            expanded += 1
            if max_expand is not None and expanded > max_expand:
                return None
            if c == gf:
                break
            gc = gcost[c]
            ci, cj = divmod(c, nx)
            for di, dj, dn, w, tN in nbrs:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < ny and 0 <= nj < nx):
                    continue
                n = c + dn
                if not okb[tN + n]:
                    continue
                ng = gc + (w * (1.0 + costf[n]) if costf is not None else w)
                if ng < gcost.get(n, 1e18):
                    gcost[n] = ng
                    came[n] = c
                    push(heap, (ng + hypot(gi - ni, gj - nj), n))
        if gf not in came and gf != sf:
            return [tuple(goal_xy)]
        path = [gf]
        while path[-1] != sf:
            path.append(came[path[-1]])
        path.reverse()
        path = [divmod(v, nx) for v in path]
        pts = [(self.x0 + (j + 0.5) * self.b, self.y0 + (i + 0.5) * self.b) for i, j in path[1:-1]]
        return pts[::2] + [tuple(goal_xy)]  # every other cell: waypoints 4 m apart


class Rover:
    def __init__(self, agency, true_pose, nav, slope: SlopeMap, rng: np.random.Generator):
        self.agency = agency
        self.true = np.array(true_pose, dtype=np.float64)          # x, y, heading
        self.nav = nav
        self.slope = slope
        self.rng = rng
        est0 = self.true[:2] + nav.err       # the simulation's landing error, applied once
        if hasattr(nav, "start"):            # a real filter: give it the starting belief only
            nav.start(est0, self.true[2])    # heading assumed known at start (spec step 3)
        self.estimate = est0.copy()
        self.sigma = nav.sigma
        self.odom = np.array([est0[0], est0[1], self.true[2]])     # dead-reckoned pose, starts at the estimate
        self.distance = 0.0
        self.path = []
        self.goal = None
        # Hazards seen by this rover's camera (sim/vision.py), in the true frame:
        # relative geometry from depth, placed around where the rover really is.
        self.haz_fine = np.zeros(slope.fine_deg.shape, dtype=bool)    # either kind (goal_blocked)
        self.haz_steep = np.zeros(slope.fine_deg.shape, dtype=bool)   # no-go, widened by half width
        self.haz_crater = np.zeros(slope.fine_deg.shape, dtype=bool)  # avoided, widened by half width
        self.haz_2m = np.zeros(slope.deg.shape, dtype=bool)           # closed planning cells
        # Lidar slope cells open to some heading (sim/lidar.py: the footprint
        # plane's gradient, world frame): (gx, gy), NaN where none. A step
        # crosses one only within both limits along its heading (_feasible).
        # Slope cells closed to every heading go to haz_steep.
        self.haz_gx = np.full(slope.fine_deg.shape, np.nan, dtype=np.float16)
        self.haz_gy = np.full(slope.fine_deg.shape, np.nan, dtype=np.float16)
        # Cells closed by the back-and-forth guard (_note_move). Kept apart from
        # haz_2m because _refresh_cells reassigns haz_2m from the lidar grids
        # every step, which would reopen them; it ORs these back in.
        self.pingpong_2m = np.zeros(slope.deg.shape, dtype=bool)
        self.pingpong_blocks = 0
        self._moves = []          # sensing rovers: true xy after each of the last three moves
        self.cost_2m = np.zeros(slope.deg.shape, dtype=np.float32)    # extra cost per planning cell
        self.point_turns = 0      # sensing runs: turns in place
        self.n_hazard_cells = 0
        self.stuck_turns = 0      # turns in place because no way forward was clear
        self.reversals = 0        # sensing runs: steps driven backwards out of a dead end
        self._backed_from = None  # where the last reversal started (one reversal per dead end)
        self.last_reverse = False # the last step was driven backwards (odometry reads it)
        # Retrace (the note at ENCLOSE_HALF_M): the true xy after each move, newest last, at
        # most retrace_m long; the route back while retracing; counts.
        self.retrace_m = _Settings().retrace_track_m
        self._track = []
        self._retrace = []
        self._retrace_failed = None   # where a retrace ran out of track still enclosed
        self._steps = 0
        self._check_due = False
        self._checked_at = -ENCLOSE_EVERY
        self.retraces = 0         # retraces started (the map closed every way out)
        self.retrace_steps = 0    # steps driven back along the track
        # Steps a sensing rover planning on the orbital map drove onto ground truly
        # over the limit (_note_true_steep): (x, y, true deg, orbital deg, flag).
        self.steep_steps = []
        # True when the rover drives on its camera and lidar. The back-and-forth
        # guard and turning in place apply only then.
        self.sensing = False

    def set_goal(self, goal_xy):
        """Plan from where the rover believes it is."""
        self.goal = tuple(goal_xy)
        self.path = self.slope.plan(self.estimate, goal_xy)
        # plan() returns [goal] alone when no route exists, e.g. when the
        # rover starts on steep ground. Plan again once it has driven off it.
        self._replan = len(self.path) == 1 and math.dist(self.estimate, goal_xy) > 3.0

    def follow(self):
        """One step along the planned path. Returns (dist, dheading).

        The global path is in the rover's believed frame. Hazards are avoided
        in its own frame: the next waypoint is taken relative to the rover
        (waypoint minus estimate) and a short A* runs on the real slopes
        around it. Relative geometry is what stereo measures exactly, so
        this is honest even when the global estimate is 20 m off.
        """
        if self.sensing:
            return self._follow_sensing()
        est = self.estimate
        if getattr(self, "_replan", False) and self.slope.open_any(self.true[0], self.true[1]):
            self.set_goal(self.goal)
        # Drop waypoints already passed: everything before the nearest one ahead,
        # then any within 3 m. The local plan may pass a waypoint off to the side.
        head = self.path[:12]
        near = int(np.argmin([math.hypot(p[0] - est[0], p[1] - est[1]) for p in head]))
        self.path = self.path[near:]
        while len(self.path) > 1 and math.hypot(self.path[0][0] - est[0], self.path[0][1] - est[1]) < 3.0:
            self.path.pop(0)
        wp = self.path[min(len(self.path) - 1, LOOKAHEAD_WAYPOINTS)]
        x, y = self.true[0], self.true[1]
        tgt = (x + wp[0] - est[0], y + wp[1] - est[1])
        local = self.slope.plan((x, y), tgt, max_expand=LOCAL_PLAN_MAX_CELLS, blocked=self.haz_2m)
        self.local_plan = [(x, y)] + list(local) if local else [(x, y), tgt]   # true frame, for drawing
        aim = tgt
        if local:  # first local waypoint far enough ahead to give a stable heading
            aim = next((p for p in local if math.hypot(p[0] - x, p[1] - y) >= 3.0), local[-1])
        d = min(STEP_M, math.hypot(self.goal[0] - est[0], self.goal[1] - est[1]))
        moved, dh = self._drive_true(aim, d)
        # Back-and-forth guard: if this step returns to where the rover was two
        # steps ago, the way ahead is blocked for the fine check but not the
        # planner. Block the planning cell it was heading into, so the next
        # local plan goes another way.
        self._hist = (getattr(self, "_hist", []) + [self.true[:2].copy()])[-3:]
        if self.sensing and moved > 0 and len(self._hist) == 3 and np.hypot(*(self._hist[2] - self._hist[0])) < 0.5:
            sm = self.slope
            hx, hy = self._hist[1]
            bi = int(np.clip((hy - sm.y0) // sm.b, 0, self.haz_2m.shape[0] - 1))
            bj = int(np.clip((hx - sm.x0) // sm.b, 0, self.haz_2m.shape[1] - 1))
            self.haz_2m[bi, bj] = True
            self.pingpong_blocks = getattr(self, "pingpong_blocks", 0) + 1
        if self.sensing and moved == 0.0 and d > 1e-6:
            # Dead end: no clear heading, not even back the way it came. Turn in
            # place so the camera looks at new ground; hazards it then sees
            # feed the next local plan, which can lead off in another direction.
            turn = math.radians(STUCK_TURN_DEG)
            self.true = np.array([self.true[0], self.true[1], self.true[2] + turn])
            self.stuck_turns += 1
            return 0.0, turn
        return moved, dh

    def _waypoint_target(self):
        """The global waypoint to steer for, placed relative to where the rover
        is (waypoint minus estimate, plus the true position): the stereo and
        lidar frame. Drops waypoints already passed."""
        est = self.estimate
        if getattr(self, "_replan", False) and self.slope.open_any(self.true[0], self.true[1]):
            self.set_goal(self.goal)
        head = self.path[:12]
        near = int(np.argmin([math.hypot(p[0] - est[0], p[1] - est[1]) for p in head]))
        self.path = self.path[near:]
        while len(self.path) > 1 and math.hypot(self.path[0][0] - est[0], self.path[0][1] - est[1]) < 3.0:
            self.path.pop(0)
        wp = self.path[min(len(self.path) - 1, LOOKAHEAD_WAYPOINTS)]
        x, y = self.true[0], self.true[1]
        return (x + wp[0] - est[0], y + wp[1] - est[1])

    def _follow_sensing(self):
        """One step for a rover on its own sensors: local A* around what the
        lidar found (steep = closed, crater = costly), then a turn of at most
        MAX_TURN_DEG toward the plan AIM_M ahead, or a turn in place."""
        est = self.estimate
        self.last_reverse = False
        self._steps += 1
        if self._retrace:
            return self._retrace_step()
        if self._check_due and self._start_retrace():
            return self._retrace_step()
        tgt = self._waypoint_target()
        x, y = self.true[0], self.true[1]
        local = self.slope.plan((x, y), tgt, max_expand=LOCAL_PLAN_MAX_CELLS, blocked=self.haz_2m, cost=self.cost_2m)
        self.local_plan = [(x, y)] + list(local) if local else [(x, y), tgt]   # true frame, for drawing
        aim = self._aim_along(self.local_plan, AIM_M)
        d = min(STEP_M, math.hypot(self.goal[0] - est[0], self.goal[1] - est[1]))
        if d < 1e-6:
            return 0.0, 0.0
        h = self.true[2]
        bearing = math.atan2(aim[1] - y, aim[0] - x)
        want = (bearing - h + math.pi) % (2 * math.pi) - math.pi
        lim = math.radians(MAX_TURN_DEG)
        best, best_cost = None, None
        if abs(want) <= math.pi / 2 + lim:      # aim not behind: drive, turning toward it
            dhs = [math.radians(float(off)) for off in np.arange(-MAX_TURN_DEG, MAX_TURN_DEG + 1e-9, TURN_STEP_DEG)]
            feas, share = self._feasible_many(x, y, [h + dh for dh in dhs], d)
            for dh, ok, sh in zip(dhs, feas, share):
                if not ok:
                    continue
                # Heading error to the aim (rad), plus the crater ground the step crosses.
                c = abs(want - dh) + 0.1 * CRATER_COST * float(sh)
                if best_cost is None or c < best_cost - 1e-9:
                    best, best_cost = dh, c
        if best is not None:
            self.true = np.array([x + d * math.cos(h + best), y + d * math.sin(h + best), h + best])
            self._note_true_steep(x, y, h + best, d)
            self._note_move()
            return d, best
        # Aim behind the rover, or no clear heading inside the turn limit:
        # turn in place to the clear heading nearest the aim (the next step
        # then drives it). With none clear, keep turning one way.
        turn, best_cost = None, None
        dhs = [math.radians(float(off)) for off in np.arange(-180.0, 180.0, POINT_TURN_SCAN_DEG)]
        feas, share = self._feasible_many(x, y, [h + dh for dh in dhs], d)
        for dh, ok, sh in zip(dhs, feas, share):
            if not ok:
                continue
            c = abs((want - dh + math.pi) % (2 * math.pi) - math.pi) + 0.1 * CRATER_COST * float(sh)
            if best_cost is None or c < best_cost - 1e-9:
                turn, best_cost = dh, c
        if turn is not None and abs(turn) <= lim:
            # The clear heading nearest the aim is inside the turn limit (the
            # aim is behind and only the way ahead is clear): drive it.
            self.true = np.array([x + d * math.cos(h + turn), y + d * math.sin(h + turn), h + turn])
            self._note_true_steep(x, y, h + turn, d)
            self._note_move()
            return d, turn
        backed_here = self._backed_from is not None and math.hypot(x - self._backed_from[0], y - self._backed_from[1]) < 0.5
        if turn is not None and abs(want) <= math.pi / 2 + lim and abs(turn) >= math.pi - lim and not backed_here:
            # A dead end ahead with the aim not behind, and the way clear
            # only back the way it faces from: back out, as a rover does,
            # instead of turning 180 deg and turning back. VIPER drives in any
            # direction (Bluethmann, LSIC 2024, p.5 "Omni-directional
            # driving"); GESTALT scores backward arcs with the forward ones
            # (Goldberg et al. 2002, Table 1, num-backward-arcs 23). The body
            # keeps facing the aim, within the turn limit; the step is judged
            # on its direction of travel (pitch and roll are the same either
            # way). Once per dead end: back at the spot it backed out of, it
            # turns in place instead, so it faces new ground rather than the
            # same dead end.
            self._backed_from = (x, y)
            travel = h + turn
            body = (turn - math.pi + math.pi) % (2 * math.pi) - math.pi
            self.true = np.array([x + d * math.cos(travel), y + d * math.sin(travel), h + body])
            self._note_true_steep(x, y, travel, d)
            self._note_move()
            self.reversals += 1
            self.last_reverse = True
            return d, body
        if turn is None:
            turn = math.radians(POINT_TURN_DEG)
        self.true = np.array([x, y, h + turn])
        self._check_due = True    # a turn in place: is every way out closed? (next step)
        self.point_turns += 1
        self.stuck_turns += 1
        return 0.0, turn

    # ------------------------------------------------ retrace (the note at ENCLOSE_HALF_M)
    def _trim_track(self):
        """Keep the track to its newest retrace_m metres."""
        T = self._track
        total, k = 0.0, len(T) - 1
        while k > 0:
            total += float(np.hypot(*(T[k] - T[k - 1])))
            if total > self.retrace_m:
                del T[:k]
                return
            k -= 1

    def seed_track(self, points):
        """Start the track from points (true xy, oldest first), e.g. a replay's
        run poses before its first step (sim/drive_check.py --warm)."""
        for p in points:
            p = np.asarray(p, float)
            if not self._track or np.hypot(*(p - self._track[-1])) > 0.5:
                self._track.append(p.copy())
        self._trim_track()

    def _enclosed(self, x, y):
        return self._flood(x, y)[0]

    def _flood(self, x, y):
        """(enclosed, reach, (x0, y0, cell)). Enclosed: the Scout's own map
        closes every way out of (x, y), that is a flood from it over the cells
        its map lets it drive into, each judged for the heading it is entered
        on as _feasible_many judges a step
        (lidar no-go cells, lidar slope cells and the planner's slope, both
        by heading), never reaches the edge of a box ENCLOSE_HALF_M each side.
        Cells are ENCLOSE_CELL_M, each closed to a heading when any 0.25 m
        cell in it is."""
        sm, c = self.slope, self.slope.cell
        k = max(1, int(round(ENCLOSE_CELL_M / c)))
        n = int(round(ENCLOSE_HALF_M / (k * c)))
        H, W = self.haz_steep.shape
        ic, jc = int(round((y - sm.y0) / c)), int(round((x - sm.x0) / c))
        i0, j0 = ic - n * k, jc - n * k
        m = 2 * n + 1
        if i0 < 0 or j0 < 0 or i0 + m * k > H or j0 + m * k > W:
            return False, None, None    # at the ground's edge: the map margin, not a trap
        win = (slice(i0, i0 + m * k), slice(j0, j0 + m * k))
        steep = self.haz_steep[win].reshape(m, k, m, k).any(axis=(1, 3))
        ii, jj = np.mgrid[i0:i0 + m * k, j0:j0 + m * k]
        px, py = sm.x0 + jj * c, sm.y0 + ii * c
        ok = []
        for di, dj in MOVES:
            hd = math.atan2(di, dj)
            sev = np.maximum(sm.sev(px, py, hd), self._lidar_sev(px, py, hd))
            ok.append(~steep & ~(sev > 1.0).reshape(m, k, m, k).any(axis=(1, 3)))
        reach = np.zeros((m, m), bool)
        reach[n, n] = True
        for _ in range(4 * m * m):
            new = reach.copy()
            for (di, dj), okt in zip(MOVES, ok):
                sh = np.zeros_like(reach)   # cells entered from a reached cell by move (di, dj)
                dst = (slice(max(di, 0), m + min(di, 0)), slice(max(dj, 0), m + min(dj, 0)))
                sh[dst] = reach[max(-di, 0):m + min(-di, 0), max(-dj, 0):m + min(-dj, 0)]
                new |= sh & okt
            if new[0].any() or new[-1].any() or new[:, 0].any() or new[:, -1].any():
                return False, new, (sm.x0 + j0 * c, sm.y0 + i0 * c, k * c)
            if (new == reach).all():
                return True, new, (sm.x0 + j0 * c, sm.y0 + i0 * c, k * c)
            reach = new
        return True, reach, (sm.x0 + j0 * c, sm.y0 + i0 * c, k * c)

    def _start_retrace(self):
        """After a turn in place or a back-and-forth block: when the map closes
        every way out, set the route back along the track (loop-erased, newest
        first) and return True."""
        self._check_due = False
        if self._steps - self._checked_at < ENCLOSE_EVERY or len(self._track) < 2:
            return False
        x, y = float(self.true[0]), float(self.true[1])
        if self._retrace_failed is not None and math.hypot(x - self._retrace_failed[0], y - self._retrace_failed[1]) < 3.0:
            return False    # a retrace from here already ran out of track
        self._checked_at = self._steps
        if not self._enclosed(x, y):
            return False
        route = []          # loop-erased: a return to a place drops the loop between
        for p in self._track:
            q = next((i for i, r in enumerate(route) if math.hypot(*(p - r)) < LOOP_ERASE_M), None)
            if q is not None:
                del route[q + 1:]
            else:
                route.append(p.copy())
        while route and math.hypot(route[-1][0] - x, route[-1][1] - y) < LOOP_ERASE_M:
            route.pop()     # where it stands now
        if not route:
            return False
        self._retrace = route[::-1]
        self.retraces += 1
        return True

    def _retrace_step(self):
        """One step back along the track toward its next point, ignoring the
        map: backed out, the body facing the way it came in (as _follow_sensing
        backs out, so the filter moves its particles back). Ends when the map
        leaves a way out, or when the track runs out still enclosed."""
        x, y, h = (float(v) for v in self.true)
        while len(self._retrace) > 1 and math.hypot(self._retrace[0][0] - x, self._retrace[0][1] - y) < 0.1:
            self._retrace.pop(0)    # a track point where it already stands
        p = self._retrace[0]
        dist = math.hypot(p[0] - x, p[1] - y)
        d = min(STEP_M, dist)
        travel = math.atan2(p[1] - y, p[0] - x)
        body = travel + math.pi
        dh = (body - h + math.pi) % (2 * math.pi) - math.pi
        if abs(dh) > math.radians(MAX_TURN_DEG):
            # Its back not yet to the track: turn in place first, as a
            # skid-steer rover does, then back along it.
            self.true = np.array([x, y, h + dh])
            self.point_turns += 1
            return 0.0, dh
        self.true = np.array([x + d * math.cos(travel), y + d * math.sin(travel), h + dh])
        self._note_true_steep(x, y, travel, d)
        self.last_reverse = True
        self.retrace_steps += 1
        if d >= dist - 1e-6:
            self._retrace.pop(0)
        nx_, ny_ = float(self.true[0]), float(self.true[1])
        if not self._enclosed(nx_, ny_):
            self._retrace = []
            # The track now ends here: what it drove back over is not a way out.
            keep = [i for i, q in enumerate(self._track) if math.hypot(q[0] - nx_, q[1] - ny_) < LOOP_ERASE_M]
            if keep:
                del self._track[keep[0] + 1:]
            self._moves = []
        elif not self._retrace:
            self._retrace_failed = (nx_, ny_)
        return d, dh

    def _note_true_steep(self, x, y, h, d):
        """Record a step onto ground truly over the limits for the heading it
        was driven on (pitch along it, roll across it), judged as _feasible
        samples a step (every half cell), at its worst sample; the record
        keeps that sample's tilt in degrees. The limits are
        planning limits (no source gives the Scout a physical one), so the
        step is driven; this only counts it. flag: what the Scout knew there,
        "none" (neither the orbital slope nor the lidar flagged it), "orbital"
        (allowed only as the way off orbital-steep ground, _feasible),
        "lidar_steep" (stepping off a lidar hazard) or "lidar_crater" (a costed
        crater interior). Orbital planning only: on the truth it cannot happen."""
        sm = self.slope
        if not sm.orbital or d <= 0:
            return
        n = max(1, int(math.ceil(d / (0.5 * sm.cell))))
        f = np.arange(1, n + 1) / n
        px, py = x + f * d * math.cos(h), y + f * d * math.sin(h)
        # Over the limits for the heading driven (sim/footprint.py severity).
        t = sm.true_sev(px, py, h)
        k = int(np.argmax(t))
        if t[k] <= 1.0:
            return
        c = sm.cell
        i = int(np.clip(round((py[k] - sm.y0) / c), 0, self.haz_fine.shape[0] - 1))
        j = int(np.clip(round((px[k] - sm.x0) / c), 0, self.haz_fine.shape[1] - 1))
        orb = float(sm.at(px[k], py[k]))
        lid = bool(self.haz_steep[i, j]) or float(self._lidar_sev(px[k:k + 1], py[k:k + 1], h)[0]) > 1.0
        flag = ("orbital" if float(sm.sev(px[k], py[k], h)) > 1.0 else "lidar_steep" if lid
                else "lidar_crater" if self.haz_crater[i, j] else "none")
        self.steep_steps.append((float(px[k]), float(py[k]), float(sm.true_at(px[k], py[k])), orb, flag))

    def _note_move(self):
        """Back-and-forth guard for a sensing rover, follow()'s rule: when a
        move ends where the move before last ended, the turnaround between
        them is a dead end the fine check refuses and the 2 m planner does
        not see (the planner reads one widened slope per cell, the fine check
        every 0.125 m). Close the planning cell of the turnaround so the next
        local plan goes another way."""
        self._track.append(self.true[:2].copy())
        self._trim_track()
        self._moves = (self._moves + [self.true[:2].copy()])[-3:]
        if len(self._moves) == 3 and np.hypot(*(self._moves[2] - self._moves[0])) < 0.5:
            sm = self.slope
            hx, hy = self._moves[1]
            bi = int(np.clip((hy - sm.y0) // sm.b, 0, self.haz_2m.shape[0] - 1))
            bj = int(np.clip((hx - sm.x0) // sm.b, 0, self.haz_2m.shape[1] - 1))
            self.pingpong_2m[bi, bj] = True
            self.haz_2m[bi, bj] = True
            self.pingpong_blocks += 1
            self._check_due = True    # rocking: is every way out closed? (next step)
            self._moves = []

    @staticmethod
    def _aim_along(pts, dist_m):
        """The point dist_m along the polyline pts (its end if shorter)."""
        total = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if total + seg >= dist_m and seg > 0:
                t = (dist_m - total) / seg
                return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            total += seg
        return pts[-1]

    def _crater_share(self, x, y, h, d):
        """Share of the 0.25 m cells along a d metre step that are crater hazard."""
        c = self.slope.cell
        n = max(1, int(math.ceil(d / c)))
        f = np.arange(1, n + 1) / n
        px, py = x + f * d * math.cos(h), y + f * d * math.sin(h)
        hi = np.clip(np.rint((py - self.slope.y0) / c).astype(int), 0, self.haz_crater.shape[0] - 1)
        hj = np.clip(np.rint((px - self.slope.x0) / c).astype(int), 0, self.haz_crater.shape[1] - 1)
        return float(self.haz_crater[hi, hj].mean())

    def planned_route(self, max_m=None):
        """Where the rover intends to drive next, true frame, starting at the
        rover: its local plan around what its sensors have found, smoothed
        (two rounds of corner cutting, as a rover follows a 2 m grid path),
        cut at max_m metres. The global route beyond it is left out: it is
        held in the believed frame and would jump with every estimate."""
        max_m = PLAN_DISPLAY_M if max_m is None else max_m
        pts = np.array([tuple(self.true[:2])] + [tuple(p) for p in getattr(self, "local_plan", [])[1:]], float)
        for _ in range(2):   # Chaikin corner cutting; keeps the first and last point
            if len(pts) < 3:
                break
            q = [pts[0]]
            for a_, b_ in zip(pts[:-1], pts[1:]):
                q += [0.75 * a_ + 0.25 * b_, 0.25 * a_ + 0.75 * b_]
            q.append(pts[-1])
            pts = np.array(q)
        out, total = [tuple(pts[0])], 0.0
        for a_, b_ in zip(pts[:-1], pts[1:]):
            seg = float(np.hypot(*(b_ - a_)))
            if total + seg >= max_m:
                t = (max_m - total) / seg if seg > 0 else 0.0
                out.append(tuple(a_ + t * (b_ - a_)))
                break
            out.append(tuple(b_))
            total += seg
        return out

    def goal_blocked(self):
        """True if the goal is on ground the rover may not enter: a sensor
        hazard or a slope over the limit. The goal is held in the rover's
        believed frame and the hazard grids in the true frame (the lidar's
        relative geometry placed at the true pose), so the goal is placed
        where the rover's estimate puts it relative to itself: true + (goal -
        estimate), as _waypoint_target does."""
        if self.goal is None:
            return False
        gx = self.true[0] + self.goal[0] - self.estimate[0]
        gy = self.true[1] + self.goal[1] - self.estimate[1]
        c = self.slope.cell
        i = int(np.clip(round((gy - self.slope.y0) / c), 0, self.haz_fine.shape[0] - 1))
        j = int(np.clip(round((gx - self.slope.x0) / c), 0, self.haz_fine.shape[1] - 1))
        # The orbital slope blocks the goal only when no heading may enter it.
        return bool(self.haz_fine[i, j]) or not bool(self.slope.open_any(gx, gy))

    def add_hazards(self, world_xy, kinds=None, clear=None, grads=None):
        """Mark hazard points from the lidar (world xy, true frame), widened
        by the rover's half width, for _feasible and the local planner.
        kinds: "crater" | "slope" | "rock" per point (sim/lidar.py); on a
        sensing rover, craters are costly and the rest no-go. Without kinds
        every point is no-go and closes its 2 m cell, as before.
        clear (sensing rover): world boxes (xmin, xmax, ymin, ymax) the
        lidar's map has judged afresh (sim/lidar.py Lidar.judge). Their marks
        are cleared first and world_xy holds every hazard now judged around
        them, so ground the map now judges safe reopens.
        grads (sensing rover, "footprint"): each point's footprint-plane
        gradient (gx, gy), world frame, from the lidar's map (NaN for none).
        A slope point some heading may cross is kept as its gradient
        (haz_gx, haz_gy) and judged per step by heading; one no heading may
        cross is no-go (haz_steep), as every slope point was before."""
        pts = np.asarray(world_xy, float).reshape(-1, 2)
        if len(pts) == 0 and not clear:
            return
        if kinds is not None and self.sensing:
            kinds = np.asarray(kinds, dtype=object)
            c, (H, W) = self.slope.cell, self.haz_fine.shape
            corners = []
            for xa, xb, ya, yb in clear or ():
                i0, i1 = max(int(np.rint((ya - self.slope.y0) / c)), 0), min(int(np.rint((yb - self.slope.y0) / c)) + 1, H)
                j0, j1 = max(int(np.rint((xa - self.slope.x0) / c)), 0), min(int(np.rint((xb - self.slope.x0) / c)) + 1, W)
                if i1 > i0 and j1 > j0:
                    self.haz_steep[i0:i1, j0:j1] = False
                    self.haz_crater[i0:i1, j0:j1] = False
                    self.haz_gx[i0:i1, j0:j1] = np.nan
                    self.haz_gy[i0:i1, j0:j1] = np.nan
                corners += [(xa, ya), (xb, yb)]
            soft = kinds == "crater"
            # A slope hazard is already judged on the footprint (lidar.hazards,
            # slope_mode "footprint"), so it closes only its own point; rocks
            # and craters keep the body margin.
            flat = (kinds == "slope") & (self.slope.mode == "footprint")
            bydir = np.zeros(len(pts), dtype=bool)
            if grads is not None and self.slope.mode == "footprint" and len(pts):
                G = np.asarray(grads, float).reshape(-1, 2)
                fin = np.isfinite(G[:, 0]) & np.isfinite(G[:, 1])
                bydir = flat & fin & _open_any(np.where(fin, G[:, 0], 0.0), np.where(fin, G[:, 1], 0.0),
                                               self.slope.tp, self.slope.tr)
                if bydir.any():
                    gi = np.clip(np.rint((pts[bydir, 1] - self.slope.y0) / c).astype(int), 0, H - 1)
                    gj = np.clip(np.rint((pts[bydir, 0] - self.slope.x0) / c).astype(int), 0, W - 1)
                    self.haz_gx[gi, gj] = G[bydir, 0]
                    self.haz_gy[gi, gj] = G[bydir, 1]
            self._mark(pts[~soft & ~flat], self.haz_steep)
            self._mark(pts[flat & ~bydir], self.haz_steep, r=0)
            self._mark(pts[soft], self.haz_crater)
            # _mark sets cells only within r of a point: refresh haz_fine and
            # its count in that window (and the cleared boxes), not over the
            # whole 0.25 m map (51M cells on a run ground; the whole-map OR and
            # sum were most of a camera step).
            if corners:
                pts = np.vstack([pts, np.asarray(corners, float)])
            r = int(math.ceil(self.slope.widen_m / c))
            ci = np.rint((pts[:, 1] - self.slope.y0) / c).astype(int)
            cj = np.rint((pts[:, 0] - self.slope.x0) / c).astype(int)
            i0, i1 = max(int(ci.min()) - r, 0), min(int(ci.max()) + r + 1, H)
            j0, j1 = max(int(cj.min()) - r, 0), min(int(cj.max()) + r + 1, W)
            if i1 > i0 and j1 > j0:
                win = (slice(i0, i1), slice(j0, j1))
                before = int(self.haz_fine[win].sum())
                np.logical_or(self.haz_steep[win], self.haz_crater[win], out=self.haz_fine[win])
                self.n_hazard_cells += int(self.haz_fine[win].sum()) - before
            self._refresh_cells(pts)
            return
        sm = self.slope
        c = sm.cell
        H, W = self.haz_fine.shape
        r = int(math.ceil(self.slope.widen_m / c))
        ci = np.rint((pts[:, 1] - sm.y0) / c).astype(int)
        cj = np.rint((pts[:, 0] - sm.x0) / c).astype(int)
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                i, j = ci + di, cj + dj
                ok = (i >= 0) & (i < H) & (j >= 0) & (j < W)
                self.haz_fine[i[ok], j[ok]] = True
        # A 2 m planning cell is blocked only when most of it is hazard: the
        # rover steers around a single rock inside a cell (checked by _feasible).
        k = int(round(sm.b / c))
        i0, i1 = max(ci.min() - r, 0) // k * k, min(ci.max() + r + k, H) // k * k
        j0, j1 = max(cj.min() - r, 0) // k * k, min(cj.max() + r + k, W) // k * k
        if i1 > i0 and j1 > j0:
            blk = self.haz_fine[i0:i1, j0:j1].reshape((i1 - i0) // k, k, (j1 - j0) // k, k).mean(axis=(1, 3))
            self.haz_2m[i0 // k:i1 // k, j0 // k:j1 // k] |= blk >= HAZARD_CELL_FRAC
        self.n_hazard_cells = int(self.haz_fine.sum())

    def _mark(self, pts, grid, r=None):
        """Set grid cells within the rover's half width of each point (r cells if given)."""
        if len(pts) == 0:
            return
        sm, c = self.slope, self.slope.cell
        H, W = grid.shape
        if r is None:
            r = int(math.ceil(self.slope.widen_m / c))
        ci = np.rint((pts[:, 1] - sm.y0) / c).astype(int)
        cj = np.rint((pts[:, 0] - sm.x0) / c).astype(int)
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                i, j = ci + di, cj + dj
                ok = (i >= 0) & (i < H) & (j >= 0) & (j < W)
                grid[i[ok], j[ok]] = True

    def _refresh_cells(self, pts):
        """Recompute closed cells and crater cost for the 2 m cells near pts."""
        sm, c = self.slope, self.slope.cell
        H, W = self.haz_steep.shape
        k = int(round(sm.b / c))
        r = int(math.ceil(self.slope.widen_m / c))
        ci = np.rint((pts[:, 1] - sm.y0) / c).astype(int)
        cj = np.rint((pts[:, 0] - sm.x0) / c).astype(int)
        i0, i1 = max(ci.min() - r, 0) // k * k, min(ci.max() + r + k, H) // k * k
        j0, j1 = max(cj.min() - r, 0) // k * k, min(cj.max() + r + k, W) // k * k
        if i1 <= i0 or j1 <= j0:
            return
        shp = ((i1 - i0) // k, k, (j1 - j0) // k, k)
        steep = self.haz_steep[i0:i1, j0:j1].reshape(shp).mean(axis=(1, 3))
        crat = self.haz_crater[i0:i1, j0:j1].reshape(shp).mean(axis=(1, 3))
        win = (slice(i0 // k, i1 // k), slice(j0 // k, j1 // k))
        self.haz_2m[win] = (steep >= STEEP_CELL_FRAC) | self.pingpong_2m[win]
        self.cost_2m[i0 // k:i1 // k, j0 // k:j1 // k] = CRATER_COST * crat

    def _drive_true(self, aim_xy, d):
        """Step d metres toward aim_xy (true frame), the nearest heading under the slope limit."""
        if d < 1e-6:
            return 0.0, 0.0
        x, y, h = self.true
        bearing = math.atan2(aim_xy[1] - y, aim_xy[0] - x)
        for off in TURN_OFFSETS_DEG:
            cand = bearing + math.radians(off)
            if self._feasible(x, y, cand, d):
                dh = (cand - h + math.pi) % (2 * math.pi) - math.pi
                self.true = np.array([x + d * math.cos(cand), y + d * math.sin(cand), h + dh])
                return d, dh
        return 0.0, 0.0

    def _feasible(self, x, y, h, d, steep_only=False):
        """True if a straight move of d metres on heading h stays within the
        slope limits along and across h (pitch and roll), checked at every
        0.25 m cell along it, lidar slope cells included.

        A rover already standing on steep ground (a scenario may start one
        inside a crater) may still move, but only onto ground no steeper than
        where it stands and ending less steep, so it climbs out the gentlest
        way instead of being stuck."""
        if d <= 0:
            return True
        # A sensing rover checks every half cell: sampling once per 0.25 m
        # cell skipped the steep cells a step clips between samples (up to
        # 16.5 deg crossed on the harness runs). Stand-in runs keep the old
        # sampling, so their routes are unchanged.
        n = max(1, int(math.ceil(d / (0.5 * self.slope.cell if steep_only else self.slope.cell))))
        f = np.arange(1, n + 1) / n
        px, py = x + f * d * math.cos(h), y + f * d * math.sin(h)
        if not (self.slope.inside(px[-1], py[-1]) and self.slope.inside(px[0], py[0])):
            return False
        # Slopes judged by heading (sim/footprint.py severity: 1 = at a limit).
        s = self.slope.sev(px, py, h)
        here = float(self.slope.sev(x, y, h))
        c = self.slope.cell
        hz = self.haz_steep if steep_only else self.haz_fine
        hi = np.clip(np.rint((py - self.slope.y0) / c).astype(int), 0, hz.shape[0] - 1)
        hj = np.clip(np.rint((px - self.slope.x0) / c).astype(int), 0, hz.shape[1] - 1)
        haz = hz[hi, hj] | (self._lidar_sev(px, py, h) > 1.0)
        i0 = int(np.clip(round((y - self.slope.y0) / c), 0, self.haz_fine.shape[0] - 1))
        j0 = int(np.clip(round((x - self.slope.x0) / c), 0, self.haz_fine.shape[1] - 1))
        on_haz = bool(hz[i0, j0]) or float(self._lidar_sev(np.array([x]), np.array([y]), h)[0]) > 1.0
        if haz.any() and not (on_haz and not haz[-1]):   # standing on a hazard: may only step off it
            return False
        if here <= 1.0:
            return bool(np.all(s <= 1.0))
        return bool(np.all(s <= here) and s[-1] < here)

    def _lidar_sev(self, px, py, h):
        """severity, for heading h, of the lidar slope cells (haz_gx, haz_gy)
        at world points; 0 where the lidar holds no such cell. Broadcasts h
        against the points (one heading per row in _feasible_many)."""
        sm, c = self.slope, self.slope.cell
        hi = np.clip(np.rint((np.asarray(py) - sm.y0) / c).astype(int), 0, self.haz_gx.shape[0] - 1)
        hj = np.clip(np.rint((np.asarray(px) - sm.x0) / c).astype(int), 0, self.haz_gx.shape[1] - 1)
        gx = self.haz_gx[hi, hj].astype(np.float64)
        gy = self.haz_gy[hi, hj].astype(np.float64)
        have = np.isfinite(gx)
        if not have.any():
            return np.zeros(np.broadcast(gx, np.asarray(h)).shape)
        return np.where(have, _severity(np.where(have, gx, 0.0), np.where(have, gy, 0.0), h, sm.tp, sm.tr), 0.0)

    def _feasible_many(self, x, y, hs, d):
        """[_feasible(x, y, h, d, steep_only=True) for h in hs] and
        [_crater_share(x, y, h, d) for h in hs], as two arrays, in one pass.
        Same arithmetic per element (math.cos/sin per heading, then numpy's
        elementwise ops, which do not depend on the array's shape)."""
        sm = self.slope
        c = sm.cell
        hs = list(hs)
        ch = np.array([math.cos(h) for h in hs])[:, None]
        sh = np.array([math.sin(h) for h in hs])[:, None]
        # _feasible (steep_only): every half cell along the step
        n = max(1, int(math.ceil(d / (0.5 * c))))
        fd = (np.arange(1, n + 1) / n) * d
        px, py = x + fd[None, :] * ch, y + fd[None, :] * sh
        ins = np.array([sm.inside(a, b) and sm.inside(e, f) for a, b, e, f in zip(px[:, -1], py[:, -1], px[:, 0], py[:, 0])])
        hcol = np.array(hs, dtype=np.float64)[:, None]          # one heading per row
        s = sm.sev(px, py, hcol)
        here = sm.sev(np.full((len(hs), 1), x), np.full((len(hs), 1), y), hcol)    # (K, 1)
        hz = self.haz_steep
        hi = np.clip(np.rint((py - sm.y0) / c).astype(int), 0, hz.shape[0] - 1)
        hj = np.clip(np.rint((px - sm.x0) / c).astype(int), 0, hz.shape[1] - 1)
        haz = hz[hi, hj] | (self._lidar_sev(px, py, hcol) > 1.0)
        i0 = int(np.clip(round((y - sm.y0) / c), 0, self.haz_fine.shape[0] - 1))
        j0 = int(np.clip(round((x - sm.x0) / c), 0, self.haz_fine.shape[1] - 1))
        on_haz = bool(hz[i0, j0]) | (self._lidar_sev(np.full((len(hs), 1), x), np.full((len(hs), 1), y), hcol)[:, 0] > 1.0)
        bad_haz = haz.any(axis=1) & ~(on_haz & ~haz[:, -1])
        steep_here = here[:, 0] > 1.0
        ok_s = np.where(steep_here, np.all(s <= here, axis=1) & (s[:, -1] < here[:, 0]), np.all(s <= 1.0, axis=1))
        feas = ins & ~bad_haz & ok_s
        # _crater_share: every cell along the step
        m = max(1, int(math.ceil(d / c)))
        gd = (np.arange(1, m + 1) / m) * d
        qx, qy = x + gd[None, :] * ch, y + gd[None, :] * sh
        ci = np.clip(np.rint((qy - sm.y0) / c).astype(int), 0, self.haz_crater.shape[0] - 1)
        cj = np.clip(np.rint((qx - sm.x0) / c).astype(int), 0, self.haz_crater.shape[1] - 1)
        share = self.haz_crater[ci, cj].mean(axis=1)
        return feas, share

    def drive_toward(self, goal_xy, max_step=STEP_M):
        """One step toward goal_xy, steering by the estimate. Returns (dist, dheading) driven."""
        ex, ey = self.estimate
        dist_goal = math.hypot(goal_xy[0] - ex, goal_xy[1] - ey)
        d = min(max_step, dist_goal)
        if d < 1e-6:
            return 0.0, 0.0
        bearing = math.atan2(goal_xy[1] - ey, goal_xy[0] - ex)
        x, y, h = self.true
        new_h = None
        for off in TURN_OFFSETS_DEG:
            cand = bearing + math.radians(off)
            if self._feasible(x, y, cand, d):
                new_h = cand
                break
        if new_h is None:
            return 0.0, 0.0  # boxed in: stay put this step
        dh = (new_h - h + math.pi) % (2 * math.pi) - math.pi
        self.true = np.array([x + d * math.cos(new_h), y + d * math.sin(new_h), h + dh])
        return d, dh

    def odometry(self, d, dh):
        """Odometry for the move just made: (distance_m, dheading_rad) with drift and noise, and its xy error."""
        d_o = d * (1.0 + ODOM_BIAS) + (self.rng.normal(0.0, ODOM_NOISE * d) if d > 0 else 0.0)
        dh_o = dh + (self.rng.normal(0.0, ODOM_HEADING_NOISE_RAD) if d > 0 else 0.0)
        before = self.odom[:2].copy()
        self.odom[2] += dh_o
        self.odom[0] += d_o * math.cos(self.odom[2])
        self.odom[1] += d_o * math.sin(self.odom[2])
        true_disp = np.array([d * math.cos(self.true[2]), d * math.sin(self.true[2])])
        self.distance += d
        return (d_o, dh_o), (self.odom[:2] - before) - true_disp

    def localize(self, odom_err_xy, d, n_rims, odom=None, rims=None):
        """Update the estimate. A real filter (sim/nav.py) gets only the
        odometry and the rim points; the stand-in gets its tracked error."""
        if hasattr(self.nav, "step"):
            est, self.sigma = self.nav.step(odom, rims)
            self.estimate = np.asarray(est, dtype=np.float64)
        else:
            self.estimate, self.sigma = self.nav.update(odom_err_xy, d, n_rims, self.true[:2])
        return self.estimate, self.sigma
