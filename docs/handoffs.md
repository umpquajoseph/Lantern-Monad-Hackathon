# Hand-offs between steps

Proposed formats. Agree them in the first 20 minutes of the build, then change
one only by telling the others and editing this file in the same commit.

## Frames and units

- World frame: metres, x east, y north. On a real LOLA tile, south polar
  stereographic metres, the frame the LOLA files use.
- Heading: radians, counterclockwise from +x.
- Rover frame: metres, x forward, y left, origin at the camera.
- Uncertainty `sigma`: one number in metres, the filter's 1-sigma radial
  spread, `sqrt(trace(cov) / 2)` of the particle cloud.

## Step 2, terrain, gives

- `height`: `np.ndarray (H, W)`, float32, metres, cell size 0.25 m, cell `[0, 0]`
  at world `(x0, y0)`. Cell `[i, j]` is at `x = x0 + j * cell`,
  `y = y0 + i * cell`: rows run north. A GeoTIFF's rows run south, so flip it.
- `craters`: `np.ndarray (N, 3)`, rows `[x, y, diameter]` in metres. This is the
  rover's orbital map.
- Slope for planning: measure it across `SLOPE_BASELINE_M` = 2 m (ASSUMED,
  about one rover length), not across one 0.25 m cell. Across single cells
  the walls of the deeper mapped craters reach about 27°, so a 0.25 m slope
  map would wall off every crater interior. Across 5 m the drive area is
  99.8% under 15° (terrain report, 2026-11-15). The 15° limit itself is the
  spec's.
- On disk: `sim/terrain.py` has `load_terrain(path) -> (height, craters, x0, y0,
  cell)`. The built file is `data/terrain/*.npz` in the main checkout
  (`C:\Monad-Hackathon-Project\data\terrain\`), git-ignored and rebuilt by the
  script.

## Terrain display (display only; the simulation never reads it)

`data/terrain/display/display.json` in the main checkout lists the files,
parameters and sources. Built by `python sim/terrain_display.py build`.
Real: the LOLA backdrops (`backdrop_near.npz`, 5 m over 5 km; `backdrop.npz`,
20 m over the 20 km tile). Everything else is made up and marked so.

At strength `s` (`display.json`, default 1; one knob for all made-up detail):

- height = sim grid (bicubic) + s x (8 m detail tile at world xy mod 8 m +
  rim lips + rocks)
- albedo = (1 + s(albedo - 1)) x (1 + s(detail_albedo - 1)); rocks at
  `rock_albedo`
- shading: Hapke, Xu et al. 2020 Eqs. (5)-(12) (Cauligi 2023 ref. [26]), with
  the `hapke_*` parameters
- backdrop: `backdrop_near` over `backdrop`, the drive area over both; lower
  each backdrop by 0.5 m plus r^2/2R; add s x `far_detail_height` (512 m
  period) to both
- rocks: `rocks.npy` rows `[x, y, long, short, height, yaw, seed]`; draw the
  first round(s x K)
- Python: `terrain_display.Display().patch(xmin, ymin, nx, ny, res)`
- Arrays run rows north, like the sim grid. PNG row 0 is the south edge.

## Scene (`sim/scene.py`), gives

One 3D scene draws both views, so the camera image the vision step reads is
the one the judges see.

```python
scene = Scene(height, craters, x0, y0, cell)
scene.set_rover(agency, true_pose)            # (x, y, heading)
img, depth = scene.first_person(agency)       # img (h, w, 3) uint8,
                                              # depth (h, w) float32: straight-line
                                              # range in metres from the camera
                                              # along each pixel's ray (not
                                              # z-depth), NaN for sky
img = scene.third_person(agency, overlays)    # (h, w, 3) uint8
```

- `overlays`: optional dict with `true_track (K, 2)`, `odom_track (K, 2)`,
  `estimate (x, y)`, `sigma` (metres), `finds` (list of `get_finds()` dicts).
- Also: `scene.camera_model()` gives the intrinsics and pitch, and
  `scene.depth_to_rover(depth)` turns depth into rover-frame points
  `[x_fwd, y_left, z_up]` for the vision step.
- The first-person camera sits on the Scout's mast, `Settings.camera_height_m`
  (1.6 m, brief p.6); the lamp is `Settings.lamp_below_camera_m` (0.2 m) below
  it, 60° horizontal field of view (Atha et al. 2024, sections IV and V).
  ShadowNav's own camera was 2.5 m up.

## Run log (`run_demo.py` writes it, the web viewer replays it)

The hosted demo replays real runs, so everything the viewer shows comes from
this log. One folder per run, `data/runs/<run_id>/` in the main checkout
(`C:\Monad-Hackathon-Project\data\runs\`, git-ignored):

- `log.json`:

```json
{
  "run_id": "run1-20261115-1400",
  "label": "Run 1: crater fixes on",
  "terrain": {"file": "shoemaker.npz", "x0": 73250.125, "y0": 36000.125, "cell": 0.25},
  "chain": {"chain_id": 0, "contract": "0x...", "explorer": "https://..."},
  "truth": {"ice": [{"x": 0, "y": 0, "radius": 5.0, "peak": 0.0}]},
  "steps": [
    {
      "t": 0.0,
      "rovers": {
        "A": {"true_pose": [x, y, heading], "odom_pose": [x, y, heading],
              "estimate": [x, y], "sigma": 0.8, "nav_on": true,
              "rims": [[x_fwd, y_left]], "fp_frame": "fp/A_0000.jpg"},
        "B": {}
      },
      "events": [
        {"type": "find", "agency": "A", "find_id": 1, "x": 0, "y": 0,
         "sigma": 0.8, "reading": 0.0, "tx": "0x..."},
        {"type": "check", "agency": "B", "find_id": 1, "x": 0, "y": 0,
         "sigma": 0.9, "reading": 0.0, "agrees": true, "tx": "0x..."}
      ]
    }
  ]
}
```

- `fp/<agency>_<step>.jpg`: the first-person frame from `scene.first_person`,
  a sensor frame. It is rendered live in the sim loop, right after the rover
  drives and before it localises, so vision can read it there.
- Per rover, optional `fp_sub`: `[[f, "fp/A_0039_1.jpg"], ...]`, extra
  first-person frames between step k and step k+1, f in (0, 1) ascending,
  about every 0.5 m driven (`run_demo.py --sub-frame-m`). Frame f is at the
  pose interpolated as `web/js/data.js poseAt` does: x, y linear, heading
  along the shorter turn, the camera's own rover only. A player shows the
  last frame with f at or below the playback fraction, else `fp_frame`.
  These are display frames, not sensor frames: they are rendered after the
  sim from the logged poses, in parallel, and no rover ever sees them.
- Per rover `particles`: `[[x, y, w], ...]`, the navigation filter's
  particles after the step (ParticleNav, 100), world metres to 1 cm, `w`
  the normalised weight (sums to 1, 4 decimals). Empty list without a filter.
- Top level `frames`: one sentence each for `fp_frame` and `fp_sub` saying
  how they were made (sensor frame vs interpolated display frame).
- Also written by `run_demo.py` (additive): top level `standins`, `dt_s`,
  `final_finds`, `truth.ice_units`, `truth.find_threshold_wt`; per rover
  `mode`, `ice_reading`, `est_error`, `distance`. `index.json` in
  `data/runs/` lists the runs.
- `standins` lists only parts that are stand-ins for something still to be
  built. A real part gets its own top-level string naming what it is: `nav`
  (the particle filter) and `planner` (A* under the 15 deg slope limit, 2 m
  baseline, 0.25 m grid). The viewer shows those as real.
- `truth` is what the rovers cannot know: the hidden ice map. The viewer may
  show it only as a labelled reveal ("hidden truth"), never as something a
  rover saw. Ice is under the surface, so it is never drawn as visible
  ground.
- Units and frames as above: world metres, heading radians, `t` in simulated
  seconds. `events` lists what happened at that step, with the transaction
  hash once step 1 exists.
- Proposed. The viewer and `run_demo.py` may change it, by telling the lead
  and editing this section in the same commit.

## Step 4, rover loop, gives each step, per rover

- `true_pose`: `(x, y, heading)`.
- `odom`: `(distance_m, dheading_rad)` since the last step, with distance noise
  of 2% (ShadowNav section 1). Until step 4 exists, step 3 uses a scripted drive
  that produces the same tuple.

## Steps 3 and 6 rim points

- `rims`: `np.ndarray (M, 2)`, rows `[x_forward, y_left]` in metres, rover frame.
- Stand-in until step 6: points on the near rims of mapped craters within 15 m
  of the true pose, with noise added and some missed.
- Answer key (step 6 scoring, `sim/rim_truth.py`, details in
  `docs/requests/rim-answer-key.md`): `rim_truth(scene, agency, depth)`
  gives the mapped rims in the frame: per point `xy` (rover frame
  `[x_forward, y_left]`), `uv` (pixels), `crater_id`, `near` (the half
  arc nearest the camera, Cauligi 2023 sec. 5A), `visible` (3-15 m, 60
  deg view, not occluded by the frame's depth) and `shadow` (casts the
  lamp shadow); `rims` (M, 2) is the visible front arc in this format.
  `score(pred_rims, truth)` returns precision, recall (front arc),
  recall_shadow, mean_dist_m and the paper's Gaussian score (sigma 0.25
  m), at a 0.5 m tolerance (assumed).
- About a third of the rim shadow in `display="full"` frames comes from the
  invented rim lips (terrain, 4 poses, 15/32 deg lamp cone: 430
  shadow-casting front-arc points in full against 270 in `display="detail"`;
  dark rim pixels 1156 against 816).

## Step 3, navigation, gives each step

- `estimate`: `(x, y)` in metres, world frame.
- `sigma`: metres, as defined above. Logged next to the true error.

## Step 1, chain client (`sim/chain.py`), gives

```python
post_find(agency, x, y, sigma, reading, data_hash) -> find_id
check_find(agency, find_id, x, y, sigma, reading, agrees) -> tx_hash
get_finds() -> list[dict]   # each: id, agency, x, y, sigma, reading,
                            # agree (agencies), disagree (agencies), status
                            # plus: address, data_hash, time (block timestamp)
```

- `agency` is `"A"` or `"B"`; the client maps it to the key in `.env`.
- On chain, positions and sigmas are whole centimetres, `data_hash` is
  `bytes32`, and status is reported, confirmed or disputed. The client takes
  metres and gives back metres; `status` comes back as one of the strings
  `"reported"`, `"confirmed"`, `"disputed"`.
- `reading` is the ice sensor reading as wt% water-equivalent (float, e.g.
  `5.6`); on chain it is hundredths of wt% (`uint32`, ASSUMED unit, see
  `contracts/src/Lantern.sol`).
- `data_hash`: `bytes` (32) or a `0x` hex string. `chain.hash_raw(obj)` makes
  one from the raw readings (canonical JSON, sorted keys, keccak256).
- Check rule: refused if the checker's agency is the finder's, or if
  `dx² + dy² > (3 × (sigma_find + sigma_check))²`, all in centimetres. Also
  refused: a second check of the same find by the same agency, and any write
  from an address that is not one of the agencies fixed at deployment. A
  refusal raises `chain.LanternRefused` with `.name` in `OwnFind`,
  `AlreadyChecked`, `TooFar`, `NoSuchFind`, `NotAnAgency` and `.values` its
  arguments; nothing is sent.
- Also available: `chain.client().tx_log` (every transaction sent: `kind`,
  `agency`, `tx_hash`, `url`, `block`, `gas_used`, `seconds`, `find_id`),
  `chain.client().last_tx` (hash of the last one, as the spine's stand-in
  has it), `chain.client().run_log_chain()` (the run log's `chain` block:
  `chain_id`, `contract`, `explorer` as the explorer's *base* URL so the
  viewer links `<explorer>/tx/<hash>`, plus `contract_url` and `verified`),
  `post_safety_zone(agency, x, y, radius, until, purpose) -> zone_id` and
  `get_zones()` (Artemis Accords section 11 upgrade).
- For `run_demo.py`: `chain.run_scope()` is a drop-in for `ChainStandin`,
  with the same `post_find`, `check_find`, `get_finds`, `last_tx` and a
  `STANDIN` of `None`. Its `get_finds()` returns only the finds posted since
  the scope was made (the contract keeps every find from every run), with
  their real on-chain IDs (0-based), and is cached for 2 s between the
  scope's own writes so a rover polling every step stays under the public
  RPC's 20 req/s. Verified end to end: the unmodified spine ran both demo
  runs against the testnet contract (2026-11-15, see `contracts/README.md`).
- Read-only use needs no keys: the address and ABI come from
  `sim/lantern_deployment.json` and `sim/lantern_abi.json` (committed, written
  by `contracts/deploy.sh`). The web viewer can read the same two files.

## Results ledger (`sim/results.py`, `sim/survey.py`, `survey/`), gives

After a run, one master file per run and its results on Monad, next to Lantern:

- `python sim/results.py build data/runs/<run_id>` reads `log.json` (above) and
  writes `data/runs/<run_id>/results.json`: per rover the path it believes it
  drove (`estimate`, or `odom_pose` when nav is off) with sigma, its length and a
  downsampled copy for the chain; per find x, y, sigma, lat/lon, reading, a
  modelled ice quantity (`ice.area_m2`, `ice.depth_m`, `ice.mass_kg`), modelled
  volatiles (`volatiles_ppm`, `volatiles_kg`, 8 species from LCROSS, Colaprete
  et al. 2010 Table 2), its checks and its status; run totals; and
  `results_hash`, keccak256 of the canonical file without `truth` and `chain`.
- `python sim/survey.py post data/runs/<run_id>` posts to `LanternSurvey`
  (`survey/src/LanternSurvey.sol`): one traverse per rover, one assay per find
  (signed by the finder, refused for anyone else), one run record with
  `results_hash`. It writes the transactions into `results.json` under `chain`.
  `--post-finds` first posts finds and checks that have no `tx` (stand-in chain
  runs) to Lantern. Re-running skips what is already on chain.
- Reads for the viewer: `SurveyClient().runs()`, `.traverses()`, `.assays()`;
  ABI and address in `sim/survey_abi.json`, `sim/survey_deployment.json`.
- Optional, asked of the spine: a per-step `"reading"` (wt%) in each rover's
  dict. With it the deposit extent is measured along the track; without it the
  area is one sensor footprint (sim/ice.py FOOTPRINT_RADIUS_M). Nothing breaks either way.
- Writers are Lantern's agencies (`isAgency`), so it needs no keys of its own.

## Step 5, dashboard, takes

- The per-step state above for both rovers, `get_finds()`, and from step 6 the
  camera image `np.ndarray (h, w)` or `(h, w, 3)` with `rims` to overlay.

## Mission (`sim/roles.py`, `sim/mission.py`), gives

The Surveyor and Prospector mission behind Mission Control (`web/mission.html`).
Its parts and storage rules are in `docs/ARCHITECTURE.md` and `docs/storage.md`;
this is only what crosses into other steps.

- `python sim/roles.py run [--render] --publish-run data/runs` writes the
  mission's store (`mission.json`, `run_log.json`, `evidence/<cid>.json`) and
  publishes it as a run: `data/runs/<id>/log.json` plus an entry in
  `data/runs/index.json`, beside `run_demo.py`'s entries. `app.py` then copies
  it into `web/runs/` like any other run.
- The run log follows "Run log" above, with the mission's additions read only
  by `web/js/mission.js`: per-step `science`, and `report_cid`, `route_cid`,
  `record_id` on its events.
- Navigation is `sim/nav.py` (ParticleNav) on the rim-point stand-in, the same
  as `run_demo.py`. First-person frames come from `sim/scene.py` through
  `sim/mission_render.py`.
- Chain: the demo's chain is Lantern (step 1 above). The mission's own
  registry, `registry/` and `sim/registry_client.py`, runs on a local anvil or
  an offline mock and is not used by the demo.

## Mission log (`run_mission.py` writes it; the demo page replays it with checkpoints)

The demo is one precomputed run of the real autonomy, replayed on the hosted
site. It is a run log as above, with these additions. Everything here is
additive: a viewer that reads only the run-log fields still works.

Top level:

- `roles`: `{"A": "Surveyor", "B": "Prospector"}`.
- `mission.sites`: the orbital candidates, in visiting order:
  `[{"id": "S1", "x", "y", "radius", "order": 1, "verdict": "dry" | "ice" | null, "kind": "orbital candidate"}]`
  (world metres). The map highlights them from the start; `verdict` is the
  end state, so a viewer that wants the reveal in order uses the `site`
  events below.
- `mission.checkpoints`: where playback pauses, in step order:
  `[{"id", "step", "t", "label", "say", "pause": true}]`. The player, on
  crossing `step` while playing, stops there when `pause` is true and shows
  `label`; `say` is one line for whoever is talking. Pressing play goes on.
  Ids today: `overview` (step 0), `divert-A` (the step before the Surveyor's
  lidar first marks a crater its orbital map does not show), `arrive-S1`,
  `arrive-S2`, `dispatch-B`, `divert-B`, `arrive-B`, `end`. A checkpoint is
  absent when its moment did not happen in the run.
- `mission.unmapped_craters`: `[{"x", "y", "diameter", "for": "A" | "B"}]`, the
  craters left out of the planning map on purpose (as a 5 m orbital DEM would
  miss them), one per rover's first leg; `mission.unmapped_note` says so in
  words. Each rover's `sees_unmapped_crater` (per step) turns true when its
  lidar first marks that crater; the `divert-A` / `divert-B` checkpoint is
  the step before. Empty when the run had no sensors.
- `mission.survey_phases`, `mission.prospect_phases`: the instrument phase
  names in order (see `instrument` below).
- `satellite_sites` and `states` (`{"A": [{"t", "state", "objective"}]}`):
  the same shapes `sim/roles.py` writes, for the mission page.
- `lidar`: a sentence saying the lidar is simulated from the terrain, or null.

Per step:

- `mission_events`: beside `events` (which stays chain transactions only,
  `find` and `check`): `{"type": "site", "agency": "A", "site_id", "verdict": "dry" | "ice",
  "reading", "x", "y", "sigma", "on_chain": bool, "find_id"?, "tx"?}` when a
  survey ends, and `{"type": "dispatch", "agency": "B", "find_id", "site_id", "x", "y", "read_from": "chain"}`
  when the Prospector reads a find and sets off. A dry site is not posted on
  chain; its `site` event is the only record.
- `events` entries carry `site_id` too.

Per rover, per step (added to the run-log fields):

- `role`: `"Surveyor"` or `"Prospector"`.
- `phase`: one short line of what it is doing: `"to S1"`, `"survey S1"`,
  `"to S2"`, `"done"`, `"wait for a find"`, `"to find 17"`, `"prospect find 17"`.
- `site_id`: the site it is heading for or working at, else null.
- `plan`: the path it is about to drive, `[[x, y], ...]` world metres from
  the rover forward (the dashed line), on steps where it moved; `[]` otherwise.
  The planned route bends when the lidar marks a hazard on it.
- `instrument`: `{"name", "progress" (0..1), "step", "of"}` while a survey or
  prospecting sequence runs, else null. Names come from the phase lists above.
- `lidar_hazard_total`: hazard cells the lidar marked on this frame (0 off),
  beside the spine's `lidar_hazards` by kind and `lidar_frame`.

Honesty: `standins` lists what is still simulated (the ice sensor and the
instrument sequences). The chain block, the hashes and the finds are real
when the run was made with the agency keys; the page shows "fake tx" otherwise.

## Campaign simulator (docs/campaign-spec.md), shared by parts 1-4

Written first, by the lead, 2026-11-16. Change it only by editing this
section in the same commit and mailing every part.

### Settings

`sim/settings.py`: `Settings` (design choices plus fixed constants) and
`WorldRanges` (what a world seed draws). Every field carries its source in
`metadata["source"]`, or `assumed`. `python sim/settings.py` lists them and
must print `unlabelled: none`. A new constant is a new field there, never a
literal in another file. `Settings.drift_frac` is used by a single run; a
batch draws drift from `WorldRanges.drift_frac` per world.

`Settings.site` picks the world: a PSR site from `sim/psr_sites.py`
(`haynes`, `s89_014e`, `s89_134e`; default `s89_134e`) or `shoemaker`
(engine stand-in, not PSR floor). It is part of `Settings.key()`, so runs
on different sites never share a folder, and it is a summary.csv column.

A run is named `w<seed:05d>-<Settings.key()>`. Same seed and same key give
the same record, byte for byte apart from `made_at`.

### Run record (part 1 writes it; parts 2-4 read it)

One folder per run, `data/campaigns/<batch_id>/<run_id>/` (git-ignored):

- `record.json`:

```json
{
  "format": "campaign-record/1",
  "run_id": "w00042-6e3f2e33", "seed": 42, "made_at": "2026-11-16T15:00:00-08:00",
  "nav": "sampled",
  "settings": {"n_scouts": 3, "...": "every Settings field"},
  "world": {"crater_n_ge10_per_km2": 640.0, "drift_frac": 0.021,
            "dock_reg_err": [1.2, -0.4], "...": "every WorldRanges draw"},
  "terrain": {"site": "shoemaker", "file": "shoemaker.npz",
              "x0": 0.0, "y0": 0.0, "cell": 5.0, "bounds": [xmin, ymin, xmax, ymax]},
  "dock": {"x": 0.0, "y": 0.0},
  "scouts": ["S1", "S2", "S3"],
  "crater_map": {
    "start": [[x, y, d]],
    "added": [{"crater_id": "C7", "x": 0, "y": 0, "d": 8.0, "after_sortie": "S2-03",
               "bound95": 2.1, "sightings": 2, "true_crater": 12, "true_offset_m": 1.4,
               "misplaced": false}]
  },
  "sorties": [
    {"sortie_id": "S1-01", "scout": "S1", "n": 1, "t_start": 0.0, "t_end": 79200.0,
     "outcome": "ok", "faults": [],
     "time_s": {"drive": 0, "check": 0, "reading": 0, "drill": 0, "wait": 0},
     "energy_end_kwh": 2.1, "distance_m": 7012.0, "checks": 21, "wrong_matches": 0}
  ],
  "events": [
    {"t": 0.0, "sortie_id": "S1-01", "scout": "S1", "type": "check",
     "x": 0, "y": 0, "est": [0, 0], "bound95": 2.8, "wrong_match": false}
  ],
  "truth": {"label": "hidden truth",
            "ice": [{"x": 0, "y": 0, "radius": 6.0, "peak": 3.0}],
            "craters_missing": [[x, y, d]]},
  "summary": {"...": "the summary row below, for this run"}
}
```

- `poses.npz`: one float32 array per sortie, key = `sortie_id`, a row every
  `log_spacing_m` driven plus one at every event:
  `[t, true_x, true_y, true_heading, est_x, est_y, bound95, energy_kwh, activity]`.
  `activity` codes: 0 descent, 1 survey, 2 return, 3 check, 4 reading,
  5 drill, 6 search, 7 docked, 8 waiting at dock.
- Units and frames as "Frames and units" above. `t` is campaign seconds from
  the first launch. `bound95` is the 95th-percentile radial position bound in
  metres the Scout believes, not the true error. Truth is never mixed into a
  Scout field.

`outcome`: `ok` (home with the reserve), `below_reserve` (home, under it),
`fault_return` (turned back on a fault, home), `lost` (never home; the run
goes on with one Scout fewer).

Event `type`s and their extra fields:

| type | fields |
|---|---|
| `launch`, `dock_arrive` | `energy_kwh` |
| `check` | `est`, `bound95`, `wrong_match`, `craters_in_view` (detectable true rims in view), `matched` (map craters matched), `fixed` (false: under `fix_min_craters` matched, no fix), `reason` (`schedule` / `find` / `search` / `drill`), truth: `true_err_before_m`, `true_err_m`; `wrong_match_cause` (null unless `wrong_match`): `base` / `missing` / `misplaced:<crater_id or start-map row>` |
| `reading` | only above `find_threshold_wt`, else counted in `time_s`: `reading_wt`, `est`, `bound95`, `anchor` |
| `find` | `find_id`, `est`, `bound95`, `reading_wt`, `anchor` |
| `search_start`, `search_end` | `find_id`, `radius_m`; end adds `result`: `confirmed` / `unconfirmed`, `reason`: `match` / `no_readings` / `no_match` / `bound_too_large`, `truth_has_deposit`, `false_confirmed`, `missed` |
| `drill` | `find_id`, `est`, `true_err_m` (the Scout's position error), truth: `miss_m` (to the find's true reading point), `hit` (a deposit under the drill) |
| `sighting` | `est`, `d_est`, `bound95`, `anchor` (a rim seen but not matched) |
| `crater_added` | `crater_id` (at the dock, `sortie_id` = the sortie just ended) |
| `turn_back` | `reason`: `energy` / `fault` |
| `fault` | `module`: `camera` / `lamp` / `neutron` |
| `swap` | `module`, `downtime_h` |
| `contact_lost`, `contact_regained` | none; `sortie_id` and `scout` are null |
| `lost` | `energy_kwh` (0): battery empty before the dock |

Part 1 also writes (2026-11-16, additive):

- Top level: `world_ranges` (the ranges the world was drawn from; a single
  run uses a zero-width drift range at `Settings.drift_frac`), `corridor`
  (descent waypoints, map frame, from the dock's registered position),
  `energy` (the per-activity draws after scaling to `avg_load_w`),
  `standins` (parts still stand-ins), `finds` (every find as the dock
  holds it at the end: the find dict plus `history` [[sortie_id, status]]
  and `confirm_est` when confirmed).
- Sorties: `fixes`, `survey_m`, `map_craters` (dock map size after this
  sortie's merge), `turn_back` (`energy` / `fault` / null). Sorties are
  ordered by `t_start`.
- Sightings are made only at checks (crossing's rule), of rims in view
  with no map partner.
- `crater_map.start` and `truth.craters_missing` list only craters within
  `fix_range_m + 10` m of a driven pose (`crater_map.scope` says so), plus
  every crater an added one points at; `true_crater` indexes that cut
  list. The full lists are regenerated from the seed.
- `truth.ice_profile`: the field readings and `crossing.deposit_at` use
  (sim/ice.py's profile).
- `poses.npz`: after `dock_arrive`, rows with activity 8 (waiting) while
  the dock is busy and 7 (docked) from charge start to charge end, the last
  with a full battery.
- `terrain` (stand-in world): the 5 m Shoemaker window; `bounds` covers the
  dock and every pose.


### Summary row (part 1)

`data/campaigns/<batch_id>/summary.csv`, one row per run: `run_id`, `seed`,
every swept Settings field, every world draw, then
`sorties`, `sorties_ok`, `sorties_below_reserve`, `sorties_fault_return`,
`scouts_lost`, `drill_err_p95_m`, `checks_per_sortie`, `wrong_match_rate`,
`finds`, `confirmed`, `false_confirmed`, `deposits_missed`, `area_km2`,
`h_drive`, `h_check`, `h_reading`, `h_drill`, `h_wait`. By-sortie-number
series for Figure 1b go in `by_sortie.csv`: `run_id`, `sortie_n`,
`checks`, `wrong_matches`, `map_craters`.

As built (part 1, 2026-11-16): `deposits_missed` counts searches that came
back unconfirmed over a real deposit (`crossing.score_search` `missed`).
`checks_per_sortie` counts every check stop, fixed or not. Added columns:
`dock_reg_err_m` (length of the hidden draw), `fixes`, `drills`,
`drill_hit_rate`, `survey_err_p95_m` (95th-pct true error over survey and
search poses), `deposits_in_area`, `deposits_unfound` (deposits inside the
surveyed area that no find landed in), `added_misplaced`, `craters_added`,
`campaign_days`. `by_sortie.csv` sums over the fleet's n-th sorties and
adds `fixes` and `sorties`; `map_craters` is the largest dock map after
any of them. A batch also writes `drills.csv` (`run_id`, `sortie_id`,
`true_err_m`, `miss_m`, `hit`), `report.md` (hand checks, timings,
stand-ins), `batch.json`, `fig1a.png`, `fig1b.png`. Command:
`python -m sim.batch --batch-id <id> --worlds N --sweep name=v1,v2 ...`.

### Part 2 gives part 1 (`sim/crossing.py`, pure functions, no I/O)

`anchor` (on `reading`, `find`, `sighting` and the find dict): `"dock"` until
the Scout's first check of the sortie, then `"<sortie_id>#<check n>"`.
Positions with one anchor share one error (the dock registration, or one
fix), so they are never treated as independent (draft 4.4). A dock-anchored
`bound95` should include `Settings.dock_reg_bound95_m`, the bound the Scouts
believe; the world's drawn error stays hidden.

Scout and dock side; none takes truth:

- `search_area(find, searcher_bound95, s) -> radius_m`:
  `search_radius_k * hypot(both bound95) + footprint_radius_m`.
- `pick_searcher(find, scouts_ready) -> scout_id | None`: first id that is
  not the finder, only while `status == "find"`.
- `search_points(find, radius_m, s) -> (K, 2)`: reading points in the
  searcher's estimated frame, drive order.
- `judge_search(find, readings, s) -> "confirmed" | "unconfirmed"`, where
  `readings` are the searcher's `(est_x, est_y, bound95, reading_wt, anchor)`
  inside the area. `judge_search_detail` adds `reason`, `match` (index),
  `dist_m`, and the confirmed deposit's `est`, `bound95`.
- `merge_sightings(sightings, crater_map, s, next_index=None) -> list[added crater]`,
  at the dock after each sortie, on every sighting not yet used. `sightings`
  as the `sighting` events; `crater_map` `(M, 3)`, start plus added. Each
  added crater: `crater_id, x, y, d, bound95, sightings, members, scouts,
  sorties`; `members` index into `sightings` and leave the pool.

World side; takes truth, for part 1's world model and scoring only:

- `crater_view(true_xy, true_craters, map_craters, s)`: `in_view`,
  `missing` (make a sighting of each), `misplaced` (map rows), `share_bad`,
  the share used in `p_wrong`, `matched` (map rows), and `fix_bias` `[dx, dy]`,
  the mean map-minus-true offset of the matched craters. Add `fix_bias` to
  the Scout's error after every check, on top of the sampled error: a fix
  on an added crater inherits its error (draft 4.3). The Scout's believed
  `bound95` does not see it.
- `wrong_match_cause(view, s, rng)`, `make_sighting(true_crater,
  scout_true_xy, scout_err_xy, scout_bound95, anchor, rng, s)`.
- `deposit_at`, `score_search` (`truth_has_deposit`, `false_confirmed`,
  `missed`), `score_added(added, truth_craters_missing, s)` (`true_crater`,
  `true_offset_m`, `misplaced`), all three written into `crater_map.added`.
  `true_crater` indexes `truth.craters_missing`. `misplaced` (offset beyond
  `map_match_tol_m`) means that crater is still effectively missing.

A find is `{"find_id", "scout", "sortie_id", "est": [x, y], "bound95", "reading_wt", "anchor", "status"}`,
`status` one of `find`, `confirmed`, `unconfirmed`, `drilled`.

### Part 3 gives part 1 (`sim/terrain.py`)

- `campaign_world(site, seed, ranges: WorldRanges) -> CampaignWorld`: the
  fields of `sim/campaign.py` `World`, same names and units: `site`, `file`,
  `x0`, `y0`, `cell` (5 m, or 10 m for `nobile`), `height`, `slope` (LOLA
  slope product at the native cell),
  `floor` (PSR patches of at least `SiteBuild.psr_min_km2`), `craters` `(N, 3)`
  (sizes `WorldRanges.crater_d_m`), `map_start` `(M, 3)`, `dock` (sunlit rim
  ground), `draws` (every `WorldRanges` draw plus `dock_reg_err`, keys as
  `campaign.draw_world_values`), `ice` (on floor within
  `WorldRanges.ice_reach_m` driving of the dock), `standin=None`.
- Sites (`sim/psr_sites.py` `SITES`): `haynes`, `s89_014e`, `s89_134e`
  (87-90 S, 5 m mosaic) and `nobile` (84.5-87 S, 10 m 83 S product).
  Built windows live in `DATA/terrain/psr_sites/<site>.npz`
  (`python -m sim.psr_sites build`). `site="shoemaker"` raises: that tile is
  not PSR floor; `sim/campaign.py` `standin_world` still serves it.
- The site is not a `Settings` field, so a run id `w<seed>-<key>` does not
  name it. Runs on different sites need different batch ids until the site
  enters the run id.

### Part 4 reads (`web/`)

Records and `summary.csv` only. For the 3D replay it converts one sortie to
the "Run log" shape above (`steps`, `rovers` keyed by scout id), so
`web/js/world.js` replays it unchanged.
