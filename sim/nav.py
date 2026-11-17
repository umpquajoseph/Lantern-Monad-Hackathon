"""Step 3, navigation: a ShadowNav particle filter that estimates position
from odometry and crater rim points, matched to the orbital crater map.

Follows Cauligi et al. 2023 (docs/sources/ShadowNav-Cauligi-2023.*):
  Alg. 1  Q-Score: mean distance from each observed rim point (moved into the
          world by the particle's pose) to the nearest mapped crater edge;
          Q = min(1, 1 / mean distance).
  Alg. 2  log-domain weights, w += log Q - min(log Q); resample when the
          effective number of particles falls below the threshold.
  Alg. 3  systematic resampling.
  Sec. 6B settings: Ns = 100 particles, Neff threshold 50, 2% odometry noise.

Interface (docs/handoffs.md, step 3):
  in   odometry (distance_m, dheading_rad) each step, rim points (M, 2)
       [x_forward, y_left] in the rover frame, the crater map (N, 3).
  out  estimate (x, y) in world metres and sigma = sqrt(trace(cov) / 2)
       of the particle cloud.
The filter never sees the true pose. It is given a starting belief (mean
and sigma) once, as a rover would be told where it was landed.

Heading is dead-reckoned from the odometry's heading changes; the spec
assumes heading is known well (star tracker), and the odometry heading noise
is small. With fixes off no measurement update runs, so the estimate is
plain dead reckoning and sigma grows with the 2% drift model.

The filter's motion model is its own (sim/settings.py pf_*), from ShadowNav,
not the truth's odometer (sim/rover.py, drift_frac and odom_*): each particle
carries an odometer scale error (C23 sec. 6B, 2%) and every step adds 2D
noise of 2% of the step per axis (A24 sec. III-E1, eqs. 3-6). PFNoise holds
it; sim/calibrate.py also runs the old model that copied the truth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

# Cauligi et al. 2023, sec. 6B: "Ns = 100 particles ... Neff,thresh = 50".
N_PARTICLES = 100
NEFF_THRESH = 50
# The filter's noise model is its own (sim/settings.py pf_*), never the
# truth's odometry model (odom_* and drift_frac, used by sim/rover.py).
try:
    from sim.settings import Settings as _Settings
except ImportError:  # run as a script from sim/
    from settings import Settings as _Settings
_S = _Settings()
# Cauligi et al. 2023, sec. 6B: "2% odometry noise". Modelled as a fixed
# scale error per particle, so the spread grows with distance (~20 m after
# 1 km), as the drift of a biased odometer does.
ODOM_DRIFT = _S.pf_drift_sigma
# Cauligi et al. 2023, sec. 1: "initial 3-sigma uncertainty of 3 m" -> 1 m.
# (Sec. 6B writes sigma_0 = 3 m; this follows sec. 1 and sim/standins.py.)
SIGMA0_M = _S.pf_sigma0_m
# Alg. 1 line 1: a negligibly small positive value to avoid divide-by-zero.
Q_EPS = 1e-6

STEP_NOISE_FRAC = _S.pf_step_noise_frac     # A24 eqs. 3-6: 2D noise per axis, fraction of the step
HEADING_WALK_RAD = _S.pf_heading_walk_rad    # A24 sec. III-A: position-only filter, heading known (0)
SCALE_WALK = _S.pf_scale_walk                # assumed: per-step jitter of each particle's scale error
CRATER_NEIGHBOURS = 6       # assumed: nearest mapped craters checked per rim point (enough within 15 m)


@dataclass(frozen=True)
class PFNoise:
    """The filter's motion model. Defaults: sim/settings.py pf_* (sourced there).
    isotropic: step noise in x and y (A24 eq. 3) rather than along the track only."""
    drift_sigma: float = ODOM_DRIFT
    step_noise_frac: float = STEP_NOISE_FRAC
    heading_walk_rad: float = HEADING_WALK_RAD
    scale_walk: float = SCALE_WALK
    sigma0_m: float = SIGMA0_M
    isotropic: bool = True


# The model nav.py used before 16 Nov 2026: its noise copied the truth's
# odometer (sim/rover.py odom_noise_frac, odom_heading_noise_rad), along the
# track only. Kept for sim/calibrate.py's before/after numbers; never a default.
PF_NOISE_COPIED_TRUTH = PFNoise(drift_sigma=_S.drift_frac, step_noise_frac=_S.odom_noise_frac,
                                heading_walk_rad=_S.odom_heading_noise_rad, scale_walk=0.001,
                                isotropic=False)

STANDIN = None  # this is the real step 3, not a stand-in


class ParticleNav:
    """ShadowNav particle filter. Drop-in for sim/standins.NavStandin.

    Construction matches NavStandin(fixes_on, rng). `err` is the start error
    the simulation applies to the rover's first belief (drawn here so the
    run's random streams are unchanged); the filter itself never uses it.
    """

    NAME = ("nav: ShadowNav particle filter (Cauligi et al. 2023, Algs. 1-3), 100 particles, "
            "on odometry and rim points only (sim/nav.py)")

    def __init__(self, fixes_on: bool, rng: np.random.Generator, craters=None, noise: PFNoise | None = None):
        self.fixes_on = fixes_on
        self.rng = rng
        self.noise = noise or PFNoise()
        self.err = rng.normal(0.0, self.noise.sigma0_m, 2)   # simulation: how wrong the landing belief is
        self.sigma = self.noise.sigma0_m
        self.craters = None
        self.tree = None
        if craters is not None:
            self.set_map(craters)
        self.p = None                              # particles (Ns, 2) world xy
        self.n_updates = 0
        self.n_resamples = 0

    # -- setup --------------------------------------------------------------
    def set_map(self, craters):
        c = np.asarray(craters, dtype=np.float64)
        self.craters = c
        self.tree = cKDTree(c[:, :2])

    def start(self, belief_xy, heading):
        """Starting belief: mean belief_xy, sigma noise.sigma0_m; heading as given.

        Particles come in mirrored pairs about the mean (antithetic), so with
        fixes off the mean stays exactly on the dead-reckoned track."""
        h = N_PARTICLES // 2
        off = self.rng.normal(0.0, self.noise.sigma0_m, (h, 2))
        s = self.rng.normal(0.0, self.noise.drift_sigma, h)
        th = np.zeros(h)
        self.p = np.asarray(belief_xy, float) + np.concatenate([off, -off])
        self.s = np.concatenate([s, -s])           # odometry scale error per particle
        self.th = np.concatenate([th, -th])        # heading error per particle
        self.heading = float(heading)
        self.logw = np.zeros(N_PARTICLES)

    # -- Alg. 2 ---------------------------------------------------------------
    def predict(self, d_odom, dh_odom):
        """Move every particle by the odometry, through its own error model."""
        self.heading += dh_odom
        if d_odom <= 0:
            return
        h = N_PARTICLES // 2
        nz = self.noise
        if nz.heading_walk_rad > 0:
            w_h = self.rng.normal(0.0, nz.heading_walk_rad, h)
            self.th = self.th + np.concatenate([w_h, -w_h])
        w_s = self.rng.normal(0.0, nz.scale_walk, h)
        self.s = self.s + np.concatenate([w_s, -w_s])
        a = self.heading + self.th
        if nz.isotropic:
            # A24 eq. 3: b_k+1 = b_k + s_k+1 + w_k, w_k ~ N(0, (p |s|)^2) in x and y.
            d = d_odom * (1.0 - self.s)
            w = self.rng.normal(0.0, nz.step_noise_frac * d_odom, (h, 2))
            self.p = self.p + np.column_stack([d * np.cos(a), d * np.sin(a)]) + np.concatenate([w, -w])
        else:
            w_d = self.rng.normal(0.0, nz.step_noise_frac * d_odom, h)
            d = d_odom * (1.0 - self.s) + np.concatenate([w_d, -w_d])
            self.p = self.p + np.column_stack([d * np.cos(a), d * np.sin(a)])

    def q_score_log(self, rims):
        """Alg. 1 for every particle at once: log Q, shape (Ns,)."""
        rims = np.asarray(rims, float).reshape(-1, 2)
        m = len(rims)
        a = self.heading + self.th                  # (Ns,)
        ca, sa = np.cos(a)[:, None], np.sin(a)[:, None]
        # Rover frame [x_forward, y_left] -> world, per particle (Alg. 1 line 3).
        wx = self.p[:, 0:1] + ca * rims[None, :, 0] - sa * rims[None, :, 1]
        wy = self.p[:, 1:2] + sa * rims[None, :, 0] + ca * rims[None, :, 1]
        pts = np.column_stack([wx.ravel(), wy.ravel()])
        k = min(CRATER_NEIGHBOURS, len(self.craters))
        _, idx = self.tree.query(pts, k=k)
        idx = np.asarray(idx).reshape(len(pts), k)
        c = self.craters[idx]                       # (P, k, 3)
        # Distance to each crater's edge, not its centre (Alg. 1 line 4).
        d_edge = np.abs(np.hypot(c[..., 0] - pts[:, 0:1], c[..., 1] - pts[:, 1:2]) - c[..., 2] / 2)
        dcr = d_edge.min(axis=1).reshape(N_PARTICLES, m)
        q_inc = Q_EPS + dcr.sum(axis=1)             # Alg. 1 lines 1-6
        q = np.minimum(1.0, (q_inc / m) ** -1)      # Alg. 1 line 7
        return np.log(q)

    def correct(self, rims):
        q = self.q_score_log(rims)                  # Alg. 2 line 9
        self.logw = self.logw + q - q.min()         # Alg. 2 lines 11-14
        self.n_updates += 1
        w = self._normalised()
        neff = 1.0 / np.sum(w * w)                  # Alg. 2 line 15
        if neff < NEFF_THRESH:                      # Alg. 2 lines 16-18
            keep = self._systematic(w)
            self.p, self.s, self.th = self.p[keep], self.s[keep], self.th[keep]
            self.logw = np.zeros(N_PARTICLES)
            self.n_resamples += 1

    def _normalised(self):
        w = np.exp(self.logw - self.logw.max())     # Alg. 3 lines 1-5, stable form
        return w / w.sum()

    def _systematic(self, w):
        """Alg. 3: one draw u0 in [0, 1/Ns), then evenly spaced picks."""
        q = np.cumsum(w)
        q[-1] = 1.0
        u = self.rng.uniform(0.0, 1.0 / N_PARTICLES) + np.arange(N_PARTICLES) / N_PARTICLES
        return np.searchsorted(q, u, side="left")

    def belief(self):
        """(estimate (x, y), sigma) with sigma = sqrt(trace(cov) / 2) (docs/handoffs.md)."""
        w = self._normalised()
        mu = w @ self.p
        dev = self.p - mu
        cov_trace = float(np.sum(w * np.einsum("ij,ij->i", dev, dev)))
        return mu, math.sqrt(cov_trace / 2.0)

    def covariance(self):
        """(mean (2,), weighted covariance (2, 2)) of the particles (C23 sec. 6B)."""
        w = self._normalised()
        mu = w @ self.p
        dev = self.p - mu
        return mu, (w[:, None] * dev).T @ dev

    def sigma_max(self):
        """C23 sec. 6B's reported uncertainty: sqrt of the largest covariance eigenvalue."""
        return math.sqrt(max(0.0, float(np.linalg.eigvalsh(self.covariance()[1])[-1])))

    # -- the per-step call ------------------------------------------------------
    def step(self, odom, rims):
        """One filter step: odometry (distance_m, dheading_rad), rims (M, 2).
        Returns (estimate (x, y), sigma)."""
        d, dh = odom
        self.predict(d, dh)
        if self.fixes_on and rims is not None and len(rims) > 0:
            self.correct(rims)
        est, self.sigma = self.belief()
        return est, self.sigma
