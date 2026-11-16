"""Step 6 scene: the rover's first-person lamp view and the judges' chase view.

One scene draws both views, so the image the vision step reads is the one
the judges see (docs/handoffs.md, Scene section).

How it draws
------------
VTK draws the terrain mesh on the GPU. A fine mesh (one grid cell, 0.25 m)
covers a square around each rover; a coarse mesh covers the rest of the map
and is stitched to the fine squares so no cracks show.

First person: VTK renders only the depth buffer from the camera. Every pixel
is turned back into a 3D point, and its brightness is computed here in
numpy: lamp spotlight, inverse-square falloff, Hapke reflectance (Xu et al.
2020, the model ShadowNav renders with, Cauligi 2023 sec. 4A), and a shadow
test. There is no sunlight and no fill light, so the image is the sensor.

Display layer (sim/terrain_display.py, Scene(display=...)): the fine squares
carry its rim lips and rocks as geometry; every pixel adds its 8 m detail
tile (albedo, slope, and pebble-scale cast shadows seen from the camera) and
its albedo map. All of that is made-up detail, marked so there. The image
then goes through a soft tone curve (no clipping), shot and read noise, and
8-bit quantisation, all assumed values.

Shadows: the lamp's shadow is computed from a polar horizon map. Rays leave
the lamp in many directions across the ground; along each ray the steepest
terrain elevation seen so far is kept (a running maximum). A point is lit
when it rises above everything between it and the lamp. For a height grid
this is the exact answer to "is the straight line from the lamp to this
point blocked", without the bias and acne of a GPU shadow map at the
grazing angles where the rim shadow lives. It is the spec's "second cast
from the lamp's position, compared by depth" (spec step 6), done along
the ground.

Third person: VTK renders the scene from a chase camera. The terrain mesh
carries per-vertex colours from the same lamp model, so the lamp pool and
rim shadows match first person, plus a dim fill light that exists only so
viewers can see the ground; the image says so on screen.

Frames (docs/handoffs.md): world metres, x east, y north, heading CCW from
+x. Rover frame: x forward, y left, z up, origin at the camera.
"""

from __future__ import annotations

import math
import os
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import vtk
from vtk.util import numpy_support as vnp

# ---------------------------------------------------------------------------
# Camera and lamp: grounded in ShadowNav.
# ---------------------------------------------------------------------------
# Camera on the Scout's mast, lamp below it: sim/settings.py camera_height_m
# (brief p.6, 1.6 m mast) and lamp_below_camera_m (A24 sec. IV, 0.2 m).
try:
    from sim.settings import Settings as _Settings
except ImportError:  # run as a script from sim/
    from settings import Settings as _Settings
CAMERA_HEIGHT_M = _Settings().camera_height_m
LAMP_BELOW_CAMERA_M = _Settings().lamp_below_camera_m
# Atha et al. 2024, sec. IV: the field rig camera has a 60 deg horizontal FOV.
HFOV_DEG = 60.0
# Atha et al. 2024, Table 1 caption: with the light below the camera, stereo
# works out to 40 m. Used only to size the fine mesh and the lamp's reach.
LAMP_USEFUL_RANGE_M = 40.0

# ---------------------------------------------------------------------------
# Assumed: not given in the papers. Named so they can be changed.
# ---------------------------------------------------------------------------
FP_SIZE = (640, 480)          # assumed: first-person image (w, h) px. The rig is 5120x3840; too big to render live.
TP_SIZE = (960, 540)          # assumed: chase-view image (w, h) px.
CAMERA_PITCH_DEG = 15.0       # assumed: camera axis this far below level. Ground in view from ~3 m out to the horizon.
LAMP_PITCH_DEG = 15.0         # assumed: lamp aimed along the camera axis.
LAMP_INNER_DEG = 15.0         # assumed: full lamp brightness within this angle of the lamp axis.
LAMP_OUTER_DEG = 32.0         # assumed: no light beyond this angle. Was 30/45 (a flood wider than the view, so frames
                              # read evenly grey); 15/32 puts a visible pool inside the 60 deg view that falls off
                              # to dark at the sides, centre brightness unchanged (exposure is set on the axis).
EXPOSURE_REF_RANGE_M = 5.0    # assumed: flat ground this far ahead renders at EXPOSURE_REF_LEVEL.
EXPOSURE_REF_LEVEL = 0.55     # assumed: 0..1 display level of that reference ground (sim/terrain_look.py uses the same).
DISPLAY_GAMMA = 2.2           # assumed: sensor-to-display curve.
# Tone curve, in display-linear light y = L * EXPOSURE_REF_LEVEL^gamma (L = 1 at
# the reference): straight below TONE_KNEE, so mid-tones and the 1/r^2 falloff
# are the plain gamma curve; above it a soft shoulder, so hot near ground
# rolls off toward white instead of clipping. Assumed.
TONE_KNEE = 0.6               # display-linear (0.79 after gamma)

# Display layer (sim/terrain_display.py, docs/handoffs.md "Terrain display").
# "full": rim lips and rocks in the fine mesh, plus the detail tile and albedo
# per pixel. "detail": texture, albedo and rocks, no rim lips (for vision, so
# invented lips do not become edges, while rocks still cast shadows and hide
# ground as they would; draft item 23 "turn rocks on"). "off": the plain sim
# grid, Lambert.
DISPLAY_MODES = ("full", "detail", "off")
TILE_MIP_LEVELS = 9           # detail tile prefiltered 2048 -> 8 texels, so far ground does not sparkle
MICRO_SHADOW_STEPS_M = (0.01, 0.02, 0.035, 0.055, 0.08, 0.12, 0.17, 0.24, 0.33)  # assumed: march for tile-relief shadows
# Off (0): with the lamp 0.2 m under the camera a pebble hides almost all of
# its own shadow, and the 9-step march misses the camera's occluder between
# steps more often than the lamp's, so it drew dark specks the references do
# not have. Pebbles read through the tile's pale albedo instead.
MICRO_SHADOW_RANGE_M = 0.0    # assumed: set > 0 (e.g. 30) to turn tile-relief shadows on
MICRO_SHADOW_MAX_LEVEL = 2    # assumed: only where the tile's mip level keeps pebbles (texel <= 1.6 cm); coarser levels smear them into blobs
MICRO_SHADOW_SOFT_M = 0.0004  # assumed: shadow edge width, metres of relief (under the lamp-camera gap, see SHADOW_SOFT_TAN_CAM)
# Sensor (assumed; not given in Atha 2024 or Cauligi 2023): shot noise with
# SENSOR_FULL_WELL_E electrons at display white, read noise SENSOR_READ_E, in
# the linear signal before the tone curve; then 8-bit quantisation.
SENSOR_FULL_WELL_E = 6000.0
SENSOR_READ_E = 2.0
NORMAL_MIN_VIEW_COS = 0.1     # assumed: detail normals bent to face the camera at least this much
BLOCK_TAPER_CELLS = 8         # assumed: display relief fades out over the fine square's outer 2 m (seam to coarse mesh)
SHARED_ARRAYS = ("height", "grid_h", "grid_hg", "alb_map", "disp_alb")  # Scene.share()
DISPLAY_DIR_MAIN ="C:/Monad-Hackathon-Project/data/terrain/display"  # built assets, git-ignored
LAMP_FADE_START_M = 60.0      # assumed: the lit region ends smoothly between these ranges.
LAMP_FADE_END_M = 80.0        # assumed: by 60 m, 1/r^2 has already made the ground near-black.
ALBEDO_VARIATION = 0.06       # assumed: +/- regolith brightness texture, so the ground is not flat grey.
SHADOW_SOFT_TAN = 0.0015      # assumed: shadow edge antialiasing width, in tan(elevation) units (~0.09 deg).
SHADOW_SELF_MARGIN_M = 0.15   # assumed: terrain this close in front of a point is not tested as its blocker.
HORIZON_AZIMUTHS = 1536       # assumed: rays in the lamp's horizon map, over 180 deg (2 mrad, 8 cm apart at 40 m).
HORIZON_DR_M = 0.125          # assumed: sample spacing along each ray (half a grid cell).
# Near surface (display on): a 5 cm height field around each rover from
# terrain_display's patch(), used only for shadows and shading, so rim lips
# narrower than the 0.25 m mesh still cast their shadow arcs.
NEAR_RES_M = 0.05             # assumed: resolves lip widths (0.06 R) of craters down to ~2 m across
NEAR_HALF_M = 42.0            # assumed: square half-width; past ~40 m a rim arc is under a pixel or two
NEAR_SNAP_M = 8.0             # assumed: square centres sit on this lattice, so the rover is within 5.7 m of the
                              # centre and the square reaches 36 m past it (beyond HORIZON_NEAR_M)
HORIZON_NEAR_M = 34.0         # assumed: horizon rays sample at HORIZON_DR_NEAR_M out to here (inside the square)
HORIZON_DR_NEAR_M = 0.05      # assumed
# Horizon spans either side of the heading. Ground in the camera's view lies
# within +-28 deg of it (60 deg HFOV, 15 deg pitch, corners). The lamp's
# cone (LAMP_OUTER_DEG about an axis LAMP_PITCH_DEG down) holds no direction
# more than asin(sin 32 / cos 15) = 33.3 deg from the heading. Outside a span
# a point counts as unlit (lamp) or unseen (camera), which only happens out of view.
CAMERA_HORIZON_HALF_DEG = 32.0
LAMP_HORIZON_HALF_DEG = 40.0
SHADOW_SELF_MARGIN_CAM_M = 0.03  # assumed: self-margin when the camera horizon also votes (no acne then)
# Edge width when the camera votes. It must be well under the gap between the
# lamp's and the camera's sight lines (0.2 m / range per metre behind a rim,
# ~0.02 at 10 m) or both votes come out half-blocked and bowls go grey.
SHADOW_SOFT_TAN_CAM = 0.0002   # assumed

FINE_HALF_CELLS = 320         # assumed: fine mesh square is 2*320 cells = 160 m across.
FINE_SNAP_M = 20.0            # assumed: fine square centres sit on this lattice, so the rover is within 14.1 m
                              # of the centre and the square reaches 66 m past it (beyond LAMP_FADE_START_M).
# Both squares are snapped, not recentred on the rover after it drives some
# distance, so a frame depends only on the two rovers' poses, never on the
# order frames were drawn in. That lets display frames render in parallel.
COARSE_STEP = 8               # assumed: coarse mesh vertex every 8 cells (2 m).
NEAR_CLIP_M = 0.2             # assumed: GPU near and far planes.
FAR_CLIP_M = 3000.0

# Third person only (viewer aids, not sensor light).
CHASE_BACK_M = 14.0           # assumed: chase camera this far behind the rover,
CHASE_UP_M = 8.0              # assumed: this far above it,
CHASE_LOOK_AHEAD_M = 9.0      # assumed: looking at a point this far ahead of it.
CHASE_VIEW_ANGLE_DEG = 50.0   # assumed: vertical view angle.
FILL_LEVEL = 0.22             # assumed: viewer fill light strength (0..1 display, before gamma).
FILL_AZ_DEG, FILL_EL_DEG = 200.0, 35.0  # assumed: fill light direction it comes from.
FILL_RGB = np.array([0.62, 0.68, 0.82])  # assumed: cool fill, so it reads as "not the lamp".
LAMP_RGB = np.array([1.00, 0.93, 0.80])  # assumed: warm lamp light in third person.
AGENCY_RGB = {"A": (0.25, 0.75, 1.00), "B": (1.00, 0.60, 0.20)}  # assumed: rover colours.
STATUS_RGB = {"reported": (1.0, 0.85, 0.2), "confirmed": (0.3, 0.95, 0.4), "disputed": (1.0, 0.3, 0.3)}
DRAPE_M = 0.10                # assumed: tracks float this far above the ground.


# Render threads: per-point work (heights, shadows, shading) is split by rows
# across threads. Each point is computed by the same arithmetic as in one
# call, so the pixels do not depend on the thread count. Threads share the
# maps, so they add no memory. LANTERN_RENDER_THREADS=1 turns this off.
RENDER_THREADS = max(1, int(os.environ.get("LANTERN_RENDER_THREADS", min(8, os.cpu_count() or 1))))  # assumed
PAR_MIN_ROWS = 16384          # assumed: below this many points per thread, one call is faster
_POOL = None


def _par(fn, *arrays):
    """fn(*arrays) with every array split into row chunks run in threads,
    results joined along axis 0. Only for fn that treats rows independently."""
    global _POOL
    n = len(arrays[0])
    k = min(RENDER_THREADS, n // PAR_MIN_ROWS)
    if k <= 1:
        return fn(*arrays)
    if _POOL is None:
        _POOL = ThreadPoolExecutor(RENDER_THREADS, thread_name_prefix="scene")
    cuts = [n * i // k for i in range(k + 1)]
    parts = list(_POOL.map(lambda i: fn(*(a[cuts[i]:cuts[i + 1]] for a in arrays)), range(k)))
    return np.concatenate(parts, axis=0)


def _snap(v, step):
    """v to the nearest multiple of step (halves round up)."""
    return step * math.floor(v / step + 0.5)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# Height grid sampling (local frame: world minus self.origin).
# ---------------------------------------------------------------------------
class _Grid:
    def __init__(self, height, x0, y0, cell, hg=None):
        self.h = np.ascontiguousarray(height, dtype=np.float32)
        self.H, self.W = self.h.shape
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self._hf = self.h.ravel()
        # Height and its central-difference slopes per cell, gathered together
        # so one lookup gives a point's height and smooth normal. hg: the same,
        # already computed (a shared memory map, see Scene.share).
        if hg is None:
            gy, gx = np.gradient(self.h, self.cell)
            hg = np.stack([self.h, gx, gy], axis=-1).reshape(-1, 3).astype(np.float32)
        self._hg = hg

    def _corners(self, x, y):
        fj = np.clip((np.asarray(x, dtype=np.float64) - self.x0) / self.cell, 0.0, self.W - 1.000001)
        fi = np.clip((np.asarray(y, dtype=np.float64) - self.y0) / self.cell, 0.0, self.H - 1.000001)
        j = fj.astype(np.int64)
        i = fi.astype(np.int64)
        return i * self.W + j, (fj - j).astype(np.float32), (fi - i).astype(np.float32)

    def height(self, x, y):
        """Bilinear height at (x, y); clamps at the map edge."""
        k, tj, ti = self._corners(x, y)
        h, W = self._hf, self.W
        h00, h01, h10, h11 = h[k], h[k + 1], h[k + W], h[k + W + 1]
        a = h00 + (h01 - h00) * tj
        b = h10 + (h11 - h10) * tj
        return a + (b - a) * ti

    def height_slopes(self, x, y):
        """(..., 3) [height, dh/dx, dh/dy], bilinear in the grid's values and slopes."""
        k, tj, ti = self._corners(x, y)
        g, W = self._hg, self.W
        tj, ti = tj[..., None], ti[..., None]
        a = g[k] + (g[k + 1] - g[k]) * tj
        b = g[k + W] + (g[k + W + 1] - g[k + W]) * tj
        return a + (b - a) * ti

    def height_normal(self, x, y):
        """(height, unit normal) at points, both bilinear in the grid's slopes."""
        s = self.height_slopes(x, y)
        return s[..., 0], _slope_normal(s[..., 1], s[..., 2])

    def contains(self, x, y):
        return ((x >= self.x0) & (x <= self.x0 + (self.W - 1) * self.cell)
                & (y >= self.y0) & (y <= self.y0 + (self.H - 1) * self.cell))

    def normal(self, x, y, e=None):
        """Unit up-normal from central differences of the bilinear height."""
        if e is None:
            return self.height_normal(x, y)[1]
        hx = (self.height(x + e, y) - self.height(x - e, y)) / (2 * e)
        hy = (self.height(x, y + e) - self.height(x, y - e)) / (2 * e)
        return _slope_normal(hx, hy)


def _slope_normal(sx, sy):
    n = np.stack([-sx, -sy, np.ones_like(sx)], axis=-1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def _by_block(base, blocks, x, y, fn):
    """fn(grid, x, y) from the first block (priority order) containing each
    point, else from base. Each point is looked up once."""
    if not blocks:
        return fn(base, x, y)
    out = None
    left = np.ones(np.shape(x), dtype=bool)
    for g in blocks + [base]:
        m = left & g.contains(x, y) if g is not base else left
        if not np.any(m):
            continue
        r = fn(g, x[m], y[m])
        if out is None:
            out = np.empty(np.shape(x) + r.shape[1:], r.dtype)
        out[m] = r
        left &= ~m
    return out


class _Surface:
    """The ground the lamp and camera see: the sim grid, overridden inside
    each rover's fine square by that square's display heights (rim lips and
    rocks). Blocks earlier in agency order win where squares overlap, the
    same rule the fine mesh uses, so shading matches the drawn mesh."""

    def __init__(self, base):
        self.base = base
        self.blocks = []  # _Grid per fine square, highest priority first

    def _each(self, x, y, fn):
        return _by_block(self.base, self.blocks, x, y, fn)

    def height(self, x, y):
        x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
        if x.ndim == 0:
            return self._each(x[None], y[None], _Grid.height)[0]
        return self._each(x, y, _Grid.height)

    def height_slopes(self, x, y):
        return self._each(np.asarray(x, np.float64), np.asarray(y, np.float64), _Grid.height_slopes)

    def height_normal(self, x, y):
        s = self.height_slopes(x, y)
        return s[..., 0], _slope_normal(s[..., 1], s[..., 2])


def _bilinear(arr, x0, y0, cell, x, y):
    """Bilinear sample of arr (rows north, [i, j] at (x0 + j cell, y0 + i cell)), clamped."""
    H, W = arr.shape[:2]
    fj = np.clip((np.asarray(x, np.float64) - x0) / cell, 0.0, W - 1.000001)
    fi = np.clip((np.asarray(y, np.float64) - y0) / cell, 0.0, H - 1.000001)
    j, i = fj.astype(np.int64), fi.astype(np.int64)
    tj, ti = (fj - j).astype(np.float32), (fi - i).astype(np.float32)
    if arr.ndim == 3:
        tj, ti = tj[..., None], ti[..., None]
    a = arr[i, j] + (arr[i, j + 1] - arr[i, j]) * tj
    b = arr[i + 1, j] + (arr[i + 1, j + 1] - arr[i + 1, j]) * tj
    return a + (b - a) * ti


def _remap1(arr, mu, mv):
    """cv2.remap of 1-D coordinate lists (bilinear, wrapping), any channel count."""
    k = len(mu)
    pad = (-k) % 1024  # remap wants image-shaped maps; rows of 1024
    mu = np.concatenate([mu, np.zeros(pad, np.float32)]).reshape(-1, 1024)
    mv = np.concatenate([mv, np.zeros(pad, np.float32)]).reshape(-1, 1024)
    out = cv2.remap(arr, mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    return out.reshape((-1,) + arr.shape[2:])[:k]


class _DetailTile:
    """terrain_display's repeating detail tile as a mip pyramid of
    [albedo factor, dh/dx, dh/dy], sampled per pixel at the pixel's ground
    footprint so distant ground averages out instead of sparkling."""

    def __init__(self, tile_h, tile_a, period_m, strength):
        n = tile_h.shape[0]
        px = period_m / n
        gx = (np.roll(tile_h, -1, 1) - np.roll(tile_h, 1, 1)) / (2 * px)  # same differences as tile_normal()
        gy = (np.roll(tile_h, -1, 0) - np.roll(tile_h, 1, 0)) / (2 * px)
        lvl = np.stack([1.0 + strength * (tile_a - 1.0), strength * gx, strength * gy], -1).astype(np.float32)
        hl = (strength * tile_h).astype(np.float32)
        self.levels, self.hlevels = [lvl], [hl]
        while len(self.levels) < TILE_MIP_LEVELS and lvl.shape[0] > 1:
            lvl = 0.25 * (lvl[0::2, 0::2] + lvl[1::2, 0::2] + lvl[0::2, 1::2] + lvl[1::2, 1::2])
            hl = 0.25 * (hl[0::2, 0::2] + hl[1::2, 0::2] + hl[0::2, 1::2] + hl[1::2, 1::2])
            self.levels.append(lvl)
            self.hlevels.append(hl)
        self.period = period_m
        self.texel0 = px

    def _uv(self, x, y):
        n0 = self.levels[0].shape[0]
        # Level-0 texel coordinates: texel [i, j] is centred at ((j + .5) p / n, (i + .5) p / n).
        return (((x % self.period) / self.period * n0 - 0.5).astype(np.float32),
                ((y % self.period) / self.period * n0 - 0.5).astype(np.float32))

    def _level(self, footprint_m):
        return np.clip(np.log2(np.maximum(footprint_m, 1e-9) / self.texel0), 0.0, len(self.levels) - 1.0)

    def micro_shadow(self, x, y, footprint_m, dir_xy, tan_lamp, tan_cam):
        """0..1 lamp visibility from the tile's own relief (pebbles, craterlets):
        march toward the lamp over MICRO_SHADOW_STEPS_M and test whether the
        relief rises above the ray to the lamp. The 0.25 m mesh cannot cast
        these; the lamp 0.2 m under the camera shows them as thin dark slivers
        on the far side of each bump (Atha 2024 sec. V's reason for the low lamp).
        The relief is only shading on a flat mesh, so the camera would see
        straight through a bump to the shadow behind it. The same march toward
        the camera (same bearing: the lamp is under it) finds where the bump
        hides the point from the camera; there the pixel really shows the
        bump's lit face, so the shadow is dropped.
        Height comes from the mip level nearest the pixel footprint."""
        L = np.rint(self._level(footprint_m)).astype(np.int64)
        u, v = self._uv(x, y)
        n0 = self.levels[0].shape[0]
        k = n0 / self.period  # texels per metre at level 0
        occ = np.full(len(x), -1.0, np.float32)
        occ_c = np.full(len(x), -1.0, np.float32)
        for lv in np.unique(L):
            m = L == lv
            hl = self.hlevels[int(lv)]
            sc = np.float32(0.5 ** lv)
            um, vm = u[m], v[m]
            dx, dy = dir_xy[m, 0] * np.float32(k), dir_xy[m, 1] * np.float32(k)
            h0 = _remap1(hl, (um + 0.5) * sc - 0.5, (vm + 0.5) * sc - 0.5)
            t, tc = tan_lamp[m], tan_cam[m]
            o = np.full(m.sum(), -1.0, np.float32)
            oc = np.full(m.sum(), -1.0, np.float32)
            for d in MICRO_SHADOW_STEPS_M:
                rise = _remap1(hl, (um + dx * d + 0.5) * sc - 0.5, (vm + dy * d + 0.5) * sc - 0.5) - h0
                np.maximum(o, rise - t * np.float32(d), out=o)
                np.maximum(oc, rise - tc * np.float32(d), out=oc)
            occ[m], occ_c[m] = o, oc
        soft = np.float32(MICRO_SHADOW_SOFT_M)
        lamp_vis = np.clip(0.5 - occ / soft, 0.0, 1.0)
        cam_vis = np.clip(0.5 - occ_c / soft, 0.0, 1.0)
        return 1.0 - (1.0 - lamp_vis) * cam_vis

    def sample(self, x, y, footprint_m):
        """(n, 3) [albedo factor, sx, sy], trilinear across mip levels."""
        u, v = self._uv(x, y)
        lf = self._level(footprint_m)
        l0 = np.floor(lf).astype(np.int64)
        t = (lf - l0).astype(np.float32)[:, None]
        out = np.zeros((len(x), 3), np.float32)
        for L in range(int(l0.min()), min(int(l0.max()) + 2, len(self.levels))):
            lo, hi = l0 == L, l0 == L - 1  # this level is the lower or upper of the pair
            m = lo | hi
            if not np.any(m):
                continue
            w = np.where(lo[m, None], 1.0 - t[m], t[m])
            sc = np.float32(0.5 ** L)
            out[m] += w * _remap1(self.levels[L], (u[m] + 0.5) * sc - 0.5, (v[m] + 0.5) * sc - 0.5)
        return out


# ---------------------------------------------------------------------------
# The lamp and its shadow.
# ---------------------------------------------------------------------------
def _horizon_n_half(half_deg):
    return int(math.ceil(math.radians(half_deg) / (math.pi / (HORIZON_AZIMUTHS - 1))))


def _horizon_heights(surface, px, py, heading, n_half):
    """Surface heights along a horizon map's rays from (px, py): rows are
    azimuths heading + k * daz, k = -n_half..n_half; columns the ray radii.
    Returns (heights, radii, n_half)."""
    daz = math.pi / (HORIZON_AZIMUTHS - 1)
    az = heading + daz * np.arange(-n_half, n_half + 1)
    n1 = int(round(HORIZON_NEAR_M / HORIZON_DR_NEAR_M))
    r1 = HORIZON_DR_NEAR_M * np.arange(1, n1 + 1)
    n2 = int(math.ceil((LAMP_FADE_END_M - HORIZON_NEAR_M) / HORIZON_DR_M)) + 2
    r = np.concatenate([r1, HORIZON_NEAR_M + HORIZON_DR_M * np.arange(1, n2 + 1)])
    X = px + np.cos(az)[:, None] * r[None, :]
    Y = py + np.sin(az)[:, None] * r[None, :]
    return _par(surface.height, X, Y), r, n_half


class _Horizon:
    """Polar horizon map from a point: along rays over the half-plane in front
    of it, the running maximum of the terrain's elevation angle. A point is
    visible from the origin when it rises above everything in between.
    Samples are HORIZON_DR_NEAR_M apart out to HORIZON_NEAR_M (where the
    5 cm near surface is), HORIZON_DR_M beyond."""

    def __init__(self, surface, pos, heading, half_deg=90.0, heights=None):
        """heights: _horizon_heights() for this pos xy and heading over a span
        at least this wide (the lamp under a camera shares the camera's rays)."""
        self.pos = np.asarray(pos, dtype=np.float64)
        self.heading = heading
        self.daz = math.pi / (HORIZON_AZIMUTHS - 1)  # same angular spacing whatever the span
        # Rays at heading + k * daz: the same azimuths whatever the span, so a
        # wider or narrower span changes no shadow inside both.
        n_half = _horizon_n_half(half_deg)
        self.half = n_half * self.daz
        self.n1 = int(round(HORIZON_NEAR_M / HORIZON_DR_NEAR_M))
        if heights is None:
            heights = _horizon_heights(surface, self.pos[0], self.pos[1], heading, n_half)
        hgt, r, n_have = heights
        if n_have < n_half:
            raise ValueError("horizon heights cover a narrower span than asked")
        hgt = hgt[n_have - n_half: n_have + n_half + 1]
        tan_el = (hgt - self.pos[2]) / r[None, :]
        self.horizon = np.maximum.accumulate(tan_el, axis=1).astype(np.float32)

    def _index(self, rho):
        """Fractional sample index of range rho (index k holds the k-th radius)."""
        near = rho / HORIZON_DR_NEAR_M - 1.0
        far = (self.n1 - 1) + (rho - HORIZON_NEAR_M) / HORIZON_DR_M
        return np.where(rho <= HORIZON_NEAR_M, near, far)

    def visibility(self, P, margin=None, soft=SHADOW_SOFT_TAN):
        """0..1: how visible point(s) P (..., 3) are from the origin."""
        d = P - self.pos.astype(P.dtype)
        rho = np.hypot(d[..., 0], d[..., 1])
        a = np.arctan2(d[..., 1], d[..., 0]) - self.heading
        a = (a + math.pi) % (2 * math.pi) - math.pi
        fa = (a + self.half) / self.daz
        # float64: in float32, R - 1.000001 rounds to R - 1 and the + 1 below overruns.
        fr = self._index(rho.astype(np.float64) - (SHADOW_SELF_MARGIN_M if margin is None else margin))
        fa = fa.astype(np.float64)
        A, R = self.horizon.shape
        inside = (fa >= 0) & (fa <= A - 1) & (fr >= 0)
        fa = np.clip(fa, 0, A - 1.000001)
        fr_c = np.clip(fr, 0, R - 1.000001)
        ia, ir = fa.astype(np.int64), fr_c.astype(np.int64)
        ta, tr = fa - ia, fr_c - ir
        Hm = self.horizon
        h0 = Hm[ia, ir] + (Hm[ia, ir + 1] - Hm[ia, ir]) * tr
        h1 = Hm[ia + 1, ir] + (Hm[ia + 1, ir + 1] - Hm[ia + 1, ir]) * tr
        blocker = h0 + (h1 - h0) * ta
        tan_p = d[..., 2] / np.maximum(rho, 1e-6)
        vis = np.clip((tan_p - blocker) / soft + 0.5, 0.0, 1.0)
        return np.where(inside, vis, np.where(fr < 0, 1.0, 0.0))


class _Lamp:
    """One rover's lamp: position, aim, and a horizon map for its shadows."""

    def __init__(self, surface, pos, heading, heights=None):
        self.pos = np.asarray(pos, dtype=np.float64)
        self.heading = heading
        p = math.radians(LAMP_PITCH_DEG)
        self.axis = np.array([math.cos(heading) * math.cos(p), math.sin(heading) * math.cos(p), -math.sin(p)])
        # Everything behind the lamp is outside the cone (pitch + outer angle < 90 deg).
        self.hz = _Horizon(surface, pos, heading, LAMP_HORIZON_HALF_DEG, heights)

    def visibility(self, P):
        return self.hz.visibility(P)

    def irradiance(self, P, N, V=None, brdf=None, cam=None):
        """Lamp irradiance on points P (n, 3) with normals N (n, 3), in lamp units / m^2.

        With V (n, 3), unit vectors from each point toward the viewer, and
        brdf(mu0, mu, cos_g), the cosine factor is replaced by brdf's
        reflectance (normalised so it equals mu0 at the exposure reference).

        With cam, the viewing camera's _Horizon: a point the lamp cannot see
        is drawn dark only if the camera can see it. Where the camera is
        blocked too, the pixel really shows the blocker's lit face (the
        drawn mesh is coarser than the shadow surface), so it stays lit."""
        out = np.zeros(len(P), dtype=np.float32)
        l = self.pos.astype(P.dtype) - P
        # Cheap cull first: inside the cone and the lamp's reach.
        along = -(l @ self.axis.astype(P.dtype))
        r2 = np.einsum("ij,ij->i", l, l)
        c_out = math.cos(math.radians(LAMP_OUTER_DEG))
        keep = (along > 0) & (along * along > c_out * c_out * r2) & (r2 < LAMP_FADE_END_M ** 2)
        if not np.any(keep):
            return out
        l, r2, along, Pk, Nk = l[keep], r2[keep], along[keep], P[keep], N[keep]
        r = np.sqrt(r2)
        ndotl = np.clip(np.einsum("ij,ij->i", Nk, l) / r, 0.0, None)
        if brdf is not None:
            Vk = V[keep]
            mu = np.einsum("ij,ij->i", Nk, Vk)
            cos_g = np.einsum("ij,ij->i", l, Vk) / r
            ndotl = np.where(ndotl > 0, brdf(ndotl, mu, cos_g), 0.0).astype(np.float32)
        spot = _smoothstep(c_out, math.cos(math.radians(LAMP_INNER_DEG)), along / r)
        fade = 1.0 - _smoothstep(LAMP_FADE_START_M, LAMP_FADE_END_M, r)
        e = spot * fade * ndotl / np.maximum(r2, 1e-6)
        lit = e > 0
        if np.any(lit):
            if cam is None:
                vl = self.visibility(Pk[lit])
            else:
                vl = self.hz.visibility(Pk[lit], SHADOW_SELF_MARGIN_CAM_M, SHADOW_SOFT_TAN_CAM)
                vc = cam.visibility(Pk[lit], SHADOW_SELF_MARGIN_CAM_M, SHADOW_SOFT_TAN_CAM)
                vl = 1.0 - (1.0 - vl) * vc
            e[lit] *= vl
        out[keep] = e
        return out


def _reference_irradiance():
    """Lamp irradiance on flat ground EXPOSURE_REF_RANGE_M straight ahead."""
    hl = CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M
    l = np.array([-EXPOSURE_REF_RANGE_M, 0.0, hl])
    r2 = float(l @ l)
    lhat = l / math.sqrt(r2)
    p = math.radians(LAMP_PITCH_DEG)
    cos_off = float(-lhat @ np.array([math.cos(p), 0.0, -math.sin(p)]))
    spot = float(_smoothstep(math.cos(math.radians(LAMP_OUTER_DEG)), math.cos(math.radians(LAMP_INNER_DEG)), cos_off))
    return spot * lhat[2] / r2


def _reference_angles():
    """(mu0, mu, cos phase) at the exposure reference point: lamp below the camera, flat ground."""
    hl = CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M
    l = np.array([-EXPOSURE_REF_RANGE_M, 0.0, hl])
    v = np.array([-EXPOSURE_REF_RANGE_M, 0.0, CAMERA_HEIGHT_M])
    l, v = l / np.linalg.norm(l), v / np.linalg.norm(v)
    return float(l[2]), float(v[2]), float(l @ v)


def _tone_lin(lin_rel):
    """Display-linear 0..1 from radiance relative to the exposure reference (1 = reference)."""
    y = np.asarray(lin_rel, np.float32) * np.float32(EXPOSURE_REF_LEVEL ** DISPLAY_GAMMA)
    k, w = np.float32(TONE_KNEE), np.float32(1.0 - TONE_KNEE)
    return np.where(y <= k, y, k + w * (1.0 - np.exp(-(y - k) / w))).astype(np.float32)


def _load_display(height, craters, x0, y0, cell, display_dir, strength):
    """terrain_display.Display over the scene's own arrays (it would otherwise
    load the terrain file again). None, with a printed reason, if unusable."""
    import os
    import sys
    from pathlib import Path
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)  # terrain_display does `import terrain`
    import terrain_display as TD
    d = Path(display_dir) if display_dir else None
    if d is None or not (d / "display.json").exists():
        cands = [Path(here).parent / "data/terrain/display", Path(DISPLAY_DIR_MAIN)]
        d = next((c for c in cands if (c / "display.json").exists()), None)
    if d is None:
        print("scene: no display assets (run `python sim/terrain_display.py build`); display off")
        return None, TD
    orig = TD.T.load_terrain
    TD.T.load_terrain = lambda path=None: (height, craters, x0, y0, cell)
    try:
        disp = TD.Display(display_dir=d, strength=strength)
    finally:
        TD.T.load_terrain = orig
    if disp.alb.shape != np.shape(height) or len(disp.lips) != len(craters):
        print(f"scene: display assets in {d} were built for another terrain; display off")
        return None, TD
    return disp, TD


# ---------------------------------------------------------------------------
# VTK helpers.
# ---------------------------------------------------------------------------
def _polydata(points, tris):
    pd = vtk.vtkPolyData()
    pts = vtk.vtkPoints()
    pts.SetData(vnp.numpy_to_vtk(np.ascontiguousarray(points, dtype=np.float32), deep=True))
    pd.SetPoints(pts)
    _set_tris(pd, tris)
    return pd


def _set_tris(pd, tris):
    tris = np.ascontiguousarray(tris, dtype=np.int64).ravel()
    offsets = np.arange(0, tris.size + 1, 3, dtype=np.int64)
    ca = vtk.vtkCellArray()
    ca.SetData(vnp.numpy_to_vtkIdTypeArray(offsets, deep=True), vnp.numpy_to_vtkIdTypeArray(tris, deep=True))
    pd.SetPolys(ca)
    pd.Modified()


def _set_colors(pd, rgb01):
    arr = vnp.numpy_to_vtk(np.ascontiguousarray(np.clip(rgb01 * 255.0 + 0.5, 0, 255).astype(np.uint8)), deep=True)
    arr.SetName("rgb")
    pd.GetPointData().SetScalars(arr)
    pd.Modified()


def _color_actor(pd, lighting=False):
    m = vtk.vtkPolyDataMapper()
    m.SetInputData(pd)
    m.SetColorModeToDirectScalars()
    m.SetScalarModeToUsePointData()
    a = vtk.vtkActor()
    a.SetMapper(m)
    if not lighting:
        a.GetProperty().LightingOff()
    return a


def _grid_tris(nr, nc, cell_mask=None):
    """Two triangles per cell of an (nr x nc) vertex grid, CCW seen from +z."""
    i, j = np.meshgrid(np.arange(nr - 1), np.arange(nc - 1), indexing="ij")
    if cell_mask is not None:
        i, j = i[cell_mask], j[cell_mask]
    v = (i * nc + j).ravel()
    t1 = np.stack([v, v + 1, v + nc + 1], axis=1)
    t2 = np.stack([v, v + nc + 1, v + nc], axis=1)
    return np.concatenate([t1, t2])


def _make_window(size, samples):
    rw = vtk.vtkRenderWindow()
    rw.SetOffScreenRendering(1)
    rw.SetSize(*size)
    rw.SetMultiSamples(samples)
    ren = vtk.vtkRenderer()
    rw.AddRenderer(ren)
    return rw, ren


def _read_rgb(rw):
    w, h = rw.GetSize()
    f = vtk.vtkWindowToImageFilter()
    f.SetInput(rw)
    f.SetInputBufferTypeToRGB()
    f.ReadFrontBufferOff()
    f.Update()
    img = vnp.vtk_to_numpy(f.GetOutput().GetPointData().GetScalars()).reshape(h, w, 3)
    return np.ascontiguousarray(img[::-1])


def _rover_polydata():
    """A simple rover in its own frame: x forward, y left, z up, origin on the ground under the mast."""
    app = vtk.vtkAppendPolyData()

    def box(cx, cy, cz, dx, dy, dz):
        s = vtk.vtkCubeSource()
        s.SetCenter(cx, cy, cz)
        s.SetXLength(dx)
        s.SetYLength(dy)
        s.SetZLength(dz)
        s.Update()
        app.AddInputData(s.GetOutput())

    box(-0.2, 0, 0.75, 1.7, 1.2, 0.5)        # body
    box(-0.2, 0, 1.05, 1.2, 1.0, 0.08)       # deck
    box(0, 0, 1.75, 0.10, 0.10, 1.5)         # mast
    box(0.02, 0, CAMERA_HEIGHT_M, 0.18, 0.40, 0.14)  # camera head
    for x in (0.45, -0.85):
        for y in (0.72, -0.72):
            c = vtk.vtkCylinderSource()
            c.SetRadius(0.3)
            c.SetHeight(0.22)
            c.SetResolution(20)
            c.SetCenter(x, y, 0.3)  # cylinder axis is y: wheels already sideways
            c.Update()
            app.AddInputData(c.GetOutput())
    app.Update()
    return app.GetOutput()


# ---------------------------------------------------------------------------
# The scene.
# ---------------------------------------------------------------------------
class Scene:
    """Both views of both rovers. See docs/handoffs.md, Scene section.

    depth from first_person() is the straight-line distance in metres from
    the camera centre to the surface along each pixel's ray, NaN for sky.
    Use camera_model() or depth_to_rover() to turn it into rover-frame points.
    """

    def __init__(self, height, craters, x0, y0, cell, display="full", display_dir=None, strength=None,
                 shared=None):
        """display: "full" (display layer with rim lips and rocks), "detail"
        (its texture, albedo, Hapke shading and rocks, but no rim lips, for
        the vision step), or "off" (the sim grid, Lambert, as before).
        strength: terrain_display's one knob (None = its built default).
        shared: a folder written by share(); its big arrays are opened as
        read-only memory maps, so worker processes hold one copy between them."""
        sh = None
        if shared is not None:
            sh = {k: np.load(Path(shared) / f"{k}.npy", mmap_mode="r") for k in SHARED_ARRAYS
                  if (Path(shared) / f"{k}.npy").exists()}
        if display not in DISPLAY_MODES:
            raise ValueError(f"display must be one of {DISPLAY_MODES}")
        height = np.asarray(height, dtype=np.float32)
        self._height = height
        H, W = height.shape
        # Render in a local frame near the map centre: world coordinates such
        # as LOLA stereographic metres (~1e5) lose millimetres in float32.
        self.origin = np.array([x0 + 0.5 * W * cell, y0 + 0.5 * H * cell, float(np.median(height))])
        if sh is not None:
            self.grid = _Grid(sh["grid_h"], x0 - self.origin[0], y0 - self.origin[1], cell, hg=sh["grid_hg"])
        else:
            self.grid = _Grid(height - self.origin[2], x0 - self.origin[0], y0 - self.origin[1], cell)
        self.surface = _Surface(self.grid)  # what the lamp and camera see; fine squares added per rover
        self.mesh_surface = _Surface(self.grid)  # what VTK draws: fine squares over the sim grid
        self.craters = np.asarray(craters, dtype=np.float64)
        self.e_ref = _reference_irradiance()
        self._albedo = self._make_albedo()

        self.disp = None
        self._brdf = None
        self._tile = None
        if display != "off":
            self.disp, TD = _load_display(height, craters, x0, y0, cell, display_dir, strength)
            if self.disp is None:
                display = "off"
        self.display = display
        if self.disp is not None:
            D = self.disp
            self._tile = _DetailTile(D.tile_h, D.tile_a, D.p.tile_period_m, D.s)
            # Per-cell albedo map at the knob's strength (terrain_display FORMAT, "Combine").
            if sh is not None:
                D.alb = sh["disp_alb"]
                self._alb_map = sh["alb_map"]
            else:
                self._alb_map = (1.0 + D.s * (D.alb - 1.0)).astype(np.float32)
            self.grid.alb = self._alb_map
            mu0r, mur, cgr = _reference_angles()
            hp = D.p
            radf_ref = float(TD.hapke_radf(mu0r, mur, math.acos(cgr), hp))
            k = np.float32(mu0r / radf_ref)

            def brdf(mu0, mu, cos_g):  # Hapke I/F (Xu et al. 2020 Eqs. 5-12), equal to mu0 at the reference
                return TD.hapke_radf(mu0, mu, np.arccos(np.clip(cos_g, -1.0, 1.0)), hp) * k
            self._brdf = brdf
            self._mesh_layers = ("lips", "rocks") if display == "full" else ("rocks",)

        self.poses = {}     # agency -> (x, y, heading), world
        self.lamps = {}     # agency -> _Lamp (local frame), built lazily
        self.blocks = {}    # agency -> fine mesh block
        self.near = {}      # agency -> 5 cm near _Grid (display on)
        self.cams = {}      # agency -> camera _Horizon, built lazily
        self._hzh = {}      # agency -> _horizon_heights() at its camera xy, shared by its lamp and camera
        self._lamp_colors_dirty = True

        # Coarse mesh over the whole map.
        g = self.grid
        K = COARSE_STEP
        ci = np.arange(0, H, K)
        cj = np.arange(0, W, K)
        if ci[-1] != H - 1:
            ci = np.append(ci, H - 1)
        if cj[-1] != W - 1:
            cj = np.append(cj, W - 1)
        self._ci, self._cj = ci, cj
        CI, CJ = np.meshgrid(ci, cj, indexing="ij")
        X = g.x0 + CJ * g.cell
        Y = g.y0 + CI * g.cell
        Z = g.h[CI, CJ]
        self._coarse_xyz = np.stack([X, Y, Z], axis=-1).reshape(-1, 3).astype(np.float64)
        self._coarse_pd = _polydata(self._coarse_xyz, _grid_tris(len(ci), len(cj)))
        n = g.normal(self._coarse_xyz[:, 0], self._coarse_xyz[:, 1], e=K * g.cell / 2)
        self._coarse_fill = self._fill_rgb(self._coarse_xyz, n)
        self._coarse_n = n
        _set_colors(self._coarse_pd, self._gamma(self._coarse_fill))
        self._coarse_actor = _color_actor(self._coarse_pd)

        # Render windows: first person (depth only, no MSAA so the depth is
        # exact) and third person (antialiased colour).
        self._fp_rw, self._fp_ren = _make_window(FP_SIZE, 0)
        self._tp_rw, self._tp_ren = _make_window(TP_SIZE, 8)
        self._tp_ren.SetBackground(0.015, 0.017, 0.03)
        self._fp_ren.SetBackground(0, 0, 0)
        for ren in (self._fp_ren, self._tp_ren):
            ren.AddActor(self._coarse_actor)
        # Third-person fill light for rover bodies (viewer aid only).
        self._tp_ren.AutomaticLightCreationOff()
        light = vtk.vtkLight()
        light.SetLightTypeToSceneLight()
        az, el = math.radians(FILL_AZ_DEG), math.radians(FILL_EL_DEG)
        light.SetPosition(math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el))
        light.SetFocalPoint(0, 0, 0)
        light.SetPositional(False)
        light.SetIntensity(0.9)
        self._tp_ren.AddLight(light)
        head = vtk.vtkLight()
        head.SetLightTypeToHeadlight()
        head.SetIntensity(0.35)
        self._tp_ren.AddLight(head)

        self._rover_pd = _rover_polydata()
        self._rover_actors = {}  # agency -> (fp actor, tp actor)
        self.timings = {}

    def share(self, folder):
        """Write the big arrays a Scene(..., shared=folder) maps instead of building."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        arrs = dict(height=self._height, grid_h=self.grid.h, grid_hg=self.grid._hg)
        if self.disp is not None:
            arrs.update(alb_map=self._alb_map, disp_alb=self.disp.alb)
        for k, a in arrs.items():
            np.save(folder / f"{k}.npy", np.ascontiguousarray(a))
        return folder

    # -- public API (docs/handoffs.md) ---------------------------------------
    def set_rover(self, agency, true_pose):
        x, y, hd = (float(v) for v in true_pose)
        self.poses[agency] = (x, y, hd)
        self.lamps.pop(agency, None)
        self._lamp_colors_dirty = True
        lx, ly = x - self.origin[0], y - self.origin[1]
        blk = self.blocks.get(agency)
        bc = (_snap(lx, FINE_SNAP_M), _snap(ly, FINE_SNAP_M))
        if blk is None or (blk["cx"], blk["cy"]) != bc:
            self._build_block(agency, *bc)
        near = self.near.get(agency)
        nc = (_snap(lx, NEAR_SNAP_M), _snap(ly, NEAR_SNAP_M))
        if self.disp is not None and (near is None or (near.cx, near.cy) != nc):
            self._build_near(agency, *nc)
        self.cams.pop(agency, None)
        self._hzh.pop(agency, None)
        self._place_rover_actor(agency)

    def first_person(self, agency):
        """(img (h, w, 3) uint8, depth (h, w) float32 metres, NaN for sky)."""
        t0 = time.perf_counter()
        C, fwd, right, up = self._camera_frame(agency)
        cam = self._fp_ren.GetActiveCamera()
        cam.SetPosition(*C)
        cam.SetFocalPoint(*(C + fwd * 10.0))
        cam.SetViewUp(*up)
        w, h = FP_SIZE
        vfov = 2.0 * math.degrees(math.atan(math.tan(math.radians(HFOV_DEG / 2)) * h / w))
        cam.SetViewAngle(vfov)
        cam.SetClippingRange(NEAR_CLIP_M, FAR_CLIP_M)
        for ag, (fa, _) in self._rover_actors.items():
            fa.SetVisibility(ag != agency)  # the camera never sees its own rover
        self._fp_rw.Render()
        zb = vtk.vtkFloatArray()
        self._fp_rw.GetZbufferData(0, 0, w - 1, h - 1, zb)
        d = vnp.vtk_to_numpy(zb).reshape(h, w)[::-1].astype(np.float64)
        n, f = cam.GetClippingRange()
        t1 = time.perf_counter()

        sky = d >= 1.0
        z_ndc = 2.0 * d - 1.0
        z_eye = 2.0 * f * n / ((f + n) - z_ndc * (f - n))
        U, V, ray_len = self._pixel_rays()
        rng = z_eye * ray_len
        rng[sky] = np.nan
        depth = rng.astype(np.float32)

        # Shade: back-project, lamp model, shadow test. Only pixels some lamp
        # can reach are shaded; the rest are black (no sunlight, no fill).
        P = (C[None, None, :] + (fwd + U[..., None] * right + V[..., None] * up) * z_eye[..., None]).astype(np.float32)
        reach = np.zeros((h, w), dtype=bool)
        for ag in self.poses:
            lp = self._lamp_pos(ag)  # position only: a lamp's shadow map is built if it lights a pixel
            reach |= (np.abs(P[..., 0] - lp[0]) < LAMP_FADE_END_M) & (np.abs(P[..., 1] - lp[1]) < LAMP_FADE_END_M)
        reach &= ~sky
        Pr = P[reach]
        S = _par(self.surface.height_slopes, Pr[:, 0], Pr[:, 1])
        hP = S[:, 0]
        V = (C[None, :] - Pr).astype(np.float32)
        V /= np.linalg.norm(V, axis=1, keepdims=True)
        alb = _par(self._albedo_at, Pr)
        if self._tile is not None:
            # Per-pixel detail in world coordinates (it repeats every 8 m in
            # world x, y, as terrain_display.patch() lays it). Footprint: the
            # pixel's width on the ground, stretched by the viewing slant.
            Nm = _slope_normal(S[:, 1], S[:, 2])
            mu = np.einsum("ij,ij->i", Nm, V)
            fx = self._intrinsics()[0]
            foot = z_eye[reach] * ray_len[reach] / fx / np.maximum(mu, 0.15)
            T = _par(self._tile.sample, Pr[:, 0] + self.origin[0], Pr[:, 1] + self.origin[1], foot)
            alb = alb * T[:, 0]
            # Inside a near square built with the tile's relief, the slopes
            # already hold it (to 5 cm); do not add it twice.
            tx, ty = T[:, 1].copy(), T[:, 2].copy()
            for ng in self.near.values():
                if getattr(ng, "has_detail", False):
                    m = ng.contains(Pr[:, 0], Pr[:, 1])
                    tx[m] = 0.0
                    ty[m] = 0.0
            N = _slope_normal(S[:, 1] + tx, S[:, 2] + ty).astype(np.float32)
        else:
            N = _slope_normal(S[:, 1], S[:, 2]).astype(np.float32)
        # Normals come from the 5 cm surface and the detail tile, finer than the
        # 0.25 m mesh the camera sees: one can face away from the camera on a
        # facet the camera does see (a rock's far flank under a mesh bump).
        # Bend those toward the viewer (standard normal-map fix) so they do
        # not render as black specks.
        nv = np.einsum("ij,ij->i", N, V)
        bad = nv < NORMAL_MIN_VIEW_COS
        if np.any(bad):
            Nb = N[bad] + (NORMAL_MIN_VIEW_COS - nv[bad])[:, None] * V[bad]
            N[bad] = Nb / np.linalg.norm(Nb, axis=1, keepdims=True)
        # Another rover: far from the drawn mesh (not the 5 cm surface, whose
        # rocks and lips the mesh only approximates).
        off_ground = np.abs(Pr[:, 2] - _par(self.mesh_surface.height, Pr[:, 0], Pr[:, 1])) > 0.05
        # Shadow tests run on the 5 cm surface: lift ground points onto it, so a
        # lip the 0.25 m mesh cannot hold does not shadow the point drawn under it.
        Ps = Pr.copy()
        Ps[~off_ground, 2] = hP[~off_ground]
        if np.any(off_ground):  # another rover: normal from the depth image instead
            # Central differences of the image's points (np.gradient's
            # formulas, one-sided at the image edge), only at these pixels.
            Pimg = np.where(sky[..., None], np.nan, P)
            pix = np.flatnonzero(reach.ravel())[off_ground]
            rr, cc = pix // w, pix % w

            def diff(i0, j0, i1, j1, den):
                return (Pimg[i1, j1] - Pimg[i0, j0]) / den

            in_c, in_r = (cc > 0) & (cc < w - 1), (rr > 0) & (rr < h - 1)
            gx = np.where(in_c[:, None], diff(rr, np.maximum(cc - 1, 0), rr, np.minimum(cc + 1, w - 1), 2.0),
                          np.where((cc == 0)[:, None], diff(rr, cc * 0, rr, cc * 0 + 1, 1.0),
                                   diff(rr, cc * 0 + w - 2, rr, cc * 0 + w - 1, 1.0)))
            gy = np.where(in_r[:, None], diff(np.maximum(rr - 1, 0), cc, np.minimum(rr + 1, h - 1), cc, 2.0),
                          np.where((rr == 0)[:, None], diff(rr * 0, cc, rr * 0 + 1, cc, 1.0),
                                   diff(rr * 0 + h - 2, cc, rr * 0 + h - 1, cc, 1.0)))
            No = np.cross(gx, gy)[:, None, :]
            No /= np.linalg.norm(No, axis=-1, keepdims=True) + 1e-12
            No *= -np.sign(np.einsum("hwi,i->hw", No, fwd.astype(np.float32)))[..., None]
            No = No[:, 0, :]
            N[off_ground] = np.where(np.isfinite(No), No, 0.0)
        E = np.zeros(len(Pr), dtype=np.float32)
        cam = self._cam_horizon(agency)
        for ag in self.poses:
            lp = self._lamp_pos(ag)
            near = (np.abs(Pr[:, 0] - lp[0]) < LAMP_FADE_END_M) & (np.abs(Pr[:, 1] - lp[1]) < LAMP_FADE_END_M)
            if not np.any(near):
                continue
            lamp = self._lamp(ag)
            e = _par(lambda p, n, v: lamp.irradiance(p, n, v, self._brdf, cam), Ps[near], N[near], V[near])
            if ag == agency and self._tile is not None:
                # Pebble-scale cast shadows from this camera's own lamp.
                d = lamp.pos[None, :2] - Ps[near, :2]
                rho = np.linalg.norm(d, axis=1)
                fine = self._tile._level(foot[near]) <= MICRO_SHADOW_MAX_LEVEL + 0.5
                sel = (e > 0) & (rho < MICRO_SHADOW_RANGE_M) & ~off_ground[near] & fine
                if np.any(sel):
                    ii = np.flatnonzero(near)[sel]
                    dirs = (d[sel] / np.maximum(rho[sel], 1e-6)[:, None]).astype(np.float32)
                    tan_l = ((lamp.pos[2] - Ps[ii, 2]) / np.maximum(rho[sel], 1e-3)).astype(np.float32)
                    tan_c = ((C[2] - Ps[ii, 2]) / np.maximum(rho[sel], 1e-3)).astype(np.float32)
                    e[sel] *= self._tile.micro_shadow(Pr[ii, 0] + self.origin[0], Pr[ii, 1] + self.origin[1],
                                                      foot[ii], dirs, tan_l, tan_c)
            E[near] += e
        lin_rel = alb * E * np.float32(1.0 / self.e_ref)  # 1 = the exposure reference ground
        y = np.zeros((h, w), dtype=np.float32)
        y[reach] = _tone_lin(lin_rel)  # display-linear, 0..1
        if SENSOR_FULL_WELL_E > 0:
            # Seeded by pose (crc32, not hash(): str hashes change per process),
            # so a frame re-renders identically.
            seed = zlib.crc32(repr((agency,) + tuple(round(v, 3) for v in self.poses[agency])).encode())
            rng = np.random.default_rng(seed)
            el = y * np.float32(SENSOR_FULL_WELL_E)
            sd = np.sqrt(el + np.float32(SENSOR_READ_E ** 2))
            y = np.clip((el + sd * rng.standard_normal((h, w), dtype=np.float32)) / np.float32(SENSOR_FULL_WELL_E), 0.0, 1.0)
        grey = (y ** np.float32(1.0 / DISPLAY_GAMMA) * 255.0 + 0.5).astype(np.uint8)
        img = np.repeat(grey[..., None], 3, axis=2)
        t2 = time.perf_counter()
        self.timings["fp_render_ms"] = 1e3 * (t1 - t0)
        self.timings["fp_shade_ms"] = 1e3 * (t2 - t1)
        return img, depth

    def third_person(self, agency, overlays=None):
        """(h, w, 3) uint8 chase view of `agency`, with optional overlays."""
        t0 = time.perf_counter()
        overlays = overlays or {}
        self._update_lamp_colors()
        x, y, hd = self.poses[agency]
        lx, ly = x - self.origin[0], y - self.origin[1]
        gz = float(self.grid.height(lx, ly))
        fwd = np.array([math.cos(hd), math.sin(hd), 0.0])
        eye = np.array([lx, ly, gz]) - fwd * CHASE_BACK_M
        eye[2] = max(gz + CHASE_UP_M, float(self.grid.height(eye[0], eye[1])) + 3.0)
        target = np.array([lx, ly, gz]) + fwd * CHASE_LOOK_AHEAD_M
        target[2] = float(self.grid.height(target[0], target[1])) + 0.5
        cam = self._tp_ren.GetActiveCamera()
        cam.SetPosition(*eye)
        cam.SetFocalPoint(*target)
        cam.SetViewUp(0, 0, 1)
        cam.SetViewAngle(CHASE_VIEW_ANGLE_DEG)
        cam.SetClippingRange(0.5, FAR_CLIP_M)
        for _, ta in self._rover_actors.values():
            ta.SetVisibility(True)

        extra = self._overlay_actors(agency, overlays)
        for a in extra:
            self._tp_ren.AddActor(a)
        self._tp_rw.Render()
        img = _read_rgb(self._tp_rw)
        for a in extra:
            self._tp_ren.RemoveActor(a)
        img = self._label_third_person(img, agency, overlays)
        self.timings["tp_ms"] = 1e3 * (time.perf_counter() - t0)
        return img

    # -- helpers for the vision step -----------------------------------------
    def camera_model(self):
        """Pinhole model of the first-person camera.

        Pixel (u, v), u right and v down from the top-left corner, has its
        centre at (u + 0.5, v + 0.5). Its ray in the camera frame (right,
        up, forward) is ((u + 0.5 - cx) / fx, (cy - v - 0.5) / fy, 1).
        The camera is pitched down by pitch_deg about its right axis and is
        levelled (no roll, no terrain tilt): an assumption of this scene.
        """
        fx, fy, cx, cy = self._intrinsics()
        return dict(width=FP_SIZE[0], height=FP_SIZE[1], fx=fx, fy=fy, cx=cx, cy=cy,
                    pitch_deg=CAMERA_PITCH_DEG, camera_height_m=CAMERA_HEIGHT_M,
                    lamp_below_camera_m=LAMP_BELOW_CAMERA_M, hfov_deg=HFOV_DEG)

    def depth_to_rover(self, depth):
        """(h, w, 3) rover-frame points [x_forward, y_left, z_up] in metres from the camera."""
        h, w = depth.shape
        fx, fy, cx, cy = self._intrinsics()
        u = (np.arange(w) + 0.5 - cx) / fx
        v = (cy - (np.arange(h) + 0.5)) / fy
        U, V = np.meshgrid(u, v)
        z_eye = depth / np.sqrt(1.0 + U * U + V * V)
        p = math.radians(CAMERA_PITCH_DEG)
        # Camera axes in the rover frame.
        fwd = np.array([math.cos(p), 0.0, -math.sin(p)])
        right = np.array([0.0, -1.0, 0.0])
        up = np.array([math.sin(p), 0.0, math.cos(p)])
        return z_eye[..., None] * (fwd + U[..., None] * right + V[..., None] * up)

    # -- internals ------------------------------------------------------------
    def _pixel_rays(self):
        """Per pixel: camera-frame ray (U right, V up, 1 forward) and its length."""
        if getattr(self, "_rays", None) is None:
            w, h = FP_SIZE
            fx, fy, cx, cy = self._intrinsics()
            u = (np.arange(w) + 0.5 - cx) / fx
            v = (cy - (np.arange(h) + 0.5)) / fy
            U, V = np.meshgrid(u, v)
            self._rays = (U, V, np.sqrt(1.0 + U * U + V * V))
        return self._rays

    def _intrinsics(self):
        w, h = FP_SIZE
        fx = (w / 2.0) / math.tan(math.radians(HFOV_DEG / 2))
        return fx, fx, w / 2.0, h / 2.0

    def _camera_frame(self, agency):
        x, y, hd = self.poses[agency]
        lx, ly = x - self.origin[0], y - self.origin[1]
        C = np.array([lx, ly, float(self.grid.height(lx, ly)) + CAMERA_HEIGHT_M])
        p = math.radians(CAMERA_PITCH_DEG)
        fwd = np.array([math.cos(hd) * math.cos(p), math.sin(hd) * math.cos(p), -math.sin(p)])
        right = np.array([math.sin(hd), -math.cos(hd), 0.0])
        up = np.cross(right, fwd)
        return C, fwd, right, up

    def _cam_horizon(self, agency):
        hz = self.cams.get(agency)
        if hz is None:
            C, *_ = self._camera_frame(agency)
            hz = _Horizon(self.surface, C, self.poses[agency][2], CAMERA_HORIZON_HALF_DEG, self._hz_heights(agency))
            self.cams[agency] = hz
        return hz

    def _set_surface(self):
        """Priority: near squares, then fine squares (each in agency order), then the sim grid."""
        self.surface.blocks = ([self.near[a] for a in sorted(self.near)]
                               + [self.blocks[a]["grid"] for a in sorted(self.blocks) if self.blocks[a]["grid"] is not None])
        self.mesh_surface.blocks = [self.blocks[a]["grid"] for a in sorted(self.blocks) if self.blocks[a]["grid"] is not None]
        self.lamps.clear()
        self.cams.clear()
        self._hzh.clear()

    def _build_near(self, agency, lx, ly):
        t = time.perf_counter()
        n = int(round(2 * NEAR_HALF_M / NEAR_RES_M)) + 1
        x0 = lx - NEAR_HALF_M
        y0 = ly - NEAR_HALF_M
        # "full" also puts the detail tile's relief into the shadow surface: its
        # bumps along a ridge cast the dashed lip lines terrain_look's 2 cm
        # geometry shows. Its albedo stays per pixel, so it is divided out here.
        detail = self.display == "full"
        layers = self._mesh_layers + (("detail",) if detail else ())
        ph, pa = self.disp.patch(x0 + self.origin[0], y0 + self.origin[1], n, n, NEAR_RES_M, layers=layers)
        if detail:
            X, Y = np.meshgrid(x0 + self.origin[0] + NEAR_RES_M * np.arange(n), y0 + self.origin[1] + NEAR_RES_M * np.arange(n))
            ta = _par(lambda x, y: self.disp._tile(self.disp.tile_a, x, y), X.ravel(), Y.ravel()).reshape(X.shape)
            pa = pa / (1.0 + self.disp.s * (ta - 1.0))
        ng = _Grid(ph - np.float32(self.origin[2]), x0, y0, NEAR_RES_M)
        ng.alb = pa.astype(np.float32)
        ng.has_detail = detail
        ng.cx, ng.cy = lx, ly
        self.near[agency] = ng
        self._set_surface()
        self.timings["near_ms"] = 1e3 * (time.perf_counter() - t)

    def _lamp_pos(self, agency):
        """The lamp, LAMP_BELOW_CAMERA_M straight under the camera (local frame)."""
        C, *_ = self._camera_frame(agency)
        return C - np.array([0.0, 0.0, LAMP_BELOW_CAMERA_M])

    def _hz_heights(self, agency):
        """Heights along the horizon rays from this rover's camera xy, over the
        wider of the lamp and camera spans. The lamp is straight under the
        camera, so both maps sample the same points; only the origin height
        differs (_Horizon applies it)."""
        hh = self._hzh.get(agency)
        if hh is None:
            C, *_ = self._camera_frame(agency)
            n = _horizon_n_half(max(LAMP_HORIZON_HALF_DEG, CAMERA_HORIZON_HALF_DEG))
            hh = _horizon_heights(self.surface, C[0], C[1], self.poses[agency][2], n)
            self._hzh[agency] = hh
        return hh

    def _lamp(self, agency):
        lamp = self.lamps.get(agency)
        if lamp is None:
            C, *_ = self._camera_frame(agency)
            lamp = _Lamp(self.surface, self._lamp_pos(agency), self.poses[agency][2], self._hz_heights(agency))
            self.lamps[agency] = lamp
        return lamp

    def _make_albedo(self):
        rng = np.random.default_rng(7)
        n = 1024
        f = np.fft.fftfreq(n)
        f2 = f[:, None] ** 2 + f[None, :] ** 2

        def blur(sigma_px):  # periodic Gaussian blur, so the tile repeats seamlessly
            w = rng.standard_normal((n, n))
            return np.fft.ifft2(np.fft.fft2(w) * np.exp(-2 * (math.pi * sigma_px) ** 2 * f2)).real

        a, b = blur(1.2), blur(14.0)
        t = a / a.std() + 0.7 * b / b.std()
        return (1.0 + ALBEDO_VARIATION * t / t.std()).astype(np.float32)

    def _albedo_at(self, P, blocks=None):
        """Ground albedo factor at local points P (..., 2+): the display layer's
        per-cell map (fine squares: their patch albedo, with rocks), or with
        the display off, the repeating stand-in tile."""
        g = self.grid
        if self.disp is not None:
            return _by_block(g, self.surface.blocks if blocks is None else blocks, P[..., 0], P[..., 1],
                             lambda gr, x, y: _bilinear(gr.alb, gr.x0, gr.y0, gr.cell, x, y))
        A = self._albedo
        n = A.shape[0]
        fj = (P[..., 0] - g.x0) / g.cell
        fi = (P[..., 1] - g.y0) / g.cell
        j0, i0 = np.floor(fj), np.floor(fi)
        tj, ti = (fj - j0).astype(np.float32), (fi - i0).astype(np.float32)
        j0 = j0.astype(np.int64) % n
        i0 = i0.astype(np.int64) % n
        j1, i1 = (j0 + 1) % n, (i0 + 1) % n
        a = A[i0, j0] + (A[i0, j1] - A[i0, j0]) * tj
        b = A[i1, j0] + (A[i1, j1] - A[i1, j0]) * tj
        return a + (b - a) * ti

    def _gamma(self, lin):
        return np.clip(lin, 0.0, 1.0) ** (1.0 / DISPLAY_GAMMA)

    def _fill_rgb(self, P, N, blocks=None):
        az, el = math.radians(FILL_AZ_DEG), math.radians(FILL_EL_DEG)
        s = np.array([math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el)])
        k = FILL_LEVEL * (0.35 + 0.65 * np.clip(N @ s, 0.0, None))
        return (self._albedo_at(P, blocks) * k)[:, None] * FILL_RGB[None, :]

    # Fine mesh blocks -----------------------------------------------------
    def _build_block(self, agency, lx, ly):
        g = self.grid
        K = COARSE_STEP
        jc = int(round((lx - g.x0) / g.cell / K)) * K
        ic = int(round((ly - g.y0) / g.cell / K)) * K
        i0 = max(0, ic - FINE_HALF_CELLS)
        j0 = max(0, jc - FINE_HALF_CELLS)
        i1 = min(g.H - 1, ic + FINE_HALF_CELLS)
        j1 = min(g.W - 1, jc + FINE_HALF_CELLS)
        I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing="ij")
        nr, nc = i1 - i0 + 1, j1 - j0 + 1
        hb = g.h[i0:i1 + 1, j0:j1 + 1]
        bgrid = None
        if self.disp is not None:
            # Display heights on the same 0.25 m vertices (terrain_display.patch,
            # rows north, [i, j] at (xmin + j res, ymin + i res)). The made-up
            # part tapers to zero at the square's edge so the stitch to the
            # coarse mesh stays closed.
            t = time.perf_counter()
            ph, pa = self.disp.patch(g.x0 + j0 * g.cell + self.origin[0], g.y0 + i0 * g.cell + self.origin[1],
                                     nc, nr, g.cell, layers=self._mesh_layers)
            ramp = np.minimum(np.arange(nr), np.arange(nr)[::-1])[:, None]
            ramp = np.minimum(ramp, np.minimum(np.arange(nc), np.arange(nc)[::-1])[None, :])
            taper = _smoothstep(0.0, float(BLOCK_TAPER_CELLS), ramp.astype(np.float32))
            hb = (hb + taper * ((ph - self.origin[2]) - hb)).astype(np.float32)
            bgrid = _Grid(hb, g.x0 + j0 * g.cell, g.y0 + i0 * g.cell, g.cell)
            bgrid.alb = pa.astype(np.float32)
            self.timings["patch_ms"] = 1e3 * (time.perf_counter() - t)
        xyz = np.stack([g.x0 + J * g.cell, g.y0 + I * g.cell, hb], axis=-1).reshape(-1, 3).astype(np.float64)
        old = self.blocks.get(agency)
        # Third-person colours (normals, fill, texture) are made on the first
        # third_person() call (_tp_colors), from the surface as it is now:
        # first-person frames never read them.
        blk = dict(i0=i0, i1=i1, j0=j0, j1=j1, cx=lx, cy=ly, xyz=xyz, grid=bgrid, nc=nc, nr=nr,
                   tp_blocks=self.surface.blocks)
        pd = _polydata(xyz, np.zeros((0, 3), dtype=np.int64))
        # Third-person colours live in a texture with one texel per vertex,
        # so a lamp update re-uploads the texture, not the whole mesh.
        tu = (np.arange(nc) + 0.5) / nc
        tv = (np.arange(nr) + 0.5) / nr
        TU, TV = np.meshgrid(tu, tv)
        pd.GetPointData().SetTCoords(vnp.numpy_to_vtk(np.column_stack([TU.ravel(), TV.ravel()]).astype(np.float32), deep=True))
        blk["pd"] = pd
        fp_m = vtk.vtkPolyDataMapper()
        fp_m.SetInputData(pd)
        fp_m.ScalarVisibilityOff()
        blk["fp_actor"] = vtk.vtkActor()
        blk["fp_actor"].SetMapper(fp_m)
        tp_m = vtk.vtkPolyDataMapper()
        tp_m.SetInputData(pd)
        tp_m.ScalarVisibilityOff()
        blk["tp_actor"] = vtk.vtkActor()
        blk["tp_actor"].SetMapper(tp_m)
        blk["tp_actor"].GetProperty().LightingOff()
        blk["tp_actor"].GetProperty().SetColor(1.0, 1.0, 1.0)
        blk["lit"] = np.zeros(0, dtype=np.int64)
        if old is not None:
            self._fp_ren.RemoveActor(old["fp_actor"])
            self._tp_ren.RemoveActor(old["tp_actor"])
        self.blocks[agency] = blk
        # The ground changed under every lamp that reaches this square.
        self._set_surface()
        self._fp_ren.AddActor(blk["fp_actor"])
        self._tp_ren.AddActor(blk["tp_actor"])
        self._rebuild_topology()
        self._lamp_colors_dirty = True

    def _rebuild_topology(self):
        """Fine blocks own their cells in agency order; coarse covers the rest; stitch the seams."""
        g = self.grid
        K = COARSE_STEP
        order = sorted(self.blocks)

        def in_block(b, i, j):
            return (i >= b["i0"]) & (i < b["i1"]) & (j >= b["j0"]) & (j < b["j1"])

        def in_any(i, j):
            m = np.zeros(np.shape(i), dtype=bool)
            for a in order:
                m |= in_block(self.blocks[a], i, j)
            return m

        for k, a in enumerate(order):
            b = self.blocks[a]
            nr, nc = b["i1"] - b["i0"] + 1, b["j1"] - b["j0"] + 1
            I, J = np.meshgrid(np.arange(b["i0"], b["i1"]), np.arange(b["j0"], b["j1"]), indexing="ij")
            owned = np.ones(I.shape, dtype=bool)
            for a2 in order[:k]:
                owned &= ~in_block(self.blocks[a2], I, J)
            tris = [_grid_tris(nr, nc, owned)]
            # Stitch fans where an owned edge cell borders coarse ground.
            def vid(i, j):
                return (i - b["i0"]) * nc + (j - b["j0"])
            for side in ("s", "n", "w", "e"):
                if side in ("s", "n"):
                    i_in = b["i0"] if side == "s" else b["i1"] - 1
                    i_out = i_in - 1 if side == "s" else i_in + 1
                    i_v = b["i0"] if side == "s" else b["i1"]
                    if i_out < 0 or i_out >= g.H - 1:
                        continue
                    for js in range(b["j0"], b["j1"], K):
                        je = min(js + K, b["j1"])
                        if owned[i_in - b["i0"], js - b["j0"]] and not in_any(i_out, js):
                            vs = [vid(i_v, j) for j in range(js, je + 1)]
                            tris.append(np.array([[vs[0], vs[t], vs[t + 1]] for t in range(1, len(vs) - 1)], dtype=np.int64).reshape(-1, 3))
                else:
                    j_in = b["j0"] if side == "w" else b["j1"] - 1
                    j_out = j_in - 1 if side == "w" else j_in + 1
                    j_v = b["j0"] if side == "w" else b["j1"]
                    if j_out < 0 or j_out >= g.W - 1:
                        continue
                    for is_ in range(b["i0"], b["i1"], K):
                        ie = min(is_ + K, b["i1"])
                        if owned[is_ - b["i0"], j_in - b["j0"]] and not in_any(is_, j_out):
                            vs = [vid(i, j_v) for i in range(is_, ie + 1)]
                            tris.append(np.array([[vs[0], vs[t], vs[t + 1]] for t in range(1, len(vs) - 1)], dtype=np.int64).reshape(-1, 3))
            _set_tris(b["pd"], np.concatenate(tris))

        ci, cj = self._ci, self._cj
        CI, CJ = np.meshgrid(ci[:-1], cj[:-1], indexing="ij")
        keep = ~in_any(CI, CJ)
        _set_tris(self._coarse_pd, _grid_tris(len(ci), len(cj), keep))

    def _tp_colors(self, blk):
        """A fine block's third-person colours: vertex normals, albedo, fill,
        and the texture holding them, from the surface the block was built on."""
        if "tex_np" in blk:
            return
        g, xyz, bgrid = self.grid, blk["xyz"], blk["grid"]
        if bgrid is not None:
            blk["n"] = bgrid.height_normal(xyz[:, 0], xyz[:, 1])[1]
            blk["albedo"] = bgrid.alb.ravel()
        else:
            blk["n"] = g.normal(xyz[:, 0], xyz[:, 1])
            blk["albedo"] = self._albedo_at(xyz, blk["tp_blocks"])
        blk["fill"] = self._fill_rgb(xyz, blk["n"], blk["tp_blocks"])
        blk["base"] = self._to_u8(blk["fill"])
        img = vtk.vtkImageData()
        img.SetDimensions(blk["nc"], blk["nr"], 1)
        img.GetPointData().SetScalars(vnp.numpy_to_vtk(blk["base"].copy(), deep=True))
        blk["tex_img"] = img
        blk["tex_np"] = vnp.vtk_to_numpy(img.GetPointData().GetScalars())  # shares memory with img
        tex = vtk.vtkTexture()
        tex.SetInputData(img)
        tex.InterpolateOn()
        tex.EdgeClampOn()
        blk["tp_actor"].SetTexture(tex)

    def _update_lamp_colors(self):
        for b in self.blocks.values():
            self._tp_colors(b)
        if not self._lamp_colors_dirty:
            return
        # Lamp light only reaches the fine blocks: each block extends at least
        # LAMP_FADE_START_M past its rover, and beyond that the lamp has faded.
        lamps = [self._lamp(a) for a in self.poses]
        for b in self.blocks.values():
            tex = b["tex_np"]
            tex[b["lit"]] = b["base"][b["lit"]]  # clear last frame's pool
            xyz = b["xyz"]
            E = np.zeros(len(xyz), dtype=np.float32)
            for lamp in lamps:
                E += lamp.irradiance(xyz, b["n"])
            lit = np.flatnonzero(E > 0)
            if len(lit):
                # Same tone curve as first person, before gamma, so the pool rolls off instead of clipping.
                lin = _tone_lin(b["albedo"][lit] * E[lit] * np.float32(1.0 / self.e_ref))
                tex[lit] = self._to_u8(b["fill"][lit] + lin[:, None] * LAMP_RGB[None, :])
            b["lit"] = lit
            b["tex_img"].GetPointData().GetScalars().Modified()
            b["tex_img"].Modified()
        self._lamp_colors_dirty = False

    def _to_u8(self, lin):
        return (self._gamma(lin) * 255.0 + 0.5).astype(np.uint8)

    def _place_rover_actor(self, agency):
        if agency not in self._rover_actors:
            pair = []
            for ren in (self._fp_ren, self._tp_ren):
                m = vtk.vtkPolyDataMapper()
                m.SetInputData(self._rover_pd)
                m.ScalarVisibilityOff()
                a = vtk.vtkActor()
                a.SetMapper(m)
                a.GetProperty().SetColor(*AGENCY_RGB.get(agency, (0.8, 0.8, 0.8)))
                a.GetProperty().SetAmbient(0.25)
                ren.AddActor(a)
                pair.append(a)
            self._rover_actors[agency] = tuple(pair)
        x, y, hd = self.poses[agency]
        lx, ly = x - self.origin[0], y - self.origin[1]
        gz = float(self.grid.height(lx, ly))
        for a in self._rover_actors[agency]:
            a.SetPosition(lx, ly, gz)
            a.SetOrientation(0, 0, math.degrees(hd))

    # Third-person overlays ------------------------------------------------
    def _drape(self, xy, lift=DRAPE_M, step=0.5):
        """World (K, 2) polyline -> local (M, 3) points following the ground."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2) - self.origin[:2]
        if len(xy) >= 2:
            out = [xy[:1]]
            for a, b in zip(xy[:-1], xy[1:]):
                n = max(1, int(math.ceil(np.hypot(*(b - a)) / step)))
                t = (np.arange(1, n + 1) / n)[:, None]
                out.append(a + (b - a) * t)
            xy = np.concatenate(out)
        z = self.grid.height(xy[:, 0], xy[:, 1]) + lift
        return np.column_stack([xy, z])

    def _line_actor(self, pts, rgb, width=3.0, opacity=1.0):
        pd = vtk.vtkPolyData()
        vp = vtk.vtkPoints()
        vp.SetData(vnp.numpy_to_vtk(np.ascontiguousarray(pts, dtype=np.float32), deep=True))
        pd.SetPoints(vp)
        line = vtk.vtkPolyLine()
        line.GetPointIds().SetNumberOfIds(len(pts))
        for k in range(len(pts)):
            line.GetPointIds().SetId(k, k)
        cells = vtk.vtkCellArray()
        cells.InsertNextCell(line)
        pd.SetLines(cells)
        m = vtk.vtkPolyDataMapper()
        m.SetInputData(pd)
        a = vtk.vtkActor()
        a.SetMapper(m)
        p = a.GetProperty()
        p.SetColor(*rgb)
        p.SetLineWidth(width)
        p.SetOpacity(opacity)
        p.LightingOff()
        return a

    def _sphere_actor(self, c, r, rgb):
        s = vtk.vtkSphereSource()
        s.SetCenter(*c)
        s.SetRadius(r)
        s.SetThetaResolution(20)
        s.SetPhiResolution(12)
        m = vtk.vtkPolyDataMapper()
        m.SetInputConnection(s.GetOutputPort())
        a = vtk.vtkActor()
        a.SetMapper(m)
        a.GetProperty().SetColor(*rgb)
        a.GetProperty().SetAmbient(0.5)
        return a

    def _ring(self, x, y, r, n=96):
        t = np.linspace(0, 2 * math.pi, n + 1)
        return self._drape(np.column_stack([x + r * np.cos(t), y + r * np.sin(t)]), lift=DRAPE_M + 0.05, step=10.0)

    def _overlay_actors(self, agency, ov):
        acts = []
        col = AGENCY_RGB.get(agency, (0.8, 0.8, 0.8))
        if ov.get("odom_track") is not None and len(ov["odom_track"]) >= 2:
            acts.append(self._line_actor(self._drape(ov["odom_track"]), (1.0, 0.55, 0.25), 3.0, 0.9))
        if ov.get("true_track") is not None and len(ov["true_track"]) >= 2:
            acts.append(self._line_actor(self._drape(ov["true_track"]), (0.95, 0.95, 0.95), 3.0))
        if ov.get("estimate") is not None:
            ex, ey = ov["estimate"]
            c = self._drape([[ex, ey]], lift=0.35)[0]
            acts.append(self._sphere_actor(c, 0.25, col))
            if ov.get("sigma") is not None and ov["sigma"] > 0:
                acts.append(self._line_actor(self._ring(ex, ey, float(ov["sigma"])), col, 4.0))
        for f in ov.get("finds") or []:
            rgb = STATUS_RGB.get(str(f.get("status", "reported")).lower(), (0.9, 0.9, 0.9))
            base = self._drape([[f["x"], f["y"]]], lift=0.0)[0]
            acts.append(self._line_actor(np.array([base, base + [0, 0, 3.0]]), rgb, 4.0))
            acts.append(self._sphere_actor(base + [0, 0, 3.0], 0.3, rgb))
            if f.get("sigma"):
                acts.append(self._line_actor(self._ring(f["x"], f["y"], float(f["sigma"])), rgb, 2.0, 0.8))
        return acts

    def _label_third_person(self, img, agency, ov):
        img = np.ascontiguousarray(img)
        font = cv2.FONT_HERSHEY_SIMPLEX
        h, w = img.shape[:2]

        def text(s, org, scale=0.6, rgb=(235, 235, 235), thick=1):
            # Drop shadow at the same thickness: OpenCV's glyph spacing grows
            # with thickness, so a thicker outline would not line up.
            cv2.putText(img, s, (org[0] + 1, org[1] + 1), font, scale, (0, 0, 0), thick, cv2.LINE_AA)
            cv2.putText(img, s, (org[0] + 2, org[1] + 2), font, scale, (0, 0, 0), thick, cv2.LINE_AA)
            cv2.putText(img, s, org, font, scale, rgb, thick, cv2.LINE_AA)

        col = tuple(int(255 * c) for c in AGENCY_RGB.get(agency, (0.8, 0.8, 0.8)))
        text(f"Rover {agency}  chase view", (14, 30), 0.75, col, 2)
        text("Blue-grey fill light is for viewers only. The rover sees by its own lamp.", (14, h - 14), 0.5, (190, 200, 225))
        y = 56
        for key, label, rgb in (("true_track", "true track", (242, 242, 242)),
                                ("odom_track", "odometry", (255, 140, 64)),
                                ("sigma", "estimate and 1-sigma", col)):
            if ov.get(key) is not None:
                cv2.line(img, (16, y - 5), (40, y - 5), rgb, 3, cv2.LINE_AA)
                text(label, (48, y), 0.5)
                y += 22
        return img


# ---------------------------------------------------------------------------
# Terrain loading and a stand-in grid.
# ---------------------------------------------------------------------------
def load_any_terrain(path=None):
    """(height, craters, x0, y0, cell): step 2's file if given, else the stand-in."""
    if path is None:
        return standin_terrain()
    try:
        from sim.terrain import load_terrain  # step 2, once merged
        return load_terrain(path)
    except ImportError:
        z = np.load(path)
        return z["height"], z["craters"], float(z["x0"]), float(z["y0"]), float(z["cell"])


def standin_terrain(seed=3, n=4000, cell=0.25):
    """A stand-in in step 2's format until data/terrain/*.npz exists.

    Gentle slope, centimetre roughness, bowl craters 2 to 12 m wide and up to
    1 m deep (scene brief), several placed along the scripted drive. All of
    it is assumed, not from a source.
    """
    rng = np.random.default_rng(seed)
    x = np.arange(n, dtype=np.float32) * cell
    h = (0.02 * x[None, :] + 0.01 * x[:, None]).astype(np.float32)  # assumed 2% east, 1% north slope
    rough = rng.standard_normal((n, n)).astype(np.float32)
    h += 0.02 * cv2.GaussianBlur(rough, (0, 0), 2.0) / 0.14  # assumed ~2 cm roughness at ~0.5 m scales
    h += 0.15 * cv2.GaussianBlur(rng.standard_normal((n, n)).astype(np.float32), (0, 0), 40.0) / 0.007  # gentle swells
    craters = []
    # Craters along the scripted drive (y = 500 m, x from 100 m east).
    for k, (dx, dy, d) in enumerate([(12, 1.5, 6.0), (24, -3.0, 9.0), (37, 2.0, 4.0), (50, -1.0, 11.0),
                                     (64, 3.5, 7.0), (76, -2.5, 5.0), (90, 0.5, 8.0)]):
        craters.append((100 + dx, 500 + dy, d))
    for _ in range(600):
        craters.append((rng.uniform(20, n * cell - 20), rng.uniform(20, n * cell - 20), rng.uniform(2.0, 12.0)))
    out = []
    for cx, cy, d in craters:
        R = d / 2
        depth = min(1.0, 0.12 * d)  # assumed depth/diameter 0.12, capped at 1 m
        r_box = int(math.ceil(1.8 * R / cell))
        ci, cj = int(cy / cell), int(cx / cell)
        i0, i1 = max(0, ci - r_box), min(n, ci + r_box + 1)
        j0, j1 = max(0, cj - r_box), min(n, cj + r_box + 1)
        Y, X = np.meshgrid(np.arange(i0, i1) * cell, np.arange(j0, j1) * cell, indexing="ij")
        r = np.hypot(X - cx, Y - cy) / R
        bowl = np.where(r < 1, -depth * (1 - r * r), 0.0)
        rim = 0.12 * depth * np.exp(-((r - 1.0) / 0.25) ** 2)  # assumed raised rim
        h[i0:i1, j0:j1] += (bowl + rim).astype(np.float32)
        out.append((cx, cy, d))
    return h, np.array(out), 0.0, 0.0, cell


# ---------------------------------------------------------------------------
# Scripted drive: python -m sim.scene [--terrain file.npz] [--out drive.mp4]
# ---------------------------------------------------------------------------
MAIN_TERRAIN = "C:/Monad-Hackathon-Project/data/terrain/shoemaker.npz"  # step 2 output, git-ignored


def _scripted_drive(start, frames, step_m=0.5):
    """Rover A's true poses: a gentle S-curve heading east from `start`."""
    poses = []
    x, y = start
    for k in range(frames):
        hd = 0.12 * math.sin(k / 25.0)
        x += step_m * math.cos(hd)
        y += step_m * math.sin(hd)
        poses.append((x, y, hd))
    return poses


def main():
    import argparse
    import os
    ap = argparse.ArgumentParser(description="Render a short scripted drive to video (both views of rover A).")
    ap.add_argument("--terrain", default=MAIN_TERRAIN if os.path.exists(MAIN_TERRAIN) else None,
                    help="terrain .npz from step 2 (default: shoemaker.npz if present, else the stand-in)")
    ap.add_argument("--out", default="frames/scene_drive.mp4")
    ap.add_argument("--frames", type=int, default=160)
    ap.add_argument("--display", choices=DISPLAY_MODES, default="full",
                    help="display layer: full (lips, rocks, texture), detail (rocks, texture), off (sim grid)")
    args = ap.parse_args()

    t = time.perf_counter()
    height, craters, x0, y0, cell = load_any_terrain(args.terrain)
    print(f"terrain {args.terrain or 'stand-in'} {height.shape} in {time.perf_counter() - t:.1f}s")
    t = time.perf_counter()
    scene = Scene(height, craters, x0, y0, cell, display=args.display)
    print(f"scene built in {time.perf_counter() - t:.1f}s")
    start = (x0 + 500.0, y0 + 500.0) if args.terrain else (100.0, 500.0)
    poses = _scripted_drive(start, args.frames)
    # Rover B parked ahead and to the left, lamp facing back across A's path.
    scene.set_rover("B", (start[0] + 55.0, start[1] + 22.0, -2.6))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fw, fh = FP_SIZE
    tw, th = TP_SIZE
    vw = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), 10, (tw + fw, th))
    rng = np.random.default_rng(1)
    times, true_track, odom_track = [], [], []
    ox, oy = start
    for k, pose in enumerate(poses):
        t0 = time.perf_counter()
        scene.set_rover("A", pose)
        img, depth = scene.first_person("A")
        t1 = time.perf_counter()
        # Stand-in overlays: odometry with 2% scale error (ShadowNav sec. 1 noise level),
        # an estimate near the truth and a sigma that grows then shrinks.
        prev = poses[k - 1] if k else (start[0], start[1], 0.0)
        ds = math.hypot(pose[0] - prev[0], pose[1] - prev[1]) * 1.02
        ox += ds * math.cos(pose[2] + 0.03)
        oy += ds * math.sin(pose[2] + 0.03)
        true_track.append(pose[:2])
        odom_track.append((ox, oy))
        sigma = 0.6 + 1.2 * abs(math.sin(k / 40.0))
        est = (pose[0] + rng.normal(0, 0.3), pose[1] + rng.normal(0, 0.3))
        tp = scene.third_person("A", {"true_track": np.array(true_track), "odom_track": np.array(odom_track),
                                      "estimate": est, "sigma": sigma})
        t2 = time.perf_counter()
        times.append((t1 - t0, t2 - t1))
        frame = np.zeros((th, tw + fw, 3), np.uint8)
        frame[:, :tw] = tp
        frame[(th - fh) // 2:(th - fh) // 2 + fh, tw:] = img
        cv2.putText(frame, "Rover A camera: lamp only", (tw + 10, (th - fh) // 2 + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
        vw.write(np.ascontiguousarray(frame[..., ::-1]))
        if k % 40 == 0:
            cv2.imwrite(args.out.replace(".mp4", f"_f{k:03d}.png"), frame[..., ::-1])
            np.save(args.out.replace(".mp4", f"_f{k:03d}_depth.npy"), depth)
    vw.release()
    ts = np.array(times[1:]) * 1e3
    print(f"{len(times)} frames -> {args.out}")
    print(f"first person ms (incl. set_rover): median {np.median(ts[:, 0]):.0f}, p95 {np.percentile(ts[:, 0], 95):.0f}, max {ts[:, 0].max():.0f}")
    print(f"third person ms: median {np.median(ts[:, 1]):.0f}, p95 {np.percentile(ts[:, 1], 95):.0f}, max {ts[:, 1].max():.0f}")


if __name__ == "__main__":
    main()
