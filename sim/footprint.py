"""The Scout's tilt on its footprint: the slope test of docs/terrain/position.md row 9.

A rover sits on its wheels, not on a point, so the slope that matters is the
tilt of the plane it rests on. Here that plane is the least-squares plane of
the ground's heights over a footprint-sized square centred on each point (the
ground between grid points is its bilinear interpolation, as the sim drives
it). Fitting a rover-sized planar patch and judging its tilt is how the MER
rovers' GESTALT navigator rated terrain (Goldberg, Maimone and Matthies 2002,
IEEE Aerospace, sec. 4 step 3, p.6: each cell's traversability from "a
rover-sized patch of surrounding cells and finding the best-fit plane"; its
pitch hazard is that plane's slope. docs/sources/GESTALT-Goldberg-2002.pdf).

FOOTPRINT_M: brief p.6, Scout "~1.55 m wide". The brief gives no length, so
the footprint is taken as square (assumed).
"""
from __future__ import annotations

import math

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


def tilt_grad(height, cell: float, side: float = FOOTPRINT_M):
    """(gx, gy) float32: the gradient (dz/dx, dz/dy) of the footprint plane
    centred on every grid point. Rows are y, columns x."""
    h = np.asarray(height, dtype=np.float32)
    kx, ky = tilt_kernels(cell, side)
    return correlate(h, kx, mode="nearest"), correlate(h, ky, mode="nearest")


def tilt_deg(height, cell: float, side: float = FOOTPRINT_M):
    """Tilt in degrees of the footprint plane centred on every grid point.
    Rows are y, columns x. NaN heights give NaN tilt nearby."""
    gx, gy = tilt_grad(height, cell, side)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


# ---------------------------------------------------------- pitch and roll
# A rover's slope limits depend on its heading (ops/HANDOFF.md, 20 Nov
# evening). On a plane of gradient g = (gx, gy), a rover facing heading h
# (unit u) sits at
#     tan(pitch) = |g . u|   the slope along its heading,
#     tan(roll)  = |g x u|   the slope across it,
# so on a slope s taken at angle a off the fall line, pitch = atan(tan s cos a)
# and roll = atan(tan s sin a). Both are the same driving forward or back.
#
# A gradient whose direction is not known (an orbital sample with no LOLA
# height slope, a slope test that keeps only magnitudes) is stored as
# gx = tan(slope), gy = NaN and judged against the lower of the two limits:
# the old heading-free rule.

def severity(gx, gy, heading, tan_pitch: float, tan_roll: float):
    """How far over its limits a rover facing `heading` stands on gradient
    (gx, gy): max(tan pitch / tan pitch limit, tan roll / tan roll limit).
    At or under 1 is within both limits. Broadcasts over all arguments."""
    c, s = np.cos(heading), np.sin(heading)
    gx = np.asarray(gx, dtype=np.float64)
    gy = np.asarray(gy, dtype=np.float64)
    known = np.isfinite(gy)
    gyk = np.where(known, gy, 0.0)
    along = np.abs(gx * c + gyk * s) / tan_pitch
    across = np.abs(gyk * c - gx * s) / tan_roll
    return np.where(known, np.maximum(along, across), np.abs(gx) / min(tan_pitch, tan_roll))


def open_any_heading(gx, gy, tan_pitch: float, tan_roll: float):
    """True where some heading is within both limits. The best heading puts
    tan pitch / tan_pitch = tan roll / tan_roll, so the ground is open to
    some heading when |g| <= hypot(tan_pitch, tan_roll)."""
    gx = np.asarray(gx, dtype=np.float64)
    gy = np.asarray(gy, dtype=np.float64)
    known = np.isfinite(gy)
    return np.where(known, np.hypot(gx, np.where(known, gy, 0.0)) <= math.hypot(tan_pitch, tan_roll),
                    np.abs(gx) <= min(tan_pitch, tan_roll))


def grad_from_slope(slope_deg, height, cell: float):
    """(gx, gy) for a slope product (deg, NaN = no data, read as 90 deg) whose
    direction comes from a height grid on the same samples (rows y, columns
    x, spacing cell): the product's own magnitude, pointing up the height
    grid's gradient. Where the height grid gives no direction, gy is NaN."""
    S = np.asarray(slope_deg, dtype=np.float64)
    T = np.tan(np.radians(np.where(np.isfinite(S), np.clip(S, 0.0, 89.9), 89.9)))
    if height is None:
        return T.astype(np.float32), np.full(T.shape, np.nan, np.float32)
    dzdy, dzdx = np.gradient(np.asarray(height, dtype=np.float64), cell)
    n = np.hypot(dzdx, dzdy)
    ok = np.isfinite(n) & (n > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        gx = np.where(ok, T * dzdx / n, T)
        gy = np.where(ok, T * dzdy / n, np.nan)
    return gx.astype(np.float32), gy.astype(np.float32)


def _square(side: float):
    s = np.arange(-side / 2 + _SUB_M / 2, side / 2, _SUB_M)
    X, Y = np.meshgrid(s, s)
    return X.ravel(), Y.ravel()


def plane_at(height_fn, x: float, y: float, heading: float, side: float = FOOTPRINT_M):
    """(z0, gx, gy): the least-squares plane z = z0 + gx dx + gy dy of the ground
    under a side x side footprint centred on (x, y) and turned to `heading`.
    height_fn(xs, ys) gives the ground (bilinear between grid points, as the
    sim drives it). A square's second moments are the same in every
    direction, so the fit is that of tilt_kernels() at any heading."""
    a, b = _square(side)
    c, s = np.cos(heading), np.sin(heading)
    dx, dy = c * a - s * b, s * a + c * b
    z = np.asarray(height_fn(x + dx, y + dy), dtype=np.float64)
    z0 = float(z.mean())
    gx = float((dx * (z - z0)).sum() / (dx * dx).sum())
    gy = float((dy * (z - z0)).sum() / (dy * dy).sum())
    return z0, gx, gy


def body_axes(gx: float, gy: float, heading: float):
    """(3, 3) rotation, columns the body's forward, left and up in the local
    world frame, for a rover resting on the plane of gradient (gx, gy) and
    facing `heading`: up is the plane's normal, forward the heading's
    direction projected onto the plane (so the heading's yaw is kept)."""
    c, s = np.cos(heading), np.sin(heading)
    n = np.array([-gx, -gy, 1.0])
    n /= np.linalg.norm(n)
    f = np.array([c, s, gx * c + gy * s])
    f /= np.linalg.norm(f)
    return np.column_stack([f, np.cross(n, f), n])
