# Changes on branch `ritvik`: what was done, how, and what is left

For Joseph and the integrator. Everything below is on `origin/ritvik`, built on
`integration`. No file owned by another person or agent was edited; all work is
in new files, apart from a few small files I wrote earlier myself. To bring it into
the team's app, merge `ritvik` into `integration`.

## 1. Changes from the website review

| Ask | Done? | How |
|---|---|---|
| Rovers labelled | Yes | 3D name tags "Surveyor A" / "Prospector B" in `web/js/mission.js` (`dressRover`). |
| Unique rover models, NASA assets | Yes | NASA's own glTF files from github.com/nasa/NASA-3D-Resources in `web/models/`: Opportunity = Surveyor, Perseverance = Prospector. Draco-decoded, measured on load, rotated to the scene frame, scaled, set on the ground (`loadModel`). On-screen credit says they are Mars rovers standing in for lunar ones (NASA has no VIPER model). If a model fails, the box rover stays. |
| Science instrument visible and clearly in use | Yes | `web/js/science_rig.js`: arm, drill, dust (ballistic, lunar gravity), core, NIR scan light. The arm appears only while an instrument is working; the name tag reads "DRILLING 40 cm", "NIR SCAN", "ANALYSING CORE", "neutron survey". |
| Fix the forward-facing camera | Yes | The Mac lacked VTK. `sim/mission_render.py` renders first-person frames with Joseph's `Scene` and the display layer (`display_dir=lantern_data/terrain/display`): 520 frames per mission, about 0.24 s each. |
| Camera POV shows the path, diverting around obstacles | Yes | The route the rover is following is projected onto its recorded camera frame (pinhole model matching `scene.py`). A found obstacle is drawn in red with "steering around"; after the replan the new route is shown. |
| Sites highlighted | Yes | Satellite sites are amber rings ("satellite: possible site"). A site turns red, with a pulsing patch, when a drill finds ice, and stays red when both rovers confirm it. |
| Does not stop after the first spot | Yes, in Mission Control | `mission.html` plays the whole mission (8 sites, about 9 simulated hours). The Vercel site is Joseph's viewer (`index.html`), which I did not change. |
| Satellite relays the site; rover navigates itself | Shown in UI | A "Who knows what" panel explains it. See "not done" for the camera part. |
| Science slowed down and zoomed in, data shown, chain explained | Yes | During a stop, playback drops to 1.5x and the camera moves in on the drill. "Data being recorded" fills water by depth every 10 cm. "On chain, in plain words" shows what went on chain, what stayed off, and a Verify button that re-hashes the evidence in the browser. |

## 2. The larger build (master implementation prompt)

The team agreed to extend Lantern, not rewrite it in React/TypeScript.

- **Registry contract** `registry/src/LunarDiscoveryRegistry.sol`: Candidate -> SurveyorInspected -> ProspectorAnalyzed; results Unknown/Positive/Negative/Inconclusive; append-only reports linked by parent; admin-set Surveyor/Prospector roles; checks on duplicates, coordinates, transitions, empty hashes and CIDs. **20 Foundry tests pass** (`cd registry && forge test`). `registry/deploy.sh` deploys to Monad testnet or a local anvil only.
- **Registry client** `sim/registry_client.py`: local anvil (real contract), Monad testnet (keys from `.env`), or an offline mock that is labelled as one.
- **Surveyor and Prospector** `sim/roles.py`: the plan's state machines. The Surveyor registers candidates, drills, and publishes Positive and Negative results with its route. The Prospector reads eligible sites from the contract, fetches the Surveyor's route by CID, checks its hash, reuses the safe parts, finds a new obstacle and replans, then publishes a linked analysis. Test run: 8 sites, 4 positive, 4 analysed.
- **Calvin's particle filter** (`sim/nav.py`) now does navigation in missions, fed by the rim-point stand-in.
- **Storage** (`docs/storage.md`): chain holds 32-byte hashes; reports and thinned routes are content-addressed JSON (valid IPFS CIDs); SQLite cache with an outbox that never double-posts after a crash.
- **Dashboard** `web/mission.html`: three cameras, HUD, registry table, transactions, site drawer with the report chain.
- **Docs**: `docs/ARCHITECTURE.md`, `docs/DEMO_SCRIPT.md`, `docs/storage.md`, `docs/science.md`.

## 3. Not done, and who owns it

| Item | Owner |
|---|---|
| Merge `ritvik` into `integration`, link `mission.html` from the hosted site, redeploy Vercel | Joseph (integrator) |
| Deploy the registry to Monad testnet (`registry/deploy.sh` with the agency keys in `.env`), then `python sim/roles.py run --chain monad` | Chain teammate (has the keys) |
| Add `bytes32 dataHash` to `Check` in the Lantern contract so check reports are anchored like finds | Chain teammate |
| Real camera obstacle detection (crater rims from the image, `sim/vision.py`). Today the slope map and one simulated obstacle stand in, and the UI says so. | Unclaimed (step 6 vision) |
| Calvin's latest `sim/rover.py` conflicts with `integration` in his own files; I did not merge it | Calvin / Joseph |
| Arm attachment: the drill arm is generic, so on Opportunity it reaches out from beside the body | Ritvik (polish) |
| IPFS upload path (`IPFS_API`) is written but untested; evidence is local | Ritvik |
| Ice, readings and drill cores are simulated and labelled so | By design |

## 4. Run it

```bash
export LANTERN_DATA="$PWD/lantern_data"
python sim/roles.py run --render --publish-run data/runs
python web/build_assets.py
cp -R data/runs/<mission id> web/runs/ && python web/serve.py   # open /mission.html
```
