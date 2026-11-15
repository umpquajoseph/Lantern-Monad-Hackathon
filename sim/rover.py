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

# Assumed: share of a 2 m planning cell that sensor hazards must cover before
# the planner routes around the whole cell.
HAZARD_CELL_FRAC = 0.01   # any hazard cell blocks its 2 m planning cell (lidar hazards are craters, not lone rocks)

# Assumed: how much of the planned route is logged for display (the camera
# view's dotted line), metres ahead of the rover.
PLAN_DISPLAY_M = 60.0

# Assumed: how far the rover turns in place when no way forward is clear.
STUCK_TURN_DEG = 60.0

# Assumed: half the rover's width, from the rover model in sim/scene.py
# (wheels at y = +-0.72 m, 0.22 m wide).
ROVER_HALF_WIDTH_M = 0.83

PLANNER_STANDIN = ("planner: A* on a 2 m grid; a cell is blocked if any point within the rover's half "
                   "width is over 15 deg across 2 m, measured on the 0.25 m grid. Followed by the "
                   "rover's estimate, with a local slope check each step (sim/rover.py)")


class SlopeMap:
    """Worst slope in degrees across a 2 m baseline, per 2 m planning cell.

    Slope is measured at every 0.25 m grid point as a central difference
    across 2 m (docs/handoffs.md, step 2), widened by the rover's half width,
    and each 2 m cell keeps the steepest value inside it. Sampling the height
    only every 2 m instead (the earlier version) missed crater walls that lie
    between samples, so the rover drove into craters steeper than 15 deg.
    """

    def __init__(self, height, x0, y0, cell):
        from scipy.ndimage import maximum_filter
        k = max(1, int(round(SLOPE_BASELINE_M / cell)))
        b = k * cell
        h = np.asarray(height, dtype=np.float32)
        half = k // 2                                   # +-1 m: a 2 m central difference
        hp = np.pad(h, half, mode="edge")
        gx = (hp[half:-half, 2 * half:] - hp[half:-half, :-2 * half]) / np.float32(2 * half * cell)
        gy = (hp[2 * half:, half:-half] - hp[:-2 * half, half:-half]) / np.float32(2 * half * cell)
        fine = np.degrees(np.arctan(np.hypot(gx, gy)))
        del hp, gx, gy
        # Widen by the rover's half width, so a cell is blocked if any part of
        # the rover standing in it would be on a steep spot.
        r = int(math.ceil(ROVER_HALF_WIDTH_M / cell))
        fine = maximum_filter(fine, size=2 * r + 1, mode="nearest")
        # The A* grid: 2 m cell [i, j] spans x0 + j*b .. x0 + (j+1)*b (the
        # floor convention of plan()); it takes the widened slope at its
        # centre, x0 + (j + 0.5)*b, where plan() puts its waypoints. Every
        # drive step is then checked against the 0.25 m map by at().
        c0 = k // 2
        self.deg = fine[c0::k, c0::k].astype(np.float64)
        self.fine_deg = fine
        self.cell = cell
        self.x0, self.y0, self.b = x0, y0, b
        self.x1 = x0 + (height.shape[1] - 1) * cell
        self.y1 = y0 + (height.shape[0] - 1) * cell

    def at(self, x, y):
        """Widened slope (deg) at world points, from the 0.25 m map."""
        j = np.clip(np.rint((np.asarray(x) - self.x0) / self.cell).astype(int), 0, self.fine_deg.shape[1] - 1)
        i = np.clip(np.rint((np.asarray(y) - self.y0) / self.cell).astype(int), 0, self.fine_deg.shape[0] - 1)
        return self.fine_deg[i, j]

    def inside(self, x, y):
        return (self.x0 + MAP_MARGIN_M <= x <= self.x1 - MAP_MARGIN_M) and (self.y0 + MAP_MARGIN_M <= y <= self.y1 - MAP_MARGIN_M)

    def plan(self, start_xy, goal_xy, max_expand=None, blocked=None):
        """A* over the 2 m grid, 8-connected, through cells under the slope limit.

        Returns world (x, y) waypoints from start to goal, [goal] if no path
        exists, or None if max_expand cells were searched without reaching it. The rover follows them by its estimate, so drift still
        moves its real path off the planned one.
        """
        import heapq
        ny, nx = self.deg.shape
        m = int(math.ceil(MAP_MARGIN_M / self.b))
        ok = self.deg <= SLOPE_LIMIT_DEG
        if blocked is not None:              # hazards the rover's camera found (2 m cells)
            ok &= ~blocked
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
        self.haz_fine = np.zeros(slope.fine_deg.shape, dtype=bool)
        self.haz_2m = np.zeros(slope.deg.shape, dtype=bool)
        self.n_hazard_cells = 0
        self.stuck_turns = 0      # turns in place because no way forward was clear
        # True when the rover drives on its camera and lidar (run_demo.py
        # --vision). The back-and-forth guard and turning in place apply only
        # then, so stand-in runs keep exactly the spine's routes.
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
        est = self.estimate
        if getattr(self, "_replan", False) and self.slope.at(self.true[0], self.true[1]) <= SLOPE_LIMIT_DEG:
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

    def planned_route(self, max_m=None):
        """Where the rover intends to drive, true frame, starting at the rover:
        the local plan around sensed hazards, then the rest of the global
        route (held in the believed frame, shifted by the rover's own offset
        between belief and where its sensors place it), cut at max_m metres."""
        max_m = PLAN_DISPLAY_M if max_m is None else max_m
        pts = [tuple(self.true[:2])] + [tuple(p) for p in getattr(self, "local_plan", [])[1:]]
        if self.path:
            off = self.true[:2] - self.estimate
            last = np.asarray(pts[-1])
            # Continue from the global waypoint nearest the end of the local plan.
            k = int(np.argmin([math.hypot(p[0] + off[0] - last[0], p[1] + off[1] - last[1]) for p in self.path]))
            pts += [(p[0] + off[0], p[1] + off[1]) for p in self.path[k + 1:]]
        out, total = [pts[0]], 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if total + seg >= max_m:
                t = (max_m - total) / seg if seg > 0 else 0.0
                out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
                break
            out.append(b)
            total += seg
        return out

    def goal_blocked(self):
        """True if the goal (taken where the rover believes it is) is on ground
        the rover may not enter: a sensor hazard or a slope over the limit."""
        if self.goal is None:
            return False
        gx, gy = self.goal
        c = self.slope.cell
        i = int(np.clip(round((gy - self.slope.y0) / c), 0, self.haz_fine.shape[0] - 1))
        j = int(np.clip(round((gx - self.slope.x0) / c), 0, self.haz_fine.shape[1] - 1))
        return bool(self.haz_fine[i, j]) or float(self.slope.at(gx, gy)) > SLOPE_LIMIT_DEG

    def add_hazards(self, world_xy):
        """Mark hazard points from the camera (world xy, true frame), widened
        by the rover's half width, for _feasible and the local planner."""
        pts = np.asarray(world_xy, float).reshape(-1, 2)
        if len(pts) == 0:
            return
        sm = self.slope
        c = sm.cell
        H, W = self.haz_fine.shape
        r = int(math.ceil(ROVER_HALF_WIDTH_M / c))
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
        """True if a straight move of d metres on heading h stays under the
        slope limit, checked at every 0.25 m cell along it.

        A rover already standing on steep ground (a scenario may start one
        inside a crater) may still move, but only onto ground no steeper than
        where it stands and ending less steep, so it climbs out the gentlest
        way instead of being stuck."""
        if d <= 0:
            return True
        n = max(1, int(math.ceil(d / self.slope.cell)))
        f = np.arange(1, n + 1) / n
        px, py = x + f * d * math.cos(h), y + f * d * math.sin(h)
        if not (self.slope.inside(px[-1], py[-1]) and self.slope.inside(px[0], py[0])):
            return False
        s = self.slope.at(px, py)
        here = float(self.slope.at(x, y))
        c = self.slope.cell
        hi = np.clip(np.rint((py - self.slope.y0) / c).astype(int), 0, self.haz_fine.shape[0] - 1)
        hj = np.clip(np.rint((px - self.slope.x0) / c).astype(int), 0, self.haz_fine.shape[1] - 1)
        haz = self.haz_fine[hi, hj]
        on_haz = bool(self.haz_fine[int(np.clip(round((y - self.slope.y0) / c), 0, self.haz_fine.shape[0] - 1)),
                                    int(np.clip(round((x - self.slope.x0) / c), 0, self.haz_fine.shape[1] - 1))])
        if haz.any() and not (on_haz and not haz[-1]):   # standing on a hazard: may only step off it
            return False
        if here <= SLOPE_LIMIT_DEG:
            return bool(np.all(s <= SLOPE_LIMIT_DEG))
        return bool(np.all(s <= here) and s[-1] < here)

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
