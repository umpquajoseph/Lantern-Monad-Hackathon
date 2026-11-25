"""The Scouts' starting crater map: the craters an orbital product resolves.

One cut, used by the campaign (sim/campaign.py, sim/terrain.py
campaign_world) and by sim/calibrate.py. The truth keeps every crater; the map keeps

- only craters at least WorldRanges.orbital_map_min_d_m across
  (orbital_dem_px_m x orbital_resolve_px, 2 m x 5 px = 10 m by default;
  C23 sec. 1: PSR DEMs at 0.5-5 m per pixel; C23 sec. 4A: LROC resolves
  craters of around 10 m), and
- of those, each one independently with probability 1 - missing_share_large
  (a share drawn per world from WorldRanges.missing_share_large),
- each placed and sized with an orbital product's error: 1-sigma
  orbital_pos_err_px per axis and orbital_diam_err_px in diameter, in
  orbital pixels (one 2 m pixel each by default; assumed),
- and, when the cut is at ShadowCam's completeness (18 m and up,
  WorldRanges.shadowcam_map_from_cut_m), the map is taken to be made from
  ShadowCam images: craters in one image share a geolocation offset of
  about 20 m (Mazarico 2025; shadowcam_offsets()). Off in the 10 m main
  case, which draws nothing extra.

orbital_map() applies all of these. in_orbital_map() is the cut alone, which
the campaign calls with its own draws; its map_start carries no position
error (sim/campaign.py and sim/terrain.py are not changed here).

    in_map = in_orbital_map(d, u, d_min, share)

`u` is one uniform draw per crater. The campaign passes its own
`rng.random(nc)`, so its random streams are what they were before this
module existed.
"""
from __future__ import annotations

import numpy as np

from sim.settings import WorldRanges


def in_orbital_map(d, u, d_min_m: float, missing_share_large: float) -> np.ndarray:
    """Mask of craters the map holds. d: diameters (N,); u: uniform [0, 1) draws (N,)."""
    d = np.asarray(d, float)
    u = np.asarray(u, float)
    return (d >= d_min_m) & (u >= missing_share_large)


def orbital_map(craters, rng: np.random.Generator, ranges: WorldRanges | None = None,
                missing_share_large: float | None = None, errors: bool = True):
    """(map craters (M, 3), mask (N,), share). craters: truth (N, 3) [x, y, d].
    Row i of the map is truth crater np.flatnonzero(mask)[i], with its orbital error.

    Draws, in order: the share left out (from ranges.missing_share_large,
    unless given), one uniform per crater, then the map errors (errors=False:
    none, the truth's own positions and sizes)."""
    ranges = ranges or WorldRanges()
    c = np.asarray(craters, float).reshape(-1, 3)
    share = float(rng.uniform(*ranges.missing_share_large)) if missing_share_large is None else float(missing_share_large)
    mask = in_orbital_map(c[:, 2], rng.random(len(c)), ranges.orbital_map_min_d_m, share)
    m = c[mask].copy()
    if errors and len(m):
        px = ranges.orbital_dem_px_m
        m[:, :2] += rng.normal(0.0, ranges.orbital_pos_err_px * px, (len(m), 2))
        m[:, 2] = np.maximum(px, m[:, 2] + rng.normal(0.0, ranges.orbital_diam_err_px * px, len(m)))
        if ranges.orbital_map_min_d_m >= ranges.shadowcam_map_from_cut_m:
            m[:, :2] += shadowcam_offsets(m[:, :2], rng, ranges)
    return m, mask, share


def shadowcam_offsets(xy, rng: np.random.Generator, ranges: WorldRanges) -> np.ndarray:
    """(M, 2) geolocation offset of each mapped crater in a map made from
    ShadowCam images: the ground is cut into square patches of
    shadowcam_image_m (one image each; the grid's origin drawn at random) and
    every crater in a patch shares that patch's offset, 1-sigma
    shadowcam_geoloc_m per axis (Mazarico 2025). Drawn after every other
    map draw, so maps with smaller cuts keep their random streams."""
    side = ranges.shadowcam_image_m
    origin = rng.uniform(0.0, side, 2)
    tiles = np.floor((np.asarray(xy, float) - origin) / side).astype(np.int64)
    keys, inv = np.unique(tiles, axis=0, return_inverse=True)
    off = rng.normal(0.0, ranges.shadowcam_geoloc_m, (len(keys), 2))
    return off[np.asarray(inv).reshape(-1)]
