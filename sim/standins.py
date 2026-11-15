"""Stand-ins for parts that teammates may build for real.

Each sits behind the interface in docs/handoffs.md, so the real part drops in
without other changes. Each carries a STANDIN string that run_demo.py writes
into the run log, so nobody mistakes it for the real thing.

- NavStandin: step 3 (particle filter).
- rims_standin: steps 3 and 6 rim points.
- ChainStandin: step 1 (contract and sim/chain.py). Fake transaction hashes.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np

# Cauligi et al. 2023, sec. 1 (contributions): "an assumed odometry drift of
# 2% and an initial 3-sigma uncertainty of 3 m", and the filter reaches
# "less than 2 m absolute error".
ODOM_DRIFT = 0.02
SIGMA0_M = 1.0                 # 3-sigma of 3 m
SIGMA_FIX_FLOOR_M = 0.7        # assumed: inside ShadowNav's "< 2 m" result
RIM_POINT_SIGMA_M = 1.5        # assumed: how much one rim point constrains position
MIN_RIMS_FOR_FIX = 6           # assumed: fewer rim points than this give no fix

# Rim stand-in (docs/handoffs.md, "Stand-in until step 6").
RIM_RANGE_M = (3.0, 15.0)      # handoffs: near rims within 15 m; 3 m is where the camera's view starts
RIM_HALF_FOV_DEG = 30.0        # Atha et al. 2024 sec. IV: 60 deg horizontal FOV
RIM_ARC_DEG = 40.0             # assumed: half-width of the shadowed near-rim arc
RIM_ARC_STEP_DEG = 10.0        # assumed
RIM_NOISE_M = 0.10             # assumed
RIM_MISS_P = 0.25              # assumed: fraction of rim points the detector misses


class NavStandin:
    """Behaves like a working filter; it is not one.

    The estimate is the true position plus an error that grows with the
    real odometry error and shrinks when enough rim points are seen. With
    fixes off it is exactly dead reckoning. Real step 3 replaces this with
    a particle filter that sees only odometry, rims and the crater map.
    """

    STANDIN = ("nav: true pose plus a tracked error; odometry error adds to it, "
               "rim fixes shrink it (sim/standins.py NavStandin), not a particle filter")

    def __init__(self, fixes_on: bool, rng: np.random.Generator):
        self.fixes_on = fixes_on
        self.rng = rng
        self.err = rng.normal(0.0, SIGMA0_M, 2)
        self.sigma = SIGMA0_M

    def update(self, odom_err_xy, dist_m, n_rims, true_xy):
        self.err = self.err + np.asarray(odom_err_xy)
        # Drift is a bias, not white noise, so the uncertainty it adds grows
        # linearly with distance: ~20 m after 1 km at 2% (spec, run 2).
        sig_pred = self.sigma + ODOM_DRIFT * dist_m
        if self.fixes_on and n_rims >= MIN_RIMS_FOR_FIX:
            # A fix is an unbiased position measurement with noise sig_m; the
            # update is the scalar Kalman one, so the error stays consistent
            # with sigma instead of carrying the odometry bias forward.
            sig_m = max(SIGMA_FIX_FLOOR_M, RIM_POINT_SIGMA_M / math.sqrt(n_rims))
            k = sig_pred ** 2 / (sig_pred ** 2 + sig_m ** 2)
            meas_err = self.rng.normal(0.0, sig_m, 2)
            self.err = (1.0 - k) * self.err + k * meas_err
            # Floored: ShadowNav reports < 2 m error, not centimetres.
            self.sigma = max(SIGMA_FIX_FLOOR_M, math.sqrt((1.0 - k) * sig_pred ** 2))
        else:
            self.sigma = sig_pred
        return np.asarray(true_xy) + self.err, self.sigma


def rims_standin(true_pose, craters, rng):
    """Near-rim points of mapped craters in view, rover frame [x_fwd, y_left]."""
    x, y, h = true_pose
    cx, cy, d = craters[:, 0], craters[:, 1], craters[:, 2]
    R = d / 2
    near = (np.abs(cx - x) < RIM_RANGE_M[1] + 12) & (np.abs(cy - y) < RIM_RANGE_M[1] + 12)
    pts = []
    for ccx, ccy, r in zip(cx[near], cy[near], R[near]):
        phi = math.atan2(y - ccy, x - ccx)  # from crater centre toward the rover: the near rim
        for dl in np.arange(-RIM_ARC_DEG, RIM_ARC_DEG + 1e-9, RIM_ARC_STEP_DEG):
            a = phi + math.radians(dl)
            px, py = ccx + r * math.cos(a), ccy + r * math.sin(a)
            dx, dy = px - x, py - y
            fwd = dx * math.cos(h) + dy * math.sin(h)
            left = -dx * math.sin(h) + dy * math.cos(h)
            rng_m = math.hypot(fwd, left)
            if not (RIM_RANGE_M[0] <= rng_m <= RIM_RANGE_M[1]):
                continue
            if abs(math.degrees(math.atan2(left, fwd))) > RIM_HALF_FOV_DEG:
                continue
            pts.append((fwd, left))
    if not pts:
        return np.zeros((0, 2))
    p = np.array(pts) + rng.normal(0.0, RIM_NOISE_M, (len(pts), 2))
    return p[rng.random(len(p)) >= RIM_MISS_P]


RIMS_STANDIN = ("rims: near-rim arcs of mapped craters within 15 m and the 60 deg view, "
                "with noise and misses (sim/standins.py rims_standin), not vision")


class ChainStandin:
    """The step 1 interface with no chain behind it.

    Positions and sigmas are kept in whole centimetres and the check rule is
    applied as docs/handoffs.md states it. Transaction hashes start with
    0xfa4efa4e ("fake fake") so nobody mistakes them for real ones.
    """

    STANDIN = "chain: in-memory record with the handoff check rule; tx hashes are fake (0xfa4efa4e...)"
    FAKE_PREFIX = "0xfa4efa4e"

    def __init__(self):
        self.finds = []
        self.last_tx = None
        self._n = 0

    def _tx(self, *parts):
        self._n += 1
        h = hashlib.sha256(repr((self._n,) + parts).encode()).hexdigest()
        self.last_tx = self.FAKE_PREFIX + h[: 64 - 8]
        return self.last_tx

    def post_find(self, agency, x, y, sigma, reading, data_hash):
        fid = len(self.finds) + 1
        self.finds.append(dict(id=fid, agency=agency, x_cm=round(x * 100), y_cm=round(y * 100),
                               sigma_cm=round(sigma * 100), reading=reading, data_hash=data_hash,
                               agree=[], disagree=[]))
        self._tx("find", fid)
        return fid

    def check_find(self, agency, find_id, x, y, sigma, reading, agrees):
        f = self.finds[find_id - 1]
        if agency == f["agency"]:
            raise ValueError("check refused: same agency as the finder")
        dx = round(x * 100) - f["x_cm"]
        dy = round(y * 100) - f["y_cm"]
        gate = 3 * (f["sigma_cm"] + round(sigma * 100))
        if dx * dx + dy * dy > gate * gate:
            raise ValueError(f"check refused: {math.hypot(dx, dy) / 100:.2f} m from the find, gate {gate / 100:.2f} m")
        (f["agree"] if agrees else f["disagree"]).append(agency)
        return self._tx("check", find_id, agency, agrees)

    def get_finds(self):
        out = []
        for f in self.finds:
            # Assumed status rule until the contract sets it: any disagreement
            # disputes a find; otherwise one agreement confirms it.
            status = "disputed" if f["disagree"] else ("confirmed" if f["agree"] else "reported")
            out.append(dict(id=f["id"], agency=f["agency"], x=f["x_cm"] / 100, y=f["y_cm"] / 100,
                            sigma=f["sigma_cm"] / 100, reading=f["reading"],
                            agree=list(f["agree"]), disagree=list(f["disagree"]), status=status))
        return out
