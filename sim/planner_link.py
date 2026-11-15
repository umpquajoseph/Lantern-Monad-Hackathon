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
        return np.array([start_xy, goal_xy], float)

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
