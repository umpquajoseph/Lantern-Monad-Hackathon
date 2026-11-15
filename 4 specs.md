# Moon Ledger: Monad Blitz Oregon Spec

Nov 14, 2026 · @Cal

## Overview

In one day, we demo a simulated lunar Scout rover that drives itself across real south-pole terrain and records each ice finding on the Monad testnet, where anyone can view it. "Moon Ledger" is a working title.

**The demo, end to end:** the rover starts at the dock on a crater rim, using real NASA LOLA elevation data. It plans a route under the 15° slope limit, finds obstacles that the orbital map missed, and replans. It surveys a shadowed crater floor with a simulated neutron spectrometer. When a reading passes the threshold, it "drills" to confirm, then the rover's own wallet sends the finding to the contract. A public map shows the route and every on-chain finding, each linked to the block explorer. The rover then returns with at least 25% battery left.

**Who does what:** four people, four parts.

1. World and Sensor Sim (also builds the dashboard)
2. Perception (CV)
3. Navigation and Autonomy (Calvin)
4. Contracts and Chain Bridge (the contracts person)

**How to describe the Artemis Accords in the pitch.** Don't say the Accords "require reports of what you found, how much and where." They are a political commitment, not law. Section 4 commits signatories to share scientific findings publicly, Section 8 to openly share scientific data, and Section 10 to tell the UN and the public about resource *extraction*. Pitch line: *"The Accords commit nations to share lunar science and resource activity publicly. Moon Ledger makes that automatic and tamper-evident."* Source: [Artemis Accords](https://www3.nasa.gov/specials/artemis-accords/img/Artemis-Accords-signed-13Oct2020.pdf).

**How this ties to PROSPECT:** this is the Scout sortie from the team brief (dock on the rim, PSR survey, choose and drill the best spots, return with reserve), shrunk to one day of work. The nav code and lessons carry over to RASC-AL.

## How the parts connect

&#91;embedded content: how the four parts connect\]

Parts talk only through the shared types. The dashboard reads the mission log and the chain, and never calls another part directly.

## Shared interfaces (agree on these in the first 30 minutes)

All four parts run in one Python process and talk only through the types below. Write them together first, before anyone splits off. This is how four people can build in parallel and still have everything fit together at the end.

**Conventions**

- Coordinates: the DEM's south polar stereographic meters (x\_m, y\_m) everywhere inside the sim. Convert to lat/lon only when writing on chain.
- Grid resolution: 5 m per cell (the LOLA tile's native resolution).
- Time: simulated mission seconds (`t_s`), sped up for the demo.
- Units: wt% for hydrogen-equivalent water, Wh for energy, m/s for speed.

**Python types** (`shared/types.py`)

```python
from dataclasses import dataclass
import numpy as np

@dataclass
class Pose:            # true pose (sim) or estimated pose (nav)
    x_m: float; y_m: float; heading_rad: float; t_s: float

@dataclass
class Grid:            # any raster aligned to the DEM tile
    data: np.ndarray; origin_x_m: float; origin_y_m: float; res_m: float

@dataclass
class HeightPatch:     # what the rover "sees": local heights around it
    z: np.ndarray; center: Pose; res_m: float; valid: np.ndarray

@dataclass
class HazardPatch:     # perception output, same shape as the HeightPatch
    blocked: np.ndarray; reason: np.ndarray; center: Pose; res_m: float

@dataclass
class CraterObs:       # crater seen by perception, relative to the rover
    dx_m: float; dy_m: float; radius_m: float; score: float

@dataclass
class NeutronReading:
    pose: Pose; counts: int; h_wt_pct_est: float; sigma: float

@dataclass
class Finding:
    rover_id: str; x_m: float; y_m: float; lat_e6: int; lon_e6: int
    h_wt_pct: float; confidence: int; kind: str      # "candidate" | "confirmed"
    instrument: str; mission_time_s: float; raw: dict; data_hash: str
```

**Function contracts** (who provides what)

| Provider | Function | Returns |
| --- | --- | --- |
| Part 1 World | `World.load(tile_path)` | world with `orbital` (DEM, slope, crater catalog) and hidden `truth` |
| Part 1 World | `world.sense_terrain(true_pose, radius_m)` | `HeightPatch` (simulated stereo/lidar, with noise) |
| Part 1 World | `world.sense_neutron(true_pose)` | `NeutronReading` |
| Part 2 Perception | `detect_hazards(patch)` | `HazardPatch` |
| Part 2 Perception | `detect_craters(patch)` | `list[CraterObs]` |
| Part 2 Perception | `stereo_to_height(left, right, calib)` | `HeightPatch` (real POLAR images) |
| Part 3 Nav | `Mission.step()` | advances one tick, appends events to the log |
| Part 4 Chain | `ChainClient.report(finding)` | tx hash |
| Part 4 Chain | `ChainClient.list_findings()` | `list[Finding]` read back from chain |

**Mission log** (`run/mission_log.jsonl`): one JSON line per event. Types: `pose`, `pose_est`, `hazard`, `reading`, `finding`, `tx`, `battery`. The dashboard reads only this file and the chain. Nobody calls the dashboard directly.

**On-chain Finding** (proposal; the contracts person has the final say)

| Field | Solidity type | Meaning |
| --- | --- | --- |
| id | uint256 | auto-increment |
| rover | address | `msg.sender`; the rover's own wallet signs, so no separate signature check is needed |
| latE6, lonE6 | int64 | microdegrees |
| hBp | uint32 | wt% × 100 (1.00 wt% = 100) |
| confidence | uint8 | 0 to 100 |
| kind | uint8 | 0 = candidate, 1 = confirmed by drill |
| instrument | uint8 | 0 = neutron spectrometer, 1 = drill + mass spectrometer |
| missionTime | uint64 | sim seconds |
| dataHash | bytes32 | keccak256 of the raw readings JSON (canonical, sorted keys) |

Contract functions: `registerRover(address, string name)` (owner only), `reportFinding(...)` (registered rovers only; emits `FindingReported`), `getFinding(id)`, `findingCount()`. Send one transaction per finding, not per reading. The raw JSON is saved as `findings/<hash>.json`, so anyone can re-hash it and check it against the chain.

## Part 1: World and Sensor Sim (also the dashboard)

Part 1 owns the "ground truth": real NASA terrain, hidden rocks the orbital map can't see, a simulated ice field, simulated sensors, and the screen the judges watch.

**Inputs:** one LOLA 5 m/px south-pole site tile. Use the elevation map (`*_surf.tif`) and the slope map (`*_slp.tif`) from [PGDA LOLA\_5mpp](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/). Shackleton rim (Site04) or de Gerlache rim (Site11) fits the "dock on a crater rim" story. Crop to about 2 × 2 km (400 × 400 cells) so planning stays fast.

**Build, in order:**

1. **Load terrain:** read the GeoTIFFs with rasterio, crop, and expose `orbital.height` and `orbital.slope` as `Grid`s. Keep the tile's CRS object; Part 4 uses it to convert coordinates.
2. **Shadow mask (a proxy, not a real PSR map):** hillshade the DEM at a low sun elevation from several azimuths, and mark cells that stay dark in all of them. Label it \[A\] on screen. The simplest fallback: the lowest cells inside the crater bowl.
3. **Hidden truth:** add rocks and small craters with a fixed random seed. They don't appear in `orbital`, so the rover has to find them itself. This is what makes the CV necessary.
4. **Ice field:** a few Gaussian blobs of hydrogen (wt%), only inside the shadow mask, zero everywhere else. Peak values are \[A\]. The team brief cites LCROSS measuring \~5.6 wt% water at Cabeus, so keep peaks around or below that.
5. **`sense_terrain(true_pose, radius_m)`:** upsample the DEM to 0.25 m around the rover, add the hidden rocks, add height noise that grows with range, and set `valid = False` beyond \~10 m. (The team's ShadowNav notes say stereo is good to about 10 m.) Returns a `HeightPatch`.
6. **`sense_neutron(true_pose)`:** average the ice field over a footprint, turn it into Poisson-noisy counts (fewer counts = more hydrogen), and convert back to `h_wt_pct_est` with a `sigma`. Footprint size and count rates are \[A\] and live in `config.py`.
7. **Dashboard (Streamlit + Plotly):** hillshade map with the shadow overlay. Show the true path and the estimated path in different colors, hazards as the rover discovers them, findings as pins (click opens the explorer tx), a battery gauge, a live neutron-reading chart, and a "speed made good" number. Add a "Public ledger" panel filled only from `ChainClient.list_findings()`. Add a "God view" toggle that reveals the true ice field, so judges can see the rover found the right places.

**Done when:** a replay of `mission_log.jsonl` animates on the map, and the ledger panel lists findings read back from chain.

**Fallback:** if GeoTIFF loading fails, use a procedural crater terrain so the team isn't blocked. Swap the real tile back in before the demo, because "real NASA terrain" is a pitch point.

## Part 2: Perception (CV)

Part 2 turns what the rover sees into two things nav needs: where it can't drive (hazards) and which craters it can see (landmarks for fixing its position). One hazard detector serves two inputs: simulated heights in the live demo, and real NASA stereo images to show the method works on real lunar-like imagery.

**Track A: in the sim (needed for the demo)**

1. **`detect_hazards(patch)`:** slide a rover-footprint-sized window over the `HeightPatch`, fit a plane, and flag a cell when:
   - the slope is over 15° (the brief's route limit, from VIPER),
   - the step height (max − min in the window) is over the wheel-obstacle limit \[A\] (get the number from the Mobility person or Kellen's Scout design doc),
   - or roughness is over a threshold \[A\].

   Set `reason` to slope, step or rough, so the dashboard can color each one.
2. **`detect_craters(patch)`:** find circular depressions in the height patch. Try a Laplacian-of-Gaussian blob detector on negative height, or Hough circles on rim edges. Return center offset, radius and a score.
3. **Crater catalog:** run the same `detect_craters` on the *orbital* DEM to build the map catalog nav matches against. One function, used twice, saves a whole sub-project.

**Track B: on real NASA images (credibility; build it after Track A works)**

1. Load one stereo sequence from the [POLAR Traverse Dataset](https://ti.arc.nasa.gov/dataset/PolarTrav/). Get the camera calibration from its README.
2. `stereo_to_height(left, right, calib)`: OpenCV StereoSGBM disparity → depth → height grid → the *same* `detect_hazards`.
3. Dashboard panel: left image | disparity | hazard overlay. This shows judges the pipeline under harsh polar lighting, which is the problem ShadowNav and the Ames team describe.

**Done when:** Track A returns hazards and craters for a patch fast enough for nav to call every step (aim for well under a second). Track B shows one processed POLAR frame.

**Fallback:** no usable calibration means showing disparity only (relative depth) and saying so. If crater detection is weak, nav runs on drift-only localization and the demo still works.

**Doesn't apply here:** ShadowNav's lamp-below-camera shadow trick and edge detection beyond 10 m. Those matter for RASC-AL, but the sim gives heights directly.

## Part 3: Navigation and Autonomy (Calvin)

Part 3 is the rover's brain. It runs the whole sortie with no human input (the brief says the region can lose Earth contact for up to 11 days) and shows a speed made good far above VIPER's.

**Mission state machine:** `DESCEND` (rim → crater floor) → `SURVEY` (parallel lines over the shadowed target box) → `DRILL` (at the best candidates) → back to `SURVEY` or on to `RETURN` → `DOCKED`. Any state jumps to `RETURN` when the energy rule fires.

**Build, in order:**

1. **Global planner:** A\* (heapq) on the orbital cost map. Cells over 15° are blocked. Cost = distance × (1 + slope penalty). At 400 × 400 cells this runs in well under a second in plain Python.
2. **Motion and local replanning:** move along the path in small steps. Every few meters, call `world.sense_terrain` → `detect_hazards`, and write blocked cells into a "learned" layer over the orbital map. If the path ahead is now blocked, run A\* again from the current position. (D\* Lite is a stretch goal; plain re-running A\* is fine at this map size.)
3. **Localization:**
   - **Dead reckoning:** the estimated pose drifts about 2% of distance traveled (the drift figure from the team's ShadowNav reading).
   - **Crater fix:** when `detect_craters` returns a crater, match it to the orbital catalog within the current uncertainty radius. Then snap the estimated pose to catalog position minus the observed offset, and shrink the uncertainty.
   - Log `pose_est` beside the true `pose`, so the dashboard can plot error over time. Our claim is "error stays bounded instead of growing," not "we match ShadowNav's 1.7 to 3.8 m."
4. **Survey and decide:** lay out lawnmower lines with spacing \[A\] and take `sense_neutron` along them. Smooth the readings. A cell becomes a candidate when the estimate passes 1.0 wt% (the brief's \[A\] threshold) with enough confidence. Then pick the top two candidates per sortie (the brief's "drill the two best spots").
5. **Drill:** stop, spend drill time and energy \[A\], and take a low-noise reading. That makes a confirmed `Finding` (kind = 1), which goes to `ChainClient.report`. Candidates can be reported too (kind = 0), so the ledger shows both.
6. **Energy:** a 7.9 kWh pack (brief). Drive and idle ("hotel") power are \[A\]; get them from Kellen's Scout\_Design\_v1.0. Return rule: when energy left ≤ energy to drive home along the planned route × a margin, plus the 25% reserve, switch to `RETURN`.
7. **Metrics in the log:** distance, elapsed time, speed made good (target ≥ 7.4 cm/s from the brief; the brief gives VIPER as \~0.8 cm/s), energy left, and position error.

**Done when:** one command runs a full sortie headless and writes the log. The rover goes around at least one hidden obstacle, makes at least one crater fix, confirms at least one finding, and docks with ≥ 25% battery.

**Fallback, in order of what to cut:** first D\* Lite, then crater fixes (keep drift-only with a note), then local replanning (follow the global path only). Never cut the energy rule or the findings. They are what the story is about.

## Part 4: Contracts, chain bridge and public viewer

Part 4 puts findings on the Monad testnet, reads them back, and proves anyone can see them without trusting our app.

**Build, in order:**

1. **Contract:** `FindingRegistry.sol` with the fields and functions in Shared interfaces. Owner registers rover wallets; only registered rovers can report. Use Foundry or Hardhat, whichever you know. Deploy a minimal version by T+1:30 so the others can test against it.
2. **Network setup:** take the testnet chain ID, RPC URL, explorer and faucet from the Monad docs or the morning workshop. Don't copy them from memory or old blog posts. Fund one wallet per rover.
3. **`ChainClient` (Python, web3.py):** load the ABI and the rover's private key from `.env`, which must never be committed. Build, sign and send `reportFinding`, wait for the receipt, and log a `tx` event with the hash. `list_findings()` reads `FindingReported` events (or loops `getFinding`) and returns `Finding`s.
4. **Coordinates:** convert (x\_m, y\_m) to lat/lon with pyproj, using the CRS read from the GeoTIFF itself (Part 1 passes it in). Don't hand-type a projection string. Store the result as microdegrees.
5. **Data hash:** canonical JSON of the raw readings (sorted keys, fixed float format) → keccak256 → `dataHash`. Save the JSON as `findings/<hash>.json` and add a "verify" button on the dashboard that re-hashes it and compares it with the chain.
6. **Access-control demo:** a second registered rover posts too, and an unregistered wallet gets rejected. This takes 30 seconds to show and answers "can anyone spam it?"
7. **Public viewer (stretch, good for anyone with React):** a tiny static page that reads events straight from the public RPC in the browser. Our Python backend isn't involved, which backs up the claim "anyone can view it."

**Done when:** `ChainClient.report` lands a finding on testnet from the sim, and `list_findings` returns it with the right values and hash.

**Fallback:** if the testnet or faucet is down during the event, run the same contract on a local Anvil or Hardhat node and say so plainly in the demo.

## Day-of timeline

Plan for about 6 hours of hacking; confirm the real start and end times at check-in. T+0 is when hacking starts. Two integration checkpoints keep the four parts from drifting apart.

1. **T+0:00 to 0:30, all four together:** make the repo, write `shared/types.py` and `config.py`, and add stub functions that return fake data, so every part can run on its own right away. The contracts person confirms the Finding fields.
2. **T+0:30 to 2:30, build in parallel against the stubs.** Targets: Part 1 has real terrain loading and `sense_*` working. Part 2 has `detect_hazards`. Part 3 has A\* and the state machine. Part 4 deploys the minimal contract by T+1:30.
3. **T+2:30, Checkpoint 1 (15 min):** swap the stubs for real code. One headless sortie writes a log, and one finding lands on testnet. If this slips, cut stretch goals right away.
4. **T+2:45 to 4:30, features:** crater fix and replanning (Parts 2 and 3), drill and energy rule (Part 3), dashboard and ledger panel (Part 1), verify button and second rover (Part 4), POLAR panel (Part 2) only if Track A is done.
5. **T+4:30, Checkpoint 2:** a full run end to end on the dashboard, with real chain transactions.
6. **T+4:30 to 5:15, harden:** fix bugs, fix the random seed so the demo run is the same every time, and screen-record one good run as a backup in case live fails. Freeze features at T+5:15.
7. **T+5:15 to end, pitch:** rehearse the demo script twice with a timer. Decide who presents and who answers which kind of question.

## Tonight, the demo, and judge questions

**Tonight (no code)**

- [ ] Ask the organizers: are downloaded public datasets and installed packages allowed before the start? What are the hacking hours, the team size cap, and the demo length?
- [ ] Download one LOLA site tile (`*_surf.tif` + `*_slp.tif`). Check file sizes first.
- [ ] Download one POLAR Traverse sequence and its README (calibration).
- [ ] Install Python 3.11 + numpy, scipy, rasterio, pyproj, opencv-python, streamlit, plotly, web3; plus Foundry or Hardhat and a wallet.
- [ ] Ask Kellen for the Scout's drive and hotel power and its wheel-obstacle limit, and whether it's OK to present PROSPECT publicly.
- [ ] Put a name on each part. Everyone reads Shared interfaces and their own part.
- [ ] Calvin and the Part 2 person reread the detection and matching sections of ShadowNav. Part 4 reads the Monad testnet quickstart.

**Demo script (aim for 3 minutes; adjust to the real time limit)**

1. **Problem (20 s):** NASA wants maps of lunar ice. Rovers in dark craters crawl because people on Earth plan every move. The Accords commit nations to share findings publicly, but nothing makes that automatic or tamper-evident.
2. **Live run (90 s):** real LOLA terrain, then the rover leaves the dock, hits a rock the orbital map missed, and replans. A crater fix snaps the position error back down. The neutron trace rises, and the rover drills and confirms. The pin appears on the map, and you click through to the explorer.
3. **Proof (30 s):** the ledger panel reads from chain, not our database. Hit "verify" to re-hash the raw data. An unregistered wallet gets rejected.
4. **Numbers (20 s):** speed made good vs VIPER (as given in our team brief), battery left at dock, position error with and without crater fixes.
5. **Why it matters (20 s):** a neutral public ledger of where lunar resources are and who found them first. It also builds toward our NASA RASC-AL entry.

**Likely judge questions**

| Question | Answer |
| --- | --- |
| Doesn't the chain just trust whatever the rover says? | Yes. The chain proves *who* reported *what* and *when*, not that it's true. Each rover signs with its own wallet, raw data is hash-locked, and drill-confirmed findings are marked separately. Next step: multiple rovers or operators confirming each other. |
| Why not a normal database or NASA's archive? | No single operator controls the record or can quietly edit it. The Accords' data-sharing section doesn't cover private companies unless they act for a signatory, so commercial prospectors need a neutral place to publish. |
| Why Monad? | Cheap, fast EVM, so many rovers can post often. Monad pitches up to 10,000 TPS ([source](https://luma.com/blitz-pune-july-2026)). Don't claim a number we didn't measure. |
| Is the terrain real? Is the ice real? | Terrain: yes, NASA LOLA 5 m/px. Ice: simulated, placed only in shadowed areas, with peak values set from LCROSS's \~5.6 wt% at Cabeus (cited in our team brief). |
| How would it see in the dark? | On the real rover: stereo cameras with its own lights, plus crater matching (ShadowNav). Our POLAR panel runs the CV on NASA's real polar-lighting images. |
| Who pays for this? (invest-ability) | Hypotheses, not claims: commercial prospectors registering finds to prove they were first, mining and ISRU planners buying verified site data, agencies meeting their transparency commitments. |

**How the demo maps to the rubric:** Invest-ability = the who-pays answer. Creativity = rover autonomy plus on-chain science records, which we haven't seen combined. Completeness = one end-to-end run plus the backup recording. Presentation = everyone can answer the trust question above.

## Assumptions, open questions and resources

Every \[A\] value lives in `config.py` with a comment, so swapping in a real number is a one-line change. Tags follow the PROSPECT brief: \[L\] literature, \[A\] assumption, \[U\] team decision.

| Parameter | Value used | Tag | Who confirms |
| --- | --- | --- | --- |
| Route slope limit | 15° | \[L\] VIPER, via team brief | — |
| Odometry drift | \~2% of distance | \[L\] ShadowNav, via team reading | — |
| Reliable stereo range | \~10 m | \[L\] ShadowNav, via team reading | — |
| Battery | 7.9 kWh, return with ≥ 25% | \[A\]/\[C\] team brief | Kellen |
| Target speed made good | ≥ 7.4 cm/s (VIPER \~0.8 cm/s) | \[C\] team brief | Kellen |
| Ice threshold | 1.0 wt% | \[A\] team brief | Science lead |
| Ice peak values | ≤ \~5.6 wt% | \[L\] LCROSS, via team brief | — |
| Drive and hotel power | TBD | \[A\] | Kellen (Scout\_Design\_v1.0) |
| Wheel obstacle height limit | TBD | \[A\] | Mobility lead |
| Neutron footprint and count rates | tuned for a readable demo | \[A\] | — |
| Shadow mask | hillshade proxy, not a real PSR map | \[A\] | — |
| Drill time and energy | TBD | \[A\] | Science lead |

**Open questions**

- Hacking hours, team cap, demo length, and whether data can be downloaded ahead of time (organizers).
- OK to present PROSPECT publicly (Kellen and the team).
- Final Finding fields (contracts person).
- If any hackathon code is reused for RASC-AL, log the AI use in the team AI log, as the brief requires.

**NASA and related resources**

- [LOLA 5 m/px south-pole site DEMs and slope maps](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/) (PGDA, NASA Goddard)
- [South Pole LOLA DEM mosaic, 87–90°S, 5 m/px](https://pgda.gsfc.nasa.gov/products/81) (large files)
- [Shape-from-shading DEMs for the 13 Artemis III regions](https://pgda.gsfc.nasa.gov/products/104)
- [POLAR Traverse Dataset](https://ti.arc.nasa.gov/dataset/PolarTrav/) (NASA Ames: sequential stereo under polar lighting)
- [POLAR Stereo Dataset](https://ti.arc.nasa.gov/dataset/IRG_PolarDB/) (NASA Ames: 2,500+ HDR stereo pairs with ground truth)
- [Characterization of Stereo Vision Performance, Resource Prospector team](https://ntrs.nasa.gov/api/citations/20160011187/downloads/20160011187.pdf) (NTRS)
- [ShadowNav, arXiv:2301.04630](https://arxiv.org/abs/2301.04630) (from the team reading list)
- [DUST lunar south-pole simulator](https://software.nasa.gov/software/MSC-27522-1) (needs a NASA software usage agreement; for RASC-AL, not tomorrow)
- [Artemis Accords, signed text](https://www3.nasa.gov/specials/artemis-accords/img/Artemis-Accords-signed-13Oct2020.pdf)
- [Monad Blitz Oregon event page](https://luma.com/e8mwhm1s)
