"""Display layer for the terrain: what the judges see, not what the sim uses.

The simulation keeps the 0.25 m grid and crater list from sim/terrain.py
unchanged. This file adds detail that exists only for drawing:

  detail tile   tileable 8 m square of cm-scale regolith relief (height,
                normal, albedo), repeated over the ground
  albedo        brightness factor per 0.25 m cell: large-scale mottling and
                bright halos around a fraction of fresh craters
  rim lips      a narrow raised lip on every listed crater rim, so the lamp
                draws a crisp shadow line where the sim's crater is
  rocks         ~27k rocks 0.1-0.6 m, background and on some crater rims
  backdrop      the rest of the LOLA Shoemaker tile (20 km, real relief)
                around the 1 km drive area, at 20 m, for the horizon
  reflectance   Hapke parameters for "old highland" (Xu et al. 2020)

Everything made up is a named parameter marked ASSUMED. Real data: the LOLA
backdrop only. Nothing here is measured detail, and the pitch must not call
it that.

One knob: `strength` (0 = the bare sim grid, 1 = default). It scales every
made-up height, the albedo contrast and the number of rocks drawn, so the
vision step can turn extra edges down in one place.

Usage:
    python sim/terrain_display.py build     # writes data/terrain/display/
Then sim/terrain_look.py renders it.

Asset format: see FORMAT below, written to display/display.json.

Sources:
  [C23] Cauligi et al. 2023, ShadowNav, section 4A (Hapke with "old
        highland" parameters from [26]; surface texture added to the DEM)
  [X20] Xu et al. 2020, Remote Sensing 12(22) 3676, = [C23] ref [26].
        Hapke model Eqs. (5)-(6) p. 6, (7)-(12) p. 7, simplifications
        in Table 3 p. 7; old-highland bounds in Table 1 p. 3, which Xu
        takes from Sato et al. 2014 LROC WAC fits (p. 2). PDF in
        docs/sources/Xu-2020-Hapke-CE1-IIM.pdf.
  [A24] Atha et al. 2024, ShadowNav, sections IV and V (camera, lamp)
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.interpolate import RectBivariateSpline

import terrain as T

OUT = T.TERRAIN_DIR / "display"


@dataclass
class DisplayParams:
    strength: float = 1.0  # the one knob, see module docstring

    # --- detail tile (all ASSUMED; no source measures this site at cm scale)
    tile_period_m: float = 8.0      # tile repeats every 8 m
    tile_px: int = 2048             # 3.9 mm texels
    tile_cut_m: float = 0.5         # only wavelengths under 0.5 m (2 grid cells); the grid owns the rest
    tile_beta: float = 4.0          # same spectral slope as sim/terrain.py roughness
    tile_rms_m: float = 0.006       # RMS of the fractal part
    craterlet_dmin_m: float = 0.05
    craterlet_dmax_m: float = 0.6
    craterlet_n_ge10cm_per_m2: float = 0.6
    craterlet_dd: float = 0.12      # depth/diameter, inside Table 1's range [C23 Table 1: 0.033-0.125]
    pebble_dmin_m: float = 0.01
    pebble_dmax_m: float = 0.09
    pebble_n_ge2cm_per_m2: float = 12.0
    pebble_h_frac: float = 0.45     # pebble height / diameter
    tile_albedo_rms: float = 0.10   # fine albedo speckle
    pebble_albedo: float = 1.25     # pebbles brighter than the soil

    # --- rocks (ASSUMED)
    rock_dmin_m: float = 0.10
    rock_dmax_m: float = 0.60
    rock_sfd_b: float = 2.5         # cumulative N(>D) ~ D^-b
    rock_n_ge10cm_per_m2: float = 0.02
    rim_rock_crater_dmin_m: float = 5.0
    rim_rock_frac: float = 0.3      # share of those craters with a blocky rim
    rim_rocks_per_m_diam: float = 2.0
    rock_h_frac: float = 0.55       # height / long axis
    rock_sink_frac: float = 0.25    # share of height buried
    rock_albedo: float = 1.3

    # --- rim lips (ASSUMED)
    lip_width_frac: float = 0.06    # Gaussian half-width, fraction of radius
    lip_height_frac: float = 0.08   # fraction of crater depth

    # --- albedo map (ASSUMED)
    albedo_cell_m: float = 0.25     # one texel per sim cell
    mottle_rms: float = 0.06
    mottle_min_wavelength_m: float = 4.0
    fresh_frac: float = 0.15        # share of craters with an immature (bright) halo
    fresh_gain: float = 0.30        # peak brightening at the rim
    fresh_extent_r: float = 2.5     # halo fades out by this many radii

    # --- backdrop
    backdrop_step_px: int = 4       # LOLA 5 m pixels -> 20 m, whole tile
    backdrop_near_half_px: int = 500  # native 5 m LOLA within 2.5 km of the drive area centre
    far_tile_px: int = 1024         # backdrop texture tile, ASSUMED size
    far_tile_cell_m: float = 0.5    # 512 m period

    # --- reflectance [X20] Table 1 p. 3, "old highland" row: w > 0.48,
    # b < 0.232, Bs0 < 1.5867, hs > 0.0626. Table 1 gives bounds only, so
    # these are the bound values (ASSUMED choice of point in the range).
    # K = 1, CBOE off, roughness off, as in [X20] Table 3 p. 7.
    hapke_w: float = 0.48
    hapke_b: float = 0.232
    hapke_bs0: float = 1.5867
    hapke_hs: float = 0.0626

    seed: int = 11


FORMAT = """\
display.json   parameters, sources, file list, and how to combine the layers
detail_height.npy  (N, N) float32 metres at strength 1, tileable, period
                   tile_period_m, texel [i, j] at world ((j+.5)p/N, (i+.5)p/N)
                   mod period (rows north). Wavelengths < tile_cut_m only.
detail_height.png  same, uint16, metres = png * png_scale + png_offset
detail_normal.png  same, RGB tangent-space normal (x east, y north, z up),
                   value = n * 127.5 + 127.5
detail_albedo.png  same, uint8, factor = png / 128
albedo.png     (4000, 4000) uint8 factor = png / 128, one texel per sim cell,
               rows north (row 0 = south edge, unlike most image viewers)
rocks.npy      (K, 7) float32 [x, y, long_axis_m, short_axis_m, height_m,
               yaw_rad, seed]; draw the first round(strength * K)
lips.npy       (N, 2) float32 [lip_height_m, lip_width_m] per crater row
backdrop.npz   height (H, W) float32 (rows north), x0, y0, cell: LOLA 5 m
               averaged to 20 m, whole 20 km tile, world frame
backdrop_near.npz  same keys, native LOLA 5 m, 5 km square centred on the
               drive area. Draw near over far, drive area over near (drive
               area bounds in json); lower each backdrop 0.5 m so seams hide.
               For horizon realism lower by r^2 / (2 moon_radius_m).
far_detail_height.npy  (1024, 1024) float32 metres, tileable, 0.5 m texels,
               period 512 m: made-up sub-5 m relief (the sim's own recipe)
               to add to both backdrops, times strength, so they shade like
               the drive area. Not for the drive area itself.
Combine, at strength s:
  height  = sim grid (bicubic) + s * (detail tile + rim lips + rocks)
  albedo  = (1 + s*(albedo-1)) * (1 + s*(detail_albedo-1)), rocks rock_albedo
  shading = Hapke [X20] Eq. (5)-(12) with the hapke_* parameters
"""


# --------------------------------------------------------------------------
# Hapke [X20] Eqs. (5)-(12), K = 1, no CBOE, no roughness.
# --------------------------------------------------------------------------

def _H(x, w):
    r0 = (1 - np.sqrt(1 - w)) / (1 + np.sqrt(1 - w))          # Eq. (12)
    x = np.maximum(x, 1e-6)
    return 1.0 / (1 - w * x * (r0 + (1 - 2 * r0 * x) / 2 * np.log((1 + x) / x)))  # Eq. (11)


def hapke_radf(mu0, mu, g, p: DisplayParams = DisplayParams()):
    """Radiance factor I/F for incidence cos mu0, emission cos mu, phase g (rad)."""
    w, b, bs0, hs = p.hapke_w, p.hapke_b, p.hapke_bs0, p.hapke_hs
    c = 3.29 * np.exp(-17.4 * b * b) - 0.98                    # Eq. (8), as printed
    cg = np.cos(g)
    pg = ((1 + c) / 2 * (1 - b * b) / (1 - 2 * b * cg + b * b) ** 1.5
          + (1 - c) / 2 * (1 - b * b) / (1 + 2 * b * cg + b * b) ** 1.5)  # Eq. (7)
    bsg = 1.0 / (1 + np.tan(g / 2) / hs)                       # Eq. (9)
    mu0 = np.clip(mu0, 0, 1)
    mu = np.clip(mu, 1e-4, 1)
    M = _H(mu0, w) * _H(mu, w) - 1                             # Eq. (10), K = 1
    return w / 4 * mu0 / (mu0 + mu) * (pg * (1 + bs0 * bsg) + M)  # Eq. (5)


# --------------------------------------------------------------------------
# Detail tile.
# --------------------------------------------------------------------------

def _powerlaw(n, dmin, dmax, b, rng):
    u = rng.random(n)
    a, z = dmin ** -b, dmax ** -b
    return (a - u * (a - z)) ** (-1.0 / b)


def _stamp_wrap(grid, cx, cy, reach_px, fn):
    """Add fn(dx_m_grid, dy_m_grid) around pixel (cx, cy), wrapping edges."""
    n = grid.shape[0]
    i = np.arange(int(cy - reach_px), int(cy + reach_px) + 2)
    j = np.arange(int(cx - reach_px), int(cx + reach_px) + 2)
    dy, dx = (i + 0.5 - cy), (j + 0.5 - cx)
    grid[np.ix_(i % n, j % n)] += fn(dx[None, :], dy[:, None])


def build_tile(p: DisplayParams, rng):
    n, P = p.tile_px, p.tile_period_m
    px = P / n
    h = T._fractal_noise(n, px, p.tile_beta, rng, k_lo=1.0 / p.tile_cut_m) * p.tile_rms_m
    alb = 1.0 + p.tile_albedo_rms * T._fractal_noise(n, px, 2.0, rng)
    area = P * P
    # Craterlets: parabolic bowls with a small rim, same shape as sim craters.
    nc = rng.poisson(p.craterlet_n_ge10cm_per_m2 * area * (p.craterlet_dmin_m / 0.1) ** -2.0)
    for d in _powerlaw(nc, p.craterlet_dmin_m, p.craterlet_dmax_m, 2.0, rng):
        R = d / 2 / px
        dep = p.craterlet_dd * d
        def bowl(dx, dy, R=R, dep=dep):
            rho = np.hypot(dx, dy) / R
            inside = 0.2 * dep - dep * (1 - rho ** 2)
            outside = 0.2 * dep * np.maximum(rho, 1) ** -3 * (rho < 2.5)
            return np.where(rho <= 1, inside, outside)
        _stamp_wrap(h, *rng.uniform(0, n, 2), 2.5 * R, bowl)
    # Pebbles: rounded caps, brighter than the soil.
    npb = rng.poisson(p.pebble_n_ge2cm_per_m2 * area * (p.pebble_dmin_m / 0.02) ** -2.5)
    for d in _powerlaw(npb, p.pebble_dmin_m, p.pebble_dmax_m, 2.5, rng):
        R = d / 2 / px
        hh = p.pebble_h_frac * d
        cx, cy = rng.uniform(0, n, 2)
        cap = lambda dx, dy, R=R, hh=hh: hh * np.sqrt(np.clip(1 - (dx * dx + dy * dy) / R ** 2, 0, 1))
        _stamp_wrap(h, cx, cy, R, cap)
        mask = lambda dx, dy, R=R: (p.pebble_albedo - 1) * ((dx * dx + dy * dy) < R ** 2)
        _stamp_wrap(alb, cx, cy, R, mask)
    h -= h.mean()
    return h.astype(np.float32), alb.astype(np.float32)


def tile_normal(h, px):
    gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) / (2 * px)
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) / (2 * px)
    n = np.stack([-gx, -gy, np.ones_like(h)], -1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


# --------------------------------------------------------------------------
# World layers.
# --------------------------------------------------------------------------

def build_rocks(craters, x0, y0, size_m, p: DisplayParams, rng):
    area = size_m * size_m
    nb = rng.poisson(p.rock_n_ge10cm_per_m2 * area * (p.rock_dmin_m / 0.1) ** -p.rock_sfd_b)
    xs = list(rng.uniform(x0, x0 + size_m, nb))
    ys = list(rng.uniform(y0, y0 + size_m, nb))
    ds = list(_powerlaw(nb, p.rock_dmin_m, p.rock_dmax_m, p.rock_sfd_b, rng))
    big = craters[craters[:, 2] >= p.rim_rock_crater_dmin_m]
    blocky = big[rng.random(len(big)) < p.rim_rock_frac]
    for cx, cy, D in blocky:
        k = rng.poisson(p.rim_rocks_per_m_diam * D)
        th = rng.uniform(0, 2 * np.pi, k)
        r = D / 2 * (0.95 + 0.6 * rng.random(k) ** 2)  # crowd the rim
        xs += list(cx + r * np.cos(th)); ys += list(cy + r * np.sin(th))
        ds += list(_powerlaw(k, p.rock_dmin_m, p.rock_dmax_m, p.rock_sfd_b - 0.7, rng))
    xs, ys, ds = map(np.asarray, (xs, ys, ds))
    k = len(xs)
    short = ds * rng.uniform(0.55, 1.0, k)
    hgt = p.rock_h_frac * short * rng.uniform(0.7, 1.2, k)
    rocks = np.column_stack([xs, ys, ds, short, hgt, rng.uniform(0, np.pi, k),
                             rng.integers(0, 2**20, k)]).astype(np.float32)
    keep = ((rocks[:, 0] > x0) & (rocks[:, 0] < x0 + size_m)
            & (rocks[:, 1] > y0) & (rocks[:, 1] < y0 + size_m))
    rocks = rocks[keep]
    return rocks[rng.permutation(len(rocks))]  # random order: the knob thins uniformly


def build_albedo(craters, depth, n, cell, x0, y0, p: DisplayParams, rng):
    a = 1.0 + p.mottle_rms * T._fractal_noise(n, cell, 3.0, rng,
                                              k_hi=1.0 / p.mottle_min_wavelength_m)
    fresh = rng.random(len(craters)) < p.fresh_frac
    for (x, y, D) in craters[fresh]:
        R = D / 2
        reach = p.fresh_extent_r * R
        j0, j1 = max(int((x - x0 - reach) / cell), 0), min(int((x - x0 + reach) / cell) + 2, n)
        i0, i1 = max(int((y - y0 - reach) / cell), 0), min(int((y - y0 + reach) / cell) + 2, n)
        xx = x0 + np.arange(j0, j1) * cell - x
        yy = y0 + np.arange(i0, i1) * cell - y
        rho = np.hypot(xx[None, :], yy[:, None]) / R
        halo = np.where(rho <= 1, 0.6 + 0.4 * rho ** 2,
                        np.clip(1 - (rho - 1) / (p.fresh_extent_r - 1), 0, 1) ** 2)
        a[i0:i1, j0:j1] += p.fresh_gain * halo
    return a.astype(np.float32)


def build_far_tile(p: DisplayParams, rng):
    """Tileable texture for the LOLA backdrop: the sim's own sub-5 m recipe
    (sim/terrain.py roughness and 2.3-20 m craters, same parameters), so the
    backdrop's shading statistics match the drive area. Made up, display only."""
    tp = T.Params()
    n, c = p.far_tile_px, p.far_tile_cell_m
    P = n * c
    h = T._fractal_noise(n, c, tp.rough_beta, rng, k_lo=1.0 / tp.rough_cutoff_m)
    h *= tp.rough_frac_of_toterr * tp.synthetic_toterr_m
    tp2 = T.Params(size_m=P)
    d = T.draw_diameters(tp2, rng)
    ratios = T.C23_TABLE1[:, 1] / T.C23_TABLE1[:, 0]
    dep = rng.choice(ratios, len(d)) * d
    for D_, de in zip(d, dep):
        R = D_ / 2 / c
        _stamp_wrap(h, *rng.uniform(0, n, 2), tp.ejecta_extent_r * R,
                    lambda dx, dy, R=R, de=de: T.crater_profile(np.hypot(dx, dy) / R, de, tp))
    return (h - h.mean()).astype(np.float32)


def build_backdrop(p: DisplayParams):
    z = T._read_tif("surf").astype(np.float64)
    s = p.backdrop_step_px
    H = (z.shape[0] // s) * s
    zb = z[:H, :H].reshape(H // s, s, H // s, s).mean((1, 3))[::-1]  # rows north
    cell = T.LOLA_PX * s
    x0 = T.LOLA_X_LEFT + cell / 2
    y0 = T.LOLA_Y_TOP - T.LOLA_PX * H + cell / 2
    return zb.astype(np.float32), x0, y0, cell


def build_backdrop_near(p: DisplayParams):
    """Native 5 m LOLA in a window around the drive area, so the ground just
    past the drive area edge keeps real relief at the drive area's scale."""
    z = T._read_tif("surf")
    h = p.backdrop_near_half_px
    npx = int(round(T.Params().size_m / T.LOLA_PX))
    rc = T.Params().lola_row0 + npx // 2
    cc = T.Params().lola_col0 + npx // 2
    r0, c0 = rc - h, cc - h
    zb = z[r0:r0 + 2 * h, c0:c0 + 2 * h][::-1].astype(np.float32)  # rows north
    x0 = T.LOLA_X_LEFT + T.LOLA_PX * (c0 + 0.5)
    y0 = T.LOLA_Y_TOP - T.LOLA_PX * (r0 + 2 * h) + T.LOLA_PX / 2
    return zb, x0, y0, T.LOLA_PX


def build(terrain_path=None, p: DisplayParams | None = None, out=OUT):
    p = p or DisplayParams()
    rng = np.random.default_rng(p.seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    height, craters, x0, y0, cell = T.load_terrain(terrain_path)
    with np.load(terrain_path or T.default_path()) as f:
        depth = f["crater_depth"]
        meta = json.loads(str(f["meta"]))
    n = height.shape[0]
    size = n * cell

    th, ta = build_tile(p, rng)
    px = p.tile_period_m / p.tile_px
    np.save(out / "detail_height.npy", th)
    from PIL import Image
    lo, hi = float(th.min()), float(th.max())
    scale = (hi - lo) / 65535
    Image.fromarray(np.round((th - lo) / scale).astype(np.uint16)).save(out / "detail_height.png")
    nrm = tile_normal(th, px)
    Image.fromarray(np.clip(nrm * 127.5 + 127.5, 0, 255).astype(np.uint8)).save(out / "detail_normal.png")
    Image.fromarray(np.clip(ta * 128, 0, 255).astype(np.uint8)).save(out / "detail_albedo.png")

    rocks = build_rocks(craters, x0 - cell / 2, y0 - cell / 2, size, p, rng)
    np.save(out / "rocks.npy", rocks)
    lips = np.column_stack([p.lip_height_frac * depth, p.lip_width_frac * craters[:, 2] / 2])
    np.save(out / "lips.npy", lips.astype(np.float32))
    alb = build_albedo(craters, depth, n, cell, x0, y0, p, rng)
    Image.fromarray(np.clip(alb * 128, 0, 255).astype(np.uint8)).save(out / "albedo.png")

    files = ["detail_height.npy", "detail_height.png", "detail_normal.png", "detail_albedo.png",
             "albedo.png", "rocks.npy", "lips.npy"]
    drive = dict(x_min=x0 - cell / 2, x_max=x0 + (n - .5) * cell,
                 y_min=y0 - cell / 2, y_max=y0 + (n - .5) * cell)
    if meta["source"].startswith("lola"):
        bz, bx0, by0, bcell = build_backdrop(p)
        np.savez(out / "backdrop.npz", height=bz, x0=bx0, y0=by0, cell=bcell)
        nz, nx0, ny0, ncell = build_backdrop_near(p)
        np.savez(out / "backdrop_near.npz", height=nz, x0=nx0, y0=ny0, cell=ncell)
        files += ["backdrop.npz", "backdrop_near.npz"]
        np.save(out / "far_detail_height.npy", build_far_tile(p, rng))
        files.append("far_detail_height.npy")
    doc = dict(
        format=FORMAT, params=asdict(p), terrain=str(Path(terrain_path or T.default_path()).name),
        drive_area=drive, files=files, png_scale=scale, png_offset=lo,
        real=["backdrop.npz (LOLA Shoemaker 5 m, averaged to 20 m)", "sim grid base (LOLA)"],
        made_up=["detail tile", "albedo map", "rocks", "rim lips"],
        sources={"hapke": "Xu et al. 2020, Remote Sensing 12(22) 3676: Eqs. 5-6 p. 6, Eqs. 7-12 "
                          "and Table 3 p. 7, Table 1 p. 3 old highland bounds (from Sato et al. "
                          "2014); cited by Cauligi et al. 2023 sec. 4A as [26]"},
        moon_radius_m=1737400.0,  # ASSUMED: IAU mean radius, for horizon drop r^2/2R
    )
    (out / "display.json").write_text(json.dumps(doc, indent=1))
    return out


# --------------------------------------------------------------------------
# Sampling, for renderers written in Python.
# --------------------------------------------------------------------------

class Display:
    """Loads the display assets and builds fine height/albedo patches."""

    def __init__(self, terrain_path=None, display_dir=OUT, strength=None):
        self.height, self.craters, self.x0, self.y0, self.cell = T.load_terrain(terrain_path)
        d = Path(display_dir)
        self.doc = json.loads((d / "display.json").read_text())
        self.p = DisplayParams(**self.doc["params"])
        self.s = self.p.strength if strength is None else strength
        self.tile_h = np.load(d / "detail_height.npy")
        from PIL import Image
        self.tile_a = np.asarray(Image.open(d / "detail_albedo.png"), np.float32) / 128
        self.alb = np.asarray(Image.open(d / "albedo.png"), np.float32) / 128
        self.rocks = np.load(d / "rocks.npy")
        self.lips = np.load(d / "lips.npy")
        self.backdrop = dict(np.load(d / "backdrop.npz")) if (d / "backdrop.npz").exists() else None
        self.backdrop_near = (dict(np.load(d / "backdrop_near.npz"))
                              if (d / "backdrop_near.npz").exists() else None)
        self.far_tile = (np.load(d / "far_detail_height.npy")
                         if (d / "far_detail_height.npy").exists() else None)

    def _tile(self, arr, X, Y):
        n, P = arr.shape[0], self.p.tile_period_m
        u = (X % P) / P * n - 0.5
        v = (Y % P) / P * n - 0.5
        j, i = np.floor(u).astype(int), np.floor(v).astype(int)
        fu, fv = u - j, v - i
        j0, j1, i0, i1 = j % n, (j + 1) % n, i % n, (i + 1) % n
        a = arr[i0, j0] * (1 - fu) + arr[i0, j1] * fu
        b = arr[i1, j0] * (1 - fu) + arr[i1, j1] * fu
        return a * (1 - fv) + b * fv

    def patch(self, xmin, ymin, nx, ny, res, layers=("detail", "lips", "rocks")):
        """Height (ny, nx) float32 and albedo (ny, nx), rows north, at spacing res.
        Point [i, j] is at (xmin + j*res, ymin + i*res)."""
        x = xmin + res * np.arange(nx)
        y = ymin + res * np.arange(ny)
        c = self.cell
        j0 = max(int((x[0] - self.x0) / c) - 3, 0); j1 = min(int((x[-1] - self.x0) / c) + 4, self.height.shape[1])
        i0 = max(int((y[0] - self.y0) / c) - 3, 0); i1 = min(int((y[-1] - self.y0) / c) + 4, self.height.shape[0])
        gx = self.x0 + c * np.arange(j0, j1)
        gy = self.y0 + c * np.arange(i0, i1)
        spl = RectBivariateSpline(gy, gx, self.height[i0:i1, j0:j1].astype(np.float64), kx=3, ky=3)
        xc, yc = np.clip(x, gx[0], gx[-1]), np.clip(y, gy[0], gy[-1])
        h = spl(yc, xc)
        # albedo: bilinear in the per-cell map
        aspl = RectBivariateSpline(gy, gx, self.alb[i0:i1, j0:j1].astype(np.float64), kx=1, ky=1)
        alb = 1 + self.s * (aspl(yc, xc) - 1)
        X, Y = np.meshgrid(x, y)
        s = self.s
        if "detail" in layers and s > 0:
            h += s * self._tile(self.tile_h, X, Y)
            alb *= 1 + s * (self._tile(self.tile_a, X, Y) - 1)
        if "lips" in layers and s > 0:
            cr, lp = self.craters, self.lips
            m = ((cr[:, 0] + cr[:, 2] > x[0]) & (cr[:, 0] - cr[:, 2] < x[-1])
                 & (cr[:, 1] + cr[:, 2] > y[0]) & (cr[:, 1] - cr[:, 2] < y[-1]))
            for (cx, cy, D), (lh, lw) in zip(cr[m], lp[m]):
                R = D / 2
                reach = R + 3 * lw
                ja, jb = np.searchsorted(x, [cx - reach, cx + reach])
                ia, ib = np.searchsorted(y, [cy - reach, cy + reach])
                if ja >= jb or ia >= ib:
                    continue
                r = np.hypot(x[ja:jb][None, :] - cx, y[ia:ib][:, None] - cy)
                h[ia:ib, ja:jb] += s * lh * np.exp(-((r - R) / lw) ** 2)
        if "rocks" in layers and s > 0:
            rk = self.rocks[: int(round(s * len(self.rocks)))]
            m = ((rk[:, 0] > x[0] - 1) & (rk[:, 0] < x[-1] + 1)
                 & (rk[:, 1] > y[0] - 1) & (rk[:, 1] < y[-1] + 1))
            for rx, ry, a_, b_, hh, yaw, seed in rk[m]:
                a_, b_ = a_ / 2, b_ / 2
                ja, jb = np.searchsorted(x, [rx - a_, rx + a_])
                ia, ib = np.searchsorted(y, [ry - a_, ry + a_])
                if ja >= jb or ia >= ib:
                    continue
                dx = x[ja:jb][None, :] - rx
                dy = y[ia:ib][:, None] - ry
                cs, sn = np.cos(yaw), np.sin(yaw)
                u = ((dx * cs + dy * sn) / a_) ** 2 + ((-dx * sn + dy * cs) / b_) ** 2
                ang = np.arctan2(dy, dx)
                rs = np.random.default_rng(int(seed))
                ph = rs.uniform(0, 2 * np.pi, 3)
                lumpy = 1 + 0.12 * (np.sin(2 * ang + ph[0]) + np.sin(3 * ang + ph[1]) + 0.5 * np.sin(5 * ang + ph[2]))
                body = hh * np.clip(1 - u * lumpy, 0, None) ** 0.45 - self.p.rock_sink_frac * hh
                inside = body > 0
                sub = h[ia:ib, ja:jb]
                base = sub.copy()
                sub[inside] = np.maximum(sub[inside], base[inside] + body[inside])
                alb[ia:ib, ja:jb][inside] = self.p.rock_albedo * (1 + 0.1 * rs.standard_normal())
        return h.astype(np.float32), alb.astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--terrain")
    a = ap.parse_args()
    out = build(a.terrain)
    print("wrote", out)
    for f in sorted(out.iterdir()):
        print(f"  {f.name:22s} {f.stat().st_size / 1e6:7.2f} MB")


if __name__ == "__main__":
    main()
