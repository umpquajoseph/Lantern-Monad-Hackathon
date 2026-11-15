# Architecture: Surveyor, Prospector and the discovery registry

Branch `ritvik`, built on `integration`. Everything here is added beside the
team's parts; none of their files were edited.

```
satellite site list ─┐
                     ▼
 Surveyor A ── explores, neutron survey ── registerCandidate ─────────────┐
     │  drills (inspection)            ── submitSurveyorInspection ──────┤
     │  report + route  ──► evidence store (CID = keccak of the bytes)    ▼
     │                                              LunarDiscoveryRegistry (chain)
 Prospector B ◄── reads eligible sites, report and route CIDs ────────────┤
     │  fetches the route by CID, checks its hash, reuses what is safe    │
     │  finds a new obstacle, replans                                      │
     └─ fuller analysis  ── submitProspectorAnalysis (names its parent) ──┘
```

| Part | File | Owner |
|---|---|---|
| Terrain, 3D scene, viewer, run_demo | `sim/terrain*.py`, `sim/scene.py`, `web/index.html`, `run_demo.py` | Joseph |
| Planner, particle filter | `sim/rover.py`, `sim/nav.py` | Calvin / the spine |
| Lantern contract and client | `contracts/`, `sim/chain.py` (branch `step-1-chain`) | chain teammate |
| Registry contract, 20 Foundry tests, deploy script | `registry/` | this branch |
| Registry client: local anvil, Monad testnet, offline mock | `sim/registry_client.py` | this branch |
| Surveyor and Prospector state machines | `sim/roles.py` | this branch |
| Missions, health, science, resources | `sim/mission.py`, `health.py`, `science.py`, `resources.py` | this branch |
| Storage: evidence, SQLite cache, outbox | `sim/evidence.py`, `mission_db.py`, `mission_store.py` (see `storage.md`) | this branch |
| First-person frames for a mission | `sim/mission_render.py` (uses Joseph's `Scene`) | this branch |
| Mission Control dashboard | `web/mission.html`, `web/mission.css`, `web/js/mission.js`, `web/js/science_rig.js` (uses Joseph's `world.js`, `data.js`) | this branch |

## What is real, what is simulated

- Real: LOLA terrain; the planner's routes; the particle filter's estimate;
  the registry contract, its transactions and reads (on a local anvil chain);
  evidence hashing and verification; first-person frames from the 3D scene.
- Simulated, and labelled so on screen and in every report: ice deposits,
  neutron and drill readings, the one new obstacle, the rim points fed to the
  filter (not camera vision yet).
- Not Monad yet: the registry runs on a local anvil chain. `registry/deploy.sh`
  deploys it to Monad testnet (chain 10143) with the agency keys in `.env`;
  then `python sim/roles.py run --chain monad`.
- Not done: IPFS upload (CIDs are computed locally and are valid IPFS CIDs;
  the Kubo upload path is untested); camera-based obstacle detection (the
  slope map stands in); NASA rover models (shapes here are our own boxes).

## Run it

```bash
export LANTERN_DATA="$PWD/lantern_data"
cd registry && forge test && cd ..                 # 20 contract tests
python sim/evidence.py check                        # storage self-checks
python sim/roles.py run --render --publish-run data/runs   # mission + frames (about 3 min)
python web/build_assets.py                          # once (terrain for the browser)
cp -R data/runs/<id> web/runs/ && python web/serve.py      # then open /mission.html
```
