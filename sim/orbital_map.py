"""The Scouts' starting crater map: the craters an orbital product resolves.

One cut, used by the campaign (sim/campaign.py, sim/terrain.py
campaign_world) and by every camera run (sim/showcase.py, run_demo.py with
vision, sim/calibrate.py). The truth keeps every crater; the map keeps

- only craters at least WorldRanges.orbital_map_min_d_m across
  (orbital_dem_px_m x orbital_resolve_px, 2 m x 5 px = 10 m by default;
  C23 sec. 1: PSR DEMs at 0.5-5 m per pixel; C23 sec. 4A: LROC resolves
  craters of around 10 m), and
- of those, each one independently with probability 1 - missing_share_large
  (a share drawn per world from WorldRanges.missing_share_large).

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
                missing_share_large: float | None = None):
    """(map craters (M, 3), mask (N,), share). craters: truth (N, 3) [x, y, d].

    The share left out is drawn from ranges.missing_share_large with `rng`
    unless given, then one uniform per crater, in that order."""
    ranges = ranges or WorldRanges()
    c = np.asarray(craters, float).reshape(-1, 3)
    share = float(rng.uniform(*ranges.missing_share_large)) if missing_share_large is None else float(missing_share_large)
    mask = in_orbital_map(c[:, 2], rng.random(len(c)), ranges.orbital_map_min_d_m, share)
    return c[mask], mask, share
