# Lantern: sources and what each one did

For the pitch deck. Every line below points at the code or the document it
comes from. Copies of the papers sit in `docs/sources/`.

## Summary

Lantern is a shared map of lunar ice that rovers write themselves. Two rovers
from two agencies drive a dark crater near the lunar south pole. They find
their position from lamp-lit crater rims, post ice finds with their position
uncertainty to a contract on Monad, and check each other's finds. The live
site is https://lantern-teal.vercel.app (`README.md:11`). The navigation
method comes from two JPL papers (ShadowNav). The ground is real NASA LOLA
elevation data. The lighting uses a published lunar reflectance model. The
rover speeds, sensor depths and ice amounts come from a student mission brief
(PROSPECT). The chain side runs on Monad testnet, built with Foundry and
verified on Sourcify. **The ice deposits and every measurement are simulated**
(`sim/evidence.py:283`).

## Navigation

| Source | What it is | What it did for Lantern | Slide hook |
|---|---|---|---|
| Cauligi et al., 2023, "ShadowNav: Crater-Based Localization for Nighttime and Permanently Shadowed Region Lunar Navigation", JPL. https://arxiv.org/abs/2301.04630 | The JPL method: find a rover's position in the dark by matching lamp-lit crater rims to an orbital map, with a particle filter. | The whole navigation design. 100 particles, resample below 50 (sec. 6B, `sim/nav.py:33-35`). 2% odometry drift and a 3 m 3-sigma start (sec. 1, `sim/nav.py:36-42`, `sim/rover.py:28`). The "< 2 m" result sets the fix floor (`sim/standins.py:19-24`). Its crater set, Table 1, gives our crater depth ratios (`sim/terrain.py:61-66`, `:290`). Its 0.25 m DEM sets our grid (`sim/terrain.py:70`). It uses OpenCV Canny for rims, so we do too (sec. 3, `sim/vision.py:7-9`, `:75`). | Without it, a rover drifts past 10 m error after about 500 m (sec. 1). |
| Atha et al., 2024, "ShadowNav: Autonomous Global Localization for Lunar Navigation in Darkness", JPL. arXiv:2405.01673 | The follow-up: field tests with a stereo rig and a lamp, and a study of where to put the light. | Camera 2.5 m up with the lamp 0.2 m below it, the best stereo set-up (sec. V, Table 1). 60 deg field of view (sec. IV). Stereo to 40 m with the lamp below (Table 1 caption). All in `sim/scene.py:61-69` and `web/js/world.js:15`. Rims seen from 5 to 25 m (`sim/vision.py:29`). Crater sizes 5-20 m (`sim/terrain.py:101-104`). | The lamp sits under the camera so every crater rim throws a shadow line. |

## Terrain and lighting

| Source | What it is | What it did for Lantern | Slide hook |
|---|---|---|---|
| Barker et al., 2021, "Improved LOLA Elevation Maps for South Pole Landing Sites", Planetary & Space Science 203, 105119. https://doi.org/10.1016/j.pss.2020.105119. Data: NASA Goddard PGDA. | Laser-altimeter elevation maps of the lunar south pole at 5 m per pixel, from the LRO spacecraft. | The ground the rovers drive: a 1 km window of the Shoemaker tile (`sim/terrain.py:51-59`, `lantern_data/README.md:16`). The real backdrop around it (`sim/terrain_display.py:102-103`). The map frame, south polar stereographic metres, is also the contract's frame (`contracts/src/Lantern.sol:16`). Its 5 m resolution is why small craters are missing from the orbital map (`run_mission.py:76`). | Real NASA laser terrain from the south pole, near Shoemaker crater. |
| Xu et al., 2020, "Photometric Correction of Chang'E-1 IIM Data with Hapke Model", Remote Sensing 12(22), 3676. | A fitted Hapke reflectance model: how lunar soil reflects light at each angle. ShadowNav renders with it (Cauligi sec. 4A, ref. [26]). | The "old highland" parameters from its Table 1 p. 3 shade all the ground (`sim/terrain_display.py:107-114`). The same formula runs in Python and in the browser shader (`web/js/regolith.js:3-6`, `:23`). | The ground reflects light the way lunar soil measurably does. |

## Ice and mission

| Source | What it is | What it did for Lantern | Slide hook |
|---|---|---|---|
| PROSPECT Team Brief, RASC-AL 2027 Theme 3, 13 Oct 2026. `docs/sources/`. | A student team's internal mission brief for a NASA RASC-AL design competition. Not a NASA publication. | Rover speed of 7.4 cm/s in the dark (p. 3, `sim/rover.py:24-25`). Neutron spectrometer sees hydrogen to about 1 m (p. 5, `sim/ice.py:24-25`). A 1 m drill (p. 5, `web/js/science_rig.js:24`). 5.6% water at Cabeus as the richest deposit (p. 12, `sim/resources.py:66-68`). The 15 deg slope limit, which the brief takes from VIPER (p. 7, `sim/resources.py:69-72`). | A rover that drives itself goes about 9 times faster than VIPER's 0.8 cm/s (p. 3). |
| Colaprete et al., 2010, "Detection of Water in the LCROSS Ejecta Plume", Science 330, 463. | The LCROSS impact result: water measured in a south pole crater. | The 5.6% water figure, via the PROSPECT brief p. 12. The mix of other volatiles in each find (Table 2, `sim/results.py:26-27`, `:91-92`). | Our ice numbers are scaled to the one real measurement at Cabeus. |
| Hu et al., arXiv 2109.00421 (2021). | A lunar regolith study. | Soil density of 1.66 g/cc, used to turn a find into kilograms of water (sec. 2, `sim/results.py:83-84`). | Finds are reported in kilograms, not just percent. |
| The Artemis Accords, NASA, 13 Oct 2020. https://www.nasa.gov/wp-content/uploads/2022/11/Artemis-Accords-signed-13Oct2020.pdf | The international agreement on lunar activity. | The policy framing: sec. 8 (share science data) and sec. 11 (notify others). The Accords do not require reporting finds; Lantern is a voluntary way to meet those two commitments (`contracts/src/Lantern.sol:21-24`). | A public, tamper-evident record for what the Accords already ask. |

## Rover models

| Source | What it is | What it did for Lantern | Slide hook |
|---|---|---|---|
| NASA 3D Resources. https://github.com/nasa/NASA-3D-Resources | NASA's free 3D models. | Opportunity is rover A and Perseverance is rover B in the 3D view (`web/js/mission.js:262-269`). Both are Mars rovers standing in for lunar ones, and the page says so (`web/js/mission.js:484`). | Rover models: NASA 3D Resources. |
| NASA "Experience Curiosity" | NASA's web viewer of the Curiosity rover. | The layout idea for Mission Control: full-window 3D scene, small camera and map boxes, one play bar (`web/js/mission.js:3-5`). | Design reference only. |

## Chain

| Source | What it is | What it did for Lantern | Slide hook |
|---|---|---|---|
| Monad testnet and docs. https://docs.monad.xyz | An EVM blockchain with fast blocks. | The Lantern contract is live on chain 10143 at `0x67f9Bedb42C845307cB1Fd6551D07Df64bd3CE98` (`sim/lantern_deployment.json`). The docs set the RPC, chain id and EVM version (`contracts/foundry.toml`). | Every find and check is a Monad transaction. |
| Foundry. https://getfoundry.sh | Solidity build, test and deploy tools. | Builds and deploys the contract (`contracts/deploy.sh`). 25 tests cover the checking rules (`contracts/test/Lantern.t.sol`). | 25 contract tests on the checking rules. |
| Sourcify (Monad's instance) | Public source-code verification for contracts. | The deployed source is verified, so explorers decode each call (`contracts/deploy.sh:49-65`, `sim/lantern_deployment.json`). | Anyone can read the exact code that enforces the rules. |
| Monadscan. https://testnet.monadscan.com | The Monad block explorer. | Where judges see each transaction (`sim/chain.py:72`, `run_mission.py:475`). | Click any find and see it on chain. |

## Software (what the code imports)

| Library | What it did |
|---|---|
| NumPy, SciPy | All the maths. SciPy's KD-tree matches rims to the crater map (`sim/nav.py:31`). Its splines and filters build the terrain (`sim/terrain.py:38-40`). |
| OpenCV | Canny edge finding for rims (`sim/vision.py:75`), and video frames. |
| VTK | The Python 3D renders of the lamp view (`sim/scene.py:55`). |
| tifffile, Pillow, Matplotlib | Reading the LOLA GeoTIFFs (`sim/terrain.py:166`), images, and plots. |
| web3.py, eth-account, python-dotenv | Python talks to Monad (`sim/chain.py`), keys stay in `.env`. |
| Three.js 0.170.0 with Draco | The browser 3D scene and NASA models (`web/index.html:17-18`, `web/js/mission.js:273`). |
| ethers 6.13.4, js-sha3 0.9.3 | The browser reads the chain (`web/js/chain.js:27`) and re-hashes reports to check them against the chain (`web/index.html:13`, `web/js/mission.js:1669`). |
| Vercel | Hosts the live site (`README.md:11`). |

## What is ours vs borrowed

The sources give methods, numbers and data. The team built:

- **A working ShadowNav particle filter** on lamp-lit rims, written from the paper's algorithms (`sim/nav.py`). JPL published the method, not this code.
- **The two-agency find-and-check contract.** Only a different agency may check a find. It must have measured within 3 times the two uncertainties added together. Disagreements stay on the record (`contracts/src/Lantern.sol:5-12`, `:69`).
- **The mission simulation**: a Surveyor and a Prospector that coordinate only through the chain, planners, ice sensor and drill sequences (`sim/roles.py`, `sim/mission.py`, `run_mission.py`).
- **Mission Control**, the hosted replay site with live chain reads and hash checks (`web/`).
- The terrain pipeline that turns 5 m LOLA into a 0.25 m drivable grid with craters (`sim/terrain.py`).

## Assumed, not sourced

Never present these as measured.

- **All ice deposits and all readings are simulated.** Not NASA data (`sim/evidence.py:283`).
- The 1 wt% ice threshold. The PROSPECT brief calls it "the ~1 wt% ice threshold we assumed" and asks for a source (p. 9, `sim/ice.py:18-20`).
- Neutron sensor footprint of 1 m and noise of 0.15 to 0.25 wt% (`sim/ice.py:27-28`, `sim/resources.py:138-139`). Deposit size 6 m (`sim/ice.py:29`).
- The LCROSS volatile mix applied to every find. LCROSS sampled one spot (`sim/results.py:91-92`).
- About 11,000 small craters stamped on the LOLA ground. LOLA cannot see them (`lantern_data/README.md:16`, `sim/terrain.py:105-123`).
- Rocks, rim lips, fine texture and the albedo map in the 3D view (`sim/terrain_display.py:371`).
- The exact Hapke values. Xu 2020 Table 1 gives only bounds, so the code uses the bound values (`sim/terrain_display.py:107-110`).
- Navigation fix floor 0.7 m, rim weight 1.5 m and at least 6 rims for a fix (`sim/standins.py:24-26`).
- Odometry noise and heading noise, and the 2 m step (`sim/rover.py:27-30`).
- Survey and drilling durations (`run_mission.py:80-81`).
- The contract's reading unit, hundredths of wt% (`contracts/src/Lantern.sol:17-18`).
- Mars rover models standing in for lunar rovers (`web/js/mission.js:262-264`).
