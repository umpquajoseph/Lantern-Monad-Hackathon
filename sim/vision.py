"""Step 6, vision: crater rims and obstacles from the rover's front camera.

Input each step: the first-person image and depth from sim/scene.py
(scene.first_person) and the scene's camera model. Nothing here reads the
rover's pose: everything comes out in the rover frame [x_forward, y_left].

Rims (spec step 6; Cauligi et al. 2023 sec. 3A and Fig. 3, Atha et al. 2024):
the lamp sits 0.2 m below the camera, so the near rim of a crater casts a
dark shadow onto the ground just beyond it. Canny finds the edges; an edge
is kept as a rim point when the image is darker just beyond it (further up
the image) than just before it, and the ground beyond is further away (the
shadowed side). Its position comes from the depth, with noise added, as the
spec says ("the renderer's depth, with noise added, places each rim point").

Obstacles: the depth is turned into rover-frame points, binned into a small
height grid ahead of the rover, and a cell is a hazard if the ground steps
by more than the wheel can climb within one cell's neighbourhood, or slopes
more than the planner's limit across 2 m. Rocks and rim lips from the
display layer exist only here: the planner's slope map never sees them.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

# --- rims
# Atha et al. 2024 sec. IV: rims are seen from about 5 to 25 m; Cauligi et
# al. 2023 sec. 1: Canny finds rims beyond 10 m. Stereo depth is good to
# about 20 m here (assumed), so rims are kept between these ranges.
RIM_RANGE_M = (3.0, 20.0)       # assumed, from the sources above
CANNY_LO, CANNY_HI = 30, 90     # assumed: Canny thresholds on the 8-bit image
SHADOW_CONTRAST = 25            # assumed: grey levels darker just beyond the edge than before it
SHADOW_PROBE_PX = 3             # assumed: pixels above/below the edge sampled for the contrast test
DEPTH_JUMP_M = 0.15             # assumed: the shadowed side must be at least this much further away
MIN_EDGE_PX = 25                # assumed: drop edge fragments shorter than this (rock shadows, noise)
MAX_RIM_POINTS = 80             # assumed: subsample to at most this many points per frame
DEPTH_NOISE_FRAC = 0.005        # assumed: 1-sigma range noise, fraction of range (0.05 m at 10 m)

# --- obstacles
HAZARD_RANGE_M = (3.0, 10.0)    # assumed: ShadowNav notes stereo is reliable to about 10 m
HAZARD_HALF_WIDTH_M = 6.0       # assumed: lateral extent of the hazard grid
HAZARD_CELL_M = 0.25            # the sim grid's cell
STEP_LIMIT_M = 0.15             # assumed: half the wheel radius in sim/scene.py's rover model (0.3 m)
SLOPE_LIMIT_DEG = 15.0          # spec step 4: the planner's slope limit
SLOPE_BASELINE_M = 2.0          # docs/handoffs.md step 2: slope measured across 2 m
MIN_POINTS_PER_CELL = 2         # assumed: cells with fewer depth points are unknown, not hazards
STEP_USES_MAX = False           # assumed: a cell's height is its mean (the max picks up depth noise)


class Vision:
    """Turns one first-person frame into rim points and hazards (rover frame)."""

    NAME = ("vision: Canny on the lamp's rim shadows, placed by depth with noise; "
            "hazards from a height grid built from depth (sim/vision.py)")

    def __init__(self, scene, rng: np.random.Generator):
        self.scene = scene
        self.rng = rng
        self.cam = scene.camera_model()

    # -- helpers ------------------------------------------------------------
    def points(self, depth):
        """(h, w, 3) rover-frame points [x_fwd, y_left, z_up] from the camera, with range noise."""
        d = depth.astype(np.float64)
        noisy = d * (1.0 + DEPTH_NOISE_FRAC * self.rng.standard_normal(d.shape))
        return self.scene.depth_to_rover(noisy.astype(np.float32))

    # -- rims -------------------------------------------------------------------
    def rims(self, img, depth, P=None):
        """Rim points (M, 2) [x_forward, y_left] in metres."""
        g = img[..., 0] if img.ndim == 3 else img
        g = cv2.GaussianBlur(g, (3, 3), 0)
        edges = cv2.Canny(g, CANNY_LO, CANNY_HI) > 0
        h, w = g.shape
        k = SHADOW_PROBE_PX
        edges[:k + 1, :] = False
        edges[h - k - 1:, :] = False
        # Drop short fragments.
        n, lab, stats, _ = cv2.connectedComponentsWithStats(edges.astype(np.uint8), connectivity=8)
        keep = np.zeros(n, bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= MIN_EDGE_PX
        edges &= keep[lab]
        v, u = np.nonzero(edges)
        if len(v) == 0:
            return np.zeros((0, 2))
        gi = g.astype(np.int16)
        before = gi[v + k, u]          # nearer ground (lower in the image)
        beyond = gi[v - k, u]          # further ground (higher in the image)
        d_before = depth[v + k, u]
        d_beyond = depth[v - k, u]
        ok = (before - beyond >= SHADOW_CONTRAST)
        ok &= np.isfinite(d_before) & (~np.isfinite(d_beyond) | (d_beyond - d_before > DEPTH_JUMP_M))
        v, u = v[ok], u[ok]
        if len(v) == 0:
            return np.zeros((0, 2))
        if P is None:
            P = self.points(depth)
        # The rim is the lit edge: place it with the depth just before the shadow.
        pts = P[np.minimum(v + 1, h - 1), u][:, :2]
        good = np.all(np.isfinite(pts), axis=1)
        pts = pts[good]
        r = np.hypot(pts[:, 0], pts[:, 1])
        pts = pts[(r >= RIM_RANGE_M[0]) & (r <= RIM_RANGE_M[1])]
        if len(pts) > MAX_RIM_POINTS:
            pts = pts[self.rng.choice(len(pts), MAX_RIM_POINTS, replace=False)]
        return pts

    # -- hazards ----------------------------------------------------------------
    def hazards(self, depth, P=None):
        """Hazard cell centres (K, 2) [x_forward, y_left], and the reason per cell
        ("step" or "slope") as a list."""
        if P is None:
            P = self.points(depth)
        p = P.reshape(-1, 3)
        p = p[np.all(np.isfinite(p), axis=1)]
        x0, x1 = HAZARD_RANGE_M
        p = p[(p[:, 0] >= x0) & (p[:, 0] < x1) & (np.abs(p[:, 1]) < HAZARD_HALF_WIDTH_M)]
        if len(p) == 0:
            return np.zeros((0, 2)), []
        c = HAZARD_CELL_M
        nx = int(math.ceil((x1 - x0) / c))
        ny = int(math.ceil(2 * HAZARD_HALF_WIDTH_M / c))
        ix = np.clip(((p[:, 0] - x0) / c).astype(int), 0, nx - 1)
        iy = np.clip(((p[:, 1] + HAZARD_HALF_WIDTH_M) / c).astype(int), 0, ny - 1)
        flat = ix * ny + iy
        cnt = np.bincount(flat, minlength=nx * ny)
        zsum = np.bincount(flat, weights=p[:, 2], minlength=nx * ny)
        zmax = np.full(nx * ny, -np.inf)
        np.maximum.at(zmax, flat, p[:, 2])
        known = cnt >= MIN_POINTS_PER_CELL
        zmean = np.where(known, zsum / np.maximum(cnt, 1), np.nan).reshape(nx, ny)
        zmax = np.where(known, zmax, np.nan).reshape(nx, ny)
        known = known.reshape(nx, ny)
        # Step: the tallest point in a cell against the mean ground of its 3x3
        # neighbourhood (a rock standing on the ground).
        pad = np.pad(zmean, 1, constant_values=np.nan)
        neigh = np.stack([pad[i:i + nx, j:j + ny] for i in range(3) for j in range(3)])
        with np.errstate(all="ignore"):
            ground = np.nanmin(neigh, axis=0)  # all-NaN cells stay NaN (unknown)
            top = zmax if STEP_USES_MAX else zmean
            step = top - ground
        # Slope across 2 m: central difference of the mean height.
        b = int(round(SLOPE_BASELINE_M / c / 2))
        with np.errstate(all="ignore"):
            gx = np.full((nx, ny), np.nan)
            gy = np.full((nx, ny), np.nan)
            gx[b:-b, :] = (zmean[2 * b:, :] - zmean[:-2 * b, :]) / (2 * b * c)
            gy[:, b:-b] = (zmean[:, 2 * b:] - zmean[:, :-2 * b]) / (2 * b * c)
            slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        is_step = known & (step > STEP_LIMIT_M)
        is_slope = known & (slope > SLOPE_LIMIT_DEG) & ~is_step
        hi, hj = np.nonzero(is_step | is_slope)
        centres = np.column_stack([x0 + (hi + 0.5) * c, -HAZARD_HALF_WIDTH_M + (hj + 0.5) * c])
        reasons = ["step" if is_step[i, j] else "slope" for i, j in zip(hi, hj)]
        return centres, reasons

    def process(self, img, depth):
        """(rims (M, 2), hazards (K, 2), reasons) for one frame."""
        P = self.points(depth)
        return self.rims(img, depth, P), *self.hazards(depth, P)


def rover_to_world(pts, pose):
    """Rover-frame [x_forward, y_left] -> world (x, y), for a pose (x, y, heading)."""
    pts = np.asarray(pts, float).reshape(-1, 2)
    x, y, h = pose
    c, s = math.cos(h), math.sin(h)
    return np.column_stack([x + c * pts[:, 0] - s * pts[:, 1], y + s * pts[:, 0] + c * pts[:, 1]])


# --------------------------------------------------------------------------
# Drawing: the planned path on the camera image (display only, never fed back
# into rim detection).
# --------------------------------------------------------------------------
PLAN_DOT_SPACING_M = 0.75   # assumed: spacing of the dots along the path
PLAN_MAX_AHEAD_M = 40.0     # assumed: draw this much of the plan
PLAN_RGB = (255, 214, 64)   # assumed: amber dots


def draw_plan(img, plan_world, pose, scene, hazards_world=None):
    """Return a copy of img with the planned path as a dotted line on the ground,
    and sensor hazards in view as small red marks. plan_world: (K, 2) world
    xy starting at the rover; pose: true (x, y, heading)."""
    out = np.ascontiguousarray(img).copy()
    cm = scene.camera_model()
    fx, fy, cx, cy = cm["fx"], cm["fy"], cm["cx"], cm["cy"]
    p = math.radians(cm["pitch_deg"])
    fwd_c = np.array([math.cos(p), 0.0, -math.sin(p)])
    right_c = np.array([0.0, -1.0, 0.0])
    up_c = np.array([math.sin(p), 0.0, math.cos(p)])
    x, y, h = pose
    o = scene.origin
    cam_z = float(scene.grid.height(x - o[0], y - o[1])) + cm["camera_height_m"]

    def project(wxy):
        wxy = np.asarray(wxy, float).reshape(-1, 2)
        gz = scene.grid.height(wxy[:, 0] - o[0], wxy[:, 1] - o[1]) + 0.05
        dx, dy = wxy[:, 0] - x, wxy[:, 1] - y
        P = np.column_stack([dx * math.cos(h) + dy * math.sin(h), -dx * math.sin(h) + dy * math.cos(h), gz - cam_z])
        zf = P @ fwd_c
        ok = zf > 0.5
        u = cx + fx * (P @ right_c) / np.where(ok, zf, 1)
        v = cy - fy * (P @ up_c) / np.where(ok, zf, 1)
        return u, v, ok, np.hypot(dx, dy)

    if hazards_world is not None and len(hazards_world):
        u, v, ok, r = project(hazards_world)
        for uu, vv, kk in zip(u, v, ok):
            if kk and 0 <= uu < out.shape[1] and 0 <= vv < out.shape[0]:
                cv2.drawMarker(out, (int(uu), int(vv)), (230, 60, 60), cv2.MARKER_TILTED_CROSS, 6, 1, cv2.LINE_AA)
    pts = np.asarray(plan_world, float).reshape(-1, 2)
    if len(pts) >= 2:
        dense = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            n = max(1, int(math.ceil(np.hypot(*(b - a)) / PLAN_DOT_SPACING_M)))
            dense += list(a + (b - a) * (np.arange(1, n + 1) / n)[:, None])
        dense = np.array(dense)
        along = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(dense, axis=0).T))])
        dense = dense[along <= PLAN_MAX_AHEAD_M]
        u, v, ok, r = project(dense)
        for uu, vv, kk, rr in zip(u, v, ok, r):
            if kk and 0 <= uu < out.shape[1] and 0 <= vv < out.shape[0]:
                rad = max(1, int(round(5.0 / max(rr, 1.0) * 2)))   # nearer dots larger
                cv2.circle(out, (int(uu), int(vv)), rad, PLAN_RGB, -1, cv2.LINE_AA)
    return out
