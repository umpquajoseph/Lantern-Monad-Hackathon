"""Step 2, terrain: the ground the rovers drive and the crater map they navigate by.

Builds a 1 km x 1 km height grid at 0.25 m cells and the crater list that is
the rover's orbital map, in the format of docs/handoffs.md (step 2):

    height  (H, W) float32 metres, cell [i, j] at x = x0 + j*cell, y = y0 + i*cell
            (rows run north)
    craters (N, 3) float64 rows [x, y, diameter] metres, world frame

Two sources, same output:

  lola       a 1 km window of the LOLA Shoemaker 5 m tile, smoothed and
             upsampled, plus fine roughness and stamped small craters.
  synthetic  the same pipeline on a rolling-noise 5 m base, as a fallback.

Usage:
    python sim/terrain.py build              # LOLA -> data/terrain/shoemaker.npz
    python sim/terrain.py build --synthetic  # -> data/terrain/synthetic.npz
    python sim/terrain.py look PATH.npz      # check images next to the file

Every number from a source names it. Every other number is a named parameter
marked ASSUMED.

Sources:
  [C23] Cauligi et al. 2023, ShadowNav, arXiv 2301.04630 (docs/sources/)
  [A24] Atha et al. 2024, ShadowNav (docs/sources/ShadowNav-Atha-2024.pdf)
  [LOLA] data/lola/README.txt and the GeoTIFF tags of the Shoemaker tile
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.interpolate import RectBivariateSpline
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree

# Data lives in the main checkout so every worktree shares it (git-ignored).
# Where the built data lives: LANTERN_DATA if set; else the main Windows checkout;
# else data/ next to this repo (git-ignored). lantern_data/ is the committed copy:
# point LANTERN_DATA at it to run without building anything.
_MAIN_CHECKOUT = Path(r"C:\Monad-Hackathon-Project\data")
DATA = (Path(os.environ["LANTERN_DATA"]) if os.environ.get("LANTERN_DATA")
        else _MAIN_CHECKOUT if _MAIN_CHECKOUT.exists()
        else Path(__file__).resolve().parents[1] / "data")
TERRAIN_DIR = DATA / "terrain"
LOLA_SHOEMAKER = DATA / "lola" / "Shoemaker" / "Shoemaker_final_adj_5mpp_{}.tif"

# [LOLA] Shoemaker tile geometry, read from the GeoTIFF tags:
# ModelPixelScaleTag (5, 5), ModelTiepointTag (0, 0, 0, 66000, 49000, 0),
# GTRasterTypeGeoKey (1025) = 1 = PixelIsArea, so the tiepoint is the top-left
# corner of pixel [0, 0] and its centre is (66002.5, 48997.5). Rows run south.
LOLA_PX = 5.0
LOLA_X_LEFT = 66000.0
LOLA_Y_TOP = 49000.0

# [C23] Table 1: the ShadowNav crater detection set, (diameter m, depth m).
C23_TABLE1 = np.array([
    (9.2, 1.0), (9.1, 0.75), (11.3, 0.84), (4.4, 0.55), (3.7, 0.40),
    (8.3, 0.27), (11.9, 0.44), (3.9, 0.48), (4.1, 0.49), (2.3, 0.25),
])


@dataclass
class Params:
    # --- grid. [C23] 4A: renders used DEMs scaled to 0.25 m resolution.
    cell: float = 0.25
    size_m: float = 1000.0  # spec: world about 1 km across

    # --- LOLA window, in LOLA pixel indices (rows run south).
    # Chosen by scanning every 1 km window on a 25 px stride: this one has
    # 99.9% of pixels under 15 deg slope (_slp.tif) and the most real LOLA
    # returns of those (12.6% of pixels with ldec > 0, tile-wide 7.8%).
    lola_row0: int = 2400
    lola_col0: int = 1450

    # ASSUMED. Gaussian smoothing of the 5 m grid before upsampling, in LOLA
    # pixels. About 92% of pixels are interpolated (measured, ldec == 0), which
    # leaves pixel-scale facets and pimples; LOLA cannot resolve below ~2 px.
    lola_smooth_px: float = 1.0

    # --- fine roughness at scales LOLA cannot see.
    # ASSUMED. Roughness covers wavelengths shorter than this (2 LOLA pixels,
    # the Nyquist wavelength of a 5 m grid).
    rough_cutoff_m: float = 10.0
    # ASSUMED. Roughness RMS as a fraction of the local LOLA height error
    # (_toterr.tif). toterr bounds what the 5 m map can get wrong, so it is an
    # upper scale for unresolved relief, not a measurement of it.
    rough_frac_of_toterr: float = 0.3
    # ASSUMED. Spectral slope of the roughness, PSD ~ k^-beta (fractal surface).
    rough_beta: float = 4.0
    # ASSUMED. toterr for the synthetic base, metres. Median of the chosen
    # Shoemaker window (measured: 0.234 m).
    synthetic_toterr_m: float = 0.234

    # --- craters.
    # [C23] Table 1 smallest diameter 2.3 m. [A24] section IV-A1 and Fig. 6:
    # craters 5-20 m in the sim set; 7.5-20 m are detected reliably.
    d_min_m: float = 2.3
    d_max_m: float = 20.0
    # ASSUMED. Cumulative size-frequency slope, N(>D) ~ D^-b.
    sfd_b: float = 2.0
    # ASSUMED. Craters with D >= 10 m per km^2. [A24] section I cites mean
    # spacing between 10 m craters of 100 m on fresh terrain and 10 m on old
    # terrain, i.e. 100 to 10,000 per km^2; this sits between them.
    n_ge10_per_km2: float = 600.0
    # ASSUMED. Two craters may not be closer than this times (R1 + R2), centre
    # to centre, so every listed rim is a clean, whole circle.
    min_sep_frac: float = 1.0
    # [C23] Table 1: depth/diameter is drawn from its ten measured ratios
    # (0.033 to 0.125), each with this ASSUMED relative jitter.
    dd_jitter: float = 0.1
    # ASSUMED. Rim crest height above the surrounding ground, as a fraction of
    # rim-to-floor depth. Table 1 depth is read as rim-to-floor.
    rim_frac: float = 0.2
    # ASSUMED. Ejecta apron decays as (r/R)^-3 and is tapered to zero by
    # this many radii.
    ejecta_extent_r: float = 3.0
    # ASSUMED. Craters keep this margin from the map edge, metres.
    edge_margin_m: float = 0.0

    seed: int = 7

    # --- source. None: the Shoemaker tile window above (hackathon terrain,
    # chosen for gentle slopes). A sim/psr_sites.py site name: a 1 km window
    # of that PSR site centred on the first PSR floor cell the dock reaches,
    # not chosen for slope (draft item 22).
    site: str | None = None

    @property
    def n(self) -> int:
        return int(round(self.size_m / self.cell))


# --------------------------------------------------------------------------
# Loading: the contract with steps 3, 4, 5 and 6.
# --------------------------------------------------------------------------

def default_path() -> Path:
    """The LOLA build if it exists, else the synthetic fallback."""
    lola = TERRAIN_DIR / "shoemaker.npz"
    return lola if lola.exists() else TERRAIN_DIR / "synthetic.npz"


def load_terrain(path: str | os.PathLike | None = None):
    """Return (height, craters, x0, y0, cell), as in docs/handoffs.md step 2."""
    path = Path(path) if path is not None else default_path()
    with np.load(path) as f:
        height = f["height"]
        craters = f["craters"]
        x0, y0, cell = float(f["x0"]), float(f["y0"]), float(f["cell"])
    return height, craters, x0, y0, cell


def save_terrain(path, height, craters, x0, y0, cell, **extra):
    """Plain arrays only (no pickle), uncompressed so it loads in about a second."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, height=height.astype(np.float32), craters=craters.astype(np.float64),
             x0=np.float64(x0), y0=np.float64(y0), cell=np.float64(cell), **extra)


# --------------------------------------------------------------------------
# Base surface at 5 m: LOLA window or synthetic noise.
# --------------------------------------------------------------------------

def _read_tif(kind):
    import tifffile
    return tifffile.imread(str(LOLA_SHOEMAKER).format(kind))


def lola_base(p: Params, margin: int = 8):
    """Return (z5, err5, x_centres, y_centres, x_left, y_bottom).

    z5, err5: 5 m grids flipped so rows run north, with `margin` extra pixels
    on every side for smoothing and the spline.
    """
    npx = int(round(p.size_m / LOLA_PX))
    r0, c0 = p.lola_row0 - margin, p.lola_col0 - margin
    r1, c1 = p.lola_row0 + npx + margin, p.lola_col0 + npx + margin
    z = _read_tif("surf")[r0:r1, c0:c1].astype(np.float64)
    e = _read_tif("toterr")[r0:r1, c0:c1].astype(np.float64)
    rows = np.arange(r0, r1)
    cols = np.arange(c0, c1)
    xc = LOLA_X_LEFT + LOLA_PX * (cols + 0.5)
    yc = LOLA_Y_TOP - LOLA_PX * (rows + 0.5)
    # Flip so rows run north (y increasing).
    z, e, yc = z[::-1], e[::-1], yc[::-1]
    x_left = LOLA_X_LEFT + LOLA_PX * p.lola_col0
    y_bottom = LOLA_Y_TOP - LOLA_PX * (p.lola_row0 + npx)
    return z, e, xc, yc, x_left, y_bottom


def site_base(p: Params, margin: int = 8):
    """Like lola_base, from a built PSR site (5 m, rows already run north).

    The window is p.size_m square, centred on the first PSR floor cell under
    the slope limit that the dock reaches. The 87-90 S mosaic has no height
    error product, so err5 is p.synthetic_toterr_m everywhere (ASSUMED).
    """
    from sim.psr_sites import load_site
    from sim.settings import SiteBuild
    d = load_site(p.site)
    z, dist, cell = d["height"], d["dist_dock"], d["cell"]
    assert cell == LOLA_PX
    fl = d["psr"] & (d["slope"] < SiteBuild().slope_limit_deg)
    i, j = divmod(int(np.nanargmin(np.where(fl, dist, np.nan))), z.shape[1])
    npx = int(round(p.size_m / LOLA_PX))
    r0, c0 = i - npx // 2, j - npx // 2
    assert r0 - margin >= 0 and c0 - margin >= 0 and r0 + npx + margin <= z.shape[0]         and c0 + npx + margin <= z.shape[1], "showcase window leaves the site"
    z5 = z[r0 - margin:r0 + npx + margin, c0 - margin:c0 + npx + margin].astype(np.float64)
    e5 = np.full_like(z5, p.synthetic_toterr_m)
    xc = d["x0"] + cell * np.arange(c0 - margin, c0 + npx + margin)
    yc = d["y0"] + cell * np.arange(r0 - margin, r0 + npx + margin)
    x_left = d["x0"] + cell * (c0 - 0.5)
    y_bottom = d["y0"] + cell * (r0 - 0.5)
    return z5, e5, xc, yc, x_left, y_bottom


def _fractal_noise(n, dx, beta, rng, k_lo=None, k_hi=None):
    """Gaussian random field with PSD ~ k^-beta, unit RMS.

    k_lo / k_hi (cycles per metre): smooth high-pass / low-pass corners.
    """
    kx = np.fft.fftfreq(n, d=dx)
    k = np.hypot(kx[None, :], kx[:, None])
    k[0, 0] = kx[1]
    amp = k ** (-beta / 2)
    if k_lo is not None:
        amp *= 1.0 / (1.0 + (k_lo / k) ** 8)
    if k_hi is not None:
        amp *= 1.0 / (1.0 + (k / k_hi) ** 8)
    amp[0, 0] = 0.0
    phase = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    f = np.fft.ifft2(amp * phase).real
    return f / f.std()


def synthetic_base(p: Params, rng, margin: int = 8):
    """Rolling 5 m noise terrain with the slope statistics of the LOLA window."""
    npx = int(round(p.size_m / LOLA_PX)) + 2 * margin
    z = _fractal_noise(npx, LOLA_PX, beta=3.6, rng=rng)  # ASSUMED spectral slope
    # Scale relief so the median 5 m slope matches the chosen LOLA window
    # (measured from _slp.tif over the window: median 4.76 deg).
    target_median_deg = 4.76
    gy, gx = np.gradient(z, LOLA_PX)
    med = np.median(np.degrees(np.arctan(np.hypot(gx, gy))))
    z *= np.tan(np.radians(target_median_deg)) / np.tan(np.radians(med))
    e = np.full_like(z, p.synthetic_toterr_m)
    xc = LOLA_PX * (np.arange(npx) - margin + 0.5)
    yc = xc.copy()
    return z, e, xc, yc, 0.0, 0.0


def upsample(z5, xc, yc, x, y, smooth_px):
    """Smooth the 5 m grid, then evaluate a bicubic spline at cell centres."""
    zs = gaussian_filter(z5, smooth_px) if smooth_px > 0 else z5
    spl = RectBivariateSpline(yc, xc, zs, kx=3, ky=3, s=0)
    return spl(y, x).astype(np.float64)


# --------------------------------------------------------------------------
# Roughness and craters at 0.25 m.
# --------------------------------------------------------------------------

def add_roughness(height, err_fine, p: Params, rng):
    noise = _fractal_noise(height.shape[0], p.cell, p.rough_beta, rng,
                           k_lo=1.0 / p.rough_cutoff_m)
    height += noise * (p.rough_frac_of_toterr * err_fine)
    return height


def draw_diameters(p: Params, rng):
    """Truncated power-law diameters, largest first."""
    area_km2 = (p.size_m / 1000.0) ** 2
    # N(>D) = n10 * (D/10)^-b, truncated to [d_min, d_max].
    n_total = p.n_ge10_per_km2 * area_km2 * ((p.d_min_m / 10.0) ** -p.sfd_b
                                             - (p.d_max_m / 10.0) ** -p.sfd_b)
    n = rng.poisson(n_total)
    u = rng.random(n)
    a, b_ = p.d_min_m ** -p.sfd_b, p.d_max_m ** -p.sfd_b
    d = (a - u * (a - b_)) ** (-1.0 / p.sfd_b)
    return np.sort(d)[::-1]


def place_craters(p: Params, rng):
    """Return craters (N, 3) [x, y, D] in map-local metres and depths (N,).

    Rejection sampling, largest first, so no two craters overlap more than
    min_sep_frac allows. Craters that cannot be placed are dropped.
    """
    diam = draw_diameters(p, rng)
    lo, hi = p.edge_margin_m, p.size_m - p.edge_margin_m
    bucket = p.d_max_m  # grid hash, bucket >= largest diameter
    grid: dict[tuple[int, int], list[int]] = {}
    xs, ys, ds = [], [], []
    for d in diam:
        r = d / 2
        for _ in range(30):
            x, y = rng.uniform(lo, hi, 2)
            bx, by = int(x // bucket), int(y // bucket)
            ok = True
            for ix in (bx - 1, bx, bx + 1):
                for iy in (by - 1, by, by + 1):
                    for k in grid.get((ix, iy), ()):
                        if np.hypot(xs[k] - x, ys[k] - y) < p.min_sep_frac * (r + ds[k] / 2):
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    break
            if ok:
                grid.setdefault((bx, by), []).append(len(xs))
                xs.append(x); ys.append(y); ds.append(d)
                break
    craters = np.column_stack([xs, ys, ds])
    ratios = C23_TABLE1[:, 1] / C23_TABLE1[:, 0]
    dd = rng.choice(ratios, len(ds)) * (1 + p.dd_jitter * rng.standard_normal(len(ds)))
    depth = np.clip(dd, 0.02, 0.15) * craters[:, 2]
    return craters, depth


def crater_profile(rho, depth, p: Params):
    """Height change at normalised radius rho = r/R. Rim crest at rho = 1."""
    h_rim = p.rim_frac * depth
    inside = h_rim - depth * (1.0 - rho ** 2)  # parabolic bowl, floor at h_rim - depth
    t = np.clip((rho - (p.ejecta_extent_r - 1.0)) / 1.0, 0, 1)
    taper = 1 - t * t * (3 - 2 * t)  # smoothstep to zero over the last radius
    outside = h_rim * np.maximum(rho, 1.0) ** -3 * taper
    out = np.where(rho <= 1.0, inside, outside)
    return np.where(rho <= p.ejecta_extent_r, out, 0.0)


def stamp_craters(height, craters, depth, p: Params):
    """Add every crater to `height` (map-local coords, cell centres at (j+.5)*cell)."""
    c = p.cell
    n = height.shape[0]
    for (x, y, d), dep in zip(craters, depth):
        reach = p.ejecta_extent_r * d / 2
        j0, j1 = max(int((x - reach) / c), 0), min(int((x + reach) / c) + 2, n)
        i0, i1 = max(int((y - reach) / c), 0), min(int((y + reach) / c) + 2, n)
        xx = (np.arange(j0, j1) + 0.5) * c - x
        yy = (np.arange(i0, i1) + 0.5) * c - y
        rho = np.hypot(xx[None, :], yy[:, None]) / (d / 2)
        height[i0:i1, j0:j1] += crater_profile(rho, dep, p)
    return height


# --------------------------------------------------------------------------
# Build.
# --------------------------------------------------------------------------

def build(synthetic: bool = False, p: Params | None = None, out=None):
    p = p or Params()
    rng = np.random.default_rng(p.seed)
    if synthetic:
        z5, e5, xc, yc, x_left, y_bottom = synthetic_base(p, rng)
        source = "synthetic"
    elif p.site:
        z5, e5, xc, yc, x_left, y_bottom = site_base(p)
        source = f"psr-site-{p.site}"
    else:
        z5, e5, xc, yc, x_left, y_bottom = lola_base(p)
        source = "lola-shoemaker"
    n, c = p.n, p.cell
    # Cell centres: the 1 km window is split into n cells; centre of [0, 0]
    # sits half a cell in from the window's south-west corner.
    x0, y0 = x_left + c / 2, y_bottom + c / 2
    x = x0 + c * np.arange(n)
    y = y0 + c * np.arange(n)
    height = upsample(z5, xc, yc, x, y, p.lola_smooth_px)
    err = upsample(gaussian_filter(e5, 2.0), xc, yc, x, y, 0)
    err = np.clip(err, 0.0, None)
    height = add_roughness(height, err, p, rng)
    craters_local, depth = place_craters(p, rng)
    height = stamp_craters(height, craters_local, depth, p)
    craters = craters_local.copy()
    # Local metres from the window's south-west corner -> world. Cell [i, j]
    # centre is local ((j + .5) c, (i + .5) c) = world (x0 + j c, y0 + i c).
    craters[:, 0] += x_left
    craters[:, 1] += y_bottom
    meta = dict(source=source, params=asdict(p), x_left=x_left, y_bottom=y_bottom)
    out = Path(out) if out else TERRAIN_DIR / ("synthetic.npz" if synthetic else
                                               f"{p.site}.npz" if p.site else "shoemaker.npz")
    save_terrain(out, height, craters, x0, y0, c,
                 crater_depth=depth.astype(np.float32),
                 meta=np.array(json.dumps(meta)))
    return out


# --------------------------------------------------------------------------
# Checks: numbers and pictures.
# --------------------------------------------------------------------------

def slope_deg(height, cell):
    gy, gx = np.gradient(height, cell)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def coverage(craters, x0, y0, size_m, near=15.0, d_min=0.0, step=10.0, margin=50.0):
    """Fraction of points (on a `step` grid) by how many crater near-rims lie
    within `near` metres. Counts craters with D >= d_min."""
    sel = craters[craters[:, 2] >= d_min]
    tree = cKDTree(sel[:, :2])
    g = np.arange(margin, size_m - margin, step)
    pts = np.stack(np.meshgrid(x0 + g, y0 + g), -1).reshape(-1, 2)
    counts = np.zeros(len(pts), int)
    for i, idx in enumerate(tree.query_ball_point(pts, near + sel[:, 2].max() / 2)):
        if idx:
            dist = np.hypot(*(sel[idx, :2] - pts[i]).T) - sel[idx, 2] / 2
            counts[i] = np.count_nonzero(dist <= near)
    return counts


def report(path):
    height, craters, x0, y0, cell = load_terrain(path)
    n = height.shape[0]
    s = slope_deg(height, cell)
    # Slope at the LOLA baseline (5 m), the scale a planner would use.
    k = int(round(LOLA_PX / cell))
    s5 = slope_deg(height[::k, ::k], cell * k)
    lines = [
        f"file {path}",
        f"height {height.shape} {height.dtype}  min {height.min():.2f}  max {height.max():.2f} m",
        f"x0 {x0}  y0 {y0}  cell {cell}  extent x {x0 - cell/2:.1f}..{x0 + (n - .5) * cell:.1f}"
        f"  y {y0 - cell/2:.1f}..{y0 + (n - .5) * cell:.1f}",
        f"craters {len(craters)}  D {craters[:, 2].min():.2f}..{craters[:, 2].max():.2f} m"
        f"  D>=5: {np.sum(craters[:, 2] >= 5)}  D>=7.5: {np.sum(craters[:, 2] >= 7.5)}",
        f"slope 0.25 m (INTERPOLATED, 5 m LOLA upsampled + stamped craters + roughness): "
        f"median {np.median(s):.1f}  under 15: {np.mean(s < 15):.3f}",
        f"slope 5 m (LOLA spacing; resampled from this grid): median {np.median(s5):.1f}  under 15: {np.mean(s5 < 15):.3f}",
    ]
    for near, dmin in ((15, 0), (15, 7.5), (25, 7.5)):
        cnt = coverage(craters, x0, y0, n * cell, near=near, d_min=dmin)
        lines.append(f"near rims within {near} m, D>={dmin}: median {np.median(cnt):.0f}"
                     f"  >=1 {np.mean(cnt >= 1):.3f}  >=3 {np.mean(cnt >= 3):.3f}")
    return "\n".join(lines)


def _lamp_render(height, cell, lamp_xy, lamp_h, half, steps=160):
    """Top-down image of the ground lit by a point lamp `lamp_h` above the
    ground at `lamp_xy` (grid indices), with cast shadows. Region is
    +-half cells around the lamp."""
    from scipy.ndimage import map_coordinates
    li, lj = lamp_xy
    i0, i1, j0, j1 = li - half, li + half, lj - half, lj + half
    sub = height[i0:i1, j0:j1].astype(np.float64)
    ii, jj = np.mgrid[i0:i1, j0:j1].astype(np.float64)
    lz = height[li, lj] + lamp_h
    # Lambert with 1/r^2.
    gy, gx = np.gradient(sub, cell)
    nrm = np.stack([-gx, -gy, np.ones_like(sub)])
    nrm /= np.linalg.norm(nrm, axis=0)
    v = np.stack([(lj - jj) * cell, (li - ii) * cell, lz - sub])
    r = np.linalg.norm(v, axis=0)
    lam = np.clip((nrm * v).sum(0) / r, 0, None) / np.maximum(r, 1.0) ** 2
    # Shadows: march from each ground point toward the lamp.
    lit = np.ones_like(sub, bool)
    for t in np.linspace(0.02, 0.98, steps):
        pi = ii + t * (li - ii)
        pj = jj + t * (lj - jj)
        ray_z = sub + 0.02 + t * (lz - sub)
        ground = map_coordinates(height, [pi, pj], order=1)
        lit &= ground <= ray_z
    img = lam * lit
    return img / np.percentile(img[img > 0], 99)


def look(path, outdir=None):
    """Write the two check images beside the npz and print the numbers."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    from matplotlib.collections import EllipseCollection

    path = Path(path)
    outdir = Path(outdir) if outdir else path.parent
    stem = path.stem
    height, craters, x0, y0, cell = load_terrain(path)
    n = height.shape[0]
    print(report(path))

    # 1. Top-down shaded map, whole kilometre, with the crater list drawn on.
    k = 4  # 1 m pixels
    hs = LightSource(azdeg=315, altdeg=25).hillshade(height[::k, ::k], dx=cell * k, dy=cell * k)
    ext = [x0 - cell / 2, x0 + (n - .5) * cell, y0 - cell / 2, y0 + (n - .5) * cell]
    fig, ax = plt.subplots(figsize=(14, 14), dpi=100)
    ax.imshow(hs, cmap="gray", origin="lower", extent=ext)
    ec = EllipseCollection(craters[:, 2], craters[:, 2], np.zeros(len(craters)), units="xy",
                           offsets=craters[:, :2], offset_transform=ax.transData,
                           facecolors="none", edgecolors="tab:red", linewidths=0.4)
    ax.add_collection(ec)
    ax.set_title(f"{stem}: {len(craters)} mapped craters (red), sun az 315 el 25")
    ax.set_xlabel("x east (m)"); ax.set_ylabel("y north (m)")
    fig.savefig(outdir / f"{stem}_map.png", bbox_inches="tight")
    plt.close(fig)

    # 2. Close-up at full resolution: a 100 m square, lit (a) by the rover's
    # lamp and (b) by a 5 deg sun, to show facet artifacts. Crater list on top.
    # Lamp height: sim/settings.py camera_height_m - lamp_below_camera_m.
    from sim.settings import Settings
    lamp_h = Settings().camera_height_m - Settings().lamp_below_camera_m
    ci, cj = n // 2, n // 2
    half = 200  # 50 m each way
    lampimg = _lamp_render(height, cell, (ci, cj), lamp_h, half)
    sub = height[ci - half:ci + half, cj - half:cj + half]
    low = LightSource(azdeg=315, altdeg=5).hillshade(sub, dx=cell, dy=cell)
    e2 = [x0 + (cj - half - .5) * cell, x0 + (cj + half - .5) * cell,
          y0 + (ci - half - .5) * cell, y0 + (ci + half - .5) * cell]
    fig, axs = plt.subplots(1, 2, figsize=(20, 10), dpi=100)
    axs[0].imshow(np.sqrt(np.clip(lampimg, 0, 1)), cmap="gray", origin="lower", extent=e2)
    axs[0].plot(x0 + cj * cell, y0 + ci * cell, "y+", ms=12)
    axs[0].set_title(f"lamp {lamp_h:.2f} m above ground at the +, cast shadows, sqrt tone")
    axs[1].imshow(low, cmap="gray", origin="lower", extent=e2)
    axs[1].set_title("sun az 315, elevation 5 deg")
    for a in axs:
        m = ((craters[:, 0] > e2[0] - 10) & (craters[:, 0] < e2[1] + 10)
             & (craters[:, 1] > e2[2] - 10) & (craters[:, 1] < e2[3] + 10))
        cc = craters[m]
        a.add_collection(EllipseCollection(cc[:, 2], cc[:, 2], np.zeros(len(cc)), units="xy",
                                           offsets=cc[:, :2], offset_transform=a.transData,
                                           facecolors="none", edgecolors="tab:red",
                                           linewidths=0.8, alpha=0.6))
        a.set_xlim(e2[:2]); a.set_ylim(e2[2:])
    fig.savefig(outdir / f"{stem}_closeup.png", bbox_inches="tight")
    plt.close(fig)
    return outdir / f"{stem}_map.png", outdir / f"{stem}_closeup.png"


# --------------------------------------------------------------------------
# Campaign worlds (docs/handoffs.md "Part 3 gives part 1").
# --------------------------------------------------------------------------

@dataclass
class CampaignWorld:
    """The fields of sim/campaign.py World, in the same order and units."""
    site: str
    file: str
    x0: float               # centre of cell [0, 0], rows run north
    y0: float
    cell: float             # 5 m, native LOLA (PGDA product 81)
    height: np.ndarray      # (H, W) float32 m, LOLA ldem, no craters stamped in
    slope: np.ndarray       # (H, W) float32 deg, LOLA ldsm at the native 5 m spacing
    floor: np.ndarray       # (H, W) bool, permanent shadow (Barker 2023 LPSR, 20 m, sampled to 5 m)
    craters: np.ndarray     # (N, 3) true craters [x, y, d], d in WorldRanges.crater_d_m
    map_start: np.ndarray   # (M, 3) the Scouts' starting map, a subset of craters
    dock: tuple             # true dock position, on sunlit rim ground
    draws: dict             # every WorldRanges draw, plus dock_reg_err
    ice: list               # hidden deposits [{x, y, radius, peak}]
    standin: str | None = None


def _world_rng(seed: int, *key: int):
    # Same keying as sim/campaign.py rng_for(seed, 0), so a world drawn here
    # equals the engine's stand-in draws for the same seed.
    return np.random.default_rng(np.random.SeedSequence([int(seed), *map(int, key)]))


def draw_world_values(seed: int, ranges, rng) -> dict:
    """Every WorldRanges draw for this seed, in a fixed order (keys as sim/campaign.py)."""
    import math
    lo, hi = ranges.crater_n_ge10_per_km2
    d = {
        "crater_n_ge10_per_km2": float(math.exp(rng.uniform(math.log(lo), math.log(hi)))),
        "orbital_map_min_d_m": float(ranges.orbital_map_min_d_m),
        "missing_share_large": float(rng.uniform(*ranges.missing_share_large)),
        "n_deposits": int(rng.integers(ranges.n_deposits[0], ranges.n_deposits[1] + 1)),
        "drift_frac": float(rng.uniform(*ranges.drift_frac)),
        "dock_reg_sigma_m": float(rng.uniform(*ranges.dock_reg_sigma_m)),
    }
    d["dock_reg_err"] = [float(v) for v in rng.normal(0.0, d["dock_reg_sigma_m"], 2)]
    return d


_SITE_CACHE: dict = {}


def campaign_world(site: str, seed: int, ranges=None) -> CampaignWorld:
    """One random world on a candidate PSR window (sim/psr_sites.py SITES).

    Terrain, dock and PSR mask are fixed per site. The seed draws the crater
    density, the true craters (uniform over the window, truncated power law
    over ranges.crater_d_m), which of them the Scouts' starting map holds
    (every crater under orbital_map_min_d_m is missing, plus a random share
    missing_share_large of the rest), and the hidden ice (uniform over PSR
    floor under the slope limit within ice_reach_m driving of the dock).
    """
    from sim.orbital_map import in_orbital_map
    from sim.psr_sites import SITES, load_site
    from sim.settings import SiteBuild, WorldRanges
    ranges = ranges or WorldRanges()
    if site not in SITES:
        raise ValueError(f"unknown site {site!r}; built PSR sites: {sorted(SITES)} "
                         f"(the Shoemaker tile is not PSR floor, spec 11.1; use sim/campaign.py standin_world)")
    if site not in _SITE_CACHE:
        _SITE_CACHE[site] = load_site(site)
    t = _SITE_CACHE[site]
    x0, y0, cell = t["x0"], t["y0"], t["cell"]
    h, w = t["height"].shape
    rng = _world_rng(seed, 0)
    draws = draw_world_values(seed, ranges, rng)

    dmin, dmax = ranges.crater_d_m
    b = ranges.crater_sfd_b
    area_km2 = (h * cell / 1000.0) * (w * cell / 1000.0)
    n_tot = draws["crater_n_ge10_per_km2"] * area_km2 * ((dmin / 10) ** -b - (dmax / 10) ** -b)
    nc = int(rng.poisson(n_tot))
    u = rng.random(nc)
    a_, b_ = dmin ** -b, dmax ** -b
    d = (a_ - u * (a_ - b_)) ** (-1.0 / b)
    xy = rng.uniform(0, 1, (nc, 2)) * [w * cell, h * cell] + [x0 - cell / 2, y0 - cell / 2]
    craters = np.column_stack([xy, d])
    in_map = in_orbital_map(d, rng.random(nc), draws["orbital_map_min_d_m"], draws["missing_share_large"])
    map_start = craters[in_map]

    dist = t["dist_dock"]
    good = t["psr"] & (t["slope"] < SiteBuild().slope_limit_deg) & (np.nan_to_num(dist, nan=np.inf) <= ranges.ice_reach_m)
    cells = np.flatnonzero(good)
    ice = []
    if len(cells):
        pick = rng.choice(cells, draws["n_deposits"], replace=len(cells) < draws["n_deposits"])
        for k in pick:
            i, j = divmod(int(k), w)
            ice.append({"x": float(x0 + (j + rng.uniform(-.5, .5)) * cell),
                        "y": float(y0 + (i + rng.uniform(-.5, .5)) * cell),
                        "radius": float(rng.uniform(*ranges.deposit_radius_m)),
                        "peak": float(rng.uniform(*ranges.deposit_peak_wt))})
    return CampaignWorld(site=site, file=f"psr_sites/{site}.npz", x0=x0, y0=y0, cell=cell,
                         height=t["height"], slope=t["slope"], floor=t["psr"], craters=craters,
                         map_start=map_start, dock=t["dock"], draws=draws, ice=ice)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--synthetic", action="store_true")
    b.add_argument("--out")
    b.add_argument("--seed", type=int, default=Params.seed)
    b.add_argument("--site", default=None, help="a sim/psr_sites.py site: 1 km at its PSR edge")
    lk = sub.add_parser("look")
    lk.add_argument("path", nargs="?")
    a = ap.parse_args()
    if a.cmd == "build":
        out = build(a.synthetic, Params(seed=a.seed, site=a.site), a.out)
        print(f"wrote {out}")
        look(out)
    else:
        look(a.path or default_path())


if __name__ == "__main__":
    main()
