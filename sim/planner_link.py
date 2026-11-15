"""Find the route planner to drive with, without owning or copying it.

The mission (sim/mission.py) needs two things from a planner: a route from
here to a goal under the 15 deg slope limit, and a drive that follows it one
step at a time. Two teammates have written one each, both in a file named
`sim/rover.py` on different branches:

  Calvin's  (branch Calvin)        Planner(height, x0, y0, cell).plan(a, b)
                                   and Drive(route, pose).step(), 0.5 m steps.
  the spine (branch step-4-spine)  SlopeMap(height, x0, y0, cell).plan(a, b)

This module looks for one, in order:
  1. the file named by LANTERN_PLANNER (a path to any rover.py), so a branch
     can be tested without checking it out;
  2. `sim.rover` on the current branch;
  3. a STAND-IN straight-line planner, labelled as one everywhere it shows.

It never edits either file.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))   # so a planner's `from sim.terrain import ...` works

STEP_M = 0.5  # ASSUMED, the same step as Calvin's Drive and sim/scene.py's scripted drive


class NoRoute(Exception):
    pass


def _load_module():
    path = os.environ.get("LANTERN_PLANNER")
    if path:
        spec = importlib.util.spec_from_file_location("lantern_planner", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, f"{Path(path).name} via LANTERN_PLANNER"
    try:
        return importlib.import_module("sim.rover"), "sim/rover.py"
    except ImportError:
        return None, None


class RouteLink:
    """One interface over whichever planner was found.

    plan(start_xy, goal_xy) -> (K, 2) world waypoints, raises NoRoute
    drive(route, start_pose) -> iterator of true poses (x, y, heading)
    blocked(x, y) -> bool, or always False for the stand-in
    """

    def __init__(self, height, x0, y0, cell, prefer="auto"):
        self.name, self.real, self._mod = "stand-in", False, None
        mod, where = (None, None) if prefer == "standin" else _load_module()
        if mod is not None and hasattr(mod, "Planner") and hasattr(mod, "Drive"):
            self._mod, self.kind = mod, "calvin"
            self._p = mod.Planner(height, x0, y0, cell)
            self.name, self.real = f"Calvin's Planner + Drive ({where})", True
        elif mod is not None and hasattr(mod, "SlopeMap"):
            self._mod, self.kind = mod, "spine"
            self._p = mod.SlopeMap(height, x0, y0, cell)
            self.name, self.real = f"spine SlopeMap A* ({where}), followed in straight steps", True
        else:
            self.kind = "standin"
            self.name = "STAND-IN: straight line, no slope check"
        self.x0, self.y0 = x0, y0
        self.x1 = x0 + (height.shape[1] - 1) * cell
        self.y1 = y0 + (height.shape[0] - 1) * cell

    # ------------------------------------------------------------------
    def plan(self, start_xy, goal_xy):
        if self.kind == "calvin":
            try:
                return np.asarray(self._p.plan(tuple(start_xy), tuple(goal_xy)), float)
            except self._mod.NoRoute as e:
                raise NoRoute(str(e)) from e
        if self.kind == "spine":
            pts = self._p.plan(tuple(start_xy), tuple(goal_xy))
            if pts is None or len(pts) < 1:
                raise NoRoute(f"spine planner found no route to {goal_xy}")
            pts = np.asarray(pts, float).reshape(-1, 2)
            if len(pts) == 1 and math.dist(pts[0], start_xy) > 1.0 and \
                    math.dist(start_xy, goal_xy) > 1.0 and np.allclose(pts[0], goal_xy):
                raise NoRoute(f"spine planner found no route to {goal_xy}")
            return np.vstack([start_xy, pts])
        # Stand-in: straight, but stepping around any known hazard.
        pts = [tuple(start_xy)]
        for hx, hy, hr in getattr(self, "hazards", []):
            a, b = np.array(pts[-1]), np.array(goal_xy, float)
            ab = b - a
            L2 = float(ab @ ab) or 1.0
            t = float(np.clip((np.array([hx, hy]) - a) @ ab / L2, 0, 1))
            c = a + t * ab
            if 0 < t < 1 and math.hypot(c[0] - hx, c[1] - hy) < hr + 1.0:
                n = np.array([-ab[1], ab[0]]) / math.sqrt(L2)
                pts.append(tuple(np.array([hx, hy]) + n * (hr + 3.0)))
        pts.append(tuple(goal_xy))
        return np.array(pts, float)

    def drive(self, route, start_pose):
        if self.kind == "calvin":
            d = self._mod.Drive(route, start_pose)
            guard = int(4 * _length(route) / STEP_M) + 50
            while not d.done and guard > 0:
                guard -= 1
                yield d.step()
            return
        # Spine routes and the stand-in: follow the polyline in STEP_M steps.
        x, y = start_pose[0], start_pose[1]
        for a, b in zip(route[:-1], route[1:]):
            seg = math.dist(a, b)
            hd = math.atan2(b[1] - a[1], b[0] - a[0])
            n = max(1, int(math.ceil(seg / STEP_M)))
            for k in range(1, n + 1):
                x, y = a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n
                yield (x, y, hd)

    def blocked(self, x, y):
        if self._in_hazard(x, y):
            return True
        if self.kind == "calvin":
            return self._p.is_blocked_xy(x, y)
        if self.kind == "spine":
            return not self._p.inside(x, y) or float(self._p.at(x, y)) > 15.0
        return False

    def blocked_grid(self):
        """(mask, x0, y0, cell) for drawing, or None."""
        if self.kind == "calvin":
            return self._p.blocked, self._p.x0, self._p.y0, self._p.pc
        if self.kind == "spine":
            return self._p.deg > 15.0, self._p.x0, self._p.y0, self._p.b
        return None

    def add_hazard(self, x, y, r):
        """Mark ground a rover has just found impassable (a new boulder, say)
        in THIS planner's map, so every later plan routes around it. The
        teammate's planner object is ours to update; its file is untouched."""
        self.hazards = getattr(self, "hazards", [])
        self.hazards.append((float(x), float(y), float(r)))
        grid = None
        if self.kind == "calvin":
            grid, gx0, gy0, gc = self._p.blocked, self._p.x0, self._p.y0, self._p.pc
        elif self.kind == "spine":
            grid, gx0, gy0, gc = self._p.deg, self._p.x0, self._p.y0, self._p.b
        if grid is not None:
            ii, jj = np.mgrid[0:grid.shape[0], 0:grid.shape[1]]
            near = np.hypot(gx0 + jj * gc - x, gy0 + ii * gc - y) <= r + gc
            grid[near] = True if grid.dtype == bool else 90.0

    def _in_hazard(self, x, y):
        return any(math.hypot(x - hx, y - hy) <= hr for hx, hy, hr in getattr(self, "hazards", []))

    def segment_clear(self, a, b, step=0.5):
        """True if the straight segment a-b crosses no blocked ground."""
        n = max(2, int(math.ceil(math.dist(a, b) / step)) + 1)
        return not any(self.blocked(a[0] + (b[0] - a[0]) * k / (n - 1), a[1] + (b[1] - a[1]) * k / (n - 1))
                       for k in range(n))

    def reuse(self, start_xy, ref_pts, goal_xy, max_detour=1.25):
        """Route to goal_xy that follows another rover's completed route where
        it is still safe on THIS rover's map, and replans only where it is not.

        Never followed blind:
          1. every reference waypoint is checked against this planner's slope
             map; waypoints on blocked ground are dropped;
          2. the rover joins the reference where that is cheapest (distance
             to the join plus what is left of the reference), planning its
             own way there;
          3. each reference segment that crosses blocked ground is replaced by
             a local plan between its two ends (a patch); the clear ones are
             kept as they are;
          4. if the result is more than `max_detour` times the direct plan,
             the direct plan wins.
        Returns (route, info)."""
        ref = [tuple(map(float, p)) for p in ref_pts]
        if not ref or math.dist(ref[-1], goal_xy) > 0.5:
            ref.append(tuple(map(float, goal_xy)))
        kept = [p for p in ref if not self.blocked(*p)]
        info = {"ref_points": len(ref), "ref_m": round(_length(ref), 1),
                "dropped_points": len(ref) - len(kept)}
        direct = self.plan(start_xy, goal_xy)
        direct_m = _length(direct)
        info["direct_m"] = round(direct_m, 1)
        if len(kept) < 2:
            return direct, dict(info, mode="replanned: reference route not usable",
                                reused_m=0.0, patches=0, route_m=round(direct_m, 1))
        rest = [0.0] * len(kept)
        for i in range(len(kept) - 2, -1, -1):
            rest[i] = rest[i + 1] + math.dist(kept[i], kept[i + 1])
        j = min(range(len(kept) - 1), key=lambda i: math.dist(start_xy, kept[i]) + rest[i])
        try:
            parts = [self.plan(start_xy, kept[j])]
            reused = patched = 0.0
            patches = 0
            for a, b in zip(kept[j:-1], kept[j + 1:]):
                if self.segment_clear(a, b):
                    parts.append(np.array([b]))
                    reused += math.dist(a, b)
                else:
                    patch = self.plan(a, b)
                    parts.append(patch[1:])
                    patched += _length(patch)
                    patches += 1
        except NoRoute:
            return direct, dict(info, mode="replanned: reference route could not be repaired",
                                reused_m=0.0, patches=0, route_m=round(direct_m, 1))
        route = np.vstack(parts)
        total = _length(route)
        if total > max_detour * direct_m:
            return direct, dict(info, mode="replanned: following it was a detour",
                                reused_m=0.0, patches=0, route_m=round(direct_m, 1),
                                reuse_would_be_m=round(total, 1))
        return route, dict(info, mode="reused reference route", reused_m=round(reused, 1),
                           patched_m=round(patched, 1), patches=patches,
                           joined_after_m=round(_length(parts[0]), 1), route_m=round(total, 1))

    def free_near(self, x, y, r_max=60.0):
        """Nearest unblocked point to (x, y), searching out to r_max metres."""
        if not self.blocked(x, y):
            return x, y
        for r in np.arange(2.0, r_max, 2.0):
            for a in np.linspace(0, 2 * math.pi, 24, endpoint=False):
                px, py = x + r * math.cos(a), y + r * math.sin(a)
                if not self.blocked(px, py):
                    return px, py
        raise NoRoute(f"no free ground within {r_max} m of ({x:.0f}, {y:.0f})")


def _length(route):
    r = np.asarray(route, float)
    return float(np.sum(np.hypot(*np.diff(r, axis=0).T))) if len(r) > 1 else 0.0


route_length = _length
