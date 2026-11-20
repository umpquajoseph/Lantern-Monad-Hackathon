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
  on draft item 15. Ask the product owner whether the team has settled it.
- **Slope test source.** The footprint plane test cites GESTALT (Goldberg,
  Maimone and Matthies 2002) from a search summary only: UNVERIFIED.
  Downloading the paper (robotics.jpl.nasa.gov, aero.pdf) into
  `docs/sources/` needs the product owner's OK.
- **The haynes return ramp.** In a 40 m box around (15062, -30199), the
  run ground's footprint tilt has median 13.6°, with 20% over the 15°
  limit. `footprint-check` passed it and `tilt-check` stuck there.
  Find out whether that steepness is LOLA's or comes from upsampling plus
  roughness (`sim/site_ground.py`). Report it; do not change the limit or
  the ground to get Scouts home.

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
