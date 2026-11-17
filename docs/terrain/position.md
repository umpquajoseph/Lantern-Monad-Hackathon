# Terrain position for the paper and the sim

17 Nov 2026, `ground`, branch `lantern-ground`. Brief: `docs/briefs/ground.md`.
Recommendations only. Nothing in `sim/` or `web/` is changed. The product
owner decides each row. `workflow` builds what is approved.

## The short version

- **LOLA 5 m, upsampled, is a defensible base.** At Hemmi's CR1 site, LOLA
  upsampled the way Lantern does it matches a real 1 m south-pole DTM:
  11.7% of planning cells over 15° against 14.9%, and rms slope within
  0.2° at every baseline from 2 m to 128 m.
- **The roughness layer is about twice too strong for the planner.** At
  CR1 it blocks 27.4% of planning cells. Real ground blocks 14.9% to about
  21%. Half the amplitude (0.035 m rms) gives 18%, inside that bracket.
- **The crater density sweep's top end is not lunar.** 10,000 craters of
  10 m and up per km² is 65% of geometric saturation. Measured lunar
  equilibrium is 1–10% (Gault) or 0.69–3.9% (Xiao and Werner), both quoted
  in Minton 2019. At CR1 that top end blocks 78% of planning cells.
- **No source gives the scale at which the 15° limit applies.** Lantern
  measures it across 2 m and widens it by the rover's half width. That
  widening is what makes the roughness matter so much.

## Comparator: real 1 m south-pole ground

Hemmi et al. 2025 (PSJ 6:264) published six shape-from-shading DTMs of
south-pole landing sites at 1 m per pixel, with data under CC BY 4.0. Their
sec. 4.2 says the DTMs follow the fractal trend at baselines of 10 m and up,
and flatten below 10 m from smoothing. So their 2 m slopes are a lower bound
on real 2 m slopes. I give an upper estimate too: Hemmi's own 2 m slopes
scaled up to the trend of their 16–256 m slopes.

All six sites are sunlit landing sites. None is a PSR floor. Shape from
shading needs sunlight, so no such DTM of a PSR floor exists in this repo.

Measured by `bench/terrain/slope_vs_baseline.py`, output
`bench/terrain/out/slope_vs_baseline.json`. "Cells over 15°" is the
planner's own test (`sim/rover.py` SlopeMap): slope across 2 m, widened
by the rover's 0.83 m half width, sampled on 2 m cells.

| Ground | rms slope at 2 m | at 16 m | at 128 m | Hurst, 16–256 m | Cells over 15° |
|---|---|---|---|---|---|
| Hemmi CR1, real, as measured | 7.1° | 7.0° | 6.4° | 0.95 | 14.9% |
| Hemmi CR1, scaled to its own fractal trend at 2 m | 7.8° | | | | 21.2% |
| LOLA Site01 at CR1, upsampled (Lantern, no roughness) | 7.1° | 7.1° | 6.6° | 0.95 | 11.7% |
| + roughness at 0.035 m rms (half) | 7.2° | | | | 18.0% |
| + roughness at 0.070 m rms (current) | 7.5° | 7.1° | 6.6° | 0.95 | 27.4% |
| + craters at 336 per km², no roughness | 7.4° | | | | 16.0% |
| + craters at 1,540 per km², no roughness | 8.4° | | | | 29.3% |
| + craters at 10,000 per km², no roughness | 13.4° | | | | 78.1% |

The half-roughness figure is the same, to 0.1 point, on three noise seeds.
Hurst across the six Hemmi sites is 0.84–0.95. Rosenburg 2011 Table 1 gives
0.95 (quartiles 0.92–0.97) for the south pole, from LOLA at 17 m to 2.7 km.

On the three `workflow-first3c` run grounds (craters as drawn, 336 per km²):

| Site | Cells over 15°, no roughness | half roughness | current roughness |
|---|---|---|---|
| haynes | 10.6% | 12.5% | 17.8% |
| s89_134e | 11.2% | 16.9% | 26.7% |
| s89_014e | 22.5% | 32.3% | 46.4% |

## One row per terrain choice

| # | Choice | Current value and source (`sim/settings.py` or code) | Recommended | Source for the recommendation | What the paper would say |
|---|---|---|---|---|---|
| 1 | Elevation product | LOLA polar mosaic at 5 m, PGDA product 81 `ldem_87s_5mpp` (`SiteBuild.cell_m`); Nobile at 10 m | Keep | LOLA is a laser altimeter, so it measures heights inside PSRs where cameras cannot. At CR1 it reproduces Hemmi's real 1 m DTM at baselines of 10 m and up (table above). The paper describing product 81 is not checked here | "Terrain is the LOLA 5 m south-polar mosaic, the one elevation source that measures PSR floors directly." |
| 2 | Upsampling | 1 LOLA-pixel Gaussian, then bicubic spline to 1 m (run ground) or 0.25 m (`sim/terrain.py upsample`, `Params.lola_smooth_px`, assumed) | Keep | At CR1: rms slope 7.1° against Hemmi 7.1° at 2 m. Planning cells over 15°: 11.7% against 14.9%. ShadowNav also scaled LROC DEMs down to 0.25 m (C23 sec. 4A) | "The 5 m grid is smoothed by one pixel and interpolated bicubically. At the CR1 site this reproduces the slope statistics of a 1 m NAC DTM (Hemmi et al. 2025) at every baseline from 2 to 128 m." |
| 3 | Small-scale roughness | Gaussian random field, spectrum k^-4, wavelengths under 10 m, rms 0.3 × 0.234 m = 0.070 m (`Params.rough_*`, all assumed; 0.234 m is the Shoemaker tile's median LOLA error) | Rms 0.035 m. Sensitivity case at 0 and at 0.070 m | Calibrated at CR1: the only amplitude tested whose planner-blocked share (18.0%) falls between Hemmi as measured (14.9%) and Hemmi scaled to its fractal trend (21.2%). ShadowNav added "two scales of fractal Brownian motion" texture with no stated amplitude (C23 sec. 4A) | "Relief below LOLA's resolution is added as fractal roughness of 3.5 cm rms. The amplitude is set so that, at CR1, the share of ground over the slope limit matches a 1 m NAC DTM of the same ground. We report runs without it and at twice it." |
| 4 | Crater density (10 m and up) | Log-uniform 100 to 10,000 per km² (`WorldRanges.crater_n_ge10_per_km2`), from A24 sec. I: mean spacing of 10 m craters is 100 m (fresh) to 10 m (old) | 106 to 1,540 per km², with 336 as the central case | Minton 2019 sec. 1.2: geometric saturation N(>r) = 0.385 r⁻² (15,400 per km² at 10 m). Equilibrium is 1–10% of that (Gault 1970, quoted there), 0.69–3.9% (Xiao and Werner 2015, quoted there), 2.2% at Apollo 15 (Fit 1, 336 per km²). 10,000 is 65% of saturation. ShadowNav's spacing claim cites Hiesinger 2012, not checked here | "Small craters follow the measured lunar equilibrium, 0.7 to 10% of geometric saturation (Minton et al. 2019, sec. 1.2): 106 to 1,540 craters of 10 m and up per km²." |
| 5 | Crater sizes | 7.5–20 m, cumulative slope 2 (`WorldRanges.crater_d_m`, `crater_sfd_b`) | Keep. Change the source tag of the slope 2 from "assumed" to Minton | A24 sec. V-B: 7.5–20 m detected reliably. Minton 2019 sec. 1.2: equilibrium slope about 2 (Fit 1 fixes 2, Fit 2 finds 1.8) | "Craters 7.5 to 20 m across are modelled, the range ShadowNav detects reliably (Atha et al. 2024, sec. V-B). Smaller craters are part of the roughness." |
| 6 | Crater shape | Depth/diameter drawn from C23 Table 1's ten ratios (0.033–0.125, median 0.108) with 10% jitter. Parabolic bowl. Rim at 0.2 of depth and ejecta to 3 radii, both assumed (`sim/terrain.py`) | Keep | C23 Table 1 measures craters in the LROC DEMs ShadowNav used. Search summaries of Stopar 2017 (fresh craters under 400 m: 0.11–0.17) and Mahanti 2018 (degraded 35–250 m craters: mostly under 0.1) bracket it. Neither paper was read; they are UNVERIFIED | "Crater depth-to-diameter ratios are drawn from the ten craters ShadowNav measured (Cauligi et al. 2023, Table 1)." |
| 7 | Orbital map cut | A crater is mapped if it spans 5 pixels of a 2 m orbital DEM, so 10 m and up, less a random 0–20% (`WorldRanges.orbital_*`, assumed) | Keep 10 m. Sensitivity case at 5 m and 20 m | C23 sec. 1: LROC DEMs are 0.5–5 m per pixel, "some DEMs within PSRs". C23 sec. 4A: LROC resolves craters "of around 10 m". Hemmi sec. 4.2: their 1 m DTMs resolve features "on the order of 10 m". No source gives the cut for a PSR floor | "The Scouts' starting map holds craters of 10 m and up, the size LROC resolves (Cauligi et al. 2023). A PSR-floor map may differ, so we report 5 m and 20 m cuts." |
| 8 | Slope limit | 15° (`Settings.slope_limit_deg`, `SiteBuild.slope_limit_deg`) | Keep | Brief p.7: "A 15° slope limit for routes (VIPER's own design limit)". VIPER mobility slides (Bluethmann, LSIC, 14 Nov 2024), p.4: "Slopes up to 15 deg (reqt)" | "Routes keep under 15°, VIPER's design requirement." |
| 9 | Scale of the slope test | Slope across 2 m at every point. A planning cell is blocked if any point within the rover's 0.83 m half width is over the limit (`sim/rover.py` SlopeMap) | Keep 2 m. Report as a choice. Sensitivity case: the slope at the cell centre only, without widening | No source states the baseline. A vehicle's tilt is set by the ground under its wheels, so a baseline near the rover's size, 2 m, is the natural reading. That is reasoning, not a source. The widening is a safety margin. Under the current roughness it roughly doubles the blocked share (CR1: 13.7% of points, 27.4% of cells) | "The 15° limit is applied to slope across 2 m, about the Scout's length, with a margin of half its width." |
| 10 | One slope scale throughout | Sites, survey lanes and ice deposits are chosen on LOLA's own 5 m slope (10 m baseline, `sim/psr_sites.py`, `campaign_world`). Driving uses 2 m (row 9) | State it in the paper. Or choose lanes with the 2 m test | Measured on one s89_134e run ground (`w00001-bcce1993`, 1,056 craters per km²): LOLA's own slope puts 4.5% of ground over 15°, the run ground 31.1% of planning cells (`data/terrain/run_ground/ground_slope_check.md`). Lanes that look clear at 10 m are not clear at 2 m | "Sites and survey lanes are chosen on LOLA's own slope. Driving checks slope at the Scout's scale." |

## Sensitivity cases the paper needs

1. **Roughness**: none, 0.035 m (main), 0.070 m. This is the choice that
   decided `workflow-first3c`.
2. **Crater density**: 106, 336 (main) and 1,540 per km². This replaces the
   100 to 10,000 axis of Figure 1a if row 4 is approved.
3. **Map cut**: 5, 10 (main) and 20 m. This drives the false-match rate.
4. **Slope test**: widened (main) against cell centre only.
5. **Site**: already swept (`haynes`, `s89_134e`, `s89_014e`). s89_014e is
   steep on LOLA alone (22.5% of cells blocked with no roughness), so
   expect it to stay the hard case whatever is chosen.

Crater shape (row 6) and the upsampling (row 2) need no case. Both rest on
measurements.

## What this does not show

- CR1 is a sunlit ridge, not a PSR floor. Using it to set the roughness
  assumes PSR floors are no smoother at 1–10 m. No PSR-floor DTM was found
  to test that.
- The calibration rests on one site. The other five Hemmi sites have no
  LOLA tile in `data/lola` lined up with them here.
- The upper estimate (21.2%) scales every 2 m slope by one factor. It is an
  estimate, not a measurement.
- Whether half roughness brings Scouts home is not run. It cuts blocked
  cells at s89_134e from 26.7% to 16.9%. Only a campaign run shows the
  effect on stranding.
- Not read in full: the paper behind PGDA product 81, Hiesinger 2012,
  Stopar 2017, Mahanti 2018, Cai and Fa 2020. Claims from them are marked.
