"""Slope against baseline: real 1 m south-pole DTMs against Lantern's run ground.

    python bench/terrain/slope_vs_baseline.py            # writes bench/terrain/out/slope_vs_baseline.json

Three kinds of ground, every one at 1 m cells:

1. Hemmi et al. 2025 (PSJ 6:264) SfS DTMs, six south-pole sites, real ground.
   Their sec. 4.2: the DTMs follow the fractal trend at baselines >= 10 m and
   flatten below 10 m from SfS smoothing, so their 2 m slopes are a LOWER
   bound on the true 2 m slopes.
2. Lantern's pipeline on the same ground as Hemmi's CR1: the LOLA Site01 5 m
   tile (SPLDEM2021, Barker et al. 2021; Hemmi Fig. 2(c)) cropped to CR1,
   then sim/terrain.py upsample (1 px Gaussian, bicubic), + the run ground's
   roughness (sim/site_ground.py: rms 0.3 x 0.234 m, beta 4, under 10 m),
   + craters at a chosen density (D 7.5-20 m, slope 2, Cauligi 2023 Table 1
   depth ratios), each layer reported.
3. The three workflow-first3c run grounds (data/terrain/run_ground), rebuilt
   layer by layer as sim/ground_slope.py does.

For each ground: Hemmi's rms bidirectional slope at baselines of 1-256 m
(their sec. 4.2 formula: two-point slope along rows and columns, rms over
valid pairs), and the planner's measures: the share of points over 15 deg
with a 2 m central difference, and the share of 2 m planning cells over
15 deg after widening by the rover's half width (sim/rover.py SlopeMap).

Read only: imports sim/ functions, changes nothing there.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import tifffile
from scipy.ndimage import maximum_filter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim.terrain import C23_TABLE1, Params, _fractal_noise, stamp_craters, upsample  # noqa: E402

DATA = Path("C:/Monad-Hackathon-Project/data")
OUT = ROOT / "bench" / "terrain" / "out"
LIMIT = 15.0
HALF_WIDTH = 0.83          # sim/ground_slope.py ROVER_HALF_WIDTH_M
BASELINES = [1, 2, 4, 8, 16, 32, 64, 128, 256]
ROUGH_RMS = 0.3 * 0.234    # Params.rough_frac_of_toterr x synthetic_toterr_m
SITES = ["cr1", "gr1", "gr2", "lp1", "mp1", "mp2"]


def read_tif(path):
    with tifffile.TiffFile(path) as t:
        p = t.pages[0]
        z = p.asarray().astype(np.float64)
        g = p.geotiff_tags
        sx = g["ModelPixelScale"][0]
        tie = g["ModelTiepoint"]
    z[(z < -1e30) | ~np.isfinite(z)] = np.nan
    return z, sx, tie[3], tie[4]        # top-left corner x, y


def rms_bidir(z, cell, L):
    """Hemmi 2025 sec. 4.2: rms of two-point slope angles along both axes."""
    a = []
    for dz in (z[:, L:] - z[:, :-L], z[L:, :] - z[:-L, :]):
        d = dz[np.isfinite(dz)]
        a.append(np.degrees(np.arctan(np.abs(d) / (L * cell))))
    a = np.concatenate(a)
    return float(np.sqrt(np.mean(a ** 2)))


def planner(z, cell):
    """sim/rover.py SlopeMap: 2 m central difference, widened by the half width, 2 m cells."""
    half = max(1, int(round(2.0 / cell)) // 2)
    gx = np.full_like(z, np.nan)
    gy = np.full_like(z, np.nan)
    gx[:, half:-half] = (z[:, 2 * half:] - z[:, :-2 * half]) / (2 * half * cell)
    gy[half:-half, :] = (z[2 * half:, :] - z[:-2 * half, :]) / (2 * half * cell)
    s = np.degrees(np.arctan(np.hypot(gx, gy)))
    ok = np.isfinite(s)
    r = int(math.ceil(HALF_WIDTH / cell))
    w = maximum_filter(np.where(ok, s, -1.0), size=2 * r + 1, mode="nearest")
    k = max(1, int(round(2.0 / cell)))
    wc = w[k // 2::k, k // 2::k]
    okc = ok[k // 2::k, k // 2::k]
    return dict(points_over=float(np.mean(s[ok] > LIMIT)), cells_over=float(np.mean(wc[okc] > LIMIT)),
                median_deg=float(np.median(s[ok])), p90_deg=float(np.percentile(s[ok], 90)))


def lola_slope(z5, c5):
    """LOLA's own slope at its spacing (central difference across 2 cells)."""
    gy, gx = np.gradient(z5, c5)
    s = np.degrees(np.arctan(np.hypot(gx, gy)))
    s = s[np.isfinite(s)]
    return dict(points_over=float(np.mean(s > LIMIT)), median_deg=float(np.median(s)))


def hurst_fit(curve, lo=16, hi=256):
    """tan(rms slope) ~ L^(H-1) over [lo, hi] m; returns H and the extrapolation to 2 m."""
    L = np.array([b for b in BASELINES if lo <= b <= hi], float)
    t = np.tan(np.radians([curve[str(int(b))] for b in L]))
    m, c = np.polyfit(np.log(L), np.log(t), 1)
    return dict(H=float(m + 1), rms_2m_extrap_deg=float(np.degrees(np.arctan(math.exp(c) * 2.0 ** m))))


def describe(z, cell):
    curve = {str(L): rms_bidir(z, cell, L) for L in BASELINES}
    return dict(rms_slope_deg=curve, hurst_16_256=hurst_fit(curve), planner=planner(z, cell))


def noise(shape, cell, rng):
    n = max(shape)
    f = _fractal_noise(n, cell, 4.0, rng, k_lo=1.0 / 10.0) * ROUGH_RMS
    return f[:shape[0], :shape[1]]


def craters_layer(shape, cell, n10, rng, dmin=7.5, dmax=20.0, b=2.0):
    """Craters as sim/terrain.py campaign_world draws them, stamped with C23 Table 1 depth ratios."""
    H, W = shape
    area = H * W * cell * cell / 1e6
    n = rng.poisson(n10 * area * ((dmin / 10) ** -b - (dmax / 10) ** -b))
    u = rng.random(n)
    d = (dmin ** -b - u * (dmin ** -b - dmax ** -b)) ** (-1 / b)
    xy = rng.uniform(0, 1, (n, 2)) * [W * cell, H * cell]
    ratios = C23_TABLE1[:, 1] / C23_TABLE1[:, 0]
    dep = np.clip(rng.choice(ratios, n) * (1 + 0.1 * rng.standard_normal(n)), 0.02, 0.15) * d
    side = max(H, W)
    p = replace(Params(), cell=cell)
    out = stamp_craters(np.zeros((side, side)), np.column_stack([xy, d]), dep, p)
    return out[:H, :W]


def cr1_pipeline():
    zh, ch, xl, yt = read_tif(DATA / "hemmi2025/DTMs/cr1_roi_sfs_1m-DEM.tif")
    H, W = zh.shape
    x = xl + ch * (np.arange(W) + 0.5)
    y = yt - ch * (np.arange(H) + 0.5)          # rows run south
    z5, c5, x5l, y5t = read_tif(DATA / "lola/Site01/Site01_final_adj_5mpp_surf.tif")
    # README: pixel-registered, tiepoint is a pixel centre.
    j0 = int((x[0] - x5l) / c5) - 8
    j1 = int((x[-1] - x5l) / c5) + 9
    i0 = int((y5t - y[0]) / c5) - 8
    i1 = int((y5t - y[-1]) / c5) + 9
    sub = z5[i0:i1, j0:j1]
    xc = x5l + c5 * np.arange(j0, j1)
    yc = y5t - c5 * np.arange(i0, i1)
    # Spline wants increasing coordinates: flip rows to run north.
    base = upsample(sub[::-1], xc, yc[::-1], x, y[::-1], 1.0)[::-1]
    mask = ~np.isfinite(zh)
    rng = np.random.default_rng(20261117)
    rough = noise(base.shape, ch, rng)
    out = {"lola5_own": lola_slope(sub[8:-9, 8:-9], c5),
           "hemmi_cr1": describe(zh, ch),
           "lola_upsampled": describe(np.where(mask, np.nan, base), ch),
           "lola_up_rough": describe(np.where(mask, np.nan, base + rough), ch)}
    for n10 in (336.0, 1540.0, 10000.0):
        cr = craters_layer(base.shape, ch, n10, np.random.default_rng(int(n10)))
        out[f"lola_up_craters{int(n10)}"] = describe(np.where(mask, np.nan, base + cr), ch)
        out[f"lola_up_rough_craters{int(n10)}"] = describe(np.where(mask, np.nan, base + rough + cr), ch)
    # Real ground with the run ground's roughness added: how much the layer adds to true 1 m relief.
    out["hemmi_cr1_plus_rough"] = describe(zh + rough, ch)
    out["offset_hemmi_minus_lola_m"] = float(np.nanmedian(zh - base))
    return out


def run_grounds():
    from sim.psr_sites import load_site
    from sim.terrain import _world_rng
    res = {}
    for name in ("w00003-9ff3a329", "w00006-60b2a45f", "w00009-265af8c2"):
        path = DATA / "terrain/run_ground" / f"{name}.npz"
        z = np.load(path)
        meta = json.loads(str(z["meta"]))
        cell = float(z["cell"])
        saved = z["height"].astype(np.float64)
        n = saved.shape[0]
        d = load_site(meta["site"])
        c5 = d["cell"]
        xl, yb, side = meta["x_left"], meta["y_bottom"], meta["side_m"]
        j0 = int(math.floor((xl - d["x0"]) / c5)) - 8
        i0 = int(math.floor((yb - d["y0"]) / c5)) - 8
        j1 = int(math.ceil((xl + side - d["x0"]) / c5)) + 9
        i1 = int(math.ceil((yb + side - d["y0"]) / c5)) + 9
        z5 = d["height"][i0:i1, j0:j1].astype(np.float64)
        xc = d["x0"] + c5 * np.arange(j0, j1)
        yc = d["y0"] + c5 * np.arange(i0, i1)
        x = xl + cell * (np.arange(n) + 0.5)
        y = yb + cell * (np.arange(n) + 0.5)
        p = replace(Params(), cell=cell, rough_cutoff_m=2 * c5)
        base = upsample(z5, xc, yc, x, y, p.lola_smooth_px)
        rough = _fractal_noise(n, cell, p.rough_beta, _world_rng(int(meta["seed"]), 4),
                               k_lo=1.0 / p.rough_cutoff_m) * ROUGH_RMS
        # The crater layer is what is left; sim/ground_slope.py checks that the
        # rebuild matches the saved ground (6e-5 m and 1.5e-5 m on its two checks).
        cr = saved - base - rough
        res[name] = dict(site=meta["site"], density=meta["world_draws"].get("crater_n_ge10_per_km2"),
                         upsampled=describe(base, cell), up_rough=describe(base + rough, cell),
                         up_craters=describe(base + cr, cell), run_ground=describe(saved, cell))
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"hemmi": {}}
    for s in SITES:
        z, c, _, _ = read_tif(DATA / f"hemmi2025/DTMs/{s}_roi_sfs_1m-DEM.tif")
        res["hemmi"][s] = describe(z, c)
        print(s, json.dumps(res["hemmi"][s]["planner"]), json.dumps(res["hemmi"][s]["hurst_16_256"]), flush=True)
    res["cr1_pipeline"] = cr1_pipeline()
    print("cr1 done", flush=True)
    res["run_grounds"] = run_grounds()
    (OUT / "slope_vs_baseline.json").write_text(json.dumps(res, indent=1))
    print("wrote", OUT / "slope_vs_baseline.json")


if __name__ == "__main__":
    main()
