"""Where a run ground's steep cells come from: LOLA, roughness or stamped craters.

    python -m sim.ground_slope RUN_GROUND.npz [...]

Rebuilds the run ground (sim/site_ground.py build_ground) layer by layer
over the same square, and reports for each layer the share of ground over
the planner's 15 deg limit:

- LOLA 5 m: the site's LOLA window at its own spacing (central difference
  across 10 m), over the same square.
- upsampled: the LOLA window smoothed and bicubic-upsampled to the run
  ground's cell (no roughness, no craters).
- + roughness: plus site_ground's fractal roughness under two LOLA cells.
- + craters: plus the world's craters (this is the run ground as saved;
  checked against the file).
- craters, no roughness: the upsampled LOLA plus craters only.

Shares: "fine" is every grid point, slope as a central difference across
2 m (docs/handoffs.md step 2); "cells" is sim/rover.py SlopeMap's 2 m
planning cells, the fine slope widened by the rover's half width
(ROVER_HALF_WIDTH_M) and sampled at the
cell centres, as the run's planner sees them.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter

from sim.psr_sites import load_site
from sim.terrain import Params, _fractal_noise, _world_rng, stamp_craters, upsample

LIMIT_DEG = 15.0
BASELINE_M = 2.0
ROVER_HALF_WIDTH_M = 0.83      # sim/rover.py ROVER_HALF_WIDTH_M


def fine_slope(h, cell):
    half = max(1, int(round(BASELINE_M / cell)) // 2)
    hp = np.pad(h, half, mode="edge")
    gx = (hp[half:-half, 2 * half:] - hp[half:-half, :-2 * half]) / (2 * half * cell)
    gy = (hp[2 * half:, half:-half] - hp[:-2 * half, half:-half]) / (2 * half * cell)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def shares(h, cell):
    f = fine_slope(h, cell)
    r = int(math.ceil(ROVER_HALF_WIDTH_M / cell))
    w = maximum_filter(f, size=2 * r + 1, mode="nearest")
    k = max(1, int(round(BASELINE_M / cell)))
    cells = w[k // 2::k, k // 2::k]
    return dict(fine=float(np.mean(f > LIMIT_DEG)), cells=float(np.mean(cells > LIMIT_DEG)),
                fine_median_deg=float(np.median(f)), rms_detrended_m=None)


def layers(path):
    z = np.load(path)
    meta = json.loads(str(z["meta"]))
    site, seed = meta["site"], int(meta["seed"])
    cell = float(z["cell"])
    saved = z["height"].astype(np.float64)
    n = saved.shape[0]
    x_left, y_bottom = meta["x_left"], meta["y_bottom"]
    side = meta["side_m"]
    d = load_site(site)
    c5 = d["cell"]
    H, W = d["height"].shape
    j0 = int(math.floor((x_left - d["x0"]) / c5)) - 8
    i0 = int(math.floor((y_bottom - d["y0"]) / c5)) - 8
    j1 = int(math.ceil((x_left + side - d["x0"]) / c5)) + 9
    i1 = int(math.ceil((y_bottom + side - d["y0"]) / c5)) + 9
    z5 = d["height"][i0:i1, j0:j1].astype(np.float64)
    xc = d["x0"] + c5 * np.arange(j0, j1)
    yc = d["y0"] + c5 * np.arange(i0, i1)
    x = x_left + cell * (np.arange(n) + 0.5)
    y = y_bottom + cell * (np.arange(n) + 0.5)
    p = replace(Params(), cell=cell, rough_cutoff_m=2 * c5)
    base = upsample(z5, xc, yc, x, y, p.lola_smooth_px)
    rng = _world_rng(seed, 4)
    rough_rms = p.rough_frac_of_toterr * p.synthetic_toterr_m
    rough = _fractal_noise(n, cell, p.rough_beta, rng, k_lo=1.0 / p.rough_cutoff_m) * rough_rms
    # The craters the ground was stamped with, and their depths, as saved in it.
    local = np.asarray(z["craters"], np.float64)[:, :3].copy()
    dep = np.asarray(z["crater_depth"], np.float64)
    local[:, 0] -= x_left
    local[:, 1] -= y_bottom
    cr = stamp_craters(np.zeros_like(base), local, dep, p)
    # LOLA at its own spacing over the square.
    jj = (xc >= x_left) & (xc <= x_left + side)
    ii = (yc >= y_bottom) & (yc <= y_bottom + side)
    z5in = z5[np.ix_(ii, jj)]
    gy, gx = np.gradient(z5in, c5)
    s5 = np.degrees(np.arctan(np.hypot(gx, gy)))
    # Self-affine extrapolation of LOLA's slopes to the 2 m baseline (Cai & Fa 2020,
    # JGR Planets 125, e2020JE006429: NAC DTMs, Hurst exponent 0.7-0.95, median 0.9;
    # height differences scale as L^H, so slope tangents as L^(H-1)). LOLA's central
    # difference spans 2 x 5 m = 10 m.
    t5 = np.tan(np.radians(s5))
    affine = {f"H{h:g}": float(np.mean(np.degrees(np.arctan(t5 * (BASELINE_M / (2 * c5)) ** (h - 1))) > LIMIT_DEG))
              for h in (0.7, 0.9, 0.95)}
    full = base + rough + cr
    out = dict(file=Path(path).name, site=site, seed=seed, cell=cell, side_m=side,
               density=meta["world_draws"].get("crater_n_ge10_per_km2"), n_craters=int(len(local)),
               rough_rms_m=rough_rms, rough_cutoff_m=p.rough_cutoff_m, rough_beta=p.rough_beta,
               rebuilt_matches_saved_m=float(np.max(np.abs(full - saved))),
               lola5=dict(fine=float(np.mean(s5 > LIMIT_DEG)), cells=None, fine_median_deg=float(np.median(s5))),
               upsampled=shares(base, cell), roughness=shares(base + rough, cell),
               craters_no_roughness=shares(base + cr, cell), run_ground=shares(full, cell),
               rough_alone=shares(rough, cell), self_affine_2m=affine)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    for pth in a.paths:
        o = layers(pth)
        print(json.dumps({k: v for k, v in o.items() if not isinstance(v, dict)}))
        print("| layer | share of 1 m points over 15 deg (2 m baseline) | share of 2 m planning cells over 15 deg | median slope |")
        print("|---|---|---|---|")
        print("self-affine LOLA at 2 m (points over 15 deg): " + ", ".join(f"{k} {100 * v:.1f}%" for k, v in o["self_affine_2m"].items()))
        for k in ("lola5", "upsampled", "roughness", "craters_no_roughness", "run_ground", "rough_alone"):
            v = o[k]
            cells = "" if v["cells"] is None else f"{100 * v['cells']:.1f}%"
            print(f"| {k} | {100 * v['fine']:.1f}% | {cells} | {v['fine_median_deg']:.1f} |")


if __name__ == "__main__":
    main()
