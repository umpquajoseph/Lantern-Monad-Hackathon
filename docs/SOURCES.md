# Lantern: sources and what each one sets

For the RASC-AL 2027 PROSPECT paper and the campaign simulator. Every row
points at the document a number comes from and at the code or the setting
that uses it. Open-access copies sit in `docs/sources/`.

## Summary

Since 16 Nov 2026 Lantern simulates the workflow the paper proposes
(`docs/campaign-spec.md`). One dock and 1 to 5 identical Scouts run sorties
into a permanently shadowed crater. Each Scout finds its position from
lamp-lit crater rims (JPL's ShadowNav). It adds what it sees to a shared
crater map that later sorties navigate on. It logs possible ice finds with
its position uncertainty, and a second Scout goes back to check each one.
Lantern runs thousands of seeded worlds and produces the paper's section 4.6
numbers and Figure 1. A local site replays any run.

The navigation method comes from the two ShadowNav papers. The ground is NASA
LOLA elevation data with sub-LOLA relief added (`docs/terrain/position.md`).
The Scouts' numbers come from the PROSPECT brief and the team's draft.
**The ice deposits and every reading are simulated.**

The hackathon version (15 Nov 2026) had two rovers from two agencies posting
finds to a contract on Monad. That chain and agency side is history. Its
sources are kept at the end of this file. Every `file:line` in the
hackathon-era rows points at the hackathon code and may have moved since.

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
| Cauligi et al., 2023, "ShadowNav: Crater-Based Localization for Nighttime and Permanently Shadowed Region Lunar Navigation", JPL. https://arxiv.org/abs/2301.04630. `docs/sources/ShadowNav-Cauligi-2023.pdf`. Tag `C23`. | The JPL method: find a rover's position in the dark by matching lamp-lit crater rims to an orbital map, with a particle filter. | 2% odometry drift (sec. 1, sec. 6B: `drift_frac`, `pf_drift_sigma`). 3 m 3-sigma start (sec. 1: `pf_sigma0_m`). Fix error and uncertainty by trajectory (Table 2: `fix_err_*`, `fix_sigma_*`). LROC resolves craters "of around 10 m" (sec. 4A: `orbital_resolve_px`). Crater depth ratios (Table 1, `sim/terrain.py C23_TABLE1`). | 100 particles, resample below 50 (sec. 6B, `sim/nav.py:33-35`). The "< 2 m" result set the fix floor (`sim/standins.py:19-24`). Its 0.25 m DEM set the grid (`sim/terrain.py:70`). OpenCV Canny for rims (sec. 3, `sim/vision.py:7-9`, `:75`). Slide hook: without it, a rover drifts past 10 m error after about 500 m (sec. 1). |
| Atha et al., 2024, "ShadowNav: Autonomous Global Localization for Lunar Navigation in Darkness", JPL. arXiv:2405.01673. `docs/sources/ShadowNav-Atha-2024.pdf`. Tag `A24`. | The follow-up: field tests with a stereo rig and a lamp, and a study of where to put the light. | Lamp 0.2 m below the camera, 0.26 m stereo baseline, 5,120 px, 60° field of view (sec. IV, IV-B: `lamp_below_camera_m`, `stereo_*`). Stereo error by range (Table 1: `stereo_disparity_sigma_px`). Additive particle noise, 2% (sec. III-E1, Table 2: `pf_step_noise_frac`). Heading from the star tracker (sec. III-A: `pf_heading_walk_rad`). Craters 7.5-20 m detected reliably (p.10, sec. V-B: `rim_detect_d_*`, `crater_d_m`). Mean spacing of 10 m craters (sec. I: `crater_n_ge10_per_km2`, being replaced, see terrain row 4). | Camera 2.5 m up with the lamp 0.2 m below it (sec. V, Table 1; `sim/scene.py:61-69`, `web/js/world.js:15`). Rims seen from 5 to 25 m (`sim/vision.py:29`). Slide hook: the lamp sits under the camera so every crater rim throws a shadow line. |

## Mission

| Source | What it is | What it sets in the campaign simulator | Hackathon use (history) |
|---|---|---|---|
| PROSPECT Team Brief, RASC-AL 2027 Theme 3, 13 Oct 2026. `docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf`. Tag `brief p.N`. | A student team's internal mission brief for a NASA RASC-AL design competition. Not a NASA publication. | Two to three Scouts on one dock (p.5: `n_scouts`). The sortie cycle: 22 h sortie, ~3 h charge at 1.5 kW, about 31 h a cycle, 16 sorties a month, 1.5 km descent, 4 km survey, 2 drill sites, 11 days without contact (p.6). 7.9 kWh battery, 25% reserve (p.6). 1.6 m mast (p.6: `camera_height_m`). 15° route limit, from VIPER (p.7: `slope_limit_deg`). The ~1 wt% find threshold, which the brief itself calls assumed (p.9). The Power Tower (p.3). | Rover speed 7.4 cm/s (p.3, `sim/rover.py:24-25`). Neutron spectrometer to about 1 m (p.5, `sim/ice.py:24-25`). 1 m drill (p.5, `web/js/science_rig.js:24`). Slide hook: a rover that drives itself goes about 9 times faster than VIPER's 0.8 cm/s (p.3). |
| The team's area 3 draft (unpublished). Tag `draft`. | The paper section the simulator feeds. **Kept outside this repo. Never copy it in.** | Top speed 15 cm/s and its sweep (4.1). The fix-check policies and the 95th-percentile bound (4.2). Map refinement (4.3). Search and confirm radius (4.4). Average load and the star tracker (4.6). Drill accuracy (Table 1). Its open items are cited by number. | none |
| Colaprete et al., 2010, "Detection of Water in the LCROSS Ejecta Plume", Science 330, 463. Tag `LCROSS`. | The LCROSS impact result: water measured in a south pole crater. | 5.6% water as the richest deposit, via brief p.12 (`rich_peak_wt`, upper end of `deposit_peak_wt`). | The mix of other volatiles in each find (Table 2, `sim/results.py:26-27`, `:91-92`). |
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
| Barker et al., 2021, "Improved LOLA elevation maps for south pole landing sites", PSS 203, 105119. https://doi.org/10.1016/j.pss.2020.105119. NTRS 20205009660. `docs/sources/Barker-2021-improved-LOLA-south-pole.pdf` | The LOLA 5 m site maps (PGDA product 81) and their error model. | The 5 m map itself. Height error 0.30-0.50 m rms, slope 1.5-2.5° (abstract). Sub-LOLA relief as a high-pass fractal of H ≈ 1 fitted to the sites' spectra (sec. 4, Figs. 10-11): the roughness spectrum and the spectrum-extension method. | 1, 3 |
| Barker et al., 2023, "A New View of the Lunar South Pole from LOLA", PSJ. NTRS 20230014419. `docs/sources/Barker-2023-new-view-LOLA-south-pole.pdf` | LOLA polar products (PGDA product 90). | Effective resolution as the largest empty circle between spots, "~20 m/pix" over much of the Artemis zone (sec. 2). Basis of `bench/terrain/lola_coverage.py`, which measures 14-30 m. | 1 |
| Hemmi et al., 2025, "LROC NAC-derived Meter-scale Topography of the Moon's South Polar Landing Sites", PSJ 6:264. https://doi.org/10.3847/PSJ/ae10a4. Data: https://doi.org/10.5281/zenodo.17153447 (CC BY 4.0). `docs/sources/Hemmi-2025-south-pole-1m-DTMs.pdf` | Six 1 m shape-from-shading DTMs of sunlit south-pole sites. | The real ground the pipeline is checked against at CR1 (`bench/terrain/slope_vs_baseline.py`). The roughness bracket 0.021-0.048 m. Smoothed below 10 m (sec. 4.2), so its 2 m slopes are a lower bound. Resolves features "on the order of 10 m". Data in `data/hemmi2025/`. | 2, 3, 7 |
| Rosenburg et al., 2011, "Global surface slopes and roughness of the Moon from the Lunar Orbiter Laser Altimeter", JGR 116, E02001. https://doi.org/10.1029/2010JE003716. `docs/sources/Rosenburg-2011-LOLA-slopes-roughness.pdf` (Caltech repository copy) | LOLA slopes and Hurst exponents, 17 m to 2.7 km. | South Pole median 17 m slope 7.6°, Hurst 0.95 (Table 1). Backs H = 1. | 3 (measurements by scale) |
| Minton et al., 2019, "The equilibrium size-frequency distribution of small craters reveals the effects of distal ejecta on lunar landscape morphology", Icarus. arXiv:1902.07746. `docs/sources/Minton-2019-small-crater-equilibrium.pdf` | Small-crater counts and the equilibrium they reach on old ground. | Geometric saturation 0.385 r⁻², equilibrium 0.69-10% of it (sec. 1.2): 106-1,540 per km², 336 main. Equilibrium slope about 2 (sec. 1.2; Fit 2 gives 1.8). | 4, 5 |
| Mazarico et al., 2025, "The distribution of 20-900 m craters in the lunar polar regions", LPSC abstract. NTRS 20250002410. `docs/sources/Mazarico-2025-polar-crater-distribution.pdf` | ShadowCam crater counts, 80-90°S. | Counts complete from 18 m, geolocation about 20 m: the 18 m map-cut case. Shadowed terrain slightly less cratered at equal slope. | 4, 7 |
| Cauligi et al., 2023 (navigation section). Tag `C23`. | | LROC DEMs scaled to 0.25 m (sec. 4A). Craters "of around 10 m" resolved (sec. 4A). Table 1's ten crater depth ratios. | 2, 6, 7 |
| Atha et al., 2024 (navigation section). Tag `A24`. | | 7.5-20 m craters detected reliably (sec. V-B). The 100-10,000 per km² axis (sec. I), now replaced. | 4, 5 |
| Bluethmann, 2024, "VIPER Rover Overview and Mobility and Hardware", LSIC, 14 Nov 2024. NTRS 20240013903. `docs/sources/VIPER-mobility-LSIC-2024.pdf` | NASA slides on VIPER's mobility. | "Slopes up to 15 deg (reqt)" (p.4). The source behind brief p.7's 15°. Gives no baseline. | 8 |
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
| Xu et al., 2020, "Photometric Correction of Chang'E-1 IIM Data with Hapke Model", Remote Sensing 12(22), 3676. `docs/sources/Xu-2020-Hapke-CE1-IIM.pdf` | A fitted Hapke reflectance model: how lunar soil reflects light at each angle. ShadowNav renders with it (C23 sec. 4A, ref. [26]). | The "old highland" parameters from its Table 1 p.3 shade the ground (hackathon: `sim/terrain_display.py:107-114`, `web/js/regolith.js:3-6`, `:23`). Table 1 gives only bounds, so the code uses the bound values. |

## Rover models and look

| Source | What it is | What it did |
|---|---|---|
| NASA 3D Resources. https://github.com/nasa/NASA-3D-Resources | NASA's free 3D models. | Opportunity and Perseverance stand in for the Scouts in the 3D view (hackathon: `web/js/mission.js:262-269`). Both are Mars rovers, and the page says so (`web/js/mission.js:484`). |
| NASA "Experience Curiosity" | NASA's web viewer of the Curiosity rover. | The layout idea for Mission Control: full-window 3D scene, small camera and map boxes, one play bar (`web/js/mission.js:3-5`). Design reference only. |

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
- Sensor footprint of 1 m, reading noise 0.15 wt%, deposit radius 6 m (`sim/ice.py:28-30`).
- The roughness amplitude. The approved 0.035 m comes from a comparison with real 1 m ground, not from a measurement in shadow (`docs/terrain/position.md` row 3).
- Crater rim height and ejecta reach (`docs/terrain/position.md` row 6).
- The orbital map's position and size errors and its missing share (`WorldRanges.orbital_*`).
- The 2 m scale of the slope test. No source states a baseline (`docs/terrain/position.md` row 9).
- No rocks in the run ground (`docs/terrain/position.md` row 11).
- Mars rover models standing in for the Scouts.

## History: the hackathon chain and agency side (15 Nov 2026)

Lantern on 15 Nov 2026 had two rovers from two agencies post ice finds to a
contract on Monad testnet and check each other's finds. The hackathon record
is https://lantern-teal.vercel.app. Never republish it. Chain code is moving
to `_archive/`. None of these sources sets anything in the campaign
simulator.

| Source | What it is | What it did for Lantern | Slide hook |
|---|---|---|---|
| The Artemis Accords, NASA, 13 Oct 2020. https://www.nasa.gov/wp-content/uploads/2022/11/Artemis-Accords-signed-13Oct2020.pdf | The international agreement on lunar activity. | The policy framing: sec. 8 (share science data) and sec. 11 (notify others). The Accords do not require reporting finds; Lantern was a voluntary way to meet those two commitments (`contracts/src/Lantern.sol:21-24`). | A public, tamper-evident record for what the Accords already ask. |
| Monad testnet and docs. https://docs.monad.xyz | An EVM blockchain with fast blocks. | The Lantern contract is on chain 10143 at `0x67f9Bedb42C845307cB1Fd6551D07Df64bd3CE98` (`sim/lantern_deployment.json`). The docs set the RPC, chain id and EVM version (`contracts/foundry.toml`). | Every find and check is a Monad transaction. |
| Foundry. https://getfoundry.sh | Solidity build, test and deploy tools. | Builds and deploys the contract (`contracts/deploy.sh`). 25 tests cover the checking rules (`contracts/test/Lantern.t.sol`). | 25 contract tests on the checking rules. |
| Sourcify (Monad's instance) | Public source-code verification for contracts. | The deployed source is verified, so explorers decode each call (`contracts/deploy.sh:49-65`). | Anyone can read the exact code that enforces the rules. |
| Monadscan. https://testnet.monadscan.com | The Monad block explorer. | Where judges saw each transaction (`sim/chain.py:72`, `run_mission.py:475`). | Click any find and see it on chain. |

Hackathon software, history: web3.py, eth-account and python-dotenv (Python
to Monad, keys in `.env`); ethers 6.13.4 and js-sha3 0.9.3 (the browser read
the chain and re-hashed reports, `web/js/chain.js:27`); Vercel (hosted the
record site).

Hackathon-only items the team built, history: the two-agency find-and-check
contract (only a different agency may check a find, within 3 times the two
uncertainties added together, disagreements kept on the record,
`contracts/src/Lantern.sol:5-12`, `:69`); the Surveyor and Prospector roles
that coordinated only through the chain (`sim/roles.py`, `sim/mission.py`,
`run_mission.py`).

Hackathon assumptions, history: the LCROSS volatile mix applied to every find;
about 11,000 small craters stamped on a 1 km Shoemaker window; a navigation
fix floor of 0.7 m, rim weight 1.5 m and at least 6 rims (`sim/standins.py:24-26`);
odometry and heading noise and the 2 m step (`sim/rover.py:27-30`); survey
and drilling durations (`run_mission.py:80-81`); the contract's reading unit,
hundredths of wt% (`contracts/src/Lantern.sol:17-18`).
