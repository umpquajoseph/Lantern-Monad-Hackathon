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

## 1b. Mission Control redesign (UI team)

Laid out after NASA's "Experience Curiosity" (eyes.nasa.gov/apps/curiosity):

- The 3D scene fills the window. One play bar along the bottom holds play/pause, reset, the clock, the timeline (blue = science stop, red = ice found, low red = obstacle), "Slow for science", speed, and zoom in/out (also `+`/`-`).
- The rover camera and an overhead map sit in two small boxes bottom right, over the scene. Each has a button to fill the window; "Back to rover view" returns. The overhead box extends Joseph's `MapView` (`web/js/map.js`, not edited) with sites and obstacles.
- A folding panel on the left with four icons: Rover, Science, Sites, On chain. During a drill stop it switches to Science by itself, then switches back.
- Most explanation text is gone from the screen. What remains is short labels; the full legend, the simulation notes and the NASA model credit are behind the (i) button.
- On a phone the camera boxes sit side by side and the panel starts folded.
- Picking a camera or rover during a drill stop now ends that stop's close-up; before, the close-up took the camera straight back.

## 1c. Surveyor maps, Prospector drills, a flagged crater (UI team, round 2)

- **Built on main.** `origin/main` (the checkpoint) is merged into `ritvik`. Terrain assets are rebuilt with main's `web/build_assets.py` from the committed `lantern_data/` (same no-go share as the team's site, 20.6%), and the page uses main's `world.js`, `map.js` and `data.js` unchanged.
- **Surveyor has no arm.** It stops 6 m short of a site, turns to face it, pans its mast camera, maps the ground, takes a neutron dwell and a photo (`science.run_site_survey`, `SURVEY_PHASES`). Its verdict comes from the dwell, against the same 1 wt% threshold; the Prospector's core confirms. Reports store the dwell and the terrain map instead of a core.
- **A crater the orbital map missed.** On the Surveyor's 2nd drive between two ice sites (with seed 11, T04 to T06), a real terrain crater has rim boulders no map shows. The Surveyor stops about 10 m short, faces it, maps it, photographs it and writes a hazard report (`lantern.hazard/1`). The hazard goes into the route object it publishes (hash on chain), then it drives around.
- **Prospector plans a better way.** Before planning, it reads every Surveyor route by its CID and checks the hash. It adds each flagged hazard to its map with a 4 m margin. Where the Surveyor's route ran into a hazard, it plans its own route instead of following that one (20 m clear of the rim in the seed-11 run).
- **First-person at Surveyor stops.** When the Surveyor stops at a site or the crater, the main view switches to its own camera, with no drawn effects on it. The steps of the stop are listed in the panel.
- **Suspension.** Each rover body tilts and lifts to a plane fitted to the ground under its wheels. On Perseverance the merged wheel and rocker meshes are cut apart by clustering, so each wheel spins with distance and rides up and down, and the rockers tip. Opportunity is one mesh, so only its body tilts. The wheel and rocker parts are groups of meshes (one per material), so they are cut apart together.
- **Rovers on the ground.** The suspension measured the model in scene coordinates, about 380 m off, so the Surveyor floated 13 m up and the Prospector sank 5 m. It now measures in the rover's own frame.
- **Crater rings.** Joseph's crater map (`terrain.craters`, 11,082 craters) is drawn as red rim rings on the ground within 150 m of the followed rover. The overhead map shows his no-go layer (`map.js`, `opts.nogo`). Both are under Layers → Craters.
- **"How to read this"** is rebuilt as sections: the two rovers, the ground colours, the camera colours, on chain, and controls.

## 1d. Lidar view and steep craters (UI team)

- **The team's lidar.** `sim/lidar.py` and `sim/scene.py` are taken unchanged from `integration` (Calvin's top-down `Lidar.view`, 40304d4, and the perf work it needs). `sim/mission_render.py` scans at every step that has a camera frame and saves `Lidar.view` as `lidar_frame`, with `lidar_hazards` beside `fp_frame`. `--lidar-only` adds lidar frames to a mission that already has camera frames (565 frames, about 15 ms each). A camera frame from the new `scene.py` differs from a stored one by 1.7/255 on average, so stored camera frames stay.
- **Camera | Lidar switch.** A pill in the rover camera box, and the same pill over the full-screen first-person view; `L` toggles it. Lidar mode shows the top-down scan (grey safe, amber crater floor, red steeper than 15 deg, rings every 5 m); the caption counts the scan's hazard cells. Runs without lidar frames disable the switch.
- **Only steep craters ringed.** The red rings on the ground are drawn only for craters with at least one cell steeper than 15 deg across 2 m inside the rim (Joseph's `nogo.png`, from the planner's `SlopeMap`): 6,680 of the 11,082. Shallow craters the planner drives through are not ringed.
- Keyboard shortcuts now work after clicking the timeline (it kept focus and swallowed them).

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
python sim/roles.py run --render --publish-run data/runs   # camera and lidar frames
python web/build_assets.py
cp -R data/runs/<mission id> web/runs/ && python web/serve.py   # open /mission.html
```
