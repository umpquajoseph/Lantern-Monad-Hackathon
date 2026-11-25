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
A24 sec. III-D's contour filter then keeps rims wide enough to be a mapped
crater's, and C23 sec. 3A's positive-obstacle rejection drops a rim point
when the ground directly beyond it is flat or falls (a terrain crest, not the
far wall of a crater), keeping it when that ground rises or has too little
stereo to judge (Vision.far_side_keep).

Depth noise is stereo's (sim/settings.py stereo_*): the camera is ShadowNav's
field rig (A24 sec. IV-B: 0.26 m baseline, 5120 px across a 60 deg view), and
each pixel's disparity carries Gaussian noise of stereo_disparity_sigma_px.
Depth is Z = f B / disparity, so its error grows as Z^2 (about 0.13 m at 5 m,
1.15 m at 15 m). That law reproduces A24 Table 1 to 20 m and not beyond, where
SGBM leaves what it cannot match as holes: from 20 m out the range error is
the table's RMS for the band (1.48 m at 20-30 m, 2.34 m at 30-40 m), and at
every range a pixel is a hole (no depth) with the table's share for its band
(6.7 / 27.5 / 50.3 / 88.4% by 10 m band). One noisy range image is drawn per frame
(Vision.noisy_depth) and every step reads it: the depth-jump test, the
contour width, rim placement and the hazard points. The renderer's own
depth is read only by the scoring outputs ("true" placements). Each pixel's
noise and hole are drawn independently (assumed: a stereo matcher's errors are
correlated over its window, which this ignores; one render pixel spans 8 of
the rig's; Settings.stereo_hole_model "block" is the test case).

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
FAR_SIDE_PX = 80                # assumed: rows above the rim searched for the ground beyond it
FAR_SIDE_WINDOW_M = (0.2, 2.5)  # assumed: "the region directly beyond the detected edge" (C23 sec. 3A), ground distance past the rim
FAR_SIDE_MIN_POINTS = 5         # assumed: fewer stereo points than this in the window is "not enough stereo" (C23 sec. 3A)
HOLE_SEARCH_PX = 10             # assumed: how far down or up a column the search steps over stereo holes
                                # (A24 sec. III-D: columns "are searched from bottom to top with holes removed")
FAR_SIDE_MIN_SLOPE = 0.0        # C23 sec. 3A: kept only on a positive slope (rise per metre of ground distance)
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
# A24 Table 1 by 10 m band of true range (sim/settings.py stereo_*): the z^2 law to
# STEREO_LAW_MAX_M, the band's RMS as range error beyond, and each band's share of holes.
STEREO_LAW_MAX_M = _S.stereo_law_max_m
STEREO_BAND_M = 10.0
STEREO_BAND_RMS_M = np.asarray(_S.stereo_band_rms_m, float)
STEREO_HOLE_SHARE = np.asarray(_S.stereo_hole_pct, float) / 100.0
STEREO_HOLE_MODELS = ("pixel", "block", "none")


def stereo_band(r):
    """Index of A24 Table 1's 10 m band of true range r (beyond 40 m: the last band's)."""
    return np.clip((np.asarray(r, float) // STEREO_BAND_M).astype(int), 0, len(STEREO_HOLE_SHARE) - 1)
# A test case: range noise a fixed 0.5% of range. For sim/calibrate.py;
# never a default.
DEPTH_NOISE_FRAC_OLD = 0.005


def stereo_sigma_z(z):
    """1-sigma stereo depth error at depth z (m) of the z^2 law, first order: z^2 sigma_d / (f B).
    Vision.noisy_range uses it to STEREO_LAW_MAX_M only."""
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


def depth_jump_ok(d_before, d_beyond):
    """The shadow test's depth rule: the ground just beyond the edge is further away
    (or has no depth). d_before, d_beyond: ranges 3 px below and above the edge."""
    d_before, d_beyond = np.asarray(d_before), np.asarray(d_beyond)
    return np.isfinite(d_before) & (~np.isfinite(d_beyond) | (d_beyond - d_before > DEPTH_JUMP_M))


def contour_stats(v, u, r, shape, fx):
    """A24 sec. III-D contours of rim pixels (v, u): close the pixel mask, label the
    pieces, and give every pixel its contour's width (m, Eq. 1, from the contour's mean
    range over r, the ranges at (v, u)) and pixel count. NaN ranges are left out of the mean."""
    h, w = shape
    mask = np.zeros((h, w), np.uint8)
    mask[v, u] = 1
    k = np.ones((CONTOUR_CLOSE_PX, CONTOUR_CLOSE_PX), np.uint8)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    n, lab, _, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    comp = lab[v, u]
    npx = np.bincount(comp, minlength=n)
    r = np.asarray(r, np.float64)
    fin = np.isfinite(r)
    nr = np.bincount(comp[fin], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        rm = np.bincount(comp[fin], weights=r[fin], minlength=n) / nr
    umin = np.full(n, w, int)
    umax = np.full(n, -1, int)
    np.minimum.at(umin, comp, u)
    np.maximum.at(umax, comp, u)
    width_m = np.nan_to_num((umax - umin + 1) * rm / fx, nan=0.0)          # A24 Eq. (1)
    return width_m[comp], npx[comp]


def contour_arc_share(source="default"):
    """Share of the leading arc the width threshold assumes. "c23": C23 sec. 1's 0.8;
    "headon": Settings.rim_arc_share_headon (0.553);
    "route": Settings.rim_arc_share_route (rule route); "measured" / "default":
    Settings.rim_arc_share_measured, the route share once it is set, else the head-on one."""
    m = {"c23": CONTOUR_ARC_SHARE, "headon": _S.rim_arc_share_headon, "route": _S.rim_arc_share_route,
         "measured": _S.rim_arc_share_measured, "default": _S.rim_arc_share_measured}[source]
    if not math.isfinite(m):
        raise ValueError(f"arc share {source!r} is not measured yet")
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
                 contour_filter: bool = True, far_side: bool = True, holes: str | None = None):
        """depth_model: "stereo" (the default) or "old" (0.5% of range, a test case).
        contour_filter: A24 sec. III-D's contour filter (default on; off only for sim/calibrate.py).
        far_side: C23 sec. 3A's positive-obstacle rejection (far_side_ok; default on).
        holes: stereo holes, "pixel" / "block" / "none" (default Settings.stereo_hole_model;
        depth_model "old" has none)."""
        self.scene = scene
        self.rng = rng
        self.cam = scene.camera_model()
        self.axes = _level_axes(self.cam["pitch_deg"], self.cam["camera_height_m"])
        self.depth_model = depth_model
        self.contour_filter = contour_filter
        self.far_side = far_side
        self.holes = _S.stereo_hole_model if holes is None else holes
        if self.holes not in STEREO_HOLE_MODELS:
            raise ValueError(f"holes {self.holes!r}: one of {STEREO_HOLE_MODELS}")
        c = self.cam
        u = (np.arange(c["width"]) + 0.5 - c["cx"]) / c["fx"]
        v = (c["cy"] - (np.arange(c["height"]) + 0.5)) / c["fy"]
        U, V = np.meshgrid(u, v)
        self._cos = 1.0 / np.sqrt(1.0 + U * U + V * V)   # depth along the optical axis / range along the ray

    def set_pose(self, pose):
        """The frame's body attitude, for a rover at the true pose: the camera's
        axes in the level rover frame (sim/scene.py rover_axes). The rover is
        assumed to know its tilt exactly (inclinometer or IMU; assumed). A
        scene without rover_axes (sim/calibrate.py's worker _Cam) stays level."""
        if hasattr(self.scene, "rover_axes"):
            self.axes = self.scene.rover_axes(pose)

    # -- helpers ------------------------------------------------------------
    def noisy_range(self, rng_m, cos, n=None, u=None):
        """Range along each pixel ray as stereo reports it, NaN where it has no match.
        rng_m, cos: same shape. n: standard normal draws, u: uniform draws for the holes
        (Vision.hole_field); either drawn here per pixel if not given.

        A24 Table 1 (sim/settings.py stereo_*): to STEREO_LAW_MAX_M of true range the
        disparity carries Gaussian noise (depth error ~ z^2); beyond, the range error is
        Gaussian with the band's RMS. A pixel is a hole with its band's share."""
        r = np.asarray(rng_m, np.float64)
        n = self.rng.standard_normal(r.shape) if n is None else np.asarray(n, np.float64)
        if self.depth_model == "old":
            return r * (1.0 + DEPTH_NOISE_FRAC_OLD * n)
        z = r * cos
        with np.errstate(divide="ignore", invalid="ignore"):
            disp = STEREO_FB_PX_M / z + DISPARITY_SIGMA_PX * n      # disparity in the rig's pixels
            rn = np.where(disp > 0, STEREO_FB_PX_M / disp, np.nan) / cos   # no match beyond infinity
        with np.errstate(invalid="ignore"):
            far = r >= STEREO_LAW_MAX_M
            band = stereo_band(np.where(np.isfinite(r), r, 0.0))
            rn = np.where(far, r + STEREO_BAND_RMS_M[band] * n, rn)
            if self.holes != "none":
                u = self.rng.random(r.shape) if u is None else np.asarray(u, np.float64)
                rn = np.where(u < STEREO_HOLE_SHARE[band], np.nan, rn)
        return rn

    def hole_field(self, shape, rng=None):
        """(h, w) uniform draws deciding one frame's stereo holes (a pixel is a hole where
        its draw is under its band's share): one per pixel ("pixel"), or one per square of
        Settings.stereo_hole_block_px pixels ("block"). rng: default self.rng."""
        rng = self.rng if rng is None else rng
        h, w = shape
        if self.holes == "block":
            b = _S.stereo_hole_block_px
            f = rng.random((-(-h // b), -(-w // b)))
            return np.repeat(np.repeat(f, b, 0), b, 1)[:h, :w]
        return rng.random((h, w))

    def noisy_depth(self, depth):
        """One frame's range image with stereo noise and holes (NaN where no match):
        drawn once per frame, and every step of that frame reads it."""
        d = depth.astype(np.float64)
        n = self.rng.standard_normal(d.shape)
        u = self.hole_field(d.shape) if self.holes != "none" and self.depth_model != "old" else None
        return self.noisy_range(d, self._cos, n, u)

    def points(self, depth, nd=None):
        """(h, w, 3) rover-frame points [x_fwd, y_left, z_up] from the camera, with stereo depth
        noise. nd: the frame's noisy_depth(), if already drawn."""
        noisy = self.noisy_depth(depth) if nd is None else nd
        return self.scene.depth_to_rover(noisy.astype(np.float32), self.axes)

    def _rover_points(self, rng_m, v, u):
        """Rover-frame [x_fwd, y_left] of pixels (v, u) at range rng_m along their
        rays, from the pose (the camera's axes and offset: self.axes)."""
        c, a = self.cam, self.axes
        U = (u + 0.5 - c["cx"]) / c["fx"]
        V = (c["cy"] - (v + 0.5)) / c["fy"]
        z_eye = np.asarray(rng_m * self._cos[v, u], np.float64)
        P = z_eye[:, None] * (a["fwd"][None, :2] + U[:, None] * a["right"][None, :2] + V[:, None] * a["up"][None, :2])
        return P + a["offset"][None, :]

    # -- rims -------------------------------------------------------------------
    def rims(self, img, depth, P=None, nd=None):
        """Rim points (M, 2) [x_forward, y_left] in metres. nd: the frame's noisy_depth(),
        if already drawn (drawn here otherwise)."""
        return self.rims_detail(img, depth, P, nd=nd)["rims"]

    def detect(self, img, depth, nd=None):
        """Rim pixels (v, u), before placement, from the frame's noisy depth nd (drawn
        here if not given; pass nd=depth for the renderer's noiseless depth)."""
        v, u, width_m, npx = self.detect_contours(img, depth, nd)
        if self.contour_filter and len(v):
            ok = contour_ok(width_m, npx)
            v, u = v[ok], u[ok]
        if self.far_side and len(v):
            # C23 sec. 3A judges single points, so it runs after A24's contours are drawn
            # from the full mask (neither paper runs both; the order is assumed).
            ok = self.far_side_ok(v, u, nd)
            v, u = v[ok], u[ok]
        return v, u

    def detect_contours(self, img, depth, nd=None):
        """Rim pixels (v, u) before A24's contour filter, with their contour's estimated
        width (m, Eq. 1) and pixel count, per pixel. Both the depth-jump test and the
        contour's mean range read nd, the frame's noisy depth (drawn here if not given)."""
        nd = self.noisy_depth(depth) if nd is None else nd
        ve, u = self.edge_candidates(img)
        if len(ve) == 0:
            return ve, u, np.zeros(0), np.zeros(0, int)
        h = nd.shape[0]
        vp = self.placed_row(ve, h)
        br, bin_ = self.below_rows(ve, h)
        ar, ain = self.far_side_rows(vp)
        nd = np.asarray(nd, np.float64)
        ok, rp = self.column_tests(np.where(bin_, nd[br, u[:, None]], np.nan),
                                   np.where(ain, nd[ar, u[:, None]], np.nan))
        v, u, rp = vp[ok], u[ok], rp[ok]
        if len(v) == 0:
            return v, u, np.zeros(0), np.zeros(0, int)
        width_m, npx = contour_stats(v, u, rp, nd.shape, self.cam["fx"])
        return v, u, width_m, npx

    @staticmethod
    def below_rows(ve, h):
        """(rows (N, SHADOW_PROBE_PX + HOLE_SEARCH_PX - 1), inside): the pixels down the column
        of each Canny edge row ve, from the placed row (ve + 1); rows off the image bottom
        are clipped and marked outside."""
        rows = np.asarray(ve)[:, None] + 1 + np.arange(SHADOW_PROBE_PX + HOLE_SEARCH_PX - 1)[None, :]
        return np.minimum(rows, h - 1), rows <= h - 1

    @staticmethod
    def first_valid(r):
        """(N,) the first finite value along each row of r (N, L), NaN if none."""
        r = np.asarray(r, np.float64)
        fin = np.isfinite(r)
        if r.shape[1] == 0:
            return np.full(len(r), np.nan)
        out = r[np.arange(len(r)), fin.argmax(1)]
        return np.where(fin.any(1), out, np.nan)

    @classmethod
    def column_tests(cls, below, above):
        """The depth-jump test and the placed range, with holes stepped over (A24 sec. III-D,
        "holes removed from the search"). below: ranges at below_rows(ve); above: ranges at
        far_side_rows(ve + 1) (row ve - 1 - j at column j). The ground before the edge is the
        first stereo at or below SHADOW_PROBE_PX under it, the ground beyond the first at or
        above SHADOW_PROBE_PX over it, each within HOLE_SEARCH_PX; the rim's range is the
        first stereo at or below the placed row. Returns (ok (N,), placed range (N,))."""
        k, n = SHADOW_PROBE_PX, HOLE_SEARCH_PX
        before = cls.first_valid(below[:, k - 1:k - 1 + n])
        beyond = cls.first_valid(above[:, k - 1:k - 1 + n])
        return depth_jump_ok(before, beyond), cls.first_valid(below[:, :n])

    def placed_range(self, v, u, nd):
        """Range of placed rim pixels (v, u) from depth nd: the first stereo at or below
        each within HOLE_SEARCH_PX (column_tests' placed range)."""
        v, u = np.asarray(v), np.asarray(u)
        rows = v[:, None] + np.arange(HOLE_SEARCH_PX)[None, :]
        inside = rows <= nd.shape[0] - 1
        r = np.where(inside, np.asarray(nd, np.float64)[np.minimum(rows, nd.shape[0] - 1), u[:, None]], np.nan)
        return self.first_valid(r)

    @staticmethod
    def far_side_rows(v):
        """(rows (N, FAR_SIDE_PX), inside (N, FAR_SIDE_PX)): the pixels up the column of each
        rim pixel row v that far_side_keep() reads, from 2 rows above the rim; rows off the
        image top are clipped to row 0 and marked outside."""
        rows = np.asarray(v)[:, None] - 1 - np.arange(1, FAR_SIDE_PX + 1)[None, :]
        return np.maximum(rows, 0), rows >= 0

    def far_side_ok(self, v, u, nd):
        """C23 sec. 3A positive-obstacle false positive rejection for rim pixels (v, u) of the
        frame's noisy depth nd: far_side_keep() on the ranges up each pixel's column."""
        v, u = np.asarray(v), np.asarray(u)
        rows, inside = self.far_side_rows(v)
        r = np.where(inside, np.asarray(nd, np.float64)[rows, u[:, None]], np.nan)
        return self.far_side_keep(v, u, self.placed_range(v, u, nd), r)

    def far_side_keep(self, v, u, r0, r_col):
        """C23 sec. 3A: "Detected edge points are kept only if, within the region directly
        beyond the detected edge, there exists a positive slope or if there is not enough
        stereo to accurately compute the slope." Rim pixels (v, u) at range r0; r_col
        (N, FAR_SIDE_PX): ranges at far_side_rows(v) (NaN: no stereo). The region is
        FAR_SIDE_WINDOW_M of ground distance past the rim; the slope is a least-squares
        line of height against ground distance in the level rover frame; fewer than
        FAR_SIDE_MIN_POINTS points there is not enough stereo."""
        c, a = self.cam, self.axes
        v, u = np.asarray(v), np.asarray(u)
        rows, _ = self.far_side_rows(v)
        cols = np.broadcast_to(u[:, None], rows.shape)

        def pts(r, vv, uu):
            U = (uu + 0.5 - c["cx"]) / c["fx"]
            V = (c["cy"] - (vv + 0.5)) / c["fy"]
            z = r * self._cos[vv, uu]
            p = z[..., None] * (a["fwd"] + U[..., None] * a["right"] + V[..., None] * a["up"])
            return np.hypot(p[..., 0] + a["offset"][0], p[..., 1] + a["offset"][1]), p[..., 2]

        s0, _ = pts(np.asarray(r0, np.float64), v, u)
        r = np.asarray(r_col, np.float64)
        fin = np.isfinite(r)
        s, z = pts(np.where(fin, r, 0.0), rows, cols)
        ds = s - s0[:, None]
        win = fin & (ds > FAR_SIDE_WINDOW_M[0]) & (ds < FAR_SIDE_WINDOW_M[1])
        n = win.sum(1)
        w = win.astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            mx = (w * ds).sum(1) / n
            mz = (w * z).sum(1) / n
            sxx = (w * (ds - mx[:, None]) ** 2).sum(1)
            sxz = (w * (ds - mx[:, None]) * (z - mz[:, None])).sum(1)
            slope = sxz / sxx
        judged = (n >= FAR_SIDE_MIN_POINTS) & (sxx > 0) & np.isfinite(s0)
        return ~judged | (slope > FAR_SIDE_MIN_SLOPE)

    @staticmethod
    def placed_row(ve, h):
        """The rim is the lit edge: it is placed one row below the Canny edge, on the
        ground just before the shadow."""
        return np.minimum(ve + 1, h - 1)

    def edge_candidates(self, img):
        """Canny edges, long enough, darker just beyond than before (the lamp-shadow test).
        The image alone: the depth-jump test is applied by detect_contours()."""
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
        ok = (before - beyond >= SHADOW_CONTRAST)
        return v[ok], u[ok]

    def rims_detail(self, img, depth, P=None, pixels=None, nd=None):
        """dict: rims (M, 2) as rims() returns; true (M, 2) the same pixels placed with the
        renderer's depth (no noise; for scoring only); uv (M, 2) their pixels.
        nd: the frame's noisy_depth() (drawn here if not given); detection and placement
        both read it. pixels: detect()'s output for this nd, if already computed."""
        nd = self.noisy_depth(depth) if nd is None else nd
        v, u = self.detect(img, depth, nd) if pixels is None else pixels
        return self.place(v, u, depth[v, u], r_noisy=self.placed_range(v, u, nd))

    def place(self, v, u, r0, P=None, r_noisy=None):
        """Place detected rim pixels (v, u), whose renderer range is r0: range cut, subsample,
        as rims_detail() returns. r_noisy: their noisy ranges from the frame's noisy depth;
        P: noisy points to use instead; neither: noise drawn here."""
        if len(v) == 0:
            return {"rims": np.zeros((0, 2)), "true": np.zeros((0, 2)), "uv": np.zeros((0, 2), int),
                    "idx": np.zeros(0, int)}
        r0 = np.asarray(r0, np.float64)
        if r_noisy is not None:
            pts = self._rover_points(np.asarray(r_noisy, np.float64), v, u)
        elif P is None:
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
        """(rims (M, 2), hazards (K, 2), reasons) for one frame, all from one noisy depth draw."""
        nd = self.noisy_depth(depth)
        P = self.points(depth, nd)
        return self.rims(img, depth, P, nd), *self.hazards(depth, P)


def _level_axes(pitch_deg, camera_height_m):
    """Camera axes of a level body in the rover frame (sim/scene.py level_axes),
    from a camera model alone."""
    p = math.radians(pitch_deg)
    return dict(fwd=np.array([math.cos(p), 0.0, -math.sin(p)]), right=np.array([0.0, -1.0, 0.0]),
                up=np.array([math.sin(p), 0.0, math.cos(p)]), offset=np.zeros(2), level=np.eye(3))


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
    ax = scene.rover_axes(pose)   # the camera tilts with the body (sim/scene.py body_frame)
    fwd_c, right_c, up_c = ax["fwd"], ax["right"], ax["up"]
    x, y, h = pose
    o = scene.origin
    G, R = scene.body_frame(pose)
    cam_z = float(G[2] + cm["camera_height_m"] * R[2, 2])
    ox, oy = ax["offset"]

    def project(wxy):
        wxy = np.asarray(wxy, float).reshape(-1, 2)
        gz = scene.grid.height(wxy[:, 0] - o[0], wxy[:, 1] - o[1]) + 0.05
        dx, dy = wxy[:, 0] - x, wxy[:, 1] - y
        P = np.column_stack([dx * math.cos(h) + dy * math.sin(h) - ox, -dx * math.sin(h) + dy * math.cos(h) - oy,
                             gz - cam_z])
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
