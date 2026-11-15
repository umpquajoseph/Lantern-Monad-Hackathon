"""Step 2 on real terrain: a 1 km window of a 1 m SfS DTM (Hemmi et al. 2025).

Same output as sim/terrain.py (docs/handoffs.md step 2), written to a new
file, data/terrain/cr1.npz. shoemaker.npz stays the default.

  height   real 1 m DTM, bicubic to 0.25 m. Holes (NODATA, mostly shadowed
           crater interiors where shape-from-shading has no signal) are
           filled with a smooth harmonic surface. Filled cells are MADE UP and
           flagged in `filled` (uint8, same grid) and in meta.
  craters  real craters found in the DTM, D >= 5 m, every one with a fully
           real, raised rim. Many have shadowed floors that SfS left empty;
           those are flagged in crater_floor_filled. No crater is only a
           filled hole: the rim must be real.

    python sim/terrain_real.py build   # -> data/terrain/cr1.npz + look images

Source (credit it, CC BY 4.0):
  [H25] Hemmi et al. 2025, "LROC NAC-derived meter-scale topography of the
        Moon's south polar landing sites", PSJ, doi 10.3847/PSJ/ae10a4;
        data Zenodo doi 10.5281/zenodo.17153447. Site CR1 (connecting ridge).
        Sunlit landing-site ridge, not a permanently shadowed crater floor.
  GeoTIFF tags of cr1_roi_sfs_1m-DEM.tif: 1 m pixels, tiepoint
  (-12042.5, -11301.5), GTRasterTypeGeoKey = 1 (PixelIsArea), so that
  point is pixel [0, 0]'s top-left corner. Rows run south. NODATA -3.4e38.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.interpolate import RectBivariateSpline
from scipy.ndimage import gaussian_filter, maximum_filter, binary_dilation
from scipy.signal import fftconvolve

import terrain as T

HEMMI = T.DATA / "hemmi2025"
CR1_DEM = HEMMI / "DTMs" / "cr1_roi_sfs_1m-DEM.tif"
CR1_ERR = HEMMI / "uncertainties" / "cr1_sfs-height-error-final.tif"
CR1_X_LEFT, CR1_Y_TOP, CR1_PX = -12042.5, -11301.5, 1.0  # GeoTIFF tags, see docstring


@dataclass
class RealParams:
    # Window: best 1 km square of CR1 by valid fraction (94.0%), scanned on a
    # 10 px stride. Pixel indices, rows south.
    row0: int = 10
    col0: int = 10
    size_px: int = 1000
    cell: float = 0.25
    # Crater finder (ASSUMED thresholds).
    d_min_m: float = 5.0            # [A24] IV-A1: 5-20 m craters "from the orbital data"
    d_max_m: float = 40.0
    detrend_sigma_m: float = 30.0
    min_depth_m: float = 0.3        # ~15x the median, ~4x the p90 of [H25] height error
    min_dd: float = 0.03            # [C23] Table 1 lowest d/D is 0.033
    min_raised_sectors: int = 11    # of 12 rim sectors above the centre
    hole_dilate_px: int = 2         # SfS pixels next to a hole are treated as unreliable
    seam_sigma_px: float = 3.0      # blur of the fill and its 3 px seam, made up
    valid_margin_r: float = 1.3     # disc + rim out to this many radii must be real


def _read(path):
    import tifffile
    import logging
    logging.getLogger("tifffile").setLevel(logging.ERROR)
    a = tifffile.imread(str(path)).astype(np.float64)
    return a, a > -1e30


def harmonic_fill(z, valid):
    """Fill invalid pixels with the solution of Laplace's equation, using
    valid neighbours as fixed boundary values (a smooth membrane)."""
    z = z.copy()
    hole = ~valid
    idx = -np.ones(z.shape, np.int64)
    hi, hj = np.nonzero(hole)
    n = len(hi)
    idx[hi, hj] = np.arange(n)
    rows, cols, vals = [], [], []
    b = np.zeros(n)
    deg = np.zeros(n)
    H, W = z.shape
    for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        ni, nj = hi + di, hj + dj
        ok = (ni >= 0) & (ni < H) & (nj >= 0) & (nj < W)
        deg[ok] += 1
        k = np.nonzero(ok)[0]
        nb = idx[ni[k], nj[k]]
        inner = nb >= 0
        rows += list(k[inner]); cols += list(nb[inner]); vals += [-1.0] * int(inner.sum())
        kb = k[~inner]
        b[kb] += z[ni[kb], nj[kb]]
    A = sp.csr_matrix((vals + list(deg), (rows + list(range(n)), cols + list(range(n)))), shape=(n, n))
    z[hi, hj] = spla.spsolve(A, b)
    return z


def _disc(r):
    k = int(np.ceil(r))
    y, x = np.mgrid[-k:k + 1, -k:k + 1]
    return np.hypot(x, y) <= r


def find_craters(z, valid, p: RealParams):
    """Return (N, 5) [col, row, D_px, depth_m, floor_filled] in 1 m pixels
    (rows south). z is the hole-filled DTM.

    Evidence rule: the rim ring (0.85-1.3 R) must be entirely real pixels and
    raised all round. Holes are allowed only inside 0.85 R: SfS leaves the
    shadowed floors of small craters empty under the low polar sun, so the
    real evidence for most craters here is the rim. Depth is measured to the
    filled floor when the floor is a hole, so it is a lower bound."""
    zd = z - gaussian_filter(z, p.detrend_sigma_m)
    cand = []
    radii = np.arange(p.d_min_m / 2, p.d_max_m / 2 + 0.01, 0.5)
    for R in radii:
        k = int(np.ceil(1.15 * R)) + 1
        y, x = np.mgrid[-k:k + 1, -k:k + 1]
        r = np.hypot(x, y)
        ring = ((r >= 0.9 * R) & (r <= 1.1 * R)).astype(float); ring /= max(ring.sum(), 1)
        core = (r <= max(0.45 * R, 1.0)).astype(float); core /= core.sum()
        contrast = fftconvolve(zd, ring, "same") - fftconvolve(zd, core, "same")
        score = contrast / (2 * R)  # depth / diameter
        peaks = (score == maximum_filter(score, size=int(2 * R) | 1)) & (contrast > p.min_depth_m) & (score > p.min_dd)
        for i, j in zip(*np.nonzero(peaks)):
            cand.append((score[i, j], j, i, 2 * R, contrast[i, j]))
    cand.sort(key=lambda c: -c[0])
    H, W = z.shape
    invalid = ~valid
    kept = []
    for s, j, i, D, dep in cand:
        R = D / 2
        if any(np.hypot(j - kj, i - ki) < 0.6 * max(R, kD / 2) for _, kj, ki, kD, _, _ in kept):
            continue
        m = int(np.ceil(p.valid_margin_r * R)) + 1
        if i - m < 0 or j - m < 0 or i + m >= H or j + m >= W:
            continue
        yy, xx = np.mgrid[-m:m + 1, -m:m + 1]
        rr = np.hypot(xx, yy); th = np.arctan2(yy, xx)
        sub = invalid[i - m:i + m + 1, j - m:j + m + 1]
        rim_zone = (rr >= 0.85 * R) & (rr <= p.valid_margin_r * R)
        if (sub & rim_zone).any():
            continue  # rim not entirely real: reject
        floor_filled = bool((sub & (rr < 0.85 * R)).any())
        patch = zd[i - m:i + m + 1, j - m:j + m + 1]
        centre = patch[rr <= max(0.45 * R, 1.0)].mean()
        ring = (rr >= 0.85 * R) & (rr <= 1.15 * R)
        sec = ((th[ring] + np.pi) / (2 * np.pi) * 12).astype(int) % 12
        sec_mean = np.bincount(sec, patch[ring], 12) / np.maximum(np.bincount(sec, None, 12), 1)
        if np.sum(sec_mean - centre > 0.3 * dep) < p.min_raised_sectors:
            continue
        kept.append((s, j, i, D, dep, floor_filled))
    return np.array([(j, i, D, dep, ff) for _, j, i, D, dep, ff in kept], float).reshape(-1, 5)


def build(p: RealParams | None = None, out=None):
    p = p or RealParams()
    z, valid = _read(CR1_DEM)
    err, ev = _read(CR1_ERR)
    r0, c0, n1 = p.row0, p.col0, p.size_px
    # window with a margin for the fill and the spline (clipped at tile edges)
    m = 10
    ra, rb = max(r0 - m, 0), min(r0 + n1 + m, z.shape[0])
    ca, cb = max(c0 - m, 0), min(c0 + n1 + m, z.shape[1])
    zw, vw = z[ra:rb, ca:cb], valid[ra:rb, ca:cb]
    # Holes widened by a couple of pixels (their edges are unreliable), filled
    # with a harmonic membrane, then the seam feathered so no stair-stepped
    # 1 m edge survives to look like a rim. Crater finding still uses the
    # original valid mask plus the widened one, so no evidence comes from a fill.
    v_real = vw
    vw = vw & ~binary_dilation(~vw, iterations=p.hole_dilate_px)
    zf = harmonic_fill(np.where(vw, zw, 0.0), vw)
    # weight 1 inside the widened hole, falling to 0 over 3 px outside it
    from scipy.ndimage import distance_transform_edt
    dist = distance_transform_edt(vw)
    wgt = np.clip(1 - dist / 3.0, 0, 1)
    zf = zf * (1 - wgt) + gaussian_filter(zf, p.seam_sigma_px) * wgt
    cr = find_craters(zf, v_real, p)
    # pixel -> world (pixel centres), keep craters centred inside the window
    cx = CR1_X_LEFT + CR1_PX * (ca + cr[:, 0] + 0.5)
    cy = CR1_Y_TOP - CR1_PX * (ra + cr[:, 1] + 0.5)
    x_left = CR1_X_LEFT + CR1_PX * c0
    y_bottom = CR1_Y_TOP - CR1_PX * (r0 + n1)
    size = n1 * CR1_PX
    inside = (cx > x_left) & (cx < x_left + size) & (cy > y_bottom) & (cy < y_bottom + size)
    craters = np.column_stack([cx, cy, cr[:, 2] * CR1_PX])[inside]
    depth = cr[inside, 3]
    floor_filled = cr[inside, 4].astype(np.uint8)
    # 1 m -> 0.25 m, rows north
    xc = CR1_X_LEFT + CR1_PX * (np.arange(ca, cb) + 0.5)
    yc = CR1_Y_TOP - CR1_PX * (np.arange(ra, rb) + 0.5)
    zf_n, vw_n, yc_n = zf[::-1], vw[::-1], yc[::-1]
    n = int(round(size / p.cell))
    x0, y0 = x_left + p.cell / 2, y_bottom + p.cell / 2
    x = x0 + p.cell * np.arange(n); y = y0 + p.cell * np.arange(n)
    height = RectBivariateSpline(yc_n, xc, zf_n, kx=3, ky=3)(y, x)
    fj = np.clip(np.round((x - xc[0]) / CR1_PX).astype(int), 0, len(xc) - 1)
    fi = np.clip(np.round((y - yc_n[0]) / CR1_PX).astype(int), 0, len(yc_n) - 1)
    filled = (~vw_n)[np.ix_(fi, fj)].astype(np.uint8)
    e_win = np.where(ev, err, np.nan)[r0:r0 + n1, c0:c0 + n1]
    meta = dict(source="hemmi2025-cr1", params=asdict(p), x_left=x_left, y_bottom=y_bottom,
                filled_fraction=float(filled.mean()),
                craters_floor_filled=int(floor_filled.sum()),
                crater_rule="rim ring 0.85-1.3 R all real and raised in >= 10 of 12 sectors; "
                            "holes allowed inside 0.85 R only (crater_floor_filled = 1, depth then a lower bound)",
                height_error_median_m=float(np.nanmedian(e_win)),
                height_error_p90_m=float(np.nanpercentile(e_win, 90)),
                made_up="height where filled == 1 (harmonic fill of NODATA holes)",
                credit="Hemmi et al. 2025, PSJ, doi 10.3847/PSJ/ae10a4; data doi "
                       "10.5281/zenodo.17153447, CC BY 4.0. Site CR1, sunlit ridge, not a PSR floor.")
    out = Path(out) if out else T.TERRAIN_DIR / "cr1.npz"
    T.save_terrain(out, height, craters, x0, y0, p.cell,
                   crater_depth=depth.astype(np.float32), crater_floor_filled=floor_filled,
                   filled=filled,
                   meta=np.array(json.dumps(meta)))
    return out


def look(path):
    """terrain.look() images plus a hole map with the crater list."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    from matplotlib.collections import EllipseCollection
    T.look(path)
    path = Path(path)
    height, craters, x0, y0, cell = T.load_terrain(path)
    with np.load(path) as f:
        filled = f["filled"]
    k = 4
    hs = LightSource(azdeg=315, altdeg=25).hillshade(height[::k, ::k], dx=cell * k, dy=cell * k)
    n = height.shape[0]
    ext = [x0 - cell / 2, x0 + (n - .5) * cell, y0 - cell / 2, y0 + (n - .5) * cell]
    fig, ax = plt.subplots(figsize=(14, 14), dpi=100)
    ax.imshow(hs, cmap="gray", origin="lower", extent=ext)
    ax.imshow(np.where(filled[::k, ::k] > 0, 1.0, np.nan), cmap="autumn", alpha=0.6, origin="lower", extent=ext)
    ax.add_collection(EllipseCollection(craters[:, 2], craters[:, 2], np.zeros(len(craters)), units="xy",
                                        offsets=craters[:, :2], offset_transform=ax.transData,
                                        facecolors="none", edgecolors="cyan", linewidths=0.7))
    ax.set_title(f"{path.stem}: {len(craters)} real craters (cyan), filled holes (orange, made up)")
    fig.savefig(path.parent / f"{path.stem}_holes.png", bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "look"])
    a = ap.parse_args()
    out = build() if a.cmd == "build" else T.TERRAIN_DIR / "cr1.npz"
    print("wrote", out)
    look(out)


if __name__ == "__main__":
    main()
