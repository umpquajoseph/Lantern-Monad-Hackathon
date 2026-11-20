# Terrain position for the paper and the sim

17 Nov 2026, `ground`, branch `lantern-ground`. Brief: `docs/briefs/ground.md`
(22650c5). Recommendations only. Nothing in `sim/` or `web/` is changed.
Each recommendation is chosen for fidelity to the Moon and to precedent,
not for what it does to the Scouts.

## The position

Lantern builds the ground the way current lunar rover simulators do. It
starts from the best orbital elevation map of the site and adds the detail
that map cannot resolve: fractal roughness, plus craters drawn from measured
size distributions. VIPER's mission simulator does this (1 m map, fractal
upscaling, craters and rocks from size-frequency studies). So do NASA
Ames's rover simulator (Allan et al. 2019) and the LOLA team's own error
model (Barker et al. 2021, fractal with H ≈ 1, the same spectrum Lantern
uses). Lantern's base map is LOLA, because a laser altimeter is the only
instrument that measures heights on a shadowed crater floor.

What Lantern adds to precedent is a check against real ground. No
precedent we read reports one. At the CR1 south-pole site, the pipeline
is compared with a published 1 m DTM (Hemmi et al. 2025) on the planner's
own measure, the share of ground over 15°. That check sets the roughness
amplitude, and the crater density comes from measured lunar equilibrium.
Two parts of the ground are not modelled and the paper says so: rocks,
and anything specific to shadowed floors that no 1 m map yet shows.

## Precedent: how lunar simulators and rover studies make terrain

Every entry was read in its own PDF (`docs/sources/`), except where marked.

| Study | Base elevation data | Detail below the base | How it is justified | Peer reviewed |
|---|---|---|---|---|
| ShadowNav, Atha et al. 2024, sec. IV-A; Cauligi et al. 2023, sec. 4A | LROC DEM, 2–5 m, scaled to 0.25 m | "two scales of fractal Brownian motion", amplitude not given, "for stereo algorithms to utilize". Renders only; no driving or slope model | Rendering realism for stereo | Yes: IEEE Trans. Field Robotics 1 (2024); IEEE Aerospace 2023 |
| VIPER simulator, Fong slides 29 Apr 2026, p.29 | 1 m shape-from-shading DEM | "Upscale DEM via fractal synthesis (4 cm/pixel)"; craters and rocks "based on size-frequency distribution studies"; "age effects (e.g., crater shape)" | VIPER Environmental Specification VIPER-MSE-SPEC-001 (2022), not public | No (slides) |
| VIPER traverse planning, Ennico-Smith et al. 2023; Shirley and Balaban 2022 | Strategic plans on 20 m LOLA-based maps; tactical plans on 1 m SfS DEM | 15° slope limit applied on those maps. Hazards smaller than that are found by the rover's cameras | Mission design | No (abstract; slides) |
| Allan et al. 2019, IEEE Aerospace (Ames simulator for Resource Prospector, VIPER's predecessor) | Orbital DEM | DEM's own high-frequency part scaled up by diamond-square; craters from Surveyor size-frequency laws refit for polar regions from LRO counts; craters aged after Fassett and Thomson 2014; rocks from Surveyor laws | Published crater and rock statistics. No comparison with real ground reported | Yes (conference) |
| Barker et al. 2021, PSS 203, sec. 4, Figs. 10–11 | LOLA 5 m site maps | High-pass fractal, Hurst H ≈ 1 fitted to the four sites' power spectra, added to the smoothed map | Spectra of the sites themselves (Fig. 10). Used to make error clones, not driving terrain | Yes |
| DLES, NASA JSC, 2024 abstract | LOLA 5 m | Artificial craters scattered from NAC crater statistics of each region, to 20 cm per pixel | Crater counts from NAC images | Abstract only; the full paper was not found |
| OmniLRS, Richard et al., ICRA 2024, sec. III-A | LRO DEMs, which "only offer details of about 10 meters" | GAN super-resolution, or procedural craters of 0.5–10 m with 100 profiles measured on SLDEM; densities set by the user | Profiles measured. Density left to the user, pointing to "ample literature" | Yes |
| PANGU, Rowell et al. c. 2012 (ESA-funded) | Optional LOLA or HRSC DEM | Fractional Brownian motion with one roughness parameter; craters (Melosh, McGetchin shapes, with ageing) and boulders "according to distribution functions taken from planetary science literature" | Literature distributions | Conference paper; venue not printed in the copy |

Common ground across all of them: an orbital map, fractal detail below it,
and craters placed from a size distribution. Every one that simulates
driving (Allan, VIPER, OmniLRS, PANGU) also places rocks. None reports
checking its detail layer against a measured map at rover scale.

## Measurements that constrain the ground, by scale

| Scale | Measurement | Value | Source |
|---|---|---|---|
| 17 m to 2.7 km | LOLA slopes and Hurst exponent, south pole | Median 17 m slope 7.6° (quartiles 4.2–12.4°). Hurst 0.95 (0.92–0.97) | Rosenburg et al. 2011, Table 1 |
| LOLA's real resolution at our sites | Empty circle between LOLA returns on PSR floors | 8–12% of 5 m pixels hold a return. Median gap 14 m, 90th percentile 22–30 m. CR1 is the same | Measured, `bench/terrain/lola_coverage.py`, by Barker et al. 2023 sec. 2's definition. Barker 2023: "Much of the Artemis zone ... has an effective resolution of ~20 m/pix" |
| LOLA height error | Site maps at 5 m | Height 0.30–0.50 m rms, slope 1.5–2.5° rms | Barker et al. 2021, abstract |
| 10 m to 512 m | 1 m SfS DTMs of six sunlit south-pole sites | Hurst 0.84–0.95 over 16–256 m. Smoothed below 10 m | Hemmi et al. 2025, sec. 4.2; measured, `bench/terrain/slope_vs_baseline.py` |
| 2–10 m wavelengths | Rms height implied by extending each map's own spectrum (Barker 2021, Allan 2019 method) | LOLA sites, medians over 16 windows: haynes 0.048 m, s89_014e 0.033 m, s89_134e 0.051 m (window range 0.013–0.092 m). Hemmi windows, as measured (a lower bound): 0.019–0.027 m | Measured, `bench/terrain/spectral_extension.py` |
| Craters, 10 m and up | Small-crater equilibrium density | 0.69–10% of geometric saturation, so 106–1,540 per km² of 10 m and up. Apollo 15: 336 per km² | Minton et al. 2019, sec. 1.2, quoting Gault 1970 and Xiao and Werner 2015 |
| Craters, 18–900 m, 80–90°S | ShadowCam crater counts | Complete from 18 m. Flatter ground keeps more small craters. PSR terrain "slightly less cratered than non-PSR terrain" at comparable slopes | Mazarico et al. 2025, LPSC abstract |
| Crater shape | Depth/diameter of craters 2.3–11.9 m in ShadowNav's DEM | 0.033–0.125, median 0.108 | Cauligi et al. 2023, Table 1 |
| Rocks | South-pole boulder catalogue from LROC NAC | Not read | Boazman et al. 2022, PSJ 3:275, known from a search summary only. UNVERIFIED |

No measurement in hand resolves a shadowed crater floor at 1–10 m. SfS
needs sunlight, and ShadowCam crater counts stop at 18 m.

## Comparator: real 1 m ground at CR1

Hemmi et al. 2025's 1 m DTM of CR1 against Lantern's pipeline run on the
LOLA Site01 tile over the same ground. "Cells over 15°" is the planner's
own test (`sim/rover.py` SlopeMap): slope across 2 m, widened by the
rover's 0.83 m half width, on 2 m cells. Hemmi's DTM is smoothed below
10 m (their sec. 4.2), so as measured it is a lower bound. The upper
estimate scales its 2 m slopes to the trend of its own 16–256 m slopes.

| Ground | rms slope at 2 m | Cells over 15° |
|---|---|---|
| Hemmi CR1, real, as measured (lower bound) | 7.1° | 14.9% |
| Hemmi CR1, scaled to its fractal trend at 2 m (upper estimate) | 7.8° | 21.2% |
| LOLA upsampled, no roughness | 7.1° | 11.7% |
| + roughness 0.018 m rms | 7.1° | 14.2% |
| + roughness 0.035 m rms | 7.2° | 18.0% |
| + roughness 0.053 m rms | 7.3° | 22.3% |
| + roughness 0.070 m rms (current) | 7.5° | 27.4% |
| + craters at 336 / 1,540 / 10,000 per km², no roughness | 7.4° / 8.4° / 13.4° | 16.0% / 29.3% / 78.1% |

The real bracket, 14.9–21.2%, corresponds to roughness of 0.021–0.048 m.
That overlaps the spectrum-extension medians (0.033–0.051 m). The figures
repeat to 0.1 point on three noise seeds. At rms 2 m slope alone the
current roughness passes (7.5° against 7.8°). It fails on the share of
steep cells, because a uniform Gaussian field puts steep spots everywhere
while real relief at this scale is patchy.

Run grounds of `workflow-first3c`, cells over 15°:

| Site | No roughness | 0.035 m | 0.070 m (current) |
|---|---|---|---|
| haynes | 10.6% | 12.5% | 17.8% |
| s89_134e | 11.2% | 16.9% | 26.7% |
| s89_014e | 22.5% | 32.3% | 46.4% |

## One row per terrain choice

| # | Choice | Current value and source | Recommended | Source | What the paper would say |
|---|---|---|---|---|---|
| 1 | Elevation map | LOLA south-polar mosaic, 5 m pixels, PGDA product 81 (`SiteBuild.cell_m`); Nobile at 10 m | Keep. State that it resolves about 15–30 m | Precedent: DLES uses LOLA 5 m; VIPER strategic planning uses LOLA-based maps. Measured: median gap between returns 14 m, 90th percentile 22–30 m. Barker 2021 gives height error 0.30–0.50 m | "The ground is the LOLA 5 m south-polar mosaic (Barker et al. 2021), the only elevation data that measure shadowed floors. Between LOLA's returns, which lie about 14 m apart, it is interpolated." |
| 2 | Upsampling | 1-pixel Gaussian, then bicubic spline to 1 m (`sim/terrain.py upsample`) | Keep | Precedent: ShadowNav scaled LROC DEMs to 0.25 m (C23 sec. 4A); Allan 2019 interpolated the low-frequency part. Measured at CR1: rms slope within 0.2° of the 1 m DTM at every baseline from 2 to 128 m | "The map is smoothed by one pixel and interpolated bicubically, as ShadowNav did. At CR1 this matches the slope statistics of a 1 m NAC DTM (Hemmi et al. 2025) from 2 to 128 m." |
| 3 | Roughness below 10 m | Gaussian fractal field, spectrum k⁻⁴ (H = 1), wavelengths under 10 m, rms 0.070 m = 0.3 × LOLA's 0.234 m height error (`Params.rough_*`, assumed) | Keep the method. Rms 0.035 m. Sensitivity cases at 0.021 and 0.048 m, the ends of the CR1 bracket | Method: fractal detail is in every precedent; H = 1 is Barker 2021's fit to LOLA site spectra. Amplitude: CR1 bracket 0.021–0.048 m; spectrum extension, as in Barker 2021 and Allan 2019, gives site medians of 0.033–0.051 m. 0.070 m lies outside the bracket | "Relief below LOLA's reach is a fractal of Hurst exponent 1, as fitted to LOLA's own spectra (Barker et al. 2021), with 3.5 cm rms. That amplitude puts as much ground over 15° at CR1 as a 1 m NAC DTM of the same ground. We report 2.1 and 4.8 cm, the ends of that comparison." |
| 4 | Crater density, 10 m and up | Log-uniform 100–10,000 per km² (`WorldRanges.crater_n_ge10_per_km2`), from A24 sec. I's spacing of 10 m craters, 100 m (fresh) to 10 m (old) | 106–1,540 per km², 336 central | Minton 2019 sec. 1.2: equilibrium is 0.69–10% of geometric saturation (0.385 r⁻²). 10,000 per km² is 65%. Mazarico 2025: shadowed terrain is, if anything, less cratered. ShadowNav's spacing cites Hiesinger 2012, which is behind a publisher bot check and was not read. Precedent: Allan 2019 and DLES use measured crater statistics. OmniLRS leaves density to the user | "Craters 10 m and larger follow measured lunar equilibrium, 0.7 to 10% of geometric saturation (Minton et al. 2019): 106 to 1,540 per km², 336 in the main case." |
| 5 | Crater sizes | 7.5–20 m, cumulative slope 2 (`WorldRanges.crater_d_m`, `crater_sfd_b`, slope tagged assumed) | Keep. Tag the slope 2 with Minton 2019 | A24 sec. V-B: 7.5–20 m detected reliably. Minton 2019 sec. 1.2: equilibrium slope about 2 (Fit 2 gives 1.8). Smaller craters are in the roughness, as in OmniLRS's 0.5–10 m procedural craters | "Craters 7.5 to 20 m across are modelled, the range ShadowNav detects reliably (Atha et al. 2024). Smaller relief is in the fractal roughness." |
| 6 | Crater shape | Parabolic bowl. Depth/diameter from C23 Table 1's ten craters (0.033–0.125) with 10% jitter. Rim at 0.2 of depth and ejecta to 3 radii, both assumed | Keep | C23 Table 1 measured these craters on LROC DEMs. Precedent ages craters explicitly (PANGU, Allan 2019, VIPER). Lantern instead draws depth from a measured spread, which stands in for age | "Crater depth-to-diameter ratios are drawn from the ten craters ShadowNav measured (Cauligi et al. 2023, Table 1), whose spread spans fresh to degraded." |
| 7 | Orbital map cut | 10 m: craters spanning 5 pixels of a 2 m orbital DEM, less a random 0–20% (`WorldRanges.orbital_*`, assumed) | Keep 10 m as the main case. Add 18 m as a sensitivity case for a map made from shadowed-floor images | C23 sec. 4A: LROC resolves craters "of around 10 m". Hemmi sec. 4.2: 1 m DTMs resolve features "on the order of 10 m". In polar shadow, ShadowCam crater counts are complete from 18 m, and image geolocation is about 20 m (Mazarico 2025) | "The Scouts' starting map holds craters of 10 m and up, the size LROC resolves (Cauligi et al. 2023). Inside permanent shadow, current crater catalogues are complete from about 18 m (Mazarico et al. 2025), so we also report that cut." |
| 8 | Slope limit | 15° (`Settings.slope_limit_deg`, `SiteBuild.slope_limit_deg`) | Keep | Brief p.7: "A 15° slope limit for routes (VIPER's own design limit)". Bluethmann 2024 p.4: "Slopes up to 15 deg (reqt)". Fong slides 2025 and 2026, p.22: "15 deg slopes + 20 cm step obstacles (design limit)" | "Routes keep under 15°, VIPER's design limit." |
| 9 | Scale of the slope test | **Changed 18 Nov 2026, approved in chat:** the tilt of the Scout's footprint plane, a least-squares plane over a 1.55 m square (brief p.6 width; length assumed equal), at every grid point, not widened; the planner and the lidar hazards alike (`sim/footprint.py`, `Settings.slope_test` "footprint"). Precedent: GESTALT (MER) rated terrain by fitting rover-sized planar patches (Goldberg, Maimone and Matthies 2002; search summary only, paper not read, UNVERIFIED). On the `texture-check` haynes ground: 8.8% blocked against 13.1% under the old rule; crater walls (D ≥ 10 m, 0.5–0.9 R) 51% against 71%. Was: slope across 2 m at every point, a 2 m cell blocked if any point within the rover's 0.83 m half width is over the limit; now the "widened" test case, with "point" (no widening) | Was: keep 2 m and the widening. Sensitivity case: slope at the cell centre, no widening | No source states the baseline. VIPER applies 15° on its 20 m and 1 m planning maps and leaves smaller hazards to its cameras. Lantern's 2 m test plays the part of those cameras. Choosing 2 m because it is near the Scout's length is reasoning, not a source | "The 15° limit is applied to the tilt of the plane under the Scout's 1.55 m footprint, as rover navigation software rates terrain by rover-sized planar patches. It stands in for the rover's own hazard check." |
| 10 | Two slope scales | Sites, lanes and ice are chosen on LOLA's own 5 m slope (10 m baseline). Driving uses row 9 | Keep. State it | VIPER does the same: strategic plans on 20 m maps, tactical plans on 1 m maps, hazards from cameras (Ennico-Smith 2023) | "As in VIPER's planning, survey lanes are chosen on the orbital map's slope and checked at the Scout's scale while driving." |
| 11 | Rocks | None in the run ground. The display has rocks, but the lidar does not detect them (`sim/lidar.py:54`, `DETECT_ROCKS = False`) | Not modelled for now, and stated. A rock layer later, once a polar rock size-frequency source is read | Every driving precedent places rocks (Allan 2019, VIPER, OmniLRS, PANGU). VIPER's design limit includes 20 cm step obstacles. No polar or PSR rock distribution was read here | "Rocks are not modelled. The Scouts' driving distances and stranding rates omit rock avoidance." |
| 12 | What the camera sees below the 1 m grid (added 18 Nov 2026; **tentatively approved 20 Nov 2026** in chat, to stand unless the ShadowNav comparison shows a problem with it) | Was: nothing. Camera runs rendered the bare 1 m grid with Lambert shading (`cam_display` "off") | ShadowNav's render recipe (`cam_display` "detail", `sim/terrain_display.py run_ground_params`): Hapke with old-highland parameters, plus fractal texture below two grid cells whose spectrum continues row 3's (k⁻⁴, same coefficient): 7.1 mm rms at 0.035 m. No rocks (row 11), no sub-metre craters, no albedo mottling | C23 sec. 4A and A24 sec. IV-A: DEM at 0.25 m plus "two scales of fractal Brownian motion" for stereo, amplitude not given; Hapke from Xu 2020 Table 1. Amplitude by the spectrum extension of row 3 (Barker 2021, Allan 2019). C23 sec. 4A: "the full amount of daytime texture is not observed during the night with an illumination source". Measured on 22 frames of `workflow-terrain-main` w00003: rim points 1,520 against 1,600 with the bare grid | "As in ShadowNav's simulation, the camera sees the map with a fractal surface texture, lit by the Hapke model with old-highland parameters. The texture continues the terrain's own roughness spectrum below 2 m." |

## Sensitivity cases the paper needs

1. **Roughness**: 0.021, 0.035 (main) and 0.048 m.
2. **Crater density**: 106, 336 (main) and 1,540 per km². This replaces the
   100–10,000 axis of Figure 1a.
3. **Map cut**: 10 m (main) and 18 m.
4. **Slope test**: widened (main) against cell centre only.
5. **Site**: already swept (`haynes`, `s89_134e`, `s89_014e`). s89_014e is
   steep on LOLA alone: 22.5% of cells blocked with no roughness.

Rows 1, 2, 5, 6, 8 and 10 need no case. Each rests on a measurement or on
direct precedent.

## Where precedent and the CR1 check agree, and where they do not

- **Method.** They agree: an orbital map plus fractal detail plus measured
  craters. Lantern's H = 1 is Barker 2021's.
- **Roughness amplitude.** They agree within the noise. Precedent's rule
  (extend the map's spectrum) gives site medians of 0.033–0.051 m. The CR1
  check gives 0.021–0.048 m. The current 0.070 m sits at the top of the
  extension range, above the CR1 bracket. Precedent never tests amplitude
  against real ground, so the CR1 check decides.
- **Crater density.** They agree. Precedent uses measured statistics, and
  ShadowNav's 10,000 per km² is the one figure no measurement supports.
- **Slope scale.** Precedent has no rover-scale slope rule. VIPER leaves it
  to cameras. Lantern's 2 m test with widening is a modelling choice, not
  precedent, so it gets a sensitivity case.
- **Rocks.** They disagree. Precedent models rocks and Lantern does not.

## What this does not show

- CR1 is a sunlit ridge, not a shadowed floor. The roughness calibration
  assumes shadowed floors are no smoother at 1–10 m. Mazarico 2025 finds
  shadowed terrain slightly less cratered, so if anything they are smoother.
- The upper estimate (21.2%) scales every slope by one factor.
- The spectrum fits vary about fivefold between 2 km windows of one site.
  They set a range, not a value per site.
- No campaign was run with any recommended value.
- Not read: Hiesinger 2012 and Cai and Fa 2020 (blocked by publisher bot
  checks), Boazman 2022, the VIPER Environmental Specification (not
  public), and the full DLES paper. ShadowCam's 1.7 m pixel scale comes
  from a search summary of its factsheet and is UNVERIFIED. No row's
  recommendation rests on any of these.
