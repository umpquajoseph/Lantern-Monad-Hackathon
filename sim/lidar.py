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

import numpy as np

import cv2

# --- mounting and scan pattern (ASSUMED: a forward-looking solid-state
# lidar, like the wedge scanners used on planetary rover testbeds)
LIDAR_RIGHT_OF_CAMERA_M = 0.25   # beside the camera on the mast head
LIDAR_HEIGHT_M = 2.5             # same height as the camera (sim/scene.py CAMERA_HEIGHT_M)
H_FOV_DEG = 120.0                # horizontal field of view
H_STEP_DEG = 0.5                 # horizontal resolution
V_ANGLES_DEG = np.linspace(-35.0, -2.0, 40)  # beam elevations, below level
MAX_RANGE_M = 30.0
MIN_RANGE_M = 1.0
RANGE_NOISE_M = 0.02             # 1-sigma range noise
MARCH_STEP_M = 0.25              # ray march step, then refined by bisection

# --- hazards (ASSUMED)
HAZ_CELL_M = 0.5                 # hazard grid cell
HAZ_RANGE_M = (2.0, 25.0)        # where the scan is dense enough to judge
HAZ_HALF_WIDTH_M = 15.0
RING_CELLS = 6                   # 3 m: ring radius used to judge a dip (craters 2.3-20 m wide)
DEPRESSION_M = 0.35              # this far below a 3 m ring: inside a crater (94% of such cells
                                 # lie in a mapped crater on shoemaker.npz; 0.15 m gave 36%)
ROCK_STEP_M = 0.15               # this far above its 1.5 m ring: a rock
DETECT_ROCKS = False             # off: at this scan density no setting found real rocks (precision 0 on
                                 # shoemaker.npz), and the tallest display rock stands 0.28 m, under
                                 # the 0.3 m wheel radius in sim/scene.py
SLOPE_LIMIT_DEG = 15.0           # spec step 4
MIN_RETURNS = 3


class Lidar:
    NAME = ("lidar: forward scanner beside the camera, 120 x 33 deg, ray-cast on the camera's ground "
            "(sim/lidar.py); hazards = crater interiors and slopes over 15 deg")

    def __init__(self, scene, rng: np.random.Generator):
        self.scene = scene
        self.rng = rng
        self.az = np.radians(np.arange(-H_FOV_DEG / 2, H_FOV_DEG / 2 + 1e-9, H_STEP_DEG))
        self.el = np.radians(V_ANGLES_DEG)

    def origin(self, pose):
        """Lidar position in the scene's local frame, and heading."""
        sc = self.scene
        x, y, h = pose
        lx = x - sc.origin[0] + LIDAR_RIGHT_OF_CAMERA_M * math.sin(h)
        ly = y - sc.origin[1] - LIDAR_RIGHT_OF_CAMERA_M * math.cos(h)
        gz = float(sc.grid.height(x - sc.origin[0], y - sc.origin[1]))
        return np.array([lx, ly, gz + LIDAR_HEIGHT_M]), h

    def scan(self, pose):
        """(points (N, 3) rover frame [fwd, left, up] from the lidar, range image (rows, cols))."""
        O, h = self.origin(pose)
        EL, AZ = np.meshgrid(self.el, self.az, indexing="ij")          # (rows, cols)
        dx = np.cos(EL) * np.cos(AZ + h)
        dy = np.cos(EL) * np.sin(AZ + h)
        dz = np.sin(EL)
        surf = self.scene.surface
        rng = np.full(EL.shape, np.nan)
        prev = np.full(EL.shape, MIN_RANGE_M)
        live = np.ones(EL.shape, bool)
        for t in np.arange(MIN_RANGE_M + MARCH_STEP_M, MAX_RANGE_M + 1e-9, MARCH_STEP_M):
            idx = np.nonzero(live)
            if len(idx[0]) == 0:
                break
            px = O[0] + dx[idx] * t
            py = O[1] + dy[idx] * t
            pz = O[2] + dz[idx] * t
            below = pz <= surf.height(px, py)
            if np.any(below):
                hi = (idx[0][below], idx[1][below])
                lo_t = prev[hi]
                hi_t = np.full(len(hi[0]), t)
                for _ in range(6):   # bisection to ~4 mm
                    mid = 0.5 * (lo_t + hi_t)
                    under = O[2] + dz[hi] * mid <= surf.height(O[0] + dx[hi] * mid, O[1] + dy[hi] * mid)
                    hi_t = np.where(under, mid, hi_t)
                    lo_t = np.where(under, lo_t, mid)
                rng[hi] = hi_t
                live[hi] = False
            prev[idx] = t
        rng = rng + RANGE_NOISE_M * self.rng.standard_normal(rng.shape)
        ok = np.isfinite(rng)
        # Rover frame: x forward, y left, z up, origin at the lidar.
        fx = rng * np.cos(EL) * np.cos(AZ)
        fy = rng * np.cos(EL) * np.sin(AZ)
        fz = rng * np.sin(EL)
        pts = np.column_stack([fx[ok], fy[ok], fz[ok]])
        return pts, rng

    def hazards(self, pts):
        """(centres (K, 2) [fwd, left], kinds list of "crater" | "rock" | "slope"), rover frame."""
        if len(pts) == 0:
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
        cnt = np.bincount(flat, minlength=nx * ny)
        zsum = np.bincount(flat, weights=p[:, 2], minlength=nx * ny)
        zmax = np.full(nx * ny, -np.inf)
        np.maximum.at(zmax, flat, p[:, 2])
        known = (cnt >= MIN_RETURNS).reshape(nx, ny)
        zmean = np.where(known.ravel(), zsum / np.maximum(cnt, 1), np.nan).reshape(nx, ny)
        zmax = np.where(known.ravel(), zmax, np.nan).reshape(nx, ny)
        # Compare each cell with the mean of a symmetric ring of cells around
        # it: on any tilted plane that mean equals the cell's own height, so
        # plain slopes do not read as rocks or dips.
        def ring_mean(rad):
            offs = [(di, dj) for di in range(-rad, rad + 1) for dj in range(-rad, rad + 1)
                    if max(abs(di), abs(dj)) == rad]
            pad = np.pad(zmean, rad, constant_values=np.nan)
            stack = np.stack([pad[rad + di:rad + di + nx, rad + dj:rad + dj + ny] for di, dj in offs])
            n = np.sum(np.isfinite(stack), axis=0)
            m = np.nansum(stack, axis=0) / np.maximum(n, 1)
            return np.where(n >= len(offs) // 2, m, np.nan)

        with np.errstate(all="ignore"):
            near = ring_mean(1)                     # 1.5 m: rocks
            far = ring_mean(RING_CELLS)             # crater-sized ring
            rock = zmax - near
            depr = far - zmean
            gx = np.full((nx, ny), np.nan)
            gy = np.full((nx, ny), np.nan)
            b = 2  # +-1 m: a 2 m central difference (docs/handoffs.md step 2)
            gx[b:-b, :] = (zmean[2 * b:, :] - zmean[:-2 * b, :]) / (2 * b * c)
            gy[:, b:-b] = (zmean[:, 2 * b:] - zmean[:, :-2 * b]) / (2 * b * c)
            slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        is_crater = known & (depr > DEPRESSION_M)
        is_rock = known & ~is_crater & (rock > ROCK_STEP_M) & DETECT_ROCKS
        is_slope = known & ~is_crater & ~is_rock & (slope > SLOPE_LIMIT_DEG)
        hi, hj = np.nonzero(is_crater | is_rock | is_slope)
        centres = np.column_stack([x0 + (hi + 0.5) * c, -HAZ_HALF_WIDTH_M + (hj + 0.5) * c])
        kinds = ["crater" if is_crater[i, j] else "rock" if is_rock[i, j] else "slope" for i, j in zip(hi, hj)]
        return centres, kinds

    def view(self, rng_img, size=(400, 140)):
        """Colour range image for the lidar panel (RGB uint8): near = warm, far = cool, no return = black."""
        r = np.clip(rng_img / MAX_RANGE_M, 0, 1)
        u8 = np.where(np.isfinite(rng_img), (255 * (1 - r)).astype(np.uint8), 0).astype(np.uint8)
        col = cv2.applyColorMap(u8[::-1], cv2.COLORMAP_TURBO)    # top row = highest beam
        col[~np.isfinite(rng_img)[::-1]] = 0
        col = cv2.resize(col, size, interpolation=cv2.INTER_NEAREST)
        return col[..., ::-1]

    def to_camera_frame(self, centres):
        """Hazard centres from the lidar origin to the camera origin (rover frame)."""
        c = np.asarray(centres, float).reshape(-1, 2).copy()
        c[:, 1] -= LIDAR_RIGHT_OF_CAMERA_M   # the lidar sits to the right (negative y_left)
        return c
