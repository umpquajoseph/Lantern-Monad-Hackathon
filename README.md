# Lantern

A shared map of lunar ice that rovers write themselves, built at Monad Blitz
Oregon, 15 November 2026. Simulated rovers from two agencies drive a dark
crater at the lunar south pole. They find their position by matching lamp-lit
crater rims to an orbital map, after JPL's ShadowNav. They post ice finds,
with their position uncertainty, to a contract on Monad testnet, and check
each other's finds. The contract refuses a check from the finder's own
agency or from too far away, and marks a find confirmed or disputed.

**Live site: https://lantern-teal.vercel.app**

## The demo mission

One run of `run_mission.py`, two rovers, replayed by the site with pauses at
its checkpoints:

1. The Surveyor (agency A) plans a route to two candidate sites from the
   orbital map, which is too coarse to show small craters. Its lidar finds
   them on the way and the route bends around them.
2. At S1 it stops and surveys with its neutron sensor. S1 is dry, so nothing
   goes on chain.
3. At S2 it finds ice and posts the find, with its position uncertainty, to
   the Lantern contract.
4. The Prospector (agency B) reads the contract, sees a find from another
   agency, drives there and runs its own instruments. Its check agrees, and
   the contract marks the find confirmed.

Both rovers navigate with the particle filter on rims seen by their cameras
(`sim/vision.py`), and plan around steep ground and lidar hazards under a
15 deg slope limit. What is still a stand-in is listed in the run log under
`standins`.

The site opens on the mission. Opened as `/?key=<DEMO_KEY>`, each
play-through posts the find and the check to Monad testnet for real, through
`web/api/chain.js`; without the key it replays the recorded hashes. Every
transaction opens on Monadscan in a new window. `/chain.html` reads the
contract live. The earlier two-run demo is still in the run list.

Contract: Lantern at `0x67f9Bedb42C845307cB1Fd6551D07Df64bd3CE98`, Monad
testnet (chain 10143), verified on Sourcify. Details in
`sim/lantern_deployment.json`.

## Run it

Python 3.11 or newer:

```bash
pip install numpy scipy tifffile opencv-python matplotlib web3 python-dotenv vtk
```

| To do this | Run | Then |
|---|---|---|
| The demo site | `python app.py` | builds what is missing, opens http://localhost:8000 |
| Rerun the demo mission | `python run_mission.py --stamp demo` | writes `data/runs/mission-demo`; posts for real when `.env` has the keys, `--no-chain` to use the stand-in, `--no-vision` for stand-in rims |
| Rerun the two earlier demo runs | `python run_demo.py` | runs land in `data/runs/` |
| Read the chain record | `python sim/chain.py finds` | needs no keys |
| Publish the site | `python web/publish.py vercel` | see `docs/hosting.md` |

Data lives in `LANTERN_DATA` if set, else `C:\Monad-Hackathon-Project\data`,
else `./data` (`sim/terrain.py`). To skip the terrain build, set
`LANTERN_DATA=lantern_data`, the committed copy.

**The chain.** With no keys, runs post to a stand-in and the site says the
hashes are stand-ins. To post for real, copy `.env.example` to `.env` and fill in
`MONAD_RPC_URL` and two throwaway testnet keys, `AGENCY_A_PRIVATE_KEY` and
`AGENCY_B_PRIVATE_KEY`, funded from the Monad faucet. Never commit `.env`.
The hosted page's live posting keeps its keys in
the Vercel project's environment, never in git (`docs/hosting.md`, "The demo
mission and the live uplink").

## Where things are

| Path | What it is |
|---|---|
| `app.py` | One command: terrain, display layer, web assets, runs, viewer |
| `run_mission.py` | The demo mission: Surveyor surveys S1 and S2, posts the find, Prospector checks it |
| `run_demo.py` | The two earlier demo runs, and the render and camera code the mission uses |
| `sim/terrain.py`, `sim/terrain_display.py`, `sim/terrain_look.py` | Height grid and crater list from NASA LOLA; display-only surface detail |
| `sim/nav.py` | Particle filter on rim points (ParticleNav) |
| `sim/rover.py`, `sim/ice.py` | Planner under the 15 deg slope limit; hidden ice and the neutron sensor |
| `sim/lidar.py`, `sim/vision.py` | Lidar hazards (top-down and first-person views); crater rims from camera frames |
| `sim/scene.py` | 3D render: first-person lamp camera with depth, chase view |
| `sim/chain.py` | Python client for the Lantern contract |
| `sim/roles.py`, `sim/mission*.py`, `sim/registry_client.py`, `sim/evidence.py`, `sim/science.py` | An earlier 8-site mission on the registry contract. Not used by the demo |
| `sim/results.py`, `sim/survey.py` | Per-run results ledger and its contract client |
| `contracts/` | **Lantern**, the demo's contract: finds, checks, safety zones. Foundry |
| `registry/` | LunarDiscoveryRegistry, the mission's site registry. Not used by the demo |
| `survey/` | LanternSurvey, the results ledger beside Lantern. Optional |
| `web/` | The static site: `index.html` (the mission), `chain.html` (the contract), `api/chain.js` (live posting on Vercel) |
| `web/chain/` | Copies of `sim/lantern_*.json` for the site, made by `web/collect_chain.py` |
| `lantern_data/` | Committed terrain, so a fresh clone runs without building |
| `science/` | Mission Control's science preview and a sample record |
| `docs/` | Spec, hand-offs, architecture, demo script, sources (below) |
| `ops/` | Coordination between the build sessions |

## Docs

- `docs/spec.html`: the goal, the demo and the six build steps.
- `docs/handoffs.md`: the data each step passes to the next.
- `docs/ARCHITECTURE.md`, `docs/storage.md`, `docs/science.md`: the mission.
- `docs/DEMO_SCRIPT.md`: the pitch, step by step.
- `docs/hosting.md`: the Vercel site and how to republish it.
- `docs/WORKING.md`: who built what, on which branch.
- `docs/CHANGES-chain.md`, `docs/CHANGES-ritvik.md`: notes from the merge.
- `docs/moon-ledger-spec.md`: the first spec, from the night before.
- `contracts/README.md`: deploy, verify, and redeploy before the demo.

## Tests

```bash
cd contracts && forge test
cd registry && forge test
cd survey && forge test
```

Foundry is at https://getfoundry.sh. `contracts/lib/` is git-ignored;
restore it with `forge install foundry-rs/forge-std --no-git` in `contracts/`.

## Sources

- Cauligi et al., ShadowNav, JPL, 2023: https://arxiv.org/abs/2301.04630
  (copy and text in `docs/sources/`)
- PROSPECT Team Brief, RASC-AL 2027 Theme 3-1, in `docs/sources/`
- LOLA south pole elevation maps, NASA Goddard PGDA:
  https://pgda.gsfc.nasa.gov/products/90
- Rover models: NASA 3D Resources, https://github.com/nasa/NASA-3D-Resources
  (Mars rovers standing in for lunar ones)
- Monad documentation: https://docs.monad.xyz
