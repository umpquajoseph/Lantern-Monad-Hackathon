# Lantern: sources and what each one sets

For the RASC-AL 2027 PROSPECT paper and the campaign simulator. Every row
points at the document a number comes from and at the code or the setting
that uses it. Open-access copies sit in `docs/sources/`.

## Summary

The navigation method comes from the two ShadowNav papers. The ground is NASA
LOLA elevation data with sub-LOLA relief added (`docs/terrain/position.md`).
The Scouts' numbers come from the PROSPECT brief and the team's draft.
**The ice deposits and every reading are simulated.**

## How to read a source tag in `sim/settings.py`

Every constant in `sim/settings.py` carries a tag. Each kind of tag maps to a
row here.

| Tag | What it names | Row |
|---|---|---|
| `brief p.N` | PROSPECT Team Brief, page N | Mission |
| `draft N.N`, `draft item N`, `draft Table 1` | The team's area 3 draft, section, open item or table | Mission |
| `C23 ...` | Cauligi et al. 2023, ShadowNav | Navigation |
| `A24 ...` | Atha et al. 2024, ShadowNav follow-up | Navigation |
| `LCROSS` | Colaprete et al. 2010, via brief p.12 | Mission |
| `PGDA product N` | A NASA Goddard PGDA dataset | Sites and data |
| `Mazarico, AVGVISIB_...` | PGDA product 69, Mazarico et al. 2011 | Sites and data |
| `VIPER` | Via brief p.7; the slides in the terrain section | Terrain |
| `code file:line`, `sim/...`, `docs/...`, commits, batches | Lantern's own code, docs or calibration runs. Not literature | none |
| `terrain fyi <timestamp>` | A mail between the simulator's builders. Mailboxes are not committed | none |
| `assumed` | No source. A named parameter with a default | Assumed, not sourced |

## Navigation

| Source | What it is | What it sets in the campaign simulator | Hackathon use (history) |
|---|---|---|---|
| Cauligi et al., 2023, "ShadowNav: Crater-Based Localization for Nighttime and Permanently Shadowed Region Lunar Navigation", JPL. https://arxiv.org/abs/2301.04630. `docs/sources/ShadowNav-Cauligi-2023.pdf`. Tag `C23`. | The JPL method: find a rover's position in the dark by matching lamp-lit crater rims to an orbital map, with a particle filter. | 2% odometry drift (sec. 1, sec. 6B: `drift_frac`, `pf_drift_sigma`). 3 m 3-sigma start (sec. 1: `pf_sigma0_m`). Fix error and uncertainty by trajectory (Table 2: `fix_err_*`, `fix_sigma_*`). LROC resolves craters "of around 10 m" (sec. 4A: `orbital_resolve_px`). Crater depth ratios (Table 1, `sim/terrain.py C23_TABLE1`). | 100 particles, resample below 50 (sec. 6B, `sim/nav.py:33-35`). Its 0.25 m DEM set the grid (`sim/terrain.py:70`). OpenCV Canny for rims (sec. 3, `sim/vision.py:7-9`, `:75`). Slide hook: without it, a rover drifts past 10 m error after about 500 m (sec. 1). |
| Atha et al., 2024, "ShadowNav: Autonomous Global Localization for Lunar Navigation in Darkness", JPL. arXiv:2405.01673. `docs/sources/ShadowNav-Atha-2024.pdf`. Tag `A24`. | The follow-up: field tests with a stereo rig and a lamp, and a study of where to put the light. | Lamp 0.2 m below the camera, 0.26 m stereo baseline, 5,120 px, 60° field of view (sec. IV, IV-B: `lamp_below_camera_m`, `stereo_*`). Stereo error by range (Table 1: `stereo_disparity_sigma_px`). Additive particle noise, 2% (sec. III-E1, Table 2: `pf_step_noise_frac`). Heading from the star tracker (sec. III-A: `pf_heading_walk_rad`). Craters 7.5-20 m detected reliably (p.10, sec. V-B: `rim_detect_d_*`, `crater_d_m`). Mean spacing of 10 m craters (sec. I: `crater_n_ge10_per_km2`, being replaced, see terrain row 4). | Camera 2.5 m up with the lamp 0.2 m below it (sec. V, Table 1; `sim/scene.py:61-69`, `web/js/world.js:15`). Rims seen from 5 to 25 m (`sim/vision.py:29`). Slide hook: the lamp sits under the camera so every crater rim throws a shadow line. |

## Mission

| Source | What it is | What it sets in the campaign simulator | Hackathon use (history) |
|---|---|---|---|
| PROSPECT Team Brief, RASC-AL 2027 Theme 3, 13 Oct 2026. `docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf`. Tag `brief p.N`. | A student team's internal mission brief for a NASA RASC-AL design competition. Not a NASA publication. | Two to three Scouts on one dock (p.5: `n_scouts`). The sortie cycle: 22 h sortie, ~3 h charge at 1.5 kW, about 31 h a cycle, 16 sorties a month, 1.5 km descent, 4 km survey, 2 drill sites, 11 days without contact (p.6). 7.9 kWh battery, 25% reserve (p.6). 1.6 m mast (p.6: `camera_height_m`). 15° route limit, from VIPER (p.7: `slope_limit_deg`). The ~1 wt% find threshold, which the brief itself calls assumed (p.9). The Power Tower (p.3). | Rover speed 7.4 cm/s (p.3, `sim/rover.py:24-25`). Neutron spectrometer to about 1 m (p.5, `Settings.footprint_radius_m`). 1 m drill (p.5, `web/js/science_rig.js:24`). Slide hook: a rover that drives itself goes about 9 times faster than VIPER's 0.8 cm/s (p.3). |
| Goldberg, Maimone and Matthies, 2002, "Stereo Vision and Rover Navigation Software for Planetary Exploration", IEEE Aerospace Conference, Big Sky. https://robotics.jpl.nasa.gov/media/documents/aero.pdf. `docs/sources/GESTALT-Goldberg-2002.pdf`, text copy beside it. Tag `Goldberg 2002`. | The MER rovers' GESTALT navigator. | Terrain rated by a best-fit plane over a rover-sized patch, pitch hazard from its slope (sec. 4 step 3, p.6): the footprint slope test, `Settings.slope_test`, `sim/footprint.py`, terrain row 9. | none |
| The team's area 3 draft (unpublished). Tag `draft`. | The paper section the simulator feeds. `docs/sources/area3-navigation-draft.pdf` (16 Nov 2026), text copy `area3-navigation-draft.txt`. | Top speed 15 cm/s and its sweep (4.1). The fix-check policies and the 95th-percentile bound (4.2). Map refinement (4.3). Search and confirm radius (4.4). Average load and the star tracker (4.6). Drill accuracy (Table 1). Its open items are cited by number. Out of date: its lines that Lantern "is not a source of performance figures" (4.6) and "is never a source of numbers" (future-work preamble). The sim's results go in the paper (`docs/campaign-spec.md`, "Two decisions"). | none |
| Colaprete et al., 2010, "Detection of Water in the LCROSS Ejecta Plume", Science 330, 463. Tag `LCROSS`. | The LCROSS impact result: water measured in a south pole crater. | 5.6% water as the richest deposit, via brief p.12 (`rich_peak_wt`, upper end of `deposit_peak_wt`). | The mix of other volatiles in each find (Table 2, `sim/results.py:26-27`, `:91-92`). |
| Colaprete et al., VIPER Project Implementation Plan (PIP), NTRS 20210015009. `docs/sources/VIPER-Colaprete-PIP-NTRS-20210015009.pdf`, text copy beside it. | NASA's plan for the VIPER rover mission and its payload. | The ice reading's noise. Sec. 3.2.1: NSS tubes "sized to achieve a detection sensitivity of 0.5% (wt) at 3-sigma while roving at 10 cm/s" (`Settings.nss_3sigma_wt`, `nss_ref_speed_mps`). Table 2-2 L2-SPL-5: WEH at a horizontal resolution of no more than 5 m (`nss_ref_length_m`; that the 3-sigma figure is per 5 m of track is our reading). `Settings.sensor_noise_wt` scales it to a Scout's reading by counting statistics: 0.204 wt% 1-sigma at 5 m spacing and 0.15 m/s, so a reading crosses 1 wt% over dry ground about 5 times in 10 million. Also L1 1.2 and L2-SPL-4: 0.5 wt% detection limit. | |
| Andrews, 2020, VIPER mission overview slides. NTRS 20205000864. `docs/sources/VIPER-Andrews-2020-overview-NTRS-20205000864.pdf`, text copy beside it. | NASA slides on VIPER and its instruments. | Confirms the NSS figure: "down to 0.5% (wt) WEH to 3-sigma while roving", sensitivity ">0.5 wt% water-equivalent at 10 cm/s"; prospecting speed 10 cm/s. Gives "Accuracy 5 - 10% absolute", a calibration error not modelled. | |
| Livengood et al., "Background and Lunar Neutron Populations Detected by LEND and Average Concentration of Near-Surface Hydrogen Near the Moon's Poles", manuscript, NTRS 20180004020. `docs/sources/LEND-polar-hydrogen-NTRS-20180004020.pdf`, text copy beside it. | LRO's LEND neutron data at the poles; not a rover instrument. | Not yet used. Abstract: polar regolith holds about 0.095 +/- 0.01 wt% WEH above 80 deg and 0.13 +/- 0.02 wt% above 88 deg, every Lantern site's latitude. Lantern's dry ground reads 0 wt%; a 0.13 wt% background would raise the false-find rate per reading from about 5e-7 to 1e-5 (open with leader, 20 Nov 2026). | |
| Hu et al., arXiv 2109.00421 (2021). | A lunar regolith study. | Not used by the campaign settings. | Soil density 1.66 g/cc, to turn a find into kilograms of water (sec. 2, `sim/results.py:83-84`). |

## Sites and data

All PGDA datasets are NASA LOLA team work, downloaded or read by HTTP range by
`sim/psr_sites.py` (its `SOURCES` table gives each URL and size). The data
live in `data/`, not in git.

| Dataset | Paper to cite | What it sets | Copy |
|---|---|---|---|
| PGDA product 81: `ldem_87s_5mpp`, `ldsm_87s_5mpp`, `ldec_87s_5mpp`. https://pgda.gsfc.nasa.gov/products/81 | Barker et al. 2021, PSS 203, 105119 (terrain section) | Height, slope and returns per pixel at 5 m for the three PSR sites (`SiteBuild.cell_m`, `plan_cell_m`). The ground every run drives. | Paper in `docs/sources/` |
| PGDA product 90: `LPSR_80S_20MPP_ADJ`, `LDEM_83S_10MPP_ADJ` and its slope and returns. https://pgda.gsfc.nasa.gov/products/90 | Barker et al. 2023, PSJ (terrain section) | Which ground is permanently shadowed, 20 m, 80-90°S. The Nobile site at 10 m, 84.5-87°S. | Paper in `docs/sources/` |
| PGDA product 69: `AVGVISIB_85S_060M_201608`, and the 75°S 120 m map where the 60 m map has no data, each with an Earth-visibility twin. https://pgda.gsfc.nasa.gov/products/69 | Mazarico et al. 2011, "Illumination conditions of the lunar polar regions using LOLA topography", Icarus 211, 1066. doi:10.1016/j.icarus.2010.10.030 | Average sunlit fraction at a dock site (`SiteBuild.dock_min_sun`, whose threshold is assumed). | Not saved. Cited for the dataset only. |
| IAU WGPSN Gazetteer of Planetary Nomenclature. https://planetarynames.wr.usgs.gov/ | (the service itself) | Crater names and centres, LOLA frame, read 16 Nov 2026 (`sim/psr_sites.py GAZETTEER`). | none |

## Terrain (`docs/terrain/position.md`, 17 Nov 2026)

"Row" is the row of the table "One row per terrain choice" in
`docs/terrain/position.md`. Rows 3, 4, 7 and 9 carry the approved sensitivity
cases.

| Source | What it is | What it sets | Row |
|---|---|---|---|
| Barker et al., 2021, "Improved LOLA elevation maps for south pole landing sites", PSS 203, 105119. https://doi.org/10.1016/j.pss.2020.105119. NTRS 20205009660. `docs/sources/Barker-2021-improved-LOLA-south-pole.pdf` | The LOLA 5 m site maps (PGDA product 81) and their error model. | The 5 m map itself. Height error 0.30-0.50 m rms, slope 1.5-2.5° (abstract). Sub-LOLA relief as a high-pass fractal of H ≈ 1 fitted to the sites' spectra (sec. 4, Figs. 10-11): the roughness spectrum and the spectrum-extension method. Track geolocation ~10-20 cm horizontally after co-adjustment (abstract): the frame of a LOLA-tied map is far better than `WorldRanges.orbital_pos_err_px`, which stays assumed. | 1, 3, 7 |
| Barker et al., 2023, "A New View of the Lunar South Pole from LOLA", PSJ. NTRS 20230014419. `docs/sources/Barker-2023-new-view-LOLA-south-pole.pdf` | LOLA polar products (PGDA product 90). | Effective resolution as the largest empty circle between spots, "~20 m/pix" over much of the Artemis zone (sec. 2). Basis of `bench/terrain/lola_coverage.py`, which measures 14-30 m. Final track geolocation 1.00/1.00/0.15 m along/cross/radial, conservative, 80-90°S (sec. 3, p.10). | 1, 7 |
| Hemmi et al., 2025, "LROC NAC-derived Meter-scale Topography of the Moon's South Polar Landing Sites", PSJ 6:264. https://doi.org/10.3847/PSJ/ae10a4. Data: https://doi.org/10.5281/zenodo.17153447 (CC BY 4.0). `docs/sources/Hemmi-2025-south-pole-1m-DTMs.pdf` | Six 1 m shape-from-shading DTMs of sunlit south-pole sites. | The real ground the pipeline is checked against at CR1 (`bench/terrain/slope_vs_baseline.py`). The roughness bracket 0.021-0.048 m. Smoothed below 10 m (sec. 4.2), so its 2 m slopes are a lower bound. Resolves features "on the order of 10 m". Keeps NoData in deep shadow (sec. 4.5), so a sub-5 m map of a PSR floor would come from ShadowCam (`WorldRanges.shadowcam_*`). Aligned to LOLA (sec. 2.2, 4.3). Text copy beside the PDF. Data in `data/hemmi2025/`. | 2, 3, 7 |
| Rosenburg et al., 2011, "Global surface slopes and roughness of the Moon from the Lunar Orbiter Laser Altimeter", JGR 116, E02001. https://doi.org/10.1029/2010JE003716. `docs/sources/Rosenburg-2011-LOLA-slopes-roughness.pdf` (Caltech repository copy) | LOLA slopes and Hurst exponents, 17 m to 2.7 km. | South Pole median 17 m slope 7.6°, Hurst 0.95 (Table 1). Backs H = 1. | 3 (measurements by scale) |
| Minton et al., 2019, "The equilibrium size-frequency distribution of small craters reveals the effects of distal ejecta on lunar landscape morphology", Icarus. arXiv:1902.07746. `docs/sources/Minton-2019-small-crater-equilibrium.pdf` | Small-crater counts and the equilibrium they reach on old ground. | Geometric saturation 0.385 r⁻², equilibrium 0.69-10% of it (sec. 1.2): 106-1,540 per km², 336 main. Equilibrium slope about 2 (sec. 1.2; Fit 2 gives 1.8). | 4, 5 |
| Mazarico et al., 2025, "The distribution of 20-900 m craters in the lunar polar regions", LPSC abstract. NTRS 20250002410. `docs/sources/Mazarico-2025-polar-crater-distribution.pdf`, text copy beside it. | ShadowCam crater counts, 80-90°S. | Counts complete from 18 m, geolocation about 20 m: the 18 m map-cut case, whose craters share one offset per image (`WorldRanges.shadowcam_map_from_cut_m`, `shadowcam_geoloc_m`, `sim/orbital_map.py shadowcam_offsets`). Diameters "may currently be slight overestimates", not modelled. Shadowed terrain slightly less cratered at equal slope. | 4, 7 |
| Cauligi et al., 2023 (navigation section). Tag `C23`. | | LROC DEMs scaled to 0.25 m (sec. 4A). Craters "of around 10 m" resolved (sec. 4A). Table 1's ten crater depth ratios. | 2, 6, 7 |
| Atha et al., 2024 (navigation section). Tag `A24`. | | 7.5-20 m craters detected reliably (sec. V-B). The 100-10,000 per km² axis (sec. I), now replaced. | 4, 5 |
| ShadowCam factsheet (KPLO), from shadowcam.im-ldi.com (formerly shadowcam.asu.edu). `docs/sources/ShadowCam-factsheet.pdf`, text copy beside it. | ASU's one-page description of the ShadowCam camera. | "Image Footprint (100 km alt.) 5.2 km x 144 km" and 1.7 m pixels: one image's cross-track width sets the side of the patch whose craters share one geolocation offset (`WorldRanges.shadowcam_image_m`; the square patch is assumed). | 7 |
| Bluethmann, 2024, "VIPER Rover Overview and Mobility and Hardware", LSIC, 14 Nov 2024. NTRS 20240013903. `docs/sources/VIPER-mobility-LSIC-2024.pdf`, text copy beside it. | NASA slides on VIPER's mobility. | "Slopes up to 15 deg (reqt)" (p.4): `Settings.pitch_limit_deg`. The source behind brief p.7's 15°. Gives no baseline. "Steering: explicit with +/-50 deg range" (p.4); "Omni-directional driving" among the driving modes (p.5): the Scout may back out of a dead end (sim/rover.py `_follow_sensing`). | 8 |
| Rezich et al., 2025, "Investigating the Geotechnical Properties of the Lunar South Pole with NASA VIPER's Mobility System", PSJ 6:169. https://doi.org/10.3847/PSJ/add13f (read on iopscience.iop.org, 23 Nov 2026; no copy in `docs/sources/` yet) | Peer-reviewed paper on VIPER's mobility system by the VIPER team. | Sec. 3.4: the suspension gives "adjustable chassis ground clearance (7–42 cm), pitch (+/−20°), and roll (+/−15°)": `Settings.roll_limit_deg` 15, read as the cross slope the chassis can be held level on. Sec. 3.5: "the mobility design requirements of the VIPER mission defined a maximum of 40% slip up a maximum slope of 15°": `Settings.pitch_limit_deg`. Sec. 2: cross-slope driving brings "side slip and transverse loading", and VIPER is "often driven in a 'crab' configuration". No route limit for roll is given. | 8 |
| Fong, VIPER slides, 24 Apr 2025 and 29 Apr 2026. NTRS 20250004148, 20260002876. `docs/sources/VIPER-Fong-2025-04-24.pdf`, `VIPER-Fong-2026-04-29.pdf` | VIPER rover and operations. | 15° slopes and 20 cm step obstacles as design limits (p.22 of both). 1 m SfS DEM, fractal synthesis to 4 cm, craters and rocks from size-frequency studies (2026 p.29). | 8, 9, 11; precedent table |
| Ennico-Smith et al., 2023, "VIPER Mission Traverse Planning", abstract. NTRS 20230004239. `docs/sources/VIPER-Ennico-Smith-2023-traverse-planning.pdf`. Shirley and Balaban, 2022, VIPER planning slides. NTRS 20220008301. `docs/sources/VIPER-Shirley-Balaban-2022-planning.pdf` | VIPER planning. | Strategic plans on 20 m LOLA-based maps, tactical on 1 m SfS, hazards from cameras: the precedent for two slope scales. | 9, 10 |
| Allan et al., 2019, "Planetary Rover Simulation for Lunar Exploration Missions", IEEE Aerospace. NTRS 20190027571. `docs/sources/Allan-2019-planetary-rover-simulation.pdf` | NASA Ames rover simulator for Resource Prospector. | Precedent: DEM upscaled by extending its own high frequencies. Craters and rocks from Surveyor size-frequency laws refit for the poles. | 2, 3, 4, 6, 11 |
| Richard et al., 2024, "OmniLRS: A Photorealistic Simulator for Lunar Robotics", ICRA 2024. arXiv:2309.08997. `docs/sources/Richard-2024-OmniLRS.pdf` | Open-source lunar robotics simulator. | Precedent: LRO DEMs plus procedural 0.5-10 m craters from SLDEM profiles (sec. III-A). Density left to the user. | 4, 5, 11 |
| Rowell et al., c. 2012, "PANGU: Virtual Spacecraft Image Generation", ESA-funded. `docs/sources/Rowell-c2012-PANGU.pdf` | ESA's planetary surface simulator. | Precedent: fBm terrain, craters and boulders from literature distributions, craters aged. | 6, 11 |
| DLES Terrain Crafting, NASA JSC, 2024 abstract. NTRS 20240008019. `docs/sources/DLES-terrain-crafting-abstract-2024.pdf` | NASA's lunar south pole digital terrain for Artemis simulation. | Precedent: LOLA 5 m enhanced to 20 cm with artificial craters from NAC statistics. | 1, 4 |
| Hiesinger et al., 2012 (cited by A24 sec. I for crater spacing). | Crater counts. | Behind a publisher bot check. **Not read.** No row rests on it. | 4 (named only) |

## Rendering

| Source | What it is | What it did |
|---|---|---|
| Xu et al., 2020, "Photometric Correction of Chang'E-1 IIM Data with Hapke Model", Remote Sensing 12(22), 3676. `docs/sources/Xu-2020-Hapke-CE1-IIM.pdf` | A fitted Hapke reflectance model: how lunar soil reflects light at each angle. ShadowNav renders with it (C23 sec. 4A, ref. [26]). | The "old highland" parameters from its Table 1 p.3 shade the ground (`sim/terrain_display.py`, `web/js/regolith.js`). Table 1 gives only bounds, so the code uses the bound values. |

## Rover models and look

| Source | What it is | What it did |
|---|---|---|
| NASA 3D Resources. https://github.com/nasa/NASA-3D-Resources | NASA's free 3D models. | Opportunity stands in for the Scouts in the 3D view (`web/js/mission.js`). It is a Mars rover, and the page says so. |
| NASA "Experience Curiosity" | NASA's web viewer of the Curiosity rover. | The layout idea for Mission Control: full-window 3D scene, small camera and map boxes, one play bar. Design reference only. |

## Software (what the code imports)

| Library | What it does |
|---|---|
| NumPy, SciPy | All the maths. SciPy's KD-tree matches rims to the crater map. Its splines and filters build the terrain (`sim/terrain.py upsample`). |
| OpenCV | Canny edge finding for rims, and video frames. |
| VTK | The Python 3D renders of the lamp view (`sim/scene.py`). |
| tifffile, Pillow, Matplotlib | Reading the LOLA GeoTIFFs, images, and plots. |
| Three.js 0.170.0 with Draco | The browser 3D scene and NASA models. |

## What is ours and what is borrowed

The sources give methods, numbers and data. The team built:

- **A ShadowNav particle filter** on lamp-lit rims, written from the papers' algorithms (`sim/nav.py`). JPL published the method, not this code.
- **The campaign simulator**: the paper's map, discover and corroborate workflow over seeded worlds, with 1 to 5 Scouts and one dock (`sim/campaign.py`, `sim/settings.py`).
- **The terrain pipeline** that turns 5 m LOLA into a 1 m drivable ground with roughness and craters (`sim/terrain.py`, `sim/site_ground.py`), and its check against real 1 m ground (`bench/terrain/`).
- **Mission Control**, the replay site (`web/`).

## Assumed, not sourced

Never present these as measured. In `sim/settings.py` every such constant is
tagged `assumed`; that tag is the full list. The ones a reader is most likely
to take for data:

- **All ice deposits and all readings are simulated.** Not NASA data.
- The 1 wt% ice threshold. The brief calls it "the ~1 wt% ice threshold we assumed" and asks for a source (p.9).
- Sensor footprint of 1 m and deposit radii 3-12 m (`sim/settings.py`). The reading noise is sourced (VIPER NSS, Mission table).
- The roughness amplitude. The approved 0.035 m comes from a comparison with real 1 m ground, not from a measurement in shadow (`docs/terrain/position.md` row 3).
- Crater rim height and ejecta reach (`docs/terrain/position.md` row 6).
- The orbital map's position and size errors and its missing share (`WorldRanges.orbital_*`).
- The 2 m scale of the slope test. No source states a baseline (`docs/terrain/position.md` row 9).
- No rocks in the run ground (`docs/terrain/position.md` row 11).
- A Mars rover model standing in for the Scouts.
