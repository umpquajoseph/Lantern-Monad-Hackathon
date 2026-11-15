# Lantern

A shared map of lunar ice that rovers write themselves, built at Monad Blitz
Oregon, 15 November 2026. Simulated rovers from two agencies drive a dark
crater at the lunar south pole. They find their position by matching lamp-lit
crater rims to an orbital map, after JPL's ShadowNav. They post ice finds,
with their position uncertainty, to a contract on Monad testnet, and check
each other's finds. The contract refuses a check from the finder's own
agency or from too far away, and marks a find confirmed or disputed.

**Live site: https://lantern-teal.vercel.app**. `/` replays the demo runs and
`/chain.html` reads the contract live. Mission Control, `web/mission.html`,
goes up with the next publish.

## Run it

Python 3.11 or newer:

```bash
pip install numpy scipy tifffile opencv-python matplotlib web3 python-dotenv vtk
```

| To do this | Run | Then |
|---|---|---|
| The whole demo site | `python app.py` | builds what is missing, runs both demo runs if there are none, opens http://localhost:8000 |
| Rerun the two demo runs | `python app.py --fresh` or `python run_demo.py` | runs land in `data/runs/` |
| The 8-site Surveyor/Prospector mission | `python sim/roles.py run --render --publish-run data/runs` | copy the mission into `web/runs/`, open `/mission.html` |
| Read the chain record | `python sim/chain.py finds` | needs no keys |
| Publish the site | `python web/publish.py vercel` | see `docs/hosting.md` |

Data lives in `LANTERN_DATA` if set, else `C:\Monad-Hackathon-Project\data`,
else `./data` (`sim/terrain.py`). To skip the terrain build, set
`LANTERN_DATA=lantern_data`, the committed copy.

**The chain.** With no keys, runs post to a stand-in and the site says the
hashes are fake. To post for real, copy `.env.example` to `.env` and fill in
`MONAD_RPC_URL` and two throwaway testnet keys, `AGENCY_A_PRIVATE_KEY` and
`AGENCY_B_PRIVATE_KEY`, funded from the Monad faucet. Never commit `.env`.
Every transaction links to Monadscan, which opens in a second window because
it cannot be embedded.

## Where things are

| Path | What it is |
|---|---|
| `app.py` | One command: terrain, display layer, web assets, runs, viewer |
| `run_demo.py` | The two demo runs end to end: rover A finds, rover B checks |
| `sim/terrain.py`, `sim/terrain_display.py`, `sim/terrain_look.py` | Height grid and crater list from NASA LOLA; display-only surface detail |
| `sim/nav.py` | Particle filter on rim points (ParticleNav) |
| `sim/rover.py`, `sim/ice.py` | Planner under the 15 deg slope limit; hidden ice and the neutron sensor |
| `sim/scene.py` | 3D render: first-person lamp camera with depth, chase view |
| `sim/chain.py` | Python client for the Lantern contract |
| `sim/roles.py`, `sim/mission*.py`, `sim/registry_client.py`, `sim/evidence.py`, `sim/science.py` | Surveyor and Prospector mission, its store, evidence hashing |
| `sim/results.py`, `sim/survey.py` | Per-run results ledger and its contract client |
| `contracts/` | **Lantern**, the demo's contract: finds, checks, safety zones. Foundry |
| `registry/` | LunarDiscoveryRegistry, the mission's site registry. Not used by the demo |
| `survey/` | LanternSurvey, the results ledger beside Lantern. Optional |
| `web/` | The static site: `index.html` viewer, `mission.html`, `chain.html` |
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
