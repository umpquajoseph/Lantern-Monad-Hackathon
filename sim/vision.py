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

Depth noise is stereo's (sim/settings.py stereo_*): the camera is ShadowNav's
field rig (A24 sec. IV-B: 0.26 m baseline, 5120 px across a 60 deg view), and
each pixel's disparity carries Gaussian noise of stereo_disparity_sigma_px.
Depth is Z = f B / disparity, so its error grows as Z^2 (about 0.13 m at 5 m,
1.15 m at 15 m, 2 m at 20 m). The shadow test above reads the noiseless
depth: it compares pixels 3 px apart, whose stereo errors are correlated.

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

try:
    from sim.settings import Settings as _Settings
except ImportError:  # run as a script from sim/
    from settings import Settings as _Settings
_S = _Settings()

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
# Contour filter, A24 sec. III-D: rim pixels are closed (dilate, then erode)
# into contours, which "are then filtered by both number of pixels and
# estimated length", the width from Eq. (1), W_m = W_px R_m / l_focal, R_m
# the contour's mean range. A24 gives no thresholds. Set from what the step
# is for, keeping rims of craters the map can hold: the map cut, times the
# share of the leading arc detected at 10 m (C23 sec. 1, "captures 80% of
# the leading crater arc at 10 m"). Fixed before the calibration was read.
CONTOUR_CLOSE_PX = 5            # assumed: closing kernel (px) that joins broken pieces of one rim
CONTOUR_MIN_PX = MIN_EDGE_PX    # assumed: the edge-fragment rule above, applied to contours
CONTOUR_ARC_SHARE = 0.8         # C23 sec. 1: 80% of the leading arc at 10 m
# Stereo depth noise (sim/settings.py, sourced there): f B in pixel-metres of
# the rig, and the disparity noise in the rig's pixels.
STEREO_FB_PX_M = (_S.stereo_width_px / 2.0) / math.tan(math.radians(_S.stereo_hfov_deg / 2.0)) * _S.stereo_baseline_m
DISPARITY_SIGMA_PX = _S.stereo_disparity_sigma_px
# The model before 16 Nov 2026: range noise a fixed 0.5% of range. Kept for
# sim/calibrate.py's before/after numbers; never a default.
DEPTH_NOISE_FRAC_OLD = 0.005


def stereo_sigma_z(z):
    """1-sigma stereo depth error at depth z (m), first order: z^2 sigma_d / (f B)."""
    return np.asarray(z, float) ** 2 * DISPARITY_SIGMA_PX / STEREO_FB_PX_M

# --- obstacles
HAZARD_RANGE_M = (3.0, 10.0)    # assumed: ShadowNav notes stereo is reliable to about 10 m
HAZARD_HALF_WIDTH_M = 6.0       # assumed: lateral extent of the hazard grid
HAZARD_CELL_M = 0.25            # the sim grid's cell
STEP_LIMIT_M = 0.15             # assumed: half the wheel radius in sim/scene.py's rover model (0.3 m)
SLOPE_LIMIT_DEG = 15.0          # spec step 4: the planner's slope limit
SLOPE_BASELINE_M = 2.0          # docs/handoffs.md step 2: slope measured across 2 m
MIN_POINTS_PER_CELL = 2         # assumed: cells with fewer depth points are unknown, not hazards
STEP_USES_MAX = False           # assumed: a cell's height is its mean (the max picks up depth noise)


def contour_arc_share(source="default"):
    """Share of the leading arc the width threshold assumes. "c23": C23 sec. 1's 0.8;
    "measured": Settings.rim_arc_share_measured (sim/arc_share.py); "default": measured
    once it is set, else C23's."""
    m = _S.rim_arc_share_measured
    if source == "c23" or (source == "default" and not math.isfinite(m)):
        return CONTOUR_ARC_SHARE
    if not math.isfinite(m):
        raise ValueError("Settings.rim_arc_share_measured is not set yet (python -m sim.arc_share)")
    return m


def contour_ok(width_m, npx, source="default"):
    """A24 sec. III-D contour filter: enough pixels, and wide enough to be a mapped crater's rim:
    width >= arc share x the map cut."""
    from sim.settings import WorldRanges
    w_min = contour_arc_share(source) * WorldRanges().orbital_map_min_d_m
    return (np.asarray(npx) >= CONTOUR_MIN_PX) & (np.asarray(width_m) >= w_min)


class Vision:
    """Turns one first-person frame into rim points and hazards (rover frame)."""

    NAME = ("vision: Canny on the lamp's rim shadows, placed by depth with noise; "
            "hazards from a height grid built from depth (sim/vision.py)")

    def __init__(self, scene, rng: np.random.Generator, depth_model: str = "stereo",
                 contour_filter: bool = True):
        """depth_model: "stereo" (the default) or "old" (0.5% of range, before 16 Nov 2026).
        contour_filter: A24 sec. III-D's contour filter (default on; off only for sim/calibrate.py)."""
        self.scene = scene
        self.rng = rng
        self.cam = scene.camera_model()
        self.depth_model = depth_model
        self.contour_filter = contour_filter
        c = self.cam
        u = (np.arange(c["width"]) + 0.5 - c["cx"]) / c["fx"]
        v = (c["cy"] - (np.arange(c["height"]) + 0.5)) / c["fy"]
        U, V = np.meshgrid(u, v)
        self._cos = 1.0 / np.sqrt(1.0 + U * U + V * V)   # depth along the optical axis / range along the ray

    # -- helpers ------------------------------------------------------------
    def noisy_range(self, rng_m, cos):
        """Range along each pixel ray with depth noise. rng_m, cos: same shape."""
        r = np.asarray(rng_m, np.float64)
        n = self.rng.standard_normal(r.shape)
        if self.depth_model == "old":
            return r * (1.0 + DEPTH_NOISE_FRAC_OLD * n)
        z = r * cos
        with np.errstate(divide="ignore", invalid="ignore"):
            disp = STEREO_FB_PX_M / z + DISPARITY_SIGMA_PX * n      # disparity in the rig's pixels
            zn = np.where(disp > 0, STEREO_FB_PX_M / disp, np.nan)  # no match beyond infinity
        return zn / cos

    def points(self, depth):
        """(h, w, 3) rover-frame points [x_fwd, y_left, z_up] from the camera, with stereo depth noise."""
        noisy = self.noisy_range(depth.astype(np.float64), self._cos)
        return self.scene.depth_to_rover(noisy.astype(np.float32))

    def _rover_points(self, rng_m, v, u):
        """Rover-frame [x_fwd, y_left] of pixels (v, u) at range rng_m along their rays."""
        c = self.cam
        U = (u + 0.5 - c["cx"]) / c["fx"]
        V = (c["cy"] - (v + 0.5)) / c["fy"]
        z_eye = rng_m * self._cos[v, u]
        p = math.radians(c["pitch_deg"])
        return np.column_stack([z_eye * (math.cos(p) + V * math.sin(p)), -z_eye * U])

    # -- rims -------------------------------------------------------------------
    def rims(self, img, depth, P=None):
        """Rim points (M, 2) [x_forward, y_left] in metres."""
        return self.rims_detail(img, depth, P)["rims"]

    def detect(self, img, depth):
        """Rim pixels (v, u), before placement: deterministic, no noise drawn."""
        v, u, width_m, npx = self.detect_contours(img, depth)
        if self.contour_filter and len(v):
            ok = contour_ok(width_m, npx)
            v, u = v[ok], u[ok]
        return v, u

    def detect_contours(self, img, depth):
        """Rim pixels (v, u) before A24's contour filter, with their contour's estimated
        width (m, Eq. 1) and pixel count, per pixel. Mean range is the renderer's depth:
        a contour averages tens of pixels, so its stereo noise is small."""
        v, u = self._shadow_edges(img, depth)
        if len(v) == 0:
            return v, u, np.zeros(0), np.zeros(0, int)
        h, w = depth.shape
        mask = np.zeros((h, w), np.uint8)
        mask[v, u] = 1
        k = np.ones((CONTOUR_CLOSE_PX, CONTOUR_CLOSE_PX), np.uint8)
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        n, lab, _, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
        comp = lab[v, u]
        npx = np.bincount(comp, minlength=n)
        r = np.bincount(comp, weights=depth[v, u].astype(np.float64), minlength=n) / np.maximum(npx, 1)
        umin = np.full(n, w, int)
        umax = np.full(n, -1, int)
        np.minimum.at(umin, comp, u)
        np.maximum.at(umax, comp, u)
        width_m = (umax - umin + 1) * r / self.cam["fx"]          # A24 Eq. (1)
        return v, u, width_m[comp], npx[comp]

    def _shadow_edges(self, img, depth):
        """Canny edges kept by the lamp-shadow and depth-jump tests (the rim pixels)."""
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
            return v, u
        gi = g.astype(np.int16)
        before = gi[v + k, u]          # nearer ground (lower in the image)
        beyond = gi[v - k, u]          # further ground (higher in the image)
        d_before = depth[v + k, u]
        d_beyond = depth[v - k, u]
        ok = (before - beyond >= SHADOW_CONTRAST)
        ok &= np.isfinite(d_before) & (~np.isfinite(d_beyond) | (d_beyond - d_before > DEPTH_JUMP_M))
        # The rim is the lit edge: it is placed with the depth just before the shadow.
        return np.minimum(v[ok] + 1, h - 1), u[ok]

    def rims_detail(self, img, depth, P=None, pixels=None):
        """dict: rims (M, 2) as rims() returns; true (M, 2) the same pixels placed with the
        renderer's depth (no noise; for scoring only); uv (M, 2) their pixels.
        pixels: detect()'s output, if already computed for this frame."""
        v, u = self.detect(img, depth) if pixels is None else pixels
        return self.place(v, u, depth[v, u], P)

    def place(self, v, u, r0, P=None):
        """Place detected rim pixels (v, u), whose renderer range is r0, with depth noise:
        range cut, subsample, as rims_detail() returns. P: noisy points to use instead."""
        if len(v) == 0:
            return {"rims": np.zeros((0, 2)), "true": np.zeros((0, 2)), "uv": np.zeros((0, 2), int),
                    "idx": np.zeros(0, int)}
        r0 = np.asarray(r0, np.float64)
        if P is None:
            pts = self._rover_points(self.noisy_range(r0, self._cos[v, u]), v, u)
        else:
            pts = P[v, u][:, :2]
        true = self._rover_points(r0, v, u)
        good = np.all(np.isfinite(pts), axis=1)
        r = np.hypot(pts[:, 0], pts[:, 1])
        keep = np.flatnonzero(good & (r >= RIM_RANGE_M[0]) & (r <= RIM_RANGE_M[1]))
        if len(keep) > MAX_RIM_POINTS:
            keep = keep[self.rng.choice(len(keep), MAX_RIM_POINTS, replace=False)]
        return {"rims": pts[keep], "true": true[keep], "uv": np.column_stack([u[keep], v[keep]]), "idx": keep}

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
