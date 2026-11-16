# Worker `realism`: three realism fixes, then calibration against ShadowNav

Branch `campaign-realism`, worktree `C:\Monad-Hackathon-realism`.
Read `docs/briefs/live-common.md` first.

**Goal.** Lantern's camera navigation currently looks too good. Fix the three
causes, then measure how good it really is against ShadowNav.

1. **The starting crater map misses small craters**, as an orbital map
   would. The Scouts' map holds only craters the orbital product resolves
   (the draft cites 0.5 to 5 m per pixel orbital DEMs for PSRs, from
   ShadowNav 2023; check it there), plus a random share of larger ones left
   out. The truth keeps all. `sim/showcase.py` `orbital_map` cuts at 10 m
   today. Make one shared cut that the campaign and every camera run use,
   with its parameters in `sim/settings.py`.
2. **The filter stops copying the true noise.** `sim/nav.py:46-48`
   (`STEP_NOISE_FRAC`, `HEADING_WALK_RAD`, `SCALE_WALK`) are set "as
   sim/rover.py's odometry", so the filter knows the truth's noise exactly.
   The filter's noise model must be its own assumption, sourced or marked
   `assumed`, and not tuned against the truth.
3. **Rim depth carries realistic noise.** `sim/vision.py` places rim points
   with the renderer's depth. Give stereo depth a noise model that grows
   with range as stereo does (baseline, focal length and disparity noise;
   take the camera from the brief and ShadowNav where they give it, else
   `assumed`).

**Calibration.** Then build a calibration set: traverses in the style of
ShadowNav's three trajectories, on the PSR sites' ground, many seeds each.
Measure Lantern's final fix error (mean and s.d.), its reported uncertainty
(ShadowNav reports the square root of the largest covariance eigenvalue,
Cauligi 2023 sec. 6B; keep that apart from radial error), and the
false-match rate (rims paired with the wrong crater, scored against the
truth, for example with `sim/rim_truth.py`). Write a table beside ShadowNav
Table 2 (traj. 1: 3.84 ± 2.78 m, traj. 2: 1.75 ± 0.78 m, traj. 3:
1.68 ± 0.70 m; check these in the source). Say plainly where Lantern is
better or worse and why. Do not tune anything to make the numbers match
ShadowNav.

**Owned files.** `sim/nav.py`, `sim/vision.py`, the orbital-map cut, the
calibration script and its output. Worker `live-nav` owns `sim/campaign.py`
and calls yours. Keep the `ParticleNav` and `Vision` interfaces stable, or
say exactly what changed in your report.

**Done when** the three fixes are in, each with a before and after number,
and the calibration table exists with the command that made it.
