"""The Scout's tilt on its footprint: the slope test of docs/terrain/position.md row 9.

A rover sits on its wheels, not on a point, so the slope that matters is the
tilt of the plane it rests on. Here that plane is the least-squares plane of
the ground's heights over a footprint-sized square centred on each point (the
ground between grid points is its bilinear interpolation, as the sim drives
it). Fitting a rover-sized planar patch and judging its tilt is how the MER
rovers' GESTALT navigator rated terrain (Goldberg, Maimone and Matthies 2002,
IEEE Aerospace, https://robotics.jpl.nasa.gov/media/documents/aero.pdf; known
from a search summary, the paper itself not yet read: UNVERIFIED).

FOOTPRINT_M: brief p.6, Scout "~1.55 m wide". The brief gives no length, so
the footprint is taken as square (assumed).
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import correlate

FOOTPRINT_M = 1.55   # brief p.6 "Size, stowed ~1.55 m wide"; length assumed equal (square)
_SUB_M = 0.05        # sample spacing inside the footprint for the fit (numerics only)
_KERNELS = {}


def tilt_kernels(cell: float, side: float = FOOTPRINT_M):
    """(kx, ky): correlation kernels on a grid of spacing `cell` whose output is
    the least-squares plane gradient (dz/dx, dz/dy) over a side x side square,
    the heights inside it bilinear between grid points."""
    key = (round(float(cell), 6), round(float(side), 6))
    if key in _KERNELS:
        return _KERNELS[key]
    s = np.arange(-side / 2 + _SUB_M / 2, side / 2, _SUB_M)
    X, Y = np.meshgrid(s, s)
    X, Y = X.ravel(), Y.ravel()
    r = int(np.ceil(side / 2 / cell)) + 1
    kx = np.zeros((2 * r + 1, 2 * r + 1))
    ky = np.zeros_like(kx)
    fx, fy = X / cell, Y / cell
    j0, i0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    tj, ti = fx - j0, fy - i0
    wx, wy = X / (X ** 2).sum(), Y / (Y ** 2).sum()
    for di, dj, w in ((0, 0, (1 - ti) * (1 - tj)), (0, 1, (1 - ti) * tj), (1, 0, ti * (1 - tj)), (1, 1, ti * tj)):
        np.add.at(kx, (i0 + di + r, j0 + dj + r), wx * w)
        np.add.at(ky, (i0 + di + r, j0 + dj + r), wy * w)
    _KERNELS[key] = (kx.astype(np.float32), ky.astype(np.float32))
    return _KERNELS[key]


def tilt_deg(height, cell: float, side: float = FOOTPRINT_M):
    """Tilt in degrees of the footprint plane centred on every grid point.
    Rows are y, columns x. NaN heights give NaN tilt nearby."""
    h = np.asarray(height, dtype=np.float32)
    kx, ky = tilt_kernels(cell, side)
    gx = correlate(h, kx, mode="nearest")
    gy = correlate(h, ky, mode="nearest")
    return np.degrees(np.arctan(np.hypot(gx, gy)))
