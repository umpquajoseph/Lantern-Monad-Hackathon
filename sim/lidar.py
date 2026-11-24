"""A forward lidar mounted beside the front camera.

It ray-casts the same ground the camera sees (sim/scene.py's surface: the
sim grid plus the display layer's rocks and rim lips around each rover), so
camera and lidar agree. Output each scan, in the rover frame
[x_forward, y_left, z_up] relative to the lidar:

  points  (N, 3) returns, with range noise
  image   (rows, cols) range image for the lidar view (NaN = no return)

hazards() turns the points into the cells the rover must not drive into:
crater interiors (ground below the local surface), rocks (ground above it)
and slopes over the planner's limit. Craters count from the moment the lidar
sees them, so the rover diverts as soon as one comes into view.

Every number below is ASSUMED unless it names a source: no source in
docs/sources/ specifies a lidar for this rover.
"""
from __future__ import annotations

import math
import os

import numpy as np

import cv2

# --- mounting and scan pattern (ASSUMED: a forward-looking solid-state
# lidar, like the wedge scanners used on planetary rover testbeds)
LIDAR_RIGHT_OF_CAMERA_M = 0.25   # beside the camera on the mast head
try:
    from sim.settings import Settings as _Settings
except ImportError:  # run as a script from sim/
    from settings import Settings as _Settings
LIDAR_HEIGHT_M = _Settings().camera_height_m  # on the mast with the camera (sim/settings.py camera_height_m)
H_FOV_DEG = 120.0                # horizontal field of view
H_STEP_DEG = 0.5                 # horizontal resolution
V_ANGLES_DEG = np.linspace(-35.0, -2.0, 40)  # beam elevations, below level
MAX_RANGE_M = 30.0
MIN_RANGE_M = 1.0
RANGE_NOISE_M = 0.02             # 1-sigma range noise
MARCH_STEP_M = 0.25              # ray march step, then refined by bisection
MARCH_BLOCK = 8                  # march steps sampled per height call (speed only; ranges do not depend on it)
GPU_LIDAR = os.environ.get("LANTERN_GPU_LIDAR", "1") != "0"   # march on the GPU when the scene can (same ranges)

# --- hazards (ASSUMED)
HAZ_CELL_M = 0.5                 # hazard grid cell
HAZ_RANGE_M = (2.0, 25.0)        # where the scan is dense enough to judge
HAZ_HALF_WIDTH_M = 15.0
RING_CELLS = 6                   # 3 m: ring radius used to judge a dip (craters 2.3-20 m wide)
DEPRESSION_M = 0.35              # this far below a 3 m ring: inside a crater (94% of such cells
                                 # lie in a mapped crater on shoemaker.npz; 0.15 m gave 36%)
RING_MIN_PAIRS = 2               # complete mirrored ring pairs (cells at +d and -d) to judge a cell:
                                 # two lines through it (assumed; see hazards())
ROCK_STEP_M = 0.15              # this far above its 1.5 m ring: a rock
DETECT_ROCKS = False             # off: at this scan density no setting found real rocks (precision 0 on
                                 # shoemaker.npz), and the tallest display rock stands 0.28 m, under
                                 # the 0.3 m wheel radius in sim/scene.py
SLOPE_LIMIT_DEG = 15.0           # the heading-free limit, sim/rover.py SLOPE_LIMIT_DEG (the lower of
                                 # pitch_limit_deg and roll_limit_deg): a "slope" cell is steeper than
                                 # this; the rover judges it by heading from its gradient (judge())
FOOTPRINT_CELLS = 3              # slope patch, cells a side: 1.5 m, the nearest whole-cell patch to the
                                 # Scout's 1.55 m footprint (sim/footprint.py FOOTPRINT_M, brief p.6)
MIN_PATCH_COVERAGE = 0.5         # GESTALT Table 1 (Goldberg et al. 2002, p.6) min-cell-coverage 0.5: "Min
                                 # fraction of cell that must contain range data to enable surface normal
                                 # (in each of X and Y)". Applied to the slope patch along the points'
                                 # narrowest direction rather than X and Y (assumed reading). Its
                                 # eigen-ratio test (2.0) is not applied: the paper does not say which
                                 # eigenvalues it compares.
MIN_RETURNS = 3
# The lidar's map over a sortie, kept as GESTALT keeps one (Goldberg et al. 2002
# sec. 4 steps 2-3, docs/sources/GESTALT-Goldberg-2002.pdf; HazardMemory below).
MAX_IDLES = 2000                 # GESTALT Table 1 max-idles, "Max number of cycles before zeroing a cell":
                                 # 2000. A cycle read as one scan, one Lantern step (assumed reading)
MEMORY_TILE = 64                 # cells a side of one stored tile (storage only; verdicts do not depend on it)

# --- the lidar panel (display only)
VIEW_SIZE = (800, 280)           # the viewer's panel is 400 x 140 (web/app.css .lidar); drawn at 2x
VIEW_RANGE_M = 25.0              # top of the panel: the far edge of the judged band (HAZ_RANGE_M)


class Lidar:
    NAME = ("lidar: forward scanner beside the camera, 120 x 33 deg, ray-cast on the camera's ground "
            "(sim/lidar.py); hazards = crater interiors and slopes over 15 deg")

    def __init__(self, scene, rng: np.random.Generator):
        self.scene = scene
        self.rng = rng
        self.az = np.radians(np.arange(-H_FOV_DEG / 2, H_FOV_DEG / 2 + 1e-9, H_STEP_DEG))
        self.el = np.radians(V_ANGLES_DEG)
        self.memory = HazardMemory()   # the sortie's map (judge()); one Lidar per sortie

    def origin(self, pose):
        """Lidar position in the scene's local frame, and the body's axes R
        (sim/scene.py body_frame): on the mast head beside the camera, so it
        pitches and rolls with the body."""
        G, R = self.scene.body_frame(pose)
        return G + R @ np.array([0.0, -LIDAR_RIGHT_OF_CAMERA_M, LIDAR_HEIGHT_M]), R

    def scan(self, pose):
        """(points (N, 3) level rover frame [fwd, left, up] from the lidar, range image (rows, cols)).
        Beams are fixed to the body; the points are levelled with its known
        tilt (inclinometer or IMU; assumed exact), so hazards are judged on
        the true ground slope."""
        O, R = self.origin(pose)
        self._O = O
        h = float(pose[2])
        c, s = math.cos(h), math.sin(h)
        self._level = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]]) @ R   # body -> level rover frame
        EL, AZ = np.meshgrid(self.el, self.az, indexing="ij")          # (rows, cols)
        bx, by, bz = np.cos(EL) * np.cos(AZ), np.cos(EL) * np.sin(AZ), np.sin(EL)   # body frame
        dx = R[0, 0] * bx + R[0, 1] * by + R[0, 2] * bz
        dy = R[1, 0] * bx + R[1, 1] * by + R[1, 2] * bz
        dz = R[2, 0] * bx + R[2, 1] * by + R[2, 2] * bz
        from sim.scene import _par   # row-chunked threads; same arithmetic per point
        surf = self.scene.surface
        # March MARCH_BLOCK steps per height call on the rays still live, then
        # bisect every hit ray together. Each sample and bisection step is the
        # same arithmetic as a step-by-step march, so ranges are unchanged.
        ts = np.arange(MIN_RANGE_M + MARCH_STEP_M, MAX_RANGE_M + 1e-9, MARCH_STEP_M)
        fdx, fdy, fdz = dx.ravel(), dy.ravel(), dz.ravel()
        gpu = self.scene.gpu() if GPU_LIDAR else None
        if gpu is not None:   # sim/scene_gpu.py: the same march and bisection, one GPU thread per beam
            rng = gpu.lidar_ranges(O, fdx, fdy, fdz, ts, MIN_RANGE_M).reshape(EL.shape)
            return self._finish(rng, EL, AZ)
        rng = np.full(fdx.shape, np.nan)
        first = np.full(fdx.shape, -1)
        live = np.arange(len(fdx))
        for s0 in range(0, len(ts), MARCH_BLOCK):
            if len(live) == 0:
                break
            tb = ts[s0:s0 + MARCH_BLOCK]
            px = O[0] + fdx[live, None] * tb[None, :]
            py = O[1] + fdy[live, None] * tb[None, :]
            pz = O[2] + fdz[live, None] * tb[None, :]
            below = pz <= _par(surf.height, px, py)
            got = below.any(axis=1)
            first[live[got]] = s0 + np.argmax(below[got], axis=1)   # first step at or under the ground
            live = live[~got]
        hit = np.flatnonzero(first >= 0)
        if len(hit):
            fi = first[hit]
            hi_t = ts[fi]
            lo_t = np.where(fi > 0, ts[np.maximum(fi - 1, 0)], MIN_RANGE_M)
            hdx, hdy, hdz = fdx[hit], fdy[hit], fdz[hit]
            for _ in range(6):   # bisection to ~4 mm
                mid = 0.5 * (lo_t + hi_t)
                under = O[2] + hdz * mid <= surf.height(O[0] + hdx * mid, O[1] + hdy * mid)
                hi_t = np.where(under, mid, hi_t)
                lo_t = np.where(under, lo_t, mid)
            rng[hit] = hi_t
        return self._finish(rng.reshape(EL.shape), EL, AZ)

    def _finish(self, rng, EL, AZ):
        """Range noise and rover-frame points from the noiseless range image."""
        rng = rng + RANGE_NOISE_M * self.rng.standard_normal(rng.shape)
        ok = np.isfinite(rng)
        # Body frame, then the level rover frame: x forward, y left, z up, origin at the lidar.
        fx = rng * np.cos(EL) * np.cos(AZ)
        fy = rng * np.cos(EL) * np.sin(AZ)
        fz = rng * np.sin(EL)
        pts = np.column_stack([fx[ok], fy[ok], fz[ok]])
        L = getattr(self, "_level", None)
        if L is not None:
            pts = pts @ L.T
        return pts, rng

    def hazards(self, pts, grids=False):
        """(centres (K, 2) [fwd, left], kinds list of "crater" | "rock" | "slope"), rover frame.
        grids=True: the judged grids instead (for view()): known, crater,
        rock, slope as (nx, ny) bools over HAZ_RANGE_M x +-HAZ_HALF_WIDTH_M."""
        if len(pts) == 0 and not grids:
            return np.zeros((0, 2)), []
        c = HAZ_CELL_M
        x0, x1 = HAZ_RANGE_M
        sel = (pts[:, 0] >= x0) & (pts[:, 0] < x1) & (np.abs(pts[:, 1]) < HAZ_HALF_WIDTH_M)
        p = pts[sel]
        nx = int(math.ceil((x1 - x0) / c))
        ny = int(math.ceil(2 * HAZ_HALF_WIDTH_M / c))
        ix = np.clip(((p[:, 0] - x0) / c).astype(int), 0, nx - 1)
        iy = np.clip(((p[:, 1] + HAZ_HALF_WIDTH_M) / c).astype(int), 0, ny - 1)
        flat = ix * ny + iy
        base = _cell_moments(p[:, 0] - x0, p[:, 1] + HAZ_HALF_WIDTH_M, p[:, 2], flat, nx, ny, c)
        g = _judge_moments(base, c, getattr(self, "slope_mode", "footprint"))
        known, is_crater, is_rock, is_slope, slope = g["known"], g["crater"], g["rock"], g["slope"], g["tilt"]
        if grids:
            return dict(known=known, crater=is_crater, rock=is_rock, slope=is_slope, tilt=slope)
        hi, hj = np.nonzero(is_crater | is_rock | is_slope)
        centres = np.column_stack([x0 + (hi + 0.5) * c, -HAZ_HALF_WIDTH_M + (hj + 0.5) * c])
        kinds = ["crater" if is_crater[i, j] else "rock" if is_rock[i, j] else "slope" for i, j in zip(hi, hj)]
        return centres, kinds

    def points_from_image(self, rng_img):
        """Level rover-frame points [fwd, left, up] from a range image, as scan()
        makes them: levelled with the tilt of the last scan (the one that made
        the image), so the panel judges the same hazards the rover did."""
        return self._image_points(rng_img)[np.isfinite(rng_img)]

    def _image_points(self, rng_img):
        """(rows, cols, 3) level rover-frame point of every beam (NaN: no return)."""
        EL, AZ = np.meshgrid(self.el, self.az, indexing="ij")
        p = np.stack([rng_img * np.cos(EL) * np.cos(AZ), rng_img * np.cos(EL) * np.sin(AZ),
                      rng_img * np.sin(EL)], axis=-1)
        L = getattr(self, "_level", None)
        return p if L is None else p @ L.T

    def view(self, rng_img, size=VIEW_SIZE):
        """The lidar panel (RGB uint8): what this scan told the rover, seen from
        above, forward up, rover at the bottom centre. Grey: ground the lidar
        judged safe. Amber: crater floor (the planner avoids it). Red: steeper
        than 15 deg (no-go). Black: no return, or too few returns to judge
        (hidden behind a rise, or outside the scan). Rings every 5 m."""
        W, H = size
        img = np.zeros((H, W, 3), np.uint8)
        px_m = (H - 14) / VIEW_RANGE_M                  # pixels per metre
        ox, oy = W // 2, H - 8                          # rover position in the image
        pts = self.points_from_image(rng_img)
        g = self.hazards(pts, grids=True)
        c = HAZ_CELL_M
        x0 = HAZ_RANGE_M[0]
        # Every return, faintly: shows how far the scan reached past the judged band.
        if len(pts):
            u = np.rint(ox - pts[:, 1] * px_m).astype(int)
            v = np.rint(oy - pts[:, 0] * px_m).astype(int)
            ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
            img[v[ok], u[ok]] = (55, 55, 60)
        colours = [(g["known"], (95, 98, 104)), (g["crater"], (240, 160, 40)),
                   (g["rock"], (240, 160, 40)), (g["slope"], (230, 50, 40))]
        for mask, col in colours:
            for i, j in zip(*np.nonzero(mask)):
                fwd0, left0 = x0 + i * c, -HAZ_HALF_WIDTH_M + j * c
                u0 = int(round(ox - (left0 + c) * px_m))
                u1 = int(round(ox - left0 * px_m))
                v0 = int(round(oy - (fwd0 + c) * px_m))
                v1 = int(round(oy - fwd0 * px_m))
                img[max(v0, 0):max(v1, 0), max(u0, 0):max(u1, 0)] = col
        ring = (150, 150, 150)
        half_fov = math.radians(H_FOV_DEG / 2)
        for rr in range(5, int(VIEW_RANGE_M) + 1, 5):
            cv2.ellipse(img, (ox, oy), (int(rr * px_m), int(rr * px_m)), 0, -90 - H_FOV_DEG / 2,
                        -90 + H_FOV_DEG / 2, ring, 1, cv2.LINE_AA)
            tx, ty = ox + int(rr * px_m * math.sin(half_fov)) + 3, oy - int(rr * px_m * math.cos(half_fov))
            cv2.putText(img, f"{rr} m", (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, ring, 1, cv2.LINE_AA)
        for sgn in (-1, 1):
            e = (ox + int(sgn * VIEW_RANGE_M * px_m * math.sin(half_fov)), oy - int(VIEW_RANGE_M * px_m * math.cos(half_fov)))
            cv2.line(img, (ox, oy), e, ring, 1, cv2.LINE_AA)
        tri = np.array([[ox, oy - 7], [ox - 5, oy + 5], [ox + 5, oy + 5]], np.int32)
        cv2.fillPoly(img, [tri], (255, 255, 255))
        y = 18
        for label, col in (("no-go: steeper than 15 deg", (230, 50, 40)), ("crater floor: avoided", (240, 160, 40)),
                           ("judged safe", (95, 98, 104))):
            cv2.rectangle(img, (6, y - 11), (17, y), col, -1)
            cv2.putText(img, label, (22, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
            y += 20
        return img

    def view_fp(self, rng_img, size=VIEW_SIZE):
        """The lidar panel's first-person view (RGB uint8): the range image as
        the lidar sees it, bearing across (left of the rover on the left),
        beam elevation up. Near returns bright, far dark (key on the right);
        returns the scan judged hazards in the top-down map's red (no-go,
        steeper than 15 deg) and amber (crater floor); black: no return."""
        W, H = size
        img = np.zeros((H, W, 3), np.uint8)
        L, R, T, B = 74, 112, 8, 50                          # margins: labels left, key right, ticks below
        pw, ph = W - L - R, H - T - B
        # Range shading, near bright: rows top = highest beam, columns left = +bearing (left).
        r = rng_img[::-1, ::-1]
        ok = np.isfinite(r)
        shade = np.where(ok, 235 - 190 * np.clip(r / MAX_RANGE_M, 0, 1), 0).astype(np.uint8)
        rgb = np.stack([shade * 0.80, shade * 0.88, shade], axis=-1).astype(np.uint8)
        # Hazard class of every return, from the same grids as the top-down map.
        g = self.hazards(self.points_from_image(rng_img), grids=True)
        P = self._image_points(rng_img)
        fx, fy = P[..., 0], P[..., 1]
        nx, ny = g["known"].shape
        x0, _ = HAZ_RANGE_M
        with np.errstate(invalid="ignore"):
            ix = np.floor((fx - x0) / HAZ_CELL_M)
            iy = np.floor((fy + HAZ_HALF_WIDTH_M) / HAZ_CELL_M)
            inb = np.isfinite(rng_img) & (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        ixi, iyi = np.where(inb, ix, 0).astype(int), np.where(inb, iy, 0).astype(int)
        crat = (inb & (g["crater"][ixi, iyi] | g["rock"][ixi, iyi]))[::-1, ::-1]
        steep = (inb & g["slope"][ixi, iyi])[::-1, ::-1]
        rgb[crat] = (240, 160, 40)
        rgb[steep] = (230, 50, 40)
        img[T:T + ph, L:L + pw] = cv2.resize(rgb, (pw, ph), interpolation=cv2.INTER_NEAREST)
        # Shown about 340 px wide (0.43 x): few labels, drawn large; the
        # colours are explained by the caption under the panel.
        grey, white, f, fs = (150, 150, 150), (235, 235, 235), cv2.FONT_HERSHEY_SIMPLEX, 1.05
        cv2.rectangle(img, (L - 1, T - 1), (L + pw, T + ph), grey, 2)
        # Bearing ticks every 30 deg, labelled at the edges and straight ahead.
        for b in (60, 30, 0, -30, -60):
            u = L + int(round((H_FOV_DEG / 2 - b) / H_FOV_DEG * pw))
            cv2.line(img, (u, T + ph), (u, T + ph + 8), grey, 2)
            if b in (60, 0, -60):
                lab = "ahead" if b == 0 else f"{abs(b)}{'L' if b > 0 else 'R'}"
                (tw, _), _ = cv2.getTextSize(lab, f, fs, 2)
                ux = min(max(u - tw // 2, 0), W - tw)
                cv2.putText(img, lab, (ux, T + ph + 40), f, fs, white, 2, cv2.LINE_AA)
        # Elevation: the top and bottom beams.
        e_hi, e_lo = float(V_ANGLES_DEG.max()), float(V_ANGLES_DEG.min())
        for e, v in ((e_hi, T + 22), (e_lo, T + ph - 4)):
            cv2.putText(img, f"{e:.0f}", (4, v), f, fs, white, 2, cv2.LINE_AA)
        # Range key, near bright at the top.
        kx, ky0, ky1 = W - R + 12, T + 32, T + ph - 34
        for v in range(ky0, ky1):
            t = (v - ky0) / max(ky1 - ky0 - 1, 1)
            sh = int(235 - 190 * t)
            img[v, kx:kx + 22] = (int(sh * 0.80), int(sh * 0.88), sh)
        cv2.putText(img, "near", (kx - 8, ky0 - 8), f, fs, white, 2, cv2.LINE_AA)
        cv2.putText(img, f"{MAX_RANGE_M:.0f}m", (kx - 8, ky1 + 30), f, fs, white, 2, cv2.LINE_AA)
        return img

    def judge(self, pts, pose, widen_m=0.0):
        """GESTALT's map update for one scan (Goldberg et al. 2002 sec. 4; HazardMemory):
        merge the scan's points in the judged band (as hazards() takes them) into the
        sortie's world-frame cells, zero cells idle MAX_IDLES scans, and judge afresh
        every cell the change can reach. pts: the scan's level-frame points, from
        scan(pose) just before. widen_m: the rover's hazard margin (SlopeMap.widen_m).

        Returns (centres (K, 2) world xy, kinds, clear, grads (K, 2)): grads is each
        slope cell's plane gradient, world frame (NaN for other kinds), for the
        rover's limits by heading. clear lists the changed regions
        widened past widen_m as world boxes (xmin, xmax, ymin, ymax); centres and kinds
        are every hazard now judged within twice that, so marking them after clearing
        the boxes (Rover.add_hazards) leaves the rover's grids as the map judges them."""
        from sim.vision import rover_to_world
        mem = self.memory
        mem.cycle += 1
        x0, x1 = HAZ_RANGE_M
        sel = (pts[:, 0] >= x0) & (pts[:, 0] < x1) & (np.abs(pts[:, 1]) < HAZ_HALF_WIDTH_M)
        p = pts[sel]
        boxes = []
        if len(p):
            xy = rover_to_world(self.to_camera_frame(p[:, :2]), pose)
            boxes.append(mem.add(np.column_stack([xy, self._O[2] + p[:, 2]])))
        mem.mark_view(rover_to_world(self.to_camera_frame(np.zeros((1, 2))), pose)[0], float(pose[2]))
        boxes += mem.expire()
        # A cell's verdict reads the cells up to RING_CELLS away (its crater ring), so
        # every cell that near a changed one is judged again.
        m = max(RING_CELLS, FOOTPRINT_CELLS // 2)
        boxes = [(i0 - m, i1 + m, j0 - m, j1 + m) for i0, i1, j0, j1 in boxes]
        mode = getattr(self, "slope_mode", "footprint")
        for bx in boxes:
            mem.judge(*bx, slope_mode=mode)
        c = mem.c
        w = int(math.ceil(widen_m / c)) + 1            # clear margin, cells: past the rover's marks
        clear, cen, kinds, grads = [], [], [], []
        for i0, i1, j0, j1 in boxes:
            clear.append(((i0 - w) * c, (i1 + w) * c, (j0 - w) * c, (j1 + w) * c))
            k = 2 * w + 1                              # hazards whose marks reach the cleared box
            hz, kd, gd = mem.hazards_in(i0 - k, i1 + k, j0 - k, j1 + k)
            cen.append(hz)
            kinds += kd
            grads.append(gd)
        cen = np.concatenate(cen) if cen else np.zeros((0, 2))
        grads = np.concatenate(grads) if grads else np.zeros((0, 2))
        return cen, kinds, clear, grads

    def to_camera_frame(self, centres):
        """Hazard centres from the lidar to the rover's reference point (the pose),
        level rover frame, for the last scan: what rover_to_world() takes. On
        a level body that is the camera's origin, LIDAR_RIGHT_OF_CAMERA_M to
        the lidar's left."""
        c = np.asarray(centres, float).reshape(-1, 2).copy()
        L = getattr(self, "_level", None)
        off = (np.array([0.0, -LIDAR_RIGHT_OF_CAMERA_M, LIDAR_HEIGHT_M]) if L is None
               else L @ np.array([0.0, -LIDAR_RIGHT_OF_CAMERA_M, LIDAR_HEIGHT_M]))
        c += off[:2]
        return c


MOMENT_KEYS = ("n", "u", "v", "z", "uu", "uv", "vv", "uz", "vz", "zz")


def _cell_moments(x, y, z, flat, nx, ny, cell):
    """GESTALT sec. 4 step 2 (Goldberg et al. 2002, p.6): "First and second order
    moment statistics are collected about all the range points in a given cell ...
    only 10 floats comprising these statistics are stored". Per cell of an (nx, ny)
    grid of side `cell`: n and the sums of u, v, z, uu, uv, vv, uz, vz, zz, with
    (u, v) a point's offset from its own cell's centre (keeps the sums well scaled),
    and zmax for the rock test. x, y: the points from the grid's corner; flat: each
    point's cell index (i * ny + j)."""
    ci, cj = flat // ny, flat % ny
    u, v = x - (ci + 0.5) * cell, y - (cj + 0.5) * cell
    m = nx * ny
    w = dict(n=np.ones_like(z), u=u, v=v, z=z, uu=u * u, uv=u * v, vv=v * v, uz=u * z, vz=v * z, zz=z * z)
    base = {k: np.bincount(flat, weights=w[k], minlength=m).reshape(nx, ny) for k in MOMENT_KEYS}
    zmax = np.full(m, -np.inf)
    np.maximum.at(zmax, flat, z)
    base["zmax"] = zmax.reshape(nx, ny)
    return base


def _judge_moments(base, c, slope_mode="footprint"):
    """GESTALT sec. 4 step 3 on cells' moment statistics (_cell_moments): each cell
    judged crater interior, rock, or slope over the limit. Returns dict known, crater,
    rock, slope (bool grids) and tilt (deg). One scan (Lidar.hazards) and the sortie's
    merged map (HazardMemory) are judged by this same code."""
    n = base["n"]
    nx, ny = n.shape
    known = n >= MIN_RETURNS
    with np.errstate(all="ignore"):
        zmean = np.where(known, base["z"] / np.maximum(n, 1), np.nan)
        zmax = np.where(known, base["zmax"], np.nan)

    # Compare each cell with the mean of a ring of cells around it, taken
    # over mirrored pairs only (the cells at +d and -d, both judged): on
    # any tilted plane each pair averages to the cell's own height, so
    # plain slopes do not read as rocks or dips. A ring cut off on one
    # side (the near edge of the scan, its sides, a shadow) would
    # otherwise lean uphill or downhill: on a 10 deg ramp the half ring
    # at the near edge sat 2 m uphill and read 0.35-0.43 m "deep"
    # (20 Nov 2026). Pairs carry no slope bias however few there are, so
    # a ring is judged from RING_MIN_PAIRS complete pairs: the far side
    # of a crater's ring hides behind its far rim, leaving its floor 4-13
    # of 24 pairs, and the old rule (half the ring) is not needed for bias.
    def ring_mean(rad):
        pairs = [(di, dj) for di in range(-rad, rad + 1) for dj in range(-rad, rad + 1)
                 if max(abs(di), abs(dj)) == rad and (di, dj) > (0, 0)]   # one of each mirrored pair
        pad = np.pad(zmean, rad, constant_values=np.nan)
        a = np.stack([pad[rad + di:rad + di + nx, rad + dj:rad + dj + ny] for di, dj in pairs])
        b = np.stack([pad[rad - di:rad - di + nx, rad - dj:rad - dj + ny] for di, dj in pairs])
        both = np.isfinite(a) & np.isfinite(b)
        k = both.sum(axis=0)
        m = np.where(both, 0.5 * (a + b), 0.0).sum(axis=0) / np.maximum(k, 1)
        return np.where(k >= RING_MIN_PAIRS, m, np.nan)

    with np.errstate(all="ignore"):
        near = ring_mean(1)                     # 1.5 m: rocks
        far = ring_mean(RING_CELLS)             # crater-sized ring
        rock = zmax - near
        depr = far - zmean
        if slope_mode == "footprint":
            # The Scout's footprint tilt (docs/terrain/position.md row 9), as
            # GESTALT judges it from range data (Goldberg et al. 2002, sec. 4
            # step 3, p.6; docs/sources/GESTALT-Goldberg-2002.pdf): merge the
            # points' moment statistics over a rover-sized patch of cells,
            # fit the best plane, and judge it "as long as there is enough
            # data ... at least half the cells have more than 1 point". The
            # patch is FOOTPRINT_CELLS square, the nearest whole-cell patch
            # to the 1.55 m footprint. Before 20 Nov 2026 the lidar fitted
            # the plane to cell means with sim/footprint.tilt_deg, which
            # judged nothing where any cell of its 3.5 m window lacked
            # returns: it flagged 18-28% of the truly steep ground it had
            # returns on in tilt-check, and showed the rest as safe.
            slope, gx, gy = _patch_tilt_deg(base, c, grad=True)
        else:
            gx = np.full((nx, ny), np.nan)
            gy = np.full((nx, ny), np.nan)
            b = 2  # +-1 m: a 2 m central difference (docs/handoffs.md step 2)
            gx[b:-b, :] = (zmean[2 * b:, :] - zmean[:-2 * b, :]) / (2 * b * c)
            gy[:, b:-b] = (zmean[:, 2 * b:] - zmean[:, :-2 * b]) / (2 * b * c)
            slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    is_crater = known & (depr > DEPRESSION_M)
    is_rock = known & ~is_crater & (rock > ROCK_STEP_M) & DETECT_ROCKS
    is_slope = known & ~is_crater & ~is_rock & (slope > SLOPE_LIMIT_DEG)
    # gx, gy: the judged plane's gradient along the grid's two axes (axis 0,
    # axis 1), NaN where unjudged. HazardMemory's axes are world x and y.
    return dict(known=known, crater=is_crater, rock=is_rock, slope=is_slope, tilt=slope, gx=gx, gy=gy)


def _patch_tilt_deg(base, cell, grad=False):
    """(nx, ny) tilt in degrees of the least-squares plane through every point
    in the FOOTPRINT_CELLS-square patch of cells centred on each cell, from the
    cells' moments (_cell_moments); NaN where under half the patch's cells hold
    more than 1 point (GESTALT sec. 4 step 3). grad=True: (tilt, gx, gy), the
    plane's gradient along axis 0 and axis 1, NaN where the tilt is."""
    from scipy.ndimage import correlate
    nx, ny = base["n"].shape
    k = np.ones((FOOTPRINT_CELLS, FOOTPRINT_CELLS))
    # Shift each cell's moments to the patch centre: a point in the cell at
    # (di, dj) from the centre sits at (u + di c, v + dj c). Sum per offset.
    S = {key: np.zeros((nx, ny)) for key in ("n", "x", "y", "z", "xx", "xy", "yy", "xz", "yz")}
    r = FOOTPRINT_CELLS // 2
    padded = {key: np.pad(base[key], r) for key in ("n", "u", "v", "z", "uu", "uv", "vv", "uz", "vz")}
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            g = {key: a[r + di:r + di + nx, r + dj:r + dj + ny] for key, a in padded.items()}
            ox, oy = di * cell, dj * cell
            S["n"] += g["n"]
            S["x"] += g["u"] + ox * g["n"]
            S["y"] += g["v"] + oy * g["n"]
            S["z"] += g["z"]
            S["xx"] += g["uu"] + 2 * ox * g["u"] + ox * ox * g["n"]
            S["yy"] += g["vv"] + 2 * oy * g["v"] + oy * oy * g["n"]
            S["xy"] += g["uv"] + ox * g["v"] + oy * g["u"] + ox * oy * g["n"]
            S["xz"] += g["uz"] + ox * g["z"]
            S["yz"] += g["vz"] + oy * g["z"]
    n = np.maximum(S["n"], 1)
    mx, my, mz = S["x"] / n, S["y"] / n, S["z"] / n
    cxx, cyy, cxy = S["xx"] / n - mx * mx, S["yy"] / n - my * my, S["xy"] / n - mx * my
    cxz, cyz = S["xz"] / n - mx * mz, S["yz"] / n - my * mz
    det = cxx * cyy - cxy * cxy
    with np.errstate(all="ignore"):
        gx = (cyy * cxz - cxy * cyz) / det
        gy = (cxx * cyz - cxy * cxz) / det
        tilt = np.degrees(np.arctan(np.hypot(gx, gy)))
        # Coverage: the points' spread across their narrowest direction, against
        # that of points evenly covering MIN_PATCH_COVERAGE of the patch's side.
        # Points along one beam line (2 cm across, 0.57 m along on a plain
        # ramp) leave the plane free about that line: before this test they
        # read an 11.6 deg ramp as 15-19 deg.
        minor = 0.5 * (cxx + cyy) - np.sqrt(0.25 * (cxx - cyy) ** 2 + cxy * cxy)
        spread = np.sqrt(np.maximum(minor, 0.0))
    side = FOOTPRINT_CELLS * cell
    full = correlate((base["n"] > 1).astype(float), k, mode="constant", cval=0.0)
    ok = (full > k.size / 2) & (det > 0) & (spread >= MIN_PATCH_COVERAGE * side / math.sqrt(12.0))
    if grad:
        return np.where(ok, tilt, np.nan), np.where(ok, gx, np.nan), np.where(ok, gy, np.nan)
    return np.where(ok, tilt, np.nan)


class HazardMemory:
    """The lidar's map over one sortie, kept as GESTALT keeps its map (Goldberg et
    al. 2002 sec. 4, docs/sources/GESTALT-Goldberg-2002.pdf). How each part is read:

    - Step 2, "moment statistics are collected about all the range points in a
      given cell": world-frame cells of HAZ_CELL_M hold the 10 sums of every point
      that has fallen in them, from every scan; a new scan's points are added to
      the sums (add). Until 20 Nov 2026 each scan was judged alone and its hazards
      ORed into the rover's grids for good, so one scan's misjudgement of a
      near-limit ramp, seen from far off, closed it for the rest of the sortie.
    - Step 3, traversability "found by merging the moment statistics from a
      rover-sized patch of surrounding cells and finding the best-fit plane": after
      each scan every cell whose verdict the new points can change (within
      RING_CELLS of a touched cell) is judged afresh from the merged sums (judge),
      by the code that judges a single scan (_judge_moments), so a cell can turn
      safe as well as steep.
    - Table 1 max-idles, 2000 "Max number of cycles before zeroing a cell": a cell
      no scan has touched for MAX_IDLES scans is zeroed (expire).
    - Table 1 grid-dist-timeout, 3.0 m "Throw out map data this far from rover": not
      applied. GESTALT keeps a 10 m map in a frame that drifts with its pose
      estimate, so old data far away is unreliable; Lantern places the points by
      the true pose (relative geometry, as add_hazards always has), so its map does
      not drift. Applied literally it would leave no cells beyond 3 m, inside the
      lidar's 2-25 m judged band.

    Cells are indexed (i, j) = floor(x / c), floor(y / c) in the world frame; heights
    are kept relative to the first scan's median (z0) to keep the sums well scaled."""

    def __init__(self, cell=HAZ_CELL_M, tile=MEMORY_TILE):
        self.c, self.T = float(cell), int(tile)
        self.tiles = {}
        self.cycle = 0
        self.z0 = None
        self.changes = []   # (cells (N, 2) int, verdicts (N,)) since take_changes(): the replay's record

    def _tile(self, key):
        t = self.tiles.get(key)
        if t is None:
            T = self.T
            t = dict(m=np.zeros((len(MOMENT_KEYS), T, T), np.float32), zmax=np.full((T, T), -np.inf, np.float32),
                     last=np.zeros((T, T), np.int64), verdict=np.zeros((T, T), np.int8),
                     g=np.full((2, T, T), np.nan, np.float32),   # slope cells' plane gradient (gx, gy), world
                     seen=np.full((T, T), -MAX_IDLES, np.int64),   # last scan with the cell in the judged band
                     oldest=math.inf, touched=-MAX_IDLES, seen_max=-MAX_IDLES)   # scalars: expire() skips quiet tiles
            self.tiles[key] = t
        return t

    def _parts(self, i0, i1, j0, j1):
        """(tile key, tile slice, box slice) for each tile overlapping [i0, i1) x [j0, j1)."""
        T = self.T
        for ti in range(i0 // T, (i1 - 1) // T + 1):
            for tj in range(j0 // T, (j1 - 1) // T + 1):
                a0, a1 = max(i0, ti * T), min(i1, (ti + 1) * T)
                b0, b1 = max(j0, tj * T), min(j1, (tj + 1) * T)
                yield ((ti, tj), (slice(a0 - ti * T, a1 - ti * T), slice(b0 - tj * T, b1 - tj * T)),
                       (slice(a0 - i0, a1 - i0), slice(b0 - j0, b1 - j0)))

    def add(self, xyz):
        """Step 2: merge world points (N, 3) into their cells' sums. Returns the
        touched cells' box (i0, i1, j0, j1), end-exclusive."""
        p = np.asarray(xyz, float).reshape(-1, 3)
        if self.z0 is None:
            self.z0 = float(np.median(p[:, 2]))
        c, T = self.c, self.T
        ci = np.floor(p[:, 0] / c).astype(np.int64)
        cj = np.floor(p[:, 1] / c).astype(np.int64)
        u, v, z = p[:, 0] - (ci + 0.5) * c, p[:, 1] - (cj + 0.5) * c, p[:, 2] - self.z0
        w = dict(n=np.ones_like(z), u=u, v=v, z=z, uu=u * u, uv=u * v, vv=v * v, uz=u * z, vz=v * z, zz=z * z)
        ti, tj = np.floor_divide(ci, T), np.floor_divide(cj, T)
        loc = (ci - ti * T) * T + (cj - tj * T)
        keys, inv = np.unique(np.column_stack([ti, tj]), axis=0, return_inverse=True)
        inv = np.asarray(inv).reshape(-1)
        for q, key in enumerate(keys):
            sel = inv == q
            t = self._tile((int(key[0]), int(key[1])))
            idx = loc[sel]
            for k, name in enumerate(MOMENT_KEYS):
                t["m"][k] += np.bincount(idx, weights=w[name][sel], minlength=T * T).reshape(T, T).astype(np.float32)
            np.maximum.at(t["zmax"].reshape(-1), idx, z[sel].astype(np.float32))
            t["last"].reshape(-1)[idx] = self.cycle
            t["oldest"] = min(t["oldest"], self.cycle)
            t["touched"] = self.cycle
        return int(ci.min()), int(ci.max()) + 1, int(cj.min()), int(cj.max()) + 1

    def mark_view(self, lidar_xy, heading):
        """Note the cells inside this scan's judged band (HAZ_RANGE_M x
        +-HAZ_HALF_WIDTH_M, within the H_FOV_DEG field) from the lidar at
        lidar_xy facing heading, returns or not. Only why_missed() reads it."""
        c, (lx, ly) = self.c, lidar_xy
        ch, sh = math.cos(heading), math.sin(heading)
        corners = [(a, b) for a in HAZ_RANGE_M for b in (-HAZ_HALF_WIDTH_M, HAZ_HALF_WIDTH_M)]
        wx = [lx + ch * a - sh * b for a, b in corners]
        wy = [ly + sh * a + ch * b for a, b in corners]
        i0, i1 = int(math.floor(min(wx) / c)), int(math.floor(max(wx) / c)) + 1
        j0, j1 = int(math.floor(min(wy) / c)), int(math.floor(max(wy) / c)) + 1
        X = (np.arange(i0, i1)[:, None] + 0.5) * c - lx
        Y = (np.arange(j0, j1)[None, :] + 0.5) * c - ly
        fx, fy = ch * X + sh * Y, -sh * X + ch * Y
        inb = ((fx >= HAZ_RANGE_M[0]) & (fx < HAZ_RANGE_M[1]) & (np.abs(fy) < HAZ_HALF_WIDTH_M)
               & (np.abs(np.arctan2(fy, fx)) <= math.radians(H_FOV_DEG / 2)))
        for key, src, dst in self._parts(i0, i1, j0, j1):
            part = inb[dst]
            if not part.any():
                continue
            t = self._tile(key)
            t["seen"][src][part] = self.cycle
            t["seen_max"] = self.cycle

    def why_missed(self, x, y, slope_mode="footprint"):
        """Why the map holds no hazard at world (x, y), for a step onto ground
        truly over the limit: "never_in_band" (no scan had it in the judged
        band within MAX_IDLES: under 2 m, beside or behind the lidar),
        "shadow" (in the band but under MIN_RETURNS returns: hidden behind
        terrain), "too_sparse" (returns, but too few or too narrow a spread to
        fit the footprint plane), "under_read" (judged, tilt at or under the
        limit), or "judged" (the map calls it a hazard; the rover's grids
        lag)."""
        c, m = self.c, RING_CELLS
        i, j = int(math.floor(x / c)), int(math.floor(y / c))
        base, verdict = self.window(i - m, i + m + 1, j - m, j + m + 1)
        if verdict[m, m]:
            return "judged"
        t = self.tiles.get((i // self.T, j // self.T))
        seen = t is not None and self.cycle - int(t["seen"][i % self.T, j % self.T]) < MAX_IDLES
        if base["n"][m, m] < MIN_RETURNS:
            return "shadow" if seen else "never_in_band"
        g = _judge_moments(base, c, slope_mode)
        return "too_sparse" if not np.isfinite(g["tilt"][m, m]) else "under_read"

    def expire(self):
        """Table 1 max-idles: zero every cell no scan has touched for MAX_IDLES scans,
        and drop tiles left with nothing in them. Returns the boxes of the tiles that
        lost cells. A tile whose oldest held cell is younger than MAX_IDLES is skipped
        without looking at its cells (speed only)."""
        out, gone, T, now = [], [], self.T, self.cycle
        for (ti, tj), t in self.tiles.items():
            if now - t["oldest"] >= MAX_IDLES:
                held = t["m"][0] > 0
                old = held & (now - t["last"] >= MAX_IDLES)
                if old.any():
                    t["m"][:, old] = 0.0
                    t["zmax"][old] = -np.inf
                    out.append((ti * T, (ti + 1) * T, tj * T, (tj + 1) * T))
                live = held & ~old
                t["oldest"] = int(t["last"][live].min()) if live.any() else math.inf
            if (t["oldest"] == math.inf and now - t["touched"] >= MAX_IDLES and now - t["seen_max"] >= MAX_IDLES
                    and not t["verdict"].any()):
                gone.append((ti, tj))
        for key in gone:
            del self.tiles[key]
        return out

    def window(self, i0, i1, j0, j1):
        """The sums over cell box [i0, i1) x [j0, j1) as _cell_moments grids (float64),
        and the stored verdicts."""
        nx, ny = i1 - i0, j1 - j0
        base = {k: np.zeros((nx, ny)) for k in MOMENT_KEYS}
        base["zmax"] = np.full((nx, ny), -np.inf)
        verdict = np.zeros((nx, ny), np.int8)
        for key, src, dst in self._parts(i0, i1, j0, j1):
            t = self.tiles.get(key)
            if t is None:
                continue
            for q, k in enumerate(MOMENT_KEYS):
                base[k][dst] = t["m"][q][src]
            base["zmax"][dst] = t["zmax"][src]
            verdict[dst] = t["verdict"][src]
        return base, verdict

    def judge(self, i0, i1, j0, j1, slope_mode="footprint"):
        """Step 3 for every cell in box [i0, i1) x [j0, j1), from the merged sums of the
        box and the RING_CELLS around it (what a verdict reads); stores the verdicts
        (0 none, 1 crater, 2 rock, 3 slope)."""
        m = RING_CELLS
        base, old = self.window(i0 - m, i1 + m, j0 - m, j1 + m)
        g = _judge_moments(base, self.c, slope_mode)
        code = np.select([g["crater"], g["rock"], g["slope"]], [1, 2, 3], 0)[m:m + i1 - i0, m:m + j1 - j0]
        code = code.astype(np.int8)
        inner = (slice(m, m + i1 - i0), slice(m, m + j1 - j0))
        grad = np.stack([np.where(code == 3, g["gx"][inner], np.nan), np.where(code == 3, g["gy"][inner], np.nan)])
        ci, cj = np.nonzero(code != old[m:m + i1 - i0, m:m + j1 - j0])
        if len(ci):
            self.changes.append((np.column_stack([i0 + ci, j0 + cj]), code[ci, cj]))
        for key, src, dst in self._parts(i0, i1, j0, j1):
            t = self.tiles.get(key)
            if t is None:
                if not code[dst].any():
                    continue
                t = self._tile(key)
            t["verdict"][src] = code[dst]
            t["g"][(slice(None),) + src] = grad[(slice(None),) + dst]

    def take_changes(self):
        """(cells (N, 2) int32 [i, j], verdicts (N,) uint8) whose verdict changed since the
        last call, in order (a later entry for a cell wins). Replaying them from an empty
        map rebuilds the map's verdicts (sim/campaign_camera.py steps.npz map_*)."""
        if not self.changes:
            return np.zeros((0, 2), np.int32), np.zeros(0, np.uint8)
        ij = np.concatenate([a for a, _ in self.changes]).astype(np.int32)
        v = np.concatenate([b for _, b in self.changes]).astype(np.uint8)
        self.changes = []
        return ij, v

    def hazards_in(self, i0, i1, j0, j1):
        """(centres (K, 2) world xy, kinds, grads (K, 2)) of the cells judged hazards in
        box [i0, i1) x [j0, j1); grads: a slope cell's plane gradient (gx, gy), world
        frame, NaN for the other kinds."""
        _, verdict = self.window(i0, i1, j0, j1)
        grad = np.full((2, i1 - i0, j1 - j0), np.nan, np.float32)
        for key, src, dst in self._parts(i0, i1, j0, j1):
            t = self.tiles.get(key)
            if t is not None:
                grad[(slice(None),) + dst] = t["g"][(slice(None),) + src]
        hi, hj = np.nonzero(verdict)
        names = {1: "crater", 2: "rock", 3: "slope"}
        centres = np.column_stack([(i0 + hi + 0.5) * self.c, (j0 + hj + 0.5) * self.c])
        return centres, [names[int(v)] for v in verdict[hi, hj]], grad[:, hi, hj].T.astype(np.float64)


def fp_name(name):
    """lidar/A_0028.jpg -> lidar/A_0028_fp.jpg: the first-person image of the same scan."""
    return name[:-4] + "_fp.jpg" if name.endswith(".jpg") else name + "_fp"


def regenerate(run_dir, display="detail"):
    """Redraw lidar/<agency>_<step>.jpg for a finished run from its log, no
    camera render: each logged lidar frame is scanned again at the step's
    true pose (fresh range noise) and drawn with view()."""
    import json
    from pathlib import Path
    from sim.scene import Scene
    from sim.terrain import TERRAIN_DIR, load_terrain, default_path
    run_dir = Path(run_dir)
    log = json.loads((run_dir / "log.json").read_text())
    h, cr, x0, y0, cell = load_terrain(default_path())
    scene = Scene(h, cr, x0, y0, cell, display=display, display_dir=TERRAIN_DIR / "display")
    lid = Lidar(scene, np.random.default_rng(0))
    n = 0
    for k, s in enumerate(log["steps"]):
        for ag, r in s["rovers"].items():
            scene.set_rover(ag, r["true_pose"])
        for ag, r in s["rovers"].items():
            name = r.get("lidar_frame")
            if not name or not name.endswith(f"_{k:04d}.jpg"):
                continue
            _, rimg = lid.scan(r["true_pose"])
            cv2.imwrite(str(run_dir / name), lid.view(rimg)[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 85])
            cv2.imwrite(str(run_dir / fp_name(name)), lid.view_fp(rimg)[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 85])
            n += 1
    return n


if __name__ == "__main__":
    import sys
    print(f"{regenerate(*sys.argv[1:])} lidar frames redrawn")
