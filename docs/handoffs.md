# Interfaces between parts

Change a format here only by telling the other workers and editing this
file in the same commit.

## Frames and units

- World frame: metres, x east, y north. On a LOLA tile, south polar
  stereographic metres, the frame the LOLA files use.
- Heading: radians, counterclockwise from +x.
- Rover frame: metres, level (z up), x along the heading, y left; x, y from
  the pose, z from the camera. The body tilts with the ground under its
  footprint (`scene.body_frame`); camera and lidar readings are levelled
  with that known tilt.
- `sigma`: the filter's 1-sigma radial spread in metres,
  `sqrt(trace(cov) / 2)` of the particle cloud. `bound95` is `K95_2D * sigma`
  (sim/nav.py `belief`): the bound the Scout believes, not its true error.

## Terrain (`sim/terrain.py`)

- `load_terrain(path) -> (height, craters, x0, y0, cell)`. `height`: float32
  (H, W) metres; cell `[i, j]` is at `x = x0 + j * cell`, `y = y0 + i * cell`,
  so rows run north (flip a GeoTIFF). `craters`: (N, 3) rows `[x, y, d]`.
- Built files live in `DATA/terrain/` (sim/terrain.py `DATA`), git-ignored.
- `campaign_world(site, seed, ranges) -> CampaignWorld`, the fields of
  sim/campaign.py `World`: `site`, `file`, `x0`, `y0`, `cell`, `height`,
  `slope` (LOLA slope product), `floor` (PSR patches of at least
  `SiteBuild.psr_min_km2`), `craters` (N, 3), `map_start` (M, 3, from
  sim/orbital_map.py with orbital error), `map_index` (M,) (the `craters`
  row each map row stands for; match by index, never by position), `dock`,
  `draws`, `ice`, `standin=None`. `clear_dock` drops every crater whose rim
  comes within `WorldRanges.dock_clear_m` of the dock: a lander is set down
  on clear ground.
- Sites (sim/psr_sites.py `SITES`): `haynes`, `s89_014e`, `s89_134e` (87-90 S,
  5 m mosaic) and `nobile` (10 m product). Windows in
  `DATA/terrain/psr_sites/<site>.npz` (`python -m sim.psr_sites build`).
  `shoemaker` is not PSR floor; sim/campaign.py `standin_world` serves it.

## Terrain display (`sim/terrain_display.py`)

Camera runs render with a display layer built per run ground,
`DATA/terrain/run_ground/<run>_display/` (`build_for_ground`): texture only,
no rocks, lips or mottling (docs/terrain/position.md row 12).
`web/build_assets.py --run-ground` draws Mission Control's ground with the
same layer. `display.json` lists files, parameters and sources. Real: the
LOLA backdrops. Everything else is made up and marked so. Shading is Hapke,
Xu et al. 2020 Eqs. (5)-(12). Arrays run rows north; PNG row 0 is the south
edge.

## Scene (`sim/scene.py`)

```python
scene = Scene(height, craters, x0, y0, cell)
scene.set_rover(agency, true_pose)            # (x, y, heading)
img, depth = scene.first_person(agency)       # img (h, w, 3) uint8; depth (h, w) float32,
                                              # range along each ray in metres, NaN for sky
```

`scene.camera_model()` gives intrinsics and pitch, `scene.rover_axes(pose)`
the camera axes for a pose, `scene.depth_to_rover(depth, axes)` rover-frame
points `[x_fwd, y_left, z_up]`. Camera at `Settings.camera_height_m`, lamp
`Settings.lamp_below_camera_m` below it, 60 deg horizontal view.

## Navigation parts

- `sim/orbital_map.py orbital_map(craters, rng) -> (map (M, 3), mask (N,), share)`:
  the one starting-map cut. Each mapped crater carries an orbital pixel's
  error (`orbital_pos_err_px`, `orbital_diam_err_px`). Map row i is truth
  crater `np.flatnonzero(mask)[i]`.
- `ParticleNav(fixes_on, rng, craters=None, noise=None)`: `noise` is a
  `nav.PFNoise` (default `Settings.pf_*`). `covariance() -> (mean, cov)`,
  `sigma_max()` (C23 sec. 6B).
- `Vision(scene, rng, depth_model="stereo")`: `rims(img, depth)`;
  `detect(img, depth)` rim pixels after A24 sec. III-D's contour filter;
  `rims_detail` adds noiseless `true` placements (scoring only).
  `vis.noisy_depth(depth)` draws one stereo range image per frame, which
  every depth reader of that frame takes as `nd=`.
- Calibration: `python -m sim.calibrate`, output in
  `campaigns/calibration/<batch>/` (`table.md`, `calibration.json`).

## Campaign simulator

### Settings

`sim/settings.py`: `Settings` (design choices and constants) and
`WorldRanges` (what a world seed draws). Every field carries its source in
`metadata["source"]`, or `assumed`. `python sim/settings.py` lists them and
must print `unlabelled: none`. A new constant is a new field there, never a
literal elsewhere.

`Settings.site` picks the world. A run is named
`w<seed:05d>-<Settings.key()>`; the key includes the site. Same seed and key
give the same record apart from `made_at`.

### Run record

One folder per run, `data/campaigns/<batch_id>/<run_id>/` (git-ignored).

`record.json`:

```json
{
  "format": "campaign-record/1",
  "run_id": "w00042-6e3f2e33", "seed": 42, "made_at": "2026-11-16T15:00:00-08:00",
  "nav": "camera",
  "settings": {"n_scouts": 3, "...": "every Settings field"},
  "world": {"crater_n_ge10_per_km2": 640.0, "drift_frac": 0.021,
            "dock_reg_err": [1.2, -0.4], "...": "every WorldRanges draw"},
  "terrain": {"site": "haynes", "file": "...", "x0": 0.0, "y0": 0.0, "cell": 5.0,
              "bounds": [xmin, ymin, xmax, ymax]},
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
  "events": [{"t": 0.0, "sortie_id": "S1-01", "scout": "S1", "type": "check", "...": "below"}],
  "truth": {"label": "hidden truth",
            "ice": [{"x": 0, "y": 0, "radius": 6.0, "peak": 3.0}],
            "craters_missing": [[x, y, d]]},
  "summary": {"...": "the summary row, for this run"}
}
```

Also top level: `world_ranges`, `corridor` (descent waypoints, map frame),
`energy`, `standins`, `finds` (each find as the dock holds it at the end,
with `history` and `confirm_est`), `pipeline` (each part's `NAME`),
`timing.frames` (`rendered`, `skipped_no_crater`, `skipped_camera_off`), and
`ground`: the fine ground the camera saw,
`{"file": "run_ground/<run_id>.npz", "x0", "y0", "cell", "bounds", "display"}`,
relative to `DATA/terrain/`. A viewer that wants that ground loads this file.
Runs from sim/live_runs.py add `seed_choice` and `paper_statistics: false`:
their seeds were picked for the site and video, so the paper must not use
them.

`poses.npz`: one float32 array per sortie, key `sortie_id`, a row every
`log_spacing_m` driven and at every event:
`[t, true_x, true_y, true_heading, est_x, est_y, bound95, energy_kwh, activity]`.
Activity: 0 descent, 1 survey, 2 return, 3 check, 4 reading, 5 drill,
6 search, 7 docked, 8 waiting at dock. `t` is seconds from the first launch.
Truth is never mixed into a Scout field.

Sortie fields beyond the example: `fixes`, `survey_m`, `map_craters`,
`turn_back`, `survey_end` (`lanes`, `budget`, `turned_back` or `lost`;
`survey_done` is true for the first two), `steps`, `point_turns`,
`waypoints_skipped`, `pingpong_blocks`, `reversals`, `retraces`,
`retrace_steps`, `drive_overhead`, `plan_slope`, `rim_points`, `rim_wrong`,
`rim_unpaired`, `rim_matched`, and the steep-step counts: steps onto ground
truly over the pitch or roll limit for the heading driven
(sim/rover.py `_note_true_steep`), `steep_unflagged` with
`steep_unflagged_by` (sim/lidar.py `HazardMemory.why_missed`: `never_in_band`,
`shadow`, `too_sparse`, `under_read`, `judged`), and `steep_flagged_by`
(`orbital`, `lidar_steep`, `lidar_crater`).

`outcome`: `ok` (home with the reserve), `below_reserve`, `fault_return`,
`lost` (never home; the run goes on with one Scout fewer).

Events:

| type | fields |
|---|---|
| `launch`, `dock_arrive` | `energy_kwh` |
| `check` | one position fix: `est`, `bound95`, `wrong_match`, `wrong_match_cause` (`base` / `missing` / `misplaced:<id>`), `craters_in_view`, `matched`, `fixed`, `reason`, `mode`, `d_since_fix_m`, `due_m`, `points`, `wrong_points`; truth `true_err_before_m`, `true_err_m`. Scheduled and preplanned fixes add `landmark` `[x, y, d]`, `landmark_row`, `stops`, `stops_planned`, `frames_used`, `stop_s`, `detour_m`, `fix_h`; preplanned adds `late`, `detour_planned_m`, `depart_at_m`, `due_at_m`. In `opportunistic` a check is one crater pass and adds `steps` |
| `fix_unavailable` | a fix was due and no landmark was in reach: `est`, `bound95`, `reason` (`no_landmark`, `no_landmark_on_leg`), `reach_m`, `d_since_fix_m`, truth `true_err_m` |
| `reading` | only above `find_threshold_wt`: `reading_wt`, `est`, `bound95`, `anchor` |
| `find` | `find_id`, `est`, `bound95`, `reading_wt`, `anchor` |
| `search_start`, `search_end` | `find_id`, `radius_m`; end adds `result` (`confirmed` / `unconfirmed`), `reason` (`match` / `no_readings` / `no_match` / `bound_too_large`), `truth_has_deposit`, `false_confirmed`, `missed` |
| `search_abandoned` | `find_id`, `reason` (`unreached` / `energy` / `time` / `fault` / `lost`), `dist_m`, `radius_m`, `points_done`, `points_planned`, `partial`, `combined_bound95`. The find stays `find`. Old records call the unreached case `search_unreached` |
| `drill` | `find_id`, `est`, `true_err_m`; truth `miss_m`, `hit` |
| `sighting` | an unmatched rim arc fitted with a circle: `est`, `d_est`, `bound95`, `anchor`, `points`, `fit_rms_m` |
| `crater_added` | `crater_id` (at the dock) |
| `waypoint_skipped` | `reason` (`blocked`, `off_ground`, `hazard`, `passed_by_fix`), `goal` |
| `turn_back` | `reason`: `energy` / `fault` / `time` |
| `fault`, `swap` | `module` (`camera` / `lamp` / `neutron`); swap adds `downtime_h` |
| `contact_lost`, `contact_regained` | none |
| `lost` | `reason`: `battery`, `stranded` (return driven, dock farther than `bound95` + `DOCK_SIGHT_M`) or `stuck` (battery flat within `STUCK_RADIUS_M` of one spot for `STUCK_MIN_H`, adds `stuck_h`); `energy_kwh`, `dist_m` |

`cam_fix_mode` (sim/campaign_camera.py): `preplanned` (default) picks each
fix's landmark when its leg is planned and drives a half survey of it;
`straight` fixes on landmark-sized craters as they come into view within
`fix_view_m`; `scheduled` and `opportunistic` (the filter corrects on every
frame with rims) are test cases. Only `opportunistic` updates the filter
outside a fix.

**False-match rate** (one definition for the summary, the page and the
paper; draft 4.2): of the rim points the filter paired with a mapped
crater, the share whose paired crater is not the rim's true crater.
Pairing is the filter's own (sim/nav.py Alg. 1 line 4, nearest edge at the
mean pose before the update). The true crater is the one whose edge lies
within `cam_match_tol_m` of the point placed at the true pose. Unpaired
points are left out and reported as `unpaired_share`. `wrong_match_rate` is
the per-check rate, not this.

### `steps.npz` (camera runs)

Every Scout-step, per sortie. Keys `<sortie_id>.<name>`; ragged lists use an
offset array `o` of length K+1 (step k owns rows `o[k]:o[k+1]`).

| key | dtype, shape | meaning |
|---|---|---|
| `<sid>.step` | float32 (K, 12) | `t, true_x, true_y, true_heading, est_x, est_y, est_heading, bound95, energy_kwh, activity, goal_x, goal_y` (goal in map frame) |
| `<sid>.particles` | float16 (K, 100, 3) | `dx, dy` from `est`, and weight |
| `<sid>.rim_o`, `<sid>.rims` | int32 (K+1), float16 (R, 2) | rim points `[x_forward, y_left]`, rover frame |
| `<sid>.rim_map`, `<sid>.rim_map_m` | int32 (R), float16 (R) | paired map crater (row of `map_craters`) and distance to its edge |
| `<sid>.rim_true` | int32 (R) | true crater (row of `true_craters`), -1 none |
| `<sid>.rim_wrong` | uint8 (R) | 1: a false match |
| `<sid>.lidar` | uint16 (K, 20, 121) | range image, every other beam of sim/lidar.py's 40 x 241 scan, cm, 0 = no return |
| `<sid>.haz_o`, `<sid>.haz`, `<sid>.haz_kind` | int32, float16 (H, 2), uint8 | lidar hazard cells, kind 0 crater, 1 slope, 2 rock |
| `<sid>.plan_o`, `<sid>.plan` | int32, float32 (P, 2) | the route about to be driven, true frame, about 20 m |
| `<sid>.map_o`, `<sid>.map_ij`, `<sid>.map_v` | int32, int32 (C, 2), uint8 | lidar map cells whose verdict changed at each step (0 none, 1 crater, 2 rock, 3 slope); replay from empty |
| `<sid>.steep`, `<sid>.steep_why` | float32 (S, 5), uint8 | `[step, x, y, true deg, orbital deg]`; why 0-4 unflagged (as above), 5 orbital, 6 lidar_steep, 7 lidar_crater |
| `map_craters`, `true_craters` | float32 (M, 3), (N, 3) | every crater a rim paired with or lay on |
| `lidar_el_deg`, `lidar_az_deg`, `map_cell_m` | float32 | beam angles; map cell side (0.5 m) |

### Summary row

`data/campaigns/<batch_id>/summary.csv`, one row per run: `run_id`, `seed`,
every swept Settings field, every world draw, then `sorties`, `sorties_ok`,
`sorties_below_reserve`, `sorties_fault_return`, `scouts_lost`,
`drill_err_p95_m`, `checks_per_sortie`, `wrong_match_rate`, `finds`,
`confirmed`, `false_confirmed`, `deposits_missed`, `area_km2`, `h_drive`,
`h_check`, `h_reading`, `h_drill`, `h_wait`, `dock_reg_err_m`, `fixes`,
`drills`, `drill_hit_rate`, `survey_err_p95_m`, `deposits_in_area`,
`deposits_unfound`, `added_misplaced`, `craters_added`, `campaign_days`,
`nav`, `steps`, `s_per_step`, `rim_points`, `false_match_rate`,
`unpaired_share`, `waypoints_skipped`, `searches_abandoned`.
`by_sortie.csv`: `run_id`, `sortie_n`, `checks`, `wrong_matches`,
`map_craters`, `fixes`, `sorties`. `python -m sim.batch` also writes
`drills.csv`, `report.md`, `batch.json`, `fig1a.png`, `fig1b.png`.

### Cross-checks (`sim/crossing.py`, pure functions)

`anchor`: `"dock"` until the Scout's first fix of the sortie, then
`"<sortie_id>#<check n>"`. Positions with one anchor share one error, so
they are never treated as independent (draft 4.4).

Scout side, no truth: `search_area`, `pick_searcher`, `search_points`,
`judge_search` / `judge_search_detail`, `merge_sightings`.
World side, truth, for scoring only: `crater_view`, `wrong_match_cause`,
`make_sighting`, `deposit_at`, `score_search`, `score_added`.

A find is `{"find_id", "scout", "sortie_id", "est": [x, y], "bound95",
"reading_wt", "anchor", "status"}`, `status` one of `find`, `confirmed`,
`unconfirmed`, `drilled`.

### Mission Control (`web/`)

Reads records, `summary.csv` and `steps.npz` only, through
`web/collect_campaigns.py` and `web/serve.py` (which turns `poses.npz` into
`poses.bin` plus `poses.json`). The 3D replay converts one sortie to the run
shape `web/js/data.js` `Run` reads (`steps`, `rovers` keyed by Scout id).
