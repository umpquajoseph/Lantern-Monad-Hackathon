# Request: vision (step 6), bring the branch up to date, from the lead session

For Calvin's agent. Written 2026-11-15 12:57 PST against `origin/integration`
at `623fbd1`. Please reply under "Answer" at the end, commit it to your
branch, and push.

## Why

Joseph says your vision work is running slowly. `origin/Calvin` (head
`b84e2e2`, 12:01) is **45 commits behind `integration`**. The site at
`python app.py` runs `integration`. Among the commits you lack:

- `sim/scene.py`, about 300 lines changed. Rim shadows come from a 5 cm near
  surface (47c99c1, 61af034). The lamp pool is 15/32 deg inside the view,
  with a new tone curve (2c8af7e). Frames you render on the old `scene.py`
  will not match what the site shows or what the answer key expects.
- `run_demo.py`: sensor frames render live in the sim loop, every 2 m step.
  Display-only frames (`fp_sub`, every 0.5 m) render after the sim, in a
  process pool (980e248). Particles are logged (bc9f6ea). The planner is
  logged as a real part (2c8af7e).
- The answer key for scoring vision: `sim/rim_truth.py` and
  `docs/requests/rim-answer-key.md` (branch `step-6-rim-truth`, merged into
  `integration` at f13b9ef).

## What to do

1. Merge `origin/integration` into your branch. `run_demo.py` conflicts.
   `integration` already contains your three ParticleNav edits (abb65cc),
   so keep `integration`'s copy and re-add only what vision needs.
2. For vision runs, skip what vision does not read:
   - `--sub-frame-m 0`: no display frames. They are about three in four of
     all frames, and vision never reads them.
   - `--only run1`: one run while you iterate.
   - `--display detail`: the terrain worker measured that the invented rim
     lips cause 29-37% of the rim shadow in `full` frames. Vision should not
     learn from geometry we made up. This is the lead's recommendation;
     Joseph has not decided yet.
3. Push your vision work-in-progress, even unfinished, so the lead can see
   what you have and where the time goes.

A performance worker (`perf`, branch `step-6-perf`) is profiling the
render and the rerun now. Its speed-ups land in `integration`. Merge them
when they arrive rather than tuning `scene.py` on your branch, so we do not
end up with two versions.

## Interface reminder (`docs/handoffs.md`)

Vision gives `rims (M, 2)` as `[x_forward, y_left]` metres in the rover frame,
from `scene.first_person(agency)` -> `(img, depth)`. It replaces
`rims_standin` behind the same call. Score it with `rim_truth.score`.

## Answer

Where the time goes in your vision run (per frame: render vs detection),
and the commit you pushed:

## Update 13:09, from the lead

Thanks for merging integration and pushing. Your WIP (a6ed160..c84f4e1)
makes camera rims the default and does not pass run 1 yet, so it is held
out of the team's `checkpoint` branch for now. The demo must run without
vision (`CLAUDE.md`). Once `checkpoint` is pushed:

1. Merge `origin/checkpoint` into your branch. It conflicts with
   `web/js/main.js`, where the viewer and designer reworked the page; keep
   their layout and add your lidar panel to it.
2. Keep stand-in rims as the default until run 1 passes end to end with
   camera rims, then flip it. Say in your commit that it passes, with the
   final error and sigma for both rovers.
3. The `merger` or `integrator` worker brings it in from there.

The `sim/rover.py` hazard blocking changes the planner's routes for
everyone, so put its before/after for run 1 in the same commit message.

**Answered on `Calvin`, 13:20 PST.**

Commits: `a6ed160` (vision and lidar WIP), `1ca566e` (integration's viewer
design merged, lidar panel kept), and the commit that carries this answer.

**Default unchanged.** Vision and lidar are now opt-in: `run_demo.py --vision`.
Without it, both runs are identical, step for step, to `integration`'s
(checked on all four rover tracks, `--no-render`). The new rover behaviour
(sensor hazards, back-and-forth guard, turning in place) only acts when a
rover drives on its sensors.

**Where the time goes**, run 1 with `--vision --only run1 --sub-frame-m 0
--display detail`, my sandbox (CPU only, no GPU), medians per live frame:

| part | ms |
|---|---|
| render (`scene.first_person`) | 372 |
| vision rims (`sim/vision.py`) | 12 |
| lidar scan + hazards (`sim/lidar.py`) | 53 |

So the render is about 85% of the time; detection adds about 65 ms. The
whole run 1 took 297 s for 455 live frames, both passes. Pass 1 now renders
too (without writing files), so A drives the same path in both passes.

**Run 1 with `--vision`: passes.** Confirmed (B read 4.81 wt%). A's find
reported 1.2 m from where A was (sigma 0.7 m).

| rover | final error | final sigma | driven |
|---|---|---|---|
| A | 1.18 m | 0.75 m | 268 m |
| B | 0.41 m | 0.72 m | 638 m |

Before/after for the planner change, run 1 (without vision vs with it): B
drove 280 m vs 638 m; track inside craters deeper than 0.35 m: A 4 m in 3
craters vs 8 m in 1, B 38 m in 3 craters vs 4 m in 1. B's longer drive is
the lidar keeping it out of craters; it is the open item below.

**Vision against your answer key** (`sim/rim_truth.score`, every 10th live
frame, 45 frames, `--display detail`): precision 0.72, recall 0.15,
recall_shadow 0.42, mean distance 1.22 m, gauss 0.60.

**What is in it**

- `sim/vision.py`: Canny on the lamp's rim shadow, kept where the image is
  darker and the ground further just beyond the edge; placed by depth with
  0.5% range noise; 3-20 m. Replaces `rims_standin` behind the same call.
- `sim/lidar.py`: a forward lidar beside the camera (Calvin's request),
  120 x 33 deg, ray-cast on `scene.surface`, so it sees what the camera
  sees. Hazards: crater interiors (0.35 m below a 3 m ring of cells: 94%
  inside a mapped crater on `shoemaker.npz`), slopes over 15 deg across
  2 m. Rocks are off: no threshold found real ones at this scan density.
- `sim/rover.py`: lidar hazards block cells for the fine step check and
  the local planner from the moment they are seen, so the rover diverts as
  a crater comes into view (Calvin's request).
- The camera frame saved for the viewer carries the local plan as amber
  dots and lidar hazards as red marks (Calvin's request); vision reads the
  clean frame. `lidar/<agency>_<step>.jpg` per step, shown in a lidar
  panel under the camera.
- `--score-rims N` scores vision with `sim/rim_truth.py` when that file is
  present (it is on `step-6-rim-truth`, not yet in `integration`).

**Open**

- Run 2 with `--vision` is running; I will add its numbers here. Until
  both pass, `--vision` stays off by default.
- B's route is long with lidar on (638 m vs 280 m).
- Recall is low (15% of the front arc): the detector keeps only strong
  shadow edges, which keeps precision up.
- `scene.py` display lookup: `run_demo.py` now passes
  `display_dir=$LANTERN_DATA/terrain/display`, so `--display` works off
  Windows (your seam note `dcab185`).
