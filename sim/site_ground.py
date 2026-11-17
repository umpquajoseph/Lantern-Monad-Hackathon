"""The ground a campaign run is drawn on, for the 3D viewer.

    python -m sim.site_ground RUN_DIR [RUN_DIR ...]   # a run folder with record.json and poses.npz
    python -m sim.site_ground --site s89_134e --seed 42 --centre 23100 -20500 --size 2000

One square grid at one cell size (GroundBuild.cell_m, 1 m) covering every
pose of the run plus the dock, GroundBuild.margin_m beyond them, rounded up to
whole tiles of GroundBuild.tile_m. It holds:

- the site's LOLA window (sim/psr_sites.py, 5 m or 10 m), smoothed and
  bicubic-upsampled as sim/terrain.py does for the 0.25 m grid. Everything
  finer than the LOLA cell is INTERPOLATED or made up;
- roughness at wavelengths under two LOLA cells (sim/terrain.py Params);
- the run's own craters: exactly the craters `campaign_world(site, seed,
  ranges)` drew for the run (the record's crater map is a subset of them),
  stamped with sim/terrain.py's crater profile. Depths are not part of the
  campaign world; they are drawn here from Cauligi 2023 Table 1 ratios on a
  stream of their own, so the same world always gets the same depths.

The run itself never used this grid: the engine drove on the LOLA window and
the crater list. This is what the viewer shows.

Output: DATA/terrain/run_ground/<run_id>.npz, readable by
sim.terrain.load_terrain (height rows north, x0/y0 = centre of cell [0, 0],
craters [x, y, d] in world metres), plus crater_depth, tile_m, meta (JSON),
and <run_id>_look.png.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.psr_sites import load_site
from sim.settings import GroundBuild, WorldRanges
from sim.terrain import (C23_TABLE1, DATA, Params, _fractal_noise, _world_rng, campaign_world, save_terrain,
                         slope_deg, stamp_craters, upsample)

OUT = DATA / "terrain" / "run_ground"


def crater_depths(craters, seed, p: Params = Params()):
    """Rim-to-floor depth per crater: a Cauligi 2023 Table 1 depth/diameter
    ratio with Params.dd_jitter, as sim/terrain.py place_craters. Drawn for the
    whole world's list on stream (seed, 3), so a crater's depth depends only on
    the world, not on which part of it is built."""
    rng = _world_rng(seed, 3)
    ratios = C23_TABLE1[:, 1] / C23_TABLE1[:, 0]
    dd = rng.choice(ratios, len(craters)) * (1 + p.dd_jitter * rng.standard_normal(len(craters)))
    return np.clip(dd, 0.02, 0.15) * craters[:, 2]


def build_ground(site, seed, x_lo, x_hi, y_lo, y_hi, ranges=None, g: GroundBuild = GroundBuild(),
                 name=None, poses=None, roughness=True):
    t0 = time.time()
    ranges = ranges or WorldRanges()
    d = load_site(site)
    c5 = d["cell"]
    c = g.cell_m
    # Square, whole tiles, centred on the asked box.
    side = math.ceil(max(x_hi - x_lo + 2 * g.margin_m, y_hi - y_lo + 2 * g.margin_m) / g.tile_m) * g.tile_m
    n = int(round(side / c))
    x_left = round((x_lo + x_hi) / 2 - side / 2)
    y_bottom = round((y_lo + y_hi) / 2 - side / 2)
    x = x_left + c * (np.arange(n) + 0.5)
    y = y_bottom + c * (np.arange(n) + 0.5)

    # LOLA cells covering the box, with 8 cells of margin for smoothing and the spline.
    H, W = d["height"].shape
    j0 = int(math.floor((x_left - d["x0"]) / c5)) - 8
    i0 = int(math.floor((y_bottom - d["y0"]) / c5)) - 8
    j1 = int(math.ceil((x_left + side - d["x0"]) / c5)) + 9
    i1 = int(math.ceil((y_bottom + side - d["y0"]) / c5)) + 9
    if j0 < 0 or i0 < 0 or j1 > W or i1 > H:
        raise ValueError(f"{site}: box leaves the {W * c5 / 1000:g} km site window")
    z5 = d["height"][i0:i1, j0:j1].astype(np.float64)
    xc = d["x0"] + c5 * np.arange(j0, j1)
    yc = d["y0"] + c5 * np.arange(i0, i1)
    p = replace(Params(), cell=c, rough_cutoff_m=2 * c5)
    height = upsample(z5, xc, yc, x, y, p.lola_smooth_px)
    # Roughness under two LOLA cells. The polar mosaics have no height-error
    # product, so the RMS is Params.synthetic_toterr_m x rough_frac_of_toterr.
    # roughness=False (Settings.ground_roughness): the labelled sensitivity
    # case without this assumed layer.
    rough_rms = p.rough_frac_of_toterr * p.synthetic_toterr_m if roughness else 0.0
    if roughness:
        rng = _world_rng(seed, 4)
        height += _fractal_noise(n, c, p.rough_beta, rng, k_lo=1.0 / p.rough_cutoff_m) * rough_rms

    w = campaign_world(site, seed, ranges)
    depth = crater_depths(w.craters, seed, p)
    reach = p.ejecta_extent_r * w.craters[:, 2] / 2
    keep = ((w.craters[:, 0] + reach > x_left) & (w.craters[:, 0] - reach < x_left + side)
            & (w.craters[:, 1] + reach > y_bottom) & (w.craters[:, 1] - reach < y_bottom + side))
    cr, dep = w.craters[keep], depth[keep]
    local = cr.copy()
    local[:, 0] -= x_left
    local[:, 1] -= y_bottom
    height = stamp_craters(height, local, dep, p)

    meta = dict(
        site=site, seed=int(seed), name=name, source=f"psr-site-{site}", lola_cell_m=c5,
        box=[x_lo, x_hi, y_lo, y_hi], x_left=x_left, y_bottom=y_bottom, side_m=side,
        tiles=int(side // g.tile_m), craters=int(keep.sum()), world_craters=int(len(w.craters)),
        roughness_rms_m=rough_rms, roughness_cutoff_m=p.rough_cutoff_m,
        note=("Display ground. LOLA upsampled (INTERPOLATED below the LOLA cell) plus made-up roughness; "
              "craters are the run's world's own (campaign_world), depths drawn here (Cauligi 2023 Table 1). "
              "The run drove on the LOLA window, not on this grid."),
        ground_build=asdict(g), world_draws=w.draws, built_s=None,
    )
    meta["built_s"] = round(time.time() - t0, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{name or f'{site}-w{seed:05d}'}.npz"
    save_terrain(out, height.astype(np.float32), cr, x_left + c / 2, y_bottom + c / 2, c,
                 crater_depth=dep.astype(np.float32), tile_m=np.float64(g.tile_m), meta=np.array(json.dumps(meta)))
    print(json.dumps({k: v for k, v in meta.items() if k not in ("world_draws", "ground_build")}))
    look(out, d, poses)
    return out


def from_run(run_dir, g: GroundBuild = GroundBuild()):
    run_dir = Path(run_dir)
    r = json.loads((run_dir / "record.json").read_text())
    site = r["terrain"]["site"]
    ranges = WorldRanges(**{k: tuple(v) if isinstance(v, list) else v for k, v in r["world_ranges"].items()})
    with np.load(run_dir / "poses.npz") as f:
        poses = {k: f[k] for k in f.files}
    # poses columns 1, 2: true x, y (docs/handoffs.md, poses.npz). The dock is the site's.
    xy = np.concatenate([v[:, 1:3] for v in poses.values()] + [np.array([load_site(site)["dock"]])])
    return build_ground(site, r["seed"], xy[:, 0].min(), xy[:, 0].max(), xy[:, 1].min(), xy[:, 1].max(),
                        ranges, g, name=r["run_id"], poses=poses)


def look(path, site_d, poses=None, close_m=120.0):
    """Hillshade of the whole grid with the run's paths and the PSR edge; a
    close-up at full resolution; slope over the grid cell and over 5 m."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with np.load(path) as f:
        h, cr, x0, y0, c = f["height"], f["craters"], float(f["x0"]), float(f["y0"]), float(f["cell"])
    n = h.shape[0]
    ext = [x0 - c / 2, x0 + (n - .5) * c, y0 - c / 2, y0 + (n - .5) * c]
    gy, gx = np.gradient(h, c)
    shade = np.clip(0.5 + 0.9 * (-gx * 0.7 + gy * 0.7) / np.sqrt(1 + gx * gx + gy * gy), 0, 1)
    fig, ax = plt.subplots(1, 2, figsize=(18, 9))
    k = max(1, n // 2000)
    ax[0].imshow(shade[::k, ::k], origin="lower", extent=ext, cmap="gray", vmin=0, vmax=1)
    P = site_d["psr"]
    sx = site_d["x0"] + site_d["cell"] * np.arange(P.shape[1])
    sy = site_d["y0"] + site_d["cell"] * np.arange(P.shape[0])
    ax[0].contour(sx, sy, P.astype(float), [0.5], colors="cyan", linewidths=1)
    if poses:
        for v in poses.values():
            ax[0].plot(v[:, 1], v[:, 2], lw=.4, color="yellow", alpha=.6)
    ax[0].plot(*site_d["dock"], "*", ms=16, mfc="gold", mec="k")
    ax[0].set_xlim(ext[:2]); ax[0].set_ylim(ext[2:])
    # Close-up: the middle of the busiest path, else the grid centre.
    if poses:
        allp = np.concatenate([v[:, 1:3] for v in poses.values()])
        cx, cy = np.median(allp, axis=0)
    else:
        cx, cy = (ext[0] + ext[1]) / 2, (ext[2] + ext[3]) / 2
    j0, i0 = int((cx - close_m / 2 - ext[0]) / c), int((cy - close_m / 2 - ext[2]) / c)
    m = int(close_m / c)
    ax[1].imshow(shade[i0:i0 + m, j0:j0 + m], origin="lower", cmap="gray", vmin=0, vmax=1,
                 extent=[ext[0] + j0 * c, ext[0] + (j0 + m) * c, ext[2] + i0 * c, ext[2] + (i0 + m) * c])
    ax[0].add_patch(plt.Rectangle((ext[0] + j0 * c, ext[2] + i0 * c), close_m, close_m, fill=False, ec="w"))
    s1 = slope_deg(h, c)
    k5 = int(round(5.0 / c))
    s5 = slope_deg(h[::k5, ::k5], c * k5)
    meta = json.loads(str(np.load(path)["meta"]))
    ax[0].set_title(f"{Path(path).stem}: {meta['site']}, seed {meta['seed']}, {n} x {n} at {c:g} m "
                    f"({meta['side_m'] / 1000:g} km, {meta['tiles']}x{meta['tiles']} tiles)\n"
                    f"{meta['craters']} craters stamped; paths yellow, PSR edge cyan, dock star")
    ax[1].set_title(f"{close_m:g} m close-up at full resolution. Slope median {np.median(s1):.1f} deg over "
                    f"{c:g} m, {np.median(s5):.1f} deg over 5 m (LOLA alone: "
                    f"{np.nanmedian(site_d['slope']):.1f})")
    out = Path(path).with_name(Path(path).stem + "_look.png")
    fig.savefig(out, dpi=80, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {path} and {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="*", help="run folders (record.json, poses.npz)")
    ap.add_argument("--site")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--centre", type=float, nargs=2)
    ap.add_argument("--size", type=float, default=2000.0)
    a = ap.parse_args()
    for r in a.runs:
        from_run(r)
    if a.site:
        cx, cy = a.centre
        build_ground(a.site, a.seed, cx - a.size / 2, cx + a.size / 2, cy - a.size / 2, cy + a.size / 2)


if __name__ == "__main__":
    main()
