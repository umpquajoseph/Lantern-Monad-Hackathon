# Lantern handoff

20 Nov 2026, 10:40 PST. This is the only current handoff; it replaces the
one of 17 Nov (in git history at `e913081`). Read it first, then
`docs/campaign-spec.md`, then `ops/PROTOCOL.md`. No workers are running.

## What Lantern is for

The product owner's words: "my vision for lantern was that its going to
simulate the rover workflow the paper proposes, and provide some
information on the uncertainty camera vision must have in order for the
rovers to make it back home." Lantern is the novel thing and the main
tool. A large Monte Carlo pool on ShadowNav alone is an optional
afterthought.

Lantern's numbers are usable in the paper only if Lantern, on the
approved terrain, reproduces ShadowNav's published accuracy. That
comparison is the gate for everything below.

A change is right when it makes the sim more faithful to the paper or the
Moon. It is wrong when its purpose is a better outcome.

## Where it stands

- Everything is on one branch, `lantern-workflow`, in
  `C:\Monad-Hackathon-Project`. Nothing goes to `main` without the
  product owner's word.
- The team's area 3 draft is in the repo:
  `docs/sources/area3-navigation-draft.pdf`, text copy beside it. Check
  every claim against it and the other sources in `docs/sources/`, never
  against a summary, this file included.
- **The product owner wants single test sorties only for now, no
  batches**: `python -m sim.live_runs --sites haynes --scouts 1 --sorties 1
  --batch-id <name> --serial --progress 500` (about 10 min).
- Every batch made before 18 Nov predates the three changes below and is
  superseded for numbers.

Changes of 18-20 Nov, each checked by running it and looking:

| Change | Commit | Where it is written down |
|---|---|---|
| Camera texture: ShadowNav's render recipe (Hapke, fractal texture continuing the terrain's roughness spectrum), `cam_display` "detail" | `ae2c128` | `docs/terrain/position.md` row 12, tentatively approved 20 Nov |
| Slope test: tilt of the plane under the Scout's 1.55 m footprint, planner and lidar alike | `95b48f6` | position.md row 9, approved 18 Nov |
| Camera, lamp and lidar tilt with the body on that plane; readings levelled with the known tilt | `e913081` | `docs/handoffs.md` rover frame |

Test sorties on haynes w00003 (1 Scout, 1 sortie), in `data/campaigns/`:
`texture-check`, `footprint-check` (came home), `tilt-check` (lost: stuck
on the return ramp near the dock until the battery ran flat). Mission
Control plays them at
`http://127.0.0.1:8070/?batch=tilt-check&run=w00003-5d9ed3d8&sortie=S1-01`.

## The next four steps, in order

### 1. Camera texture: done, tentatively approved

Approved 20 Nov in chat, to stand unless step 2 shows a problem with it.
Nothing to do. If the comparison does show a problem, report it before
changing the texture.

### 2. Rerun the ShadowNav comparison on the approved terrain

`sim/calibrate.py` runs Lantern's camera navigation on three
ShadowNav-style traverses and sets the result beside Cauligi et al. 2023
Table 2. Read the table from `docs/sources/`, not from memory. The last
result is `data/campaigns/calibration/realism-eq336-nd/table.md` (16 Nov),
made before the changes above.

What has to be settled before the rerun counts:

- **Which ground.** calibrate builds its own 0.25 m grid with
  `sim/terrain.py` (s89_134e, or the `_eq336` variant) and renders with
  `--display full` by default. The campaign runs use `sim/site_ground.py`
  run grounds (1 m, 0.035 m roughness) rendered "detail". The comparison
  must use the approved terrain recipe of `docs/terrain/position.md`. Find
  out whether the two differ and say so before running.
- **Body tilt.** calibrate keeps a level body (`scene.body_tilt = False`)
  because its worker processes place rims with a level camera model.
  Check in C23 and A24 whether ShadowNav's simulated rover tilted with the
  ground. Then either keep it level, with that source, or pass each
  frame's attitude to the workers (`Scene.rover_axes`, `Vision.set_pose`).
- Nothing in calibrate may be tuned toward C23's numbers.

**Done 20 Nov, 10:56.** Both questions settled in `sim/calibrate.py`:

- Ground: the two differed (0.25 m grid, roughness 0.070 m, craters
  2.3-20 m, display "full" with rim lips, rocks and mottling). calibrate
  now builds a run ground with `site_ground.build_ground` over the same
  1 km of s89_134e (`--ground run`, the default), renders it "detail"
  with its own camera texture. `--ground grid` keeps the old one.
- Tilt: neither paper says whether its rendered camera tilted. Both give
  the rover its 3D orientation (C23 sec. 3, star tracker; A24 sec. V-C,
  "For simulation, the ground truth orientation is used"). So the body
  tilts as in live runs and each frame's attitude is saved and used by
  the workers. `--level` is the test case.
- The table's headline row now reads `filter-route-nd`, the config that
  detects as live runs do. It looked for `after` and printed empty.

Result, `data/campaigns/calibration/shadownav-run-ground/table.md`,
100 seeds: **it does not match.**

| Traverse | Lantern error | C23 error | Lantern unc. | C23 unc. | odometry only |
|---|---|---|---|---|---|
| T1 gap | 5.14 ± 4.96 | 3.84 ± 2.78 | 3.65 | 1.84 | 13.68 |
| T2 straight | 10.01 ± 7.42 | 1.75 ± 0.78 | 4.25 | 1.32 | 12.63 |
| T3 half survey | 3.18 ± 1.91 | 1.68 ± 0.70 | 1.44 | 1.39 | 6.30 |

What differs from C23's setup, each a candidate for step 3:

- Spurious rim points, 11-27% after the range cut. The frames show
  them on terrain crests: where LOLA relief curves away 0.5-1 m over
  25 m, the lamp-lit ground ends in a sharp line the detector reads as a
  leading edge. Physical, not a render bug. Looked at in renders of T2
  frames 127-131.
- Matching. A24 sec. III-E2 scores rims only against the front half of
  each landmark crater, chosen by heading, and runs the filter only
  within 20 m of a landmark. Lantern scores against the full circle of
  the 6 nearest map craters, always. C23 sec. 6B notes the filter "had
  difficulty reconciling the front edge of the rim with the back edge".
- The truth's odometer drifts 1-4% (`WorldRanges.drift_frac`); C23 used
  2%. Lantern steps 2 m; C23 imaged every 1 m.
- T2's route has a 186 m blind stretch and sees 5 mapped craters. C23's
  traverses each held two landmark craters they drove to.

The filter itself matches C23 Algs. 1-3 and A24 eq. 3 line by line.

**Rerun 20 Nov, 11:20, after two changes approved in chat:** the filter
matches rims only to the near half of each mapped crater and skips the
update when its estimate is over 20 m from every mapped rim (A24 sec.
III-E2; `Settings.pf_match`, `pf_gate_m`; old matching kept as test case
`filter-route-nd-full`). In calibrate only, the true odometer drifts as
A24 eqs. 4-6: 2% in one random direction, heading known (`--truth a24`).

| Traverse | Lantern error | old matching | odometry only | C23 error |
|---|---|---|---|---|
| T1 gap | 6.33 ± 4.31 | 6.41 ± 2.99 | 10.92 | 3.84 ± 2.78 |
| T2 straight | 5.84 ± 4.90 | 9.98 ± 5.02 | 9.44 | 1.75 ± 0.78 |
| T3 half survey | 3.85 ± 2.34 | 3.96 ± 2.42 | 6.56 | 1.68 ± 0.70 |

Still no match. The filter updates on only 7-24 frames per traverse;
the routes see a mapped crater on 11-46% of steps. C23's traverses were
built around two landmark craters each. Eq. (6)'s covariance is read as a
standard deviation (assumed; as printed its units do not agree).

### 3. If it does not match: audit Lantern

Known problems, each seen in a run:

- **Overconfidence after one wrong match.** In `tilt-check`, at t = 6,732 s,
  a wrong match took the filter's 95% bound to 0.77 m while the true
  error was 5.97 m. From then on, correct rims no longer paired with their
  map crater within `cam_match_tol_m`, and drift grew to 18 m on the
  long leg east. `texture-check` showed the same: error 5.2 m, bound
  0.8 m, and 226 of 240 points wrong at 2.16 h. Every lost or drifting run
  looked at so far traces back to this. Check the filter against A24
  eqs. 3-6 and C23 Alg. 1 before changing anything.
- **False lidar craters on plain slopes.** On a synthetic 10° by 6° ramp
  the lidar flags 36-44 "crater" cells about 2.2 m ahead, with or without
  body tilt. Likely the ring-mean edge bias in `sim/lidar.py hazards`.
- **One unexplained lidar sample.** At step 450 of `tilt-check`, only 1 of
  the tilted lidar's 8 slope cells was truly over 15°, against 11 of 15
  for a level lidar. One sample; check more before calling it a bug. The
  scratch check that found it rendered 12 poses of that run with tilt on
  and off and compared slope cells with `sim/footprint.tilt_deg` on the
  ground.

### 4. Settle the open assumptions

- **Dock registration.** `dock_reg_bound95_m` = 6.0 m, assumed
  (`sim/settings.py`). It sets how uncertain every sortie starts. It waits
  on draft item 15. Not settled (asked 20 Nov).
- **Slope test source.** Done 20 Nov (`a06d44b`): GESTALT read, sec. 4
  step 3, p.6, a best-fit plane over a rover-sized patch. Verified.
- **The haynes return ramp.** In a 40 m box around (15062, -30199), the
  run ground's footprint tilt has median 13.6°, with 20% over the 15°
  limit. `footprint-check` passed it and `tilt-check` stuck there.
  Find out whether that steepness is LOLA's or comes from upsampling plus
  roughness (`sim/site_ground.py`). Report it; do not change the limit or
  the ground to get Scouts home.
  **Answered 20 Nov: it is LOLA's.** LOLA's own 5 m slope in the box has
  median 13.7°, 27% of pixels over 15°; 10 of its 64 pixels hold a real
  LOLA return. By layer, footprint tilt over 15°: LOLA smoothed and
  upsampled 13.5%, plus craters 13.5% (none on the ramp), plus roughness
  20.3%. Medians stay 13.6° throughout. Smoothing lowers the steep share,
  roughness raises it back part way.

## After the four steps (proposed in the 17 Nov handoff, not started)

Planned position checks (`check_policy` "fixed", draft Table 1 spacing,
5 min stops); sourcing the ice sensor's false-alarm rate and map crater
error in shadow; the run count with the product owner; then the paper
batch on random seeds, Figure 1 and the fleet and site tables. Due
15 Jan 2027. Site and video 10 Feb, proposal 24 Feb.

## Decided

- Live camera navigation in every run.
- Terrain as `docs/terrain/position.md`: roughness 0.035 m rms (test
  0.021, 0.048); crater density 106-1,540 per km² (336 central); map cut
  10 m (test 18 m); footprint slope test (test cases "widened", "point");
  ShadowNav camera texture; no rocks, stated.
- Confirm rule stays 6 m, marked assumed.
- Filter scale walk 0, as in A24 eqs. 3-6.
- Lost reasons include `stranded` and `stuck`. No free hop home.
- Beacons only as comms relays, not homing.
- Mission Control never opens a test case by default.
- Hosting: local only until the product owner says it is right; never
  republish lantern-teal.vercel.app.

## Workers

None running. A new job gets a new worker with a fresh brief in
`docs/briefs/`, branched from `lantern-workflow`, run through the
mailboxes (`ops/PROTOCOL.md`). Retired workers' briefs are in
`_archive/briefs/`, their mailboxes in `ops/mailboxes/_old/`.
`ops/_old/` is untracked and not this project's work; leave it.

## Running Mission Control

From `C:\Monad-Hackathon-Project`: `python app.py --no-open --port 8070`,
then open http://127.0.0.1:8070/ (it listens on IPv4 only, so
`localhost` may fail). For a new camera run:
`python web/build_assets.py --run-ground <batch>/<run>`, then
`python -c "import app; app.sync_runs(None)"`.
