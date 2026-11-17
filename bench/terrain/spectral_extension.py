"""Roughness amplitude by extending the measured height spectrum below LOLA's reach.

    python bench/terrain/spectral_extension.py

Precedent for adding detail finer than an orbital DEM is to extend that DEM's
own fractal spectrum: Barker et al. 2021 (PSS 203, 105119, sec. 4, Figs. 10-11)
add a high-pass fractal with H ~ 1 fitted to the LOLA sites' power spectra;
Allan et al. 2019 (IEEE Aerospace) scale up the DEM's own high-frequency part.
This script does that for each ground and reports the rms height the extended
spectrum puts at wavelengths of 2-10 m, the band Lantern's roughness layer
fills (sim/site_ground.py: under 10 m, on a 1 m grid). Lantern uses 0.070 m.

Method: Hann-windowed, plane-detrended square window; radially binned
spectral density P(k) (m^2 per (cycle/m)^2); a power law P = A k^-beta fitted
over wavelengths [lam_lo, lam_hi] where the DEM resolves the ground
(LOLA: 40-400 m, above its 14-30 m effective resolution, bench/terrain/out/
lola_coverage.json; Hemmi: 20-200 m, above its smoothing below 10 m).
Then rms^2 = 2 pi A * integral of k^(1-beta) dk over 1/10 to 1/2 cycles/m.
H = (beta - 2) / 2.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench" / "terrain"))
from slope_vs_baseline import DATA, read_tif  # noqa: E402

OUT = ROOT / "bench" / "terrain" / "out"


def psd(z, d):
    z = z.copy()
    n = z.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    A = np.column_stack([xx.ravel(), yy.ravel(), np.ones(n * n)])
    coef, *_ = np.linalg.lstsq(A, z.ravel(), rcond=None)
    z -= (A @ coef).reshape(n, n)
    w = np.outer(np.hanning(n), np.hanning(n))
    Z = np.fft.fft2(z * w)
    S = np.abs(Z) ** 2 / (w ** 2).sum()          # per-mode variance, window-corrected
    P = S * d * d / 1.0                           # density: variance per (cycle/m)^2 = S * (N d)^2 / N^2
    k = np.hypot(*np.meshgrid(np.fft.fftfreq(n, d), np.fft.fftfreq(n, d)))
    return k.ravel(), P.ravel()


def fit(k, P, lam_lo, lam_hi):
    sel = (k >= 1 / lam_hi) & (k <= 1 / lam_lo)
    edges = np.logspace(np.log10(1 / lam_hi), np.log10(1 / lam_lo), 13)
    kb, Pb = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        m = sel & (k >= a) & (k < b)
        if m.sum() > 3:
            kb.append(np.exp(np.mean(np.log(k[m]))))
            Pb.append(np.mean(P[m]))
    slope, c = np.polyfit(np.log(kb), np.log(Pb), 1)
    return -slope, math.exp(c)


def band_rms(beta, A, k1=0.1, k2=0.5):
    e = 2 - beta
    integ = (k2 ** e - k1 ** e) / e if abs(e) > 1e-9 else math.log(k2 / k1)
    return math.sqrt(2 * math.pi * A * integ)


def measured_band_rms(k, P, n, d, k1=0.1, k2=0.5):
    m = (k >= k1) & (k <= k2)
    return math.sqrt(P[m].sum() / (n * d) ** 2)


def square(z, size):
    """Largest finite square of `size` px from the middle of z."""
    H, W = z.shape
    i0, j0 = (H - size) // 2, (W - size) // 2
    s = z[i0:i0 + size, j0:j0 + size]
    return s if np.isfinite(s).all() else None


def report(name, z, d, lam_lo, lam_hi):
    k, P = psd(z, d)
    beta, A = fit(k, P, lam_lo, lam_hi)
    r = dict(cell_m=d, side_m=z.shape[0] * d, fit_wavelengths_m=[lam_lo, lam_hi], beta=beta, H=(beta - 2) / 2,
             extended_rms_2_10m=band_rms(beta, A))
    if d <= 1.0:
        r["measured_rms_2_10m"] = measured_band_rms(k, P, z.shape[0], d)
    print(name, json.dumps(r))
    return r


def main():
    res = {}
    # Hemmi CR1, real 1 m: the largest gap-free square.
    zh, ch, _, _ = read_tif(DATA / "hemmi2025/DTMs/cr1_roi_sfs_1m-DEM.tif")
    for size in (1400, 1200, 1024, 800):
        s = square(zh, size)
        if s is not None:
            res["hemmi_cr1"] = report("hemmi_cr1", s, ch, 20, 200)
            break
    # LOLA Site01 5 m over CR1.
    z5, c5, x5l, y5t = read_tif(DATA / "lola/Site01/Site01_final_adj_5mpp_surf.tif")
    j = int((-11040 - x5l) / c5)
    i = int((y5t - -12154) / c5)
    res["lola_site01_cr1"] = report("lola_site01_cr1", z5[i - 200:i + 200, j - 200:j + 200], c5, 40, 400)
    # LOLA 5 m at each PSR site, centred on the dock.
    for s in ("haynes", "s89_014e", "s89_134e"):
        d = np.load(DATA / f"terrain/psr_sites/{s}.npz", allow_pickle=True)
        h = d["height"].astype(np.float64)
        n = h.shape[0]
        res[f"lola_{s}"] = report(f"lola_{s}", h[n // 2 - 400:n // 2 + 400, n // 2 - 400:n // 2 + 400], float(d["cell"]), 40, 400)
    res["lantern_current_rms_m"] = 0.3 * 0.234
    (OUT / "spectral_extension.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
