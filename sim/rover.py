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

import numpy as np

from sim.standins import ODOM_DRIFT

# Spec ("How it works", step 4): the planner avoids slopes over 15 deg.
SLOPE_LIMIT_DEG = 15.0
# docs/handoffs.md, step 2: slope for planning is measured across 2 m (assumed there).
SLOPE_BASELINE_M = 2.0
# PROSPECT brief p.3: the Scout must average at least ~7.4 cm/s in the dark.
SPEED_M_S = 0.074

STEP_M = 2.0                    # assumed: distance per sim step (one see-locate-plan-drive cycle)
ODOM_BIAS = ODOM_DRIFT          # Cauligi et al. 2023 sec. 1: ~2% odometry drift, applied as a scale bias (sign assumed)
ODOM_NOISE = 0.005              # assumed: per-step distance noise, fraction of the step
ODOM_HEADING_NOISE_RAD = 0.0005 # assumed: per-step heading noise in odometry (~0.7 deg after 1 km)
TURN_OFFSETS_DEG = (0, 10, -10, 20, -20, 30, -30, 45, -45, 60, -60, 90, -90, 135, -135, 180)  # assumed
MAP_MARGIN_M = 15.0             # assumed: stay this far inside the map edge
LOOKAHEAD_WAYPOINTS = 4         # assumed: steer for the global waypoint ~16 m ahead
LOCAL_PLAN_MAX_CELLS = 3000     # assumed: local A* search budget (2 m cells)

PLANNER_STANDIN = ("planner: A* on a 2 m grid through ground under 15 deg across 2 m, followed by "
                   "the rover's estimate, with a local slope check each step (sim/rover.py)")


class SlopeMap:
    """Slope in degrees across a 2 m baseline, on a 2 m grid."""

    def __init__(self, height, x0, y0, cell):
        k = max(1, int(round(SLOPE_BASELINE_M / cell)))
        hs = np.asarray(height[::k, ::k], dtype=np.float64)
        b = k * cell
        gx = np.zeros_like(hs)
        gy = np.zeros_like(hs)
        gx[:, :-1] = (hs[:, 1:] - hs[:, :-1]) / b
        gy[:-1, :] = (hs[1:, :] - hs[:-1, :]) / b
        gx[:, -1] = gx[:, -2]
        gy[-1, :] = gy[-2, :]
        self.deg = np.degrees(np.arctan(np.hypot(gx, gy)))
        self.x0, self.y0, self.b = x0, y0, b
        self.x1 = x0 + (height.shape[1] - 1) * cell
        self.y1 = y0 + (height.shape[0] - 1) * cell

    def at(self, x, y):
        j = np.clip(((np.asarray(x) - self.x0) / self.b).astype(int), 0, self.deg.shape[1] - 1)
        i = np.clip(((np.asarray(y) - self.y0) / self.b).astype(int), 0, self.deg.shape[0] - 1)
        return self.deg[i, j]

    def inside(self, x, y):
        return (self.x0 + MAP_MARGIN_M <= x <= self.x1 - MAP_MARGIN_M) and (self.y0 + MAP_MARGIN_M <= y <= self.y1 - MAP_MARGIN_M)

    def plan(self, start_xy, goal_xy, max_expand=None):
        """A* over the 2 m grid, 8-connected, through cells under the slope limit.

        Returns world (x, y) waypoints from start to goal, [goal] if no path
        exists, or None if max_expand cells were searched without reaching it. The rover follows them by its estimate, so drift still
        moves its real path off the planned one.
        """
        import heapq
        ny, nx = self.deg.shape
        m = int(math.ceil(MAP_MARGIN_M / self.b))
        ok = self.deg <= SLOPE_LIMIT_DEG
        ok[:m, :] = ok[-m:, :] = False
        ok[:, :m] = ok[:, -m:] = False

        def cell(p):  # same floor convention as at(): cell j spans [x0 + j*b, x0 + (j+1)*b)
            return (int(np.clip(math.floor((p[1] - self.y0) / self.b), 0, ny - 1)),
                    int(np.clip(math.floor((p[0] - self.x0) / self.b), 0, nx - 1)))

        s, g = cell(start_xy), cell(goal_xy)
        ok[s] = ok[g] = True
        nbrs = [(-1, -1, 1.4142), (-1, 0, 1.0), (-1, 1, 1.4142), (0, -1, 1.0),
                (0, 1, 1.0), (1, -1, 1.4142), (1, 0, 1.0), (1, 1, 1.4142)]
        gcost = {s: 0.0}
        came = {}
        heap = [(math.hypot(g[0] - s[0], g[1] - s[1]), s)]
        expanded = 0
        while heap:
            _, c = heapq.heappop(heap)
            expanded += 1
            if max_expand is not None and expanded > max_expand:
                return None
            if c == g:
                break
            gc = gcost[c]
            for di, dj, w in nbrs:
                n = (c[0] + di, c[1] + dj)
                if not (0 <= n[0] < ny and 0 <= n[1] < nx) or not ok[n]:
                    continue
                ng = gc + w
                if ng < gcost.get(n, 1e18):
                    gcost[n] = ng
                    came[n] = c
                    heapq.heappush(heap, (ng + math.hypot(g[0] - n[0], g[1] - n[1]), n))
        if g not in came and g != s:
            return [tuple(goal_xy)]
        path = [g]
        while path[-1] != s:
            path.append(came[path[-1]])
        path.reverse()
        pts = [(self.x0 + (j + 0.5) * self.b, self.y0 + (i + 0.5) * self.b) for i, j in path[1:-1]]
        return pts[::2] + [tuple(goal_xy)]  # every other cell: waypoints 4 m apart


class Rover:
    def __init__(self, agency, true_pose, nav, slope: SlopeMap, rng: np.random.Generator):
        self.agency = agency
        self.true = np.array(true_pose, dtype=np.float64)          # x, y, heading
        self.nav = nav
        self.slope = slope
        self.rng = rng
        est0 = self.true[:2] + nav.err
        self.estimate = est0.copy()
        self.sigma = nav.sigma
        self.odom = np.array([est0[0], est0[1], self.true[2]])     # dead-reckoned pose, starts at the estimate
        self.distance = 0.0
        self.path = []
        self.goal = None

    def set_goal(self, goal_xy):
        """Plan from where the rover believes it is."""
        self.goal = tuple(goal_xy)
        self.path = self.slope.plan(self.estimate, goal_xy)

    def follow(self):
        """One step along the planned path. Returns (dist, dheading).

        The global path is in the rover's believed frame. Hazards are avoided
        in its own frame: the next waypoint is taken relative to the rover
        (waypoint minus estimate) and a short A* runs on the real slopes
        around it. Relative geometry is what stereo measures exactly, so
        this is honest even when the global estimate is 20 m off.
        """
        est = self.estimate
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
        local = self.slope.plan((x, y), tgt, max_expand=LOCAL_PLAN_MAX_CELLS)
        aim = tgt
        if local:  # first local waypoint far enough ahead to give a stable heading
            aim = next((p for p in local if math.hypot(p[0] - x, p[1] - y) >= 3.0), local[-1])
        d = min(STEP_M, math.hypot(self.goal[0] - est[0], self.goal[1] - est[1]))
        return self._drive_true(aim, d)

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

    def _feasible(self, x, y, h, d):
        if d <= 0:
            return True
        for f in (0.25, 0.5, 0.75, 1.0):
            px, py = x + f * d * math.cos(h), y + f * d * math.sin(h)
            if not self.slope.inside(px, py) or self.slope.at(px, py) > SLOPE_LIMIT_DEG:
                return False
        return True

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

    def localize(self, odom_err_xy, d, n_rims):
        self.estimate, self.sigma = self.nav.update(odom_err_xy, d, n_rims, self.true[:2])
        return self.estimate, self.sigma
