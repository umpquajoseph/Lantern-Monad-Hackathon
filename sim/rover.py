"""Step 4, path finding: route search on the slope map and the drive that follows it.

Plans a route from the rover to a goal on the 15 deg slope map (spec step 4:
"Route search on a slope map with a 15 deg limit"), then drives it in small
steps, turning smoothly, so the Scene's first- and third-person cameras
follow a believable track. Two rovers, the ice sensor and the chain are not
in this file yet.

Frames and formats follow docs/handoffs.md: world metres, x east, y north,
heading radians counterclockwise from +x, terrain from sim.terrain.

    planner = Planner(height, x0, y0, cell)
    route = planner.plan((x, y), (gx, gy))       # (K, 2) world waypoints
    drive = Drive(route, start_pose)              # start_pose (x, y, heading)
    while not drive.done:
        true_pose = drive.step()                  # (x, y, heading), one step
        scene.set_rover("A", true_pose)           # cameras follow

Demo (renders both views plus a map inset to video):
    python -m sim.rover --terrain data/terrain/synthetic.npz --start X Y --goal X Y

Every number from a source names it. Every other number is a named parameter
marked ASSUMED.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

from sim.terrain import LOLA_PX, slope_deg

# --------------------------------------------------------------------------
# Parameters.
# --------------------------------------------------------------------------
# Spec step 4 and PROSPECT brief: routes avoid slopes over 15 deg.
SLOPE_LIMIT_DEG = 15.0
# sim/terrain.py report(): slope at the LOLA baseline (5 m) is "the scale a
# planner would use". The planner grid is the height grid sampled every 5 m,
# with slope from terrain.slope_deg on it, exactly as report() computes it.
PLAN_CELL_M = LOLA_PX

# ASSUMED. Half the rover's width, metres: sim/scene.py's rover model has its
# wheels at y = +-0.72 m, 0.22 m wide, so 0.83 m. Straightened route segments
# keep this clearance from every blocked cell.
ROVER_HALF_WIDTH_M = 0.83
# ASSUMED. Distance driven per step, metres (sim/scene.py's scripted drive
# uses 0.5 m per frame).
STEP_M = 0.5
# ASSUMED. The drive steers at a point this far ahead on the route.
LOOKAHEAD_M = 3.0
# ASSUMED. Most the heading may change per metre driven, rad/m, so the
# cameras turn smoothly (about 17 deg per metre).
MAX_TURN_RAD_PER_M = 0.3
# ASSUMED. The drive stops when this close to the goal, metres.
GOAL_TOL_M = 0.5
# ASSUMED. Cells within this many metres of the map edge are blocked, so the
# rover and its cameras stay over terrain.
EDGE_MARGIN_M = 20.0


class NoRoute(Exception):
    """No route under the slope limit joins start and goal."""


# --------------------------------------------------------------------------
# Planner.
# --------------------------------------------------------------------------
class Planner:
    """A* over the 5 m slope map; route straightened by line of sight."""

    def __init__(self, height, x0, y0, cell, slope_limit_deg=SLOPE_LIMIT_DEG,
                 plan_cell_m=PLAN_CELL_M, edge_margin_m=EDGE_MARGIN_M):
        k = int(round(plan_cell_m / cell))
        self.k = k
        self.pc = k * cell                      # planner cell size, metres
        # Planner cell [I, J] is height cell [I*k, J*k]: world x0 + J*pc, y0 + I*pc.
        self.x0, self.y0 = float(x0), float(y0)
        h5 = np.asarray(height)[::k, ::k]
        self.slope = slope_deg(h5, self.pc)
        self.blocked = self.slope > slope_limit_deg
        m = int(math.ceil(edge_margin_m / self.pc))
        if m > 0:
            self.blocked[:m, :] = self.blocked[-m:, :] = True
            self.blocked[:, :m] = self.blocked[:, -m:] = True
        self.H, self.W = self.blocked.shape
        self.slope_limit_deg = slope_limit_deg

    # -- frames ------------------------------------------------------------
    def to_cell(self, x, y):
        return (int(round((y - self.y0) / self.pc)), int(round((x - self.x0) / self.pc)))

    def to_world(self, i, j):
        return (self.x0 + j * self.pc, self.y0 + i * self.pc)

    def is_blocked_xy(self, x, y):
        i, j = self.to_cell(x, y)
        if not (0 <= i < self.H and 0 <= j < self.W):
            return True
        return bool(self.blocked[i, j])

    # -- search ------------------------------------------------------------
    def plan(self, start_xy, goal_xy):
        """World route (K, 2) from start to goal, first row = start, last = goal."""
        s, g = self.to_cell(*start_xy), self.to_cell(*goal_xy)
        for name, c in (("start", s), ("goal", g)):
            if not (0 <= c[0] < self.H and 0 <= c[1] < self.W):
                raise NoRoute(f"{name} {c} is off the map")
            if self.blocked[c]:
                raise NoRoute(f"{name} cell {c} is over {self.slope_limit_deg} deg "
                              f"({self.slope[c]:.1f} deg) or inside the edge margin")
        cells = self._astar(s, g)
        if cells is None:
            raise NoRoute(f"no route under {self.slope_limit_deg} deg from {start_xy} to {goal_xy}")
        pts = [tuple(start_xy)] + [self.to_world(i, j) for i, j in cells[1:-1]] + [tuple(goal_xy)]
        return self._straighten(np.array(pts, dtype=np.float64))

    def _astar(self, s, g):
        """8-connected A* in cell units; diagonals may not cut a blocked corner."""
        H, W, B = self.H, self.W, self.blocked
        moves = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
                 (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
                 (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]

        def h(c):  # octile distance: admissible for these moves
            di, dj = abs(c[0] - g[0]), abs(c[1] - g[1])
            return max(di, dj) + (math.sqrt(2) - 1) * min(di, dj)

        best = {s: 0.0}
        parent = {s: None}
        heap = [(h(s), 0.0, s)]
        while heap:
            _, cost, c = heapq.heappop(heap)
            if c == g:
                out = []
                while c is not None:
                    out.append(c)
                    c = parent[c]
                return out[::-1]
            if cost > best[c]:
                continue
            for di, dj, w in moves:
                n = (c[0] + di, c[1] + dj)
                if not (0 <= n[0] < H and 0 <= n[1] < W) or B[n]:
                    continue
                if di and dj and (B[c[0] + di, c[1]] or B[c[0], c[1] + dj]):
                    continue
                nc = cost + w
                if nc < best.get(n, math.inf):
                    best[n] = nc
                    parent[n] = c
                    heapq.heappush(heap, (nc + h(n), nc, n))
        return None

    # -- straightening -----------------------------------------------------
    def segment_clear(self, a, b):
        """True if the segment a-b, widened by the rover's half width, touches no blocked cell."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        n = max(2, int(math.ceil(np.hypot(*(b - a)) / (0.25 * self.pc))) + 1)
        t = np.linspace(0.0, 1.0, n)[:, None]
        p = a + (b - a) * t
        r = ROVER_HALF_WIDTH_M
        for dx in (-r, 0.0, r):
            for dy in (-r, 0.0, r):
                fi = np.rint((p[:, 1] + dy - self.y0) / self.pc).astype(int)
                fj = np.rint((p[:, 0] + dx - self.x0) / self.pc).astype(int)
                if np.any((fi < 0) | (fi >= self.H) | (fj < 0) | (fj >= self.W)):
                    return False
                if np.any(self.blocked[fi, fj]):
                    return False
        return True

    def clearance(self, pts):
        """Smallest distance, metres, from any point to the edge of a blocked
        cell (0 inside one). Edge-margin cells count as blocked."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        bi, bj = np.nonzero(self.blocked)
        cx = self.x0 + bj * self.pc
        cy = self.y0 + bi * self.pc
        best = math.inf
        half = self.pc / 2
        for p in pts:
            near = (np.abs(cx - p[0]) < 30) & (np.abs(cy - p[1]) < 30)
            if not np.any(near):
                continue
            dx = np.maximum(np.abs(cx[near] - p[0]) - half, 0.0)
            dy = np.maximum(np.abs(cy[near] - p[1]) - half, 0.0)
            best = min(best, float(np.min(np.hypot(dx, dy))))
        return best

    def _straighten(self, pts):
        """Drop waypoints while the straight line to a later one stays clear."""
        out = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.segment_clear(pts[i], pts[j]):
                j -= 1
            out.append(pts[j])
            i = j
        return np.array(out)


# --------------------------------------------------------------------------
# Drive: follows a route in small steps, turning smoothly.
# --------------------------------------------------------------------------
class Drive:
    """Pure pursuit along a route. step() returns the next true pose (x, y, heading)."""

    def __init__(self, route, start_pose, step_m=STEP_M, lookahead_m=LOOKAHEAD_M,
                 max_turn_rad_per_m=MAX_TURN_RAD_PER_M, goal_tol_m=GOAL_TOL_M):
        self.route = np.asarray(route, dtype=np.float64)
        self.pose = tuple(float(v) for v in start_pose)
        self.step_m, self.L = step_m, lookahead_m
        self.max_turn = max_turn_rad_per_m * step_m
        self.goal_tol = goal_tol_m
        seg = np.diff(self.route, axis=0)
        self._seg_len = np.hypot(seg[:, 0], seg[:, 1])
        self._cum = np.concatenate([[0.0], np.cumsum(self._seg_len)])
        self._s = 0.0  # progress along the route, metres (never goes back)
        self.done = np.hypot(*(self.route[-1] - self.pose[:2])) <= goal_tol_m

    def _point_at(self, s):
        s = min(max(s, 0.0), self._cum[-1])
        k = min(int(np.searchsorted(self._cum, s, side="right")) - 1, len(self._seg_len) - 1)
        t = 0.0 if self._seg_len[k] == 0 else (s - self._cum[k]) / self._seg_len[k]
        return self.route[k] + t * (self.route[k + 1] - self.route[k])

    def _project(self, xy):
        """Arc length of the route point nearest xy, searched forward from current progress."""
        best_s, best_d = self._s, math.inf
        for k in range(len(self._seg_len)):
            if self._cum[k + 1] < self._s:
                continue
            a, b = self.route[k], self.route[k + 1]
            L = self._seg_len[k]
            t = 0.0 if L == 0 else float(np.clip(np.dot(xy - a, b - a) / (L * L), 0.0, 1.0))
            d = float(np.hypot(*(a + t * (b - a) - xy)))
            s = self._cum[k] + t * L
            if d < best_d and s >= self._s:
                best_d, best_s = d, s
            if self._cum[k] > self._s + 4 * self.L:
                break
        return best_s

    def step(self, nav_xy=None):
        """Advance one step. Steers from nav_xy (the navigation estimate) if
        given, else from the true position. Returns the new true pose."""
        if self.done:
            return self.pose
        x, y, hd = self.pose
        here = np.array(nav_xy if nav_xy is not None else (x, y), dtype=np.float64)
        self._s = self._project(here)
        goal = self.route[-1]
        remaining = self._cum[-1] - self._s
        target = goal if remaining <= self.L else self._point_at(self._s + self.L)
        want = math.atan2(target[1] - here[1], target[0] - here[0])
        turn = (want - hd + math.pi) % (2 * math.pi) - math.pi
        hd += float(np.clip(turn, -self.max_turn, self.max_turn))
        d = min(self.step_m, float(np.hypot(*(goal - here))))
        x += d * math.cos(hd)
        y += d * math.sin(hd)
        self.pose = (x, y, (hd + math.pi) % (2 * math.pi) - math.pi)
        if np.hypot(*(goal - np.array([x, y]))) <= self.goal_tol:
            self.done = True
        return self.pose


# --------------------------------------------------------------------------
# Demo: plan, drive, render both Scene views and a map inset to video.
# --------------------------------------------------------------------------
INSET_PX = 220        # ASSUMED: map inset size in the demo video, pixels,
INSET_VIEW_M = 160.0  # ASSUMED: and the width of ground it shows, metres.


def _map_inset(planner, route, track, pose, size=300, view_m=300.0):
    """Top-down inset (size, size, 3) uint8 BGR->RGB: blocked cells red, route yellow, track white."""
    import cv2
    x, y, hd = pose
    half = view_m / 2
    img = np.full((size, size, 3), 18, np.uint8)
    s = size / view_m

    def px(wx, wy):
        return (int(round((wx - (x - half)) * s)), int(round(((y + half) - wy) * s)))

    i0, j0 = planner.to_cell(x - half, y - half)
    i1, j1 = planner.to_cell(x + half, y + half)
    for i in range(max(i0, 0), min(i1 + 1, planner.H)):
        for j in range(max(j0, 0), min(j1 + 1, planner.W)):
            if planner.blocked[i, j]:
                cx, cy = planner.to_world(i, j)
                p0 = px(cx - planner.pc / 2, cy + planner.pc / 2)
                p1 = px(cx + planner.pc / 2, cy - planner.pc / 2)
                cv2.rectangle(img, p0, p1, (200, 40, 40), -1)
    pts = np.array([px(*p) for p in route], np.int32)
    cv2.polylines(img, [pts], False, (255, 210, 40), 1, cv2.LINE_AA)
    if len(track) >= 2:
        cv2.polylines(img, [np.array([px(*p) for p in track], np.int32)], False, (240, 240, 240), 2, cv2.LINE_AA)
    if hd is not None:
        c = px(x, y)
        tip = (int(c[0] + 10 * math.cos(hd)), int(c[1] - 10 * math.sin(hd)))
        cv2.arrowedLine(img, c, tip, (80, 190, 255), 2, cv2.LINE_AA, tipLength=0.5)
    cv2.putText(img, f"red: slope > {planner.slope_limit_deg:.0f} deg at {planner.pc:.0f} m",
                (6, size - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 220, 220), 1, cv2.LINE_AA)
    cv2.putText(img, "yellow: route  white: driven", (6, size - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 220, 220), 1, cv2.LINE_AA)
    return img


def main():
    import argparse
    import os
    import time

    import cv2

    from sim.scene import FP_SIZE, TP_SIZE, Scene
    from sim.terrain import default_path, load_terrain

    ap = argparse.ArgumentParser(description="Plan a route under the slope limit and drive it, rendering both views.")
    ap.add_argument("--terrain", default=None, help="terrain .npz from step 2 (default: sim.terrain.default_path())")
    ap.add_argument("--start", nargs=2, type=float, required=True, metavar=("X", "Y"), help="world metres")
    ap.add_argument("--goal", nargs=2, type=float, required=True, metavar=("X", "Y"), help="world metres")
    ap.add_argument("--out", default="frames/rover_drive.mp4")
    ap.add_argument("--max-frames", type=int, default=4000)
    ap.add_argument("--no-render", action="store_true", help="plan and drive only; write the map PNG")
    a = ap.parse_args()

    path = a.terrain or default_path()
    height, craters, x0, y0, cell = load_terrain(path)
    t = time.perf_counter()
    planner = Planner(height, x0, y0, cell)
    route = planner.plan(tuple(a.start), tuple(a.goal))
    print(f"terrain {path}: {planner.blocked.sum()} of {planner.blocked.size} planner cells blocked")
    print(f"route: {len(route)} waypoints, {np.sum(np.hypot(*np.diff(route, axis=0).T)):.1f} m, "
          f"straight line {math.dist(a.start, a.goal):.1f} m, planned in {1e3 * (time.perf_counter() - t):.0f} ms")

    hd0 = math.atan2(route[1][1] - route[0][1], route[1][0] - route[0][0])
    drive = Drive(route, (a.start[0], a.start[1], hd0))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    scene = vw = None
    if not a.no_render:
        scene = Scene(height, craters, x0, y0, cell)
        fw, fh = FP_SIZE
        tw, th = TP_SIZE
        vw = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), 10, (tw + fw, th))
    track = [drive.pose[:2]]
    k = 0
    while not drive.done and k < a.max_frames:
        pose = drive.step()
        track.append(pose[:2])
        if scene is not None:
            scene.set_rover("A", pose)
            img, _ = scene.first_person("A")
            tp = scene.third_person("A", {"true_track": np.array(track)})
            frame = np.zeros((th, tw + fw, 3), np.uint8)
            frame[:, :tw] = tp
            frame[(th - fh) // 2:(th - fh) // 2 + fh, tw:] = img
            frame[10:10 + INSET_PX, tw - 10 - INSET_PX:tw - 10] = _map_inset(planner, route, track, pose,
                                                                         size=INSET_PX, view_m=INSET_VIEW_M)
            vw.write(np.ascontiguousarray(frame[..., ::-1]))
            if k % 100 == 0:
                cv2.imwrite(a.out.replace(".mp4", f"_f{k:04d}.png"), frame[..., ::-1])
        k += 1
    if vw is not None:
        vw.release()
    track = np.array(track)
    print(f"drove {np.sum(np.hypot(*np.diff(track, axis=0).T)):.1f} m in {k} steps, "
          f"{'reached goal' if drive.done else 'STOPPED at max-frames'}, "
          f"closest approach of the rover's centre to a blocked cell: {planner.clearance(track):.2f} m "
          f"(rover half width {ROVER_HALF_WIDTH_M} m)")
    full = _map_inset(planner, route, track, (np.mean(track[:, 0]), np.mean(track[:, 1]), None), size=900,
                      view_m=max(np.ptp(track[:, 0]), np.ptp(track[:, 1])) + 60.0)
    cv2.imwrite(a.out.replace(".mp4", "_map.png"), full[..., ::-1])
    print(f"wrote {a.out.replace('.mp4', '_map.png')}" + ("" if a.no_render else f" and {a.out}"))


if __name__ == "__main__":
    main()
