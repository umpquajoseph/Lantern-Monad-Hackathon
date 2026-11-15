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
- The first-person camera and lamp follow ShadowNav: camera 2.5 m above the
  ground, lamp 0.2 m below it, 60° horizontal field of view (Atha et al. 2024,
  sections IV and V).

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
- Top level `frames`: one sentence each for `fp_frame` and `fp_sub` saying
  how they were made (sensor frame vs interpolated display frame).
- Also written by `run_demo.py` (additive): top level `standins`, `dt_s`,
  `final_finds`, `truth.ice_units`, `truth.find_threshold_wt`; per rover
  `mode`, `ice_reading`, `est_error`, `distance`. `index.json` in
  `data/runs/` lists the runs.
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

## Step 3, navigation, gives each step

- `estimate`: `(x, y)` in metres, world frame.
- `sigma`: metres, as defined above. Logged next to the true error.

## Step 1, chain client (`sim/chain.py`), gives

```python
post_find(agency, x, y, sigma, reading, data_hash) -> find_id
check_find(agency, find_id, x, y, sigma, reading, agrees) -> tx_hash
get_finds() -> list[dict]   # each: id, agency, x, y, sigma, reading,
                            # agree (agencies), disagree (agencies), status
```

- `agency` is `"A"` or `"B"`; the client maps it to the key in `.env`.
- On chain, positions and sigmas are whole centimetres, `data_hash` is
  `bytes32`, and status is reported, confirmed or disputed.
- Check rule: refused if the checker's agency is the finder's, or if
  `dx² + dy² > (3 × (sigma_find + sigma_check))²`, all in centimetres.

## Step 5, dashboard, takes

- The per-step state above for both rovers, `get_finds()`, and from step 6 the
  camera image `np.ndarray (h, w)` or `(h, w, 3)` with `rims` to overlay.
